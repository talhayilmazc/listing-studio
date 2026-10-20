"""Worker jobs: set a profile up in other shops (v8 §C).

``link_profile``      reads the main shop's settings and each new shop's, links
                      every exact match at once (no question), and keeps what is
                      left for the seller: what could be created (with its
                      request cost) and what to choose from.
``create_link_resources``  creates, in each shop, only what the seller confirmed
                      (shipping profile with its destinations and upgrades,
                      return policy, processing profile), then links it. Lists are
                      read again first, so a setting that appeared meanwhile is
                      linked, not created twice.
``refresh_shop_links``  checks, in the background, that the ids a shop's links
                      hold are still in that shop (upkeep, one shop's lists for
                      every profile linked to it).

Both are started by the seller, but their requests are the app's preparation for
drafting in those shops: they count toward the app-wide daily budget and not
toward the seller's own ceiling (``gate.UPKEEP``).

Every request goes through the queue, the gate and the client (CLAUDE.md).
When Etsy refuses a copy, its own one-line reason is shown to that shop's owner
only, in plain words with the action that fixes it (``links.create_failure``):
"creating it in this shop failed; try again" alone gave the seller nothing to
act on, and retrying sent the same refused request again. The dropdown choices
are kept in Redis for a few hours only (the shop's own settings, shown to the
seller).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.core import limits
from app.core.config import get_settings
from app.db.models import ConnectionStatus, EtsyConnection, ListingProfile, ProfileShopLink, Tenant
from app.etsy.api import EtsyApiClient, RateLimitExceeded
from app.etsy.errors import EtsyError
from app.pipeline import links as L

logger = logging.getLogger(__name__)

#: Where a profile's open choices wait for the seller (shop -> resource -> choice).
PLAN_KEY = "link-plan:{profile_id}"
PLAN_TTL_SECONDS = 6 * 3600

FAILED = "setting it up in this shop failed; try again"


def shop_name(connection: EtsyConnection | None) -> str | None:
    if connection is None:
        return None
    return connection.display_name or connection.shop_name


def read_failure(exc: BaseException, shop: str | None) -> str:
    """Why a shop's lists could not be read, as the seller reads it."""
    here = shop or "this shop"
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return f"Etsy refused to show {here}'s settings; reconnect {here} on the Connect page, then try again"
    if isinstance(exc, EtsyError):
        from app.etsy.errors import explain

        return f"{here}'s settings could not be read: {explain(exc, here)}"
    return FAILED


def _client(ctx: dict[str, Any], http: httpx.AsyncClient, shop: uuid.UUID, *, upkeep: bool) -> EtsyApiClient:
    settings = get_settings()
    return EtsyApiClient(
        client_id=settings.etsy_client_id,
        shared_secret=settings.etsy_client_secret,
        http_client=http,
        bucket=ctx["bucket"],
        quota=ctx["quota"],
        usage=ctx.get("usage"),
        cache=ctx.get("redis"),
        shop=shop,
        upkeep=upkeep,
    )


async def _settings(client: EtsyApiClient, shop_id: int, kw: dict[str, Any], *, currency: bool) -> L.ShopSettings:
    out = L.ShopSettings(
        shipping=L.results(await client.get_shop_shipping_profiles(shop_id, **kw)),
        returns=L.results(await client.get_shop_return_policies(shop_id, **kw)),
        readiness=L.results(await client.get_shop_readiness_state_definitions(shop_id, **kw)),
        partners=L.results(await client.get_shop_production_partners(shop_id, **kw)),
    )
    if currency:
        out.currency = (await client.get_shop(shop_id, **kw)).get("currency_code")
    return out


async def _shop_id(session, client: EtsyApiClient, connection: EtsyConnection, kw: dict[str, Any]) -> int:  # noqa: ANN001
    from app.workers.profiles import _resolve_shop_id

    return await _resolve_shop_id(session, client, connection, kw)


async def _token(session, connection: EtsyConnection) -> str:  # noqa: ANN001
    from app.workers.profiles import _connection_service
    from app.workers.profiles import _token as token

    return await token(_connection_service(get_settings()), session, connection)


async def _plan_store(ctx: dict[str, Any], profile_id: uuid.UUID) -> dict[str, Any]:
    redis = ctx.get("redis")
    if redis is None:
        return {}
    raw = await redis.get(PLAN_KEY.format(profile_id=profile_id))
    return json.loads(raw) if raw else {}


async def _plan_save(ctx: dict[str, Any], profile_id: uuid.UUID, plan: dict[str, Any]) -> None:
    redis = ctx.get("redis")
    if redis is not None:
        await redis.set(PLAN_KEY.format(profile_id=profile_id), json.dumps(plan), ex=PLAN_TTL_SECONDS)


def _label(outcome: L.Outcome, value: Any) -> str | None:
    ids = value if isinstance(value, list) else [value]
    names = {int(o["id"]): o["label"] for o in outcome.options if o.get("id") is not None}
    found = [names.get(int(i)) for i in ids]
    return ", ".join(n for n in found if n) or None


def _apply(link: ProfileShopLink, outcomes: dict[str, L.Outcome], currency_note: str | None) -> dict[str, Any]:
    """Write what was linked onto the link; return, per resource, what the shop
    uses and how (same name, same terms, your choice, created) or what is left
    for the seller, with the shop's own list so any choice can be changed."""
    notes: dict[str, str] = {}
    left: dict[str, Any] = {}
    for resource, outcome in outcomes.items():
        key = L.PAYLOAD_KEY[resource]
        if not outcome.needed:
            setattr(link, key, [] if resource == L.PARTNERS else None)
            continue
        current = getattr(link, key)
        known = {int(o["id"]) for o in outcome.options if o.get("id") is not None}
        current_ids = (current if isinstance(current, list) else [current]) if current not in (None, []) else []
        # The seller's own choice (or a copy made earlier) stays while that shop still has it.
        kept = bool(current_ids) and all(int(i) in known for i in current_ids) and current != outcome.linked
        if kept or outcome.linked in (None, []) and current not in (None, []):
            left[resource] = {"linked": current, "how": "created" if outcome.how == "created" else "your choice",
                              "label": _label(outcome, current) or outcome.label, "options": outcome.options,
                              "source_label": outcome.source_label}
            continue
        if outcome.linked not in (None, []):
            setattr(link, key, outcome.linked)
            left[resource] = {"linked": outcome.linked, "how": outcome.how, "label": outcome.label or _label(outcome, outcome.linked),
                              "options": outcome.options, "source_label": outcome.source_label}
            continue
        notes[resource] = outcome.reason or L.NOT_FOUND
        left[resource] = {
            "reason": outcome.reason,
            "creatable": outcome.creatable,
            "requests": outcome.requests,
            "create": outcome.create,
            "options": outcome.options,
            "source_label": outcome.source_label,
        }
    if currency_note:
        notes["currency"] = currency_note
    link.notes = notes or None
    link.status = "incomplete" if notes else "ready"
    link.checked_at = datetime.now(timezone.utc)
    return left


async def _gated(ctx: dict[str, Any], function: str, arg: str, tenant_id: uuid.UUID, body: Any) -> str:
    from app.workers.profiles import _run_gated

    return await _run_gated(ctx, function, arg, tenant_id, body)


async def _profile_tenant(ctx: dict[str, Any], profile_id: str) -> uuid.UUID | None:
    async with ctx["sessionmaker"]() as session:
        profile = await session.get(ListingProfile, uuid.UUID(profile_id))
        return profile.tenant_id if profile is not None else None


# --- link_profile --------------------------------------------------------------------------------


async def link_profile(ctx: dict[str, Any], profile_id: str) -> str:
    tenant_id = await _profile_tenant(ctx, profile_id)
    if tenant_id is None:
        return "missing"
    return await _gated(ctx, "link_profile", profile_id, tenant_id, lambda: _link_profile(ctx, profile_id))


async def _fail_checking(ctx: dict[str, Any], profile_id: uuid.UUID, why: str) -> None:
    async with ctx["sessionmaker"]() as session:
        for link in (await session.execute(select(ProfileShopLink).where(
            ProfileShopLink.profile_id == profile_id, ProfileShopLink.status == "checking"
        ))).scalars():
            link.status, link.notes = "error", {"error": why}
        await session.commit()


async def _link_profile(ctx: dict[str, Any], profile_id: str) -> str:
    pid = uuid.UUID(profile_id)
    try:
        return await _link_profile_body(ctx, pid)
    except RateLimitExceeded:
        raise
    except Exception:  # noqa: BLE001 - the seller sees a fixed sentence, never Etsy's text
        logger.warning("profile %s: linking failed", profile_id, exc_info=True)
        await _fail_checking(ctx, pid, FAILED)
        return "failed"


async def _link_profile_body(ctx: dict[str, Any], pid: uuid.UUID) -> str:
    async with ctx["sessionmaker"]() as session:
        profile = await session.get(ListingProfile, pid)
        if profile is None:
            return "missing"
        if not profile.cached_payload:
            # The main shop's reference is needed to know what to link: read it first.
            from app.workers.profiles import refresh_profile

            await refresh_profile(ctx, str(pid))
            await session.refresh(profile)
        if not profile.cached_payload:
            await _fail_checking(ctx, pid, "the profile's reference listing could not be read; refresh the profile first")
            return "no-reference"
        main = await session.get(EtsyConnection, profile.connection_id)
        if main is None or main.status is not ConnectionStatus.active:
            await _fail_checking(ctx, pid, "the profile's main shop is not connected")
            return "no-connection"
        pending = list((await session.execute(select(ProfileShopLink).where(
            ProfileShopLink.profile_id == pid,
            ProfileShopLink.status == "checking",
            ProfileShopLink.connection_id != profile.connection_id,
        ))).scalars())
        if not pending:
            return "nothing"
        tenant = await session.get(Tenant, profile.tenant_id)
        payload = dict(profile.cached_payload)
        plan = await _plan_store(ctx, pid)

        async with httpx.AsyncClient(timeout=30.0) as http:
            source_client = _client(ctx, http, main.id, upkeep=True)
            kw = {"access_token": await _token(session, main), "tenant_id": profile.tenant_id,
                  "tenant_limit": limits.ceiling_limit(tenant) if tenant else None}
            try:
                source = await _settings(source_client, await _shop_id(session, source_client, main, kw), kw,
                                         currency=False)
            except EtsyError as exc:
                logger.warning("profile %s: reading the main shop's settings failed (%s)", pid, exc)
                why = read_failure(exc, shop_name(main))
                for link in pending:
                    link.status, link.notes = "error", {"error": why}
                await session.commit()
                return "failed"
            for link in pending:
                shop = await session.get(EtsyConnection, link.connection_id)
                if shop is None or shop.status is not ConnectionStatus.active or shop.tenant_id != profile.tenant_id:
                    link.status, link.notes = "error", {"error": "this shop is not connected"}
                    continue
                client = _client(ctx, http, shop.id, upkeep=True)
                try:
                    tkw = {**kw, "access_token": await _token(session, shop)}
                    target = await _settings(client, await _shop_id(session, client, shop, tkw), tkw, currency=True)
                except EtsyError as exc:
                    # One shop's refusal does not stop the others.
                    logger.warning("profile %s: reading shop %s failed (%s)", pid, shop.id, exc)
                    link.status, link.notes = "error", {"error": read_failure(exc, shop_name(shop))}
                    continue
                outcomes = L.plan_shop(payload, source, target, set(shop.scopes) if shop.scopes is not None else None)
                left = _apply(link, outcomes, L.currency_problem(payload.get("currency"), target.currency))
                plan[str(shop.id)] = left
        await session.commit()
    await _plan_save(ctx, pid, plan)
    return "linked"


# --- create_link_resources -----------------------------------------------------------------------


async def create_link_resources(ctx: dict[str, Any], profile_id: str) -> str:
    tenant_id = await _profile_tenant(ctx, profile_id)
    if tenant_id is None:
        return "missing"
    return await _gated(
        ctx, "create_link_resources", profile_id, tenant_id, lambda: _create_link_resources(ctx, profile_id)
    )


async def _create_link_resources(ctx: dict[str, Any], profile_id: str) -> str:
    pid = uuid.UUID(profile_id)
    try:
        return await _create_body(ctx, pid)
    except RateLimitExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - a refused copy is handled per setting in _create_body
        logger.warning("profile %s: creating shop settings failed", profile_id, exc_info=True)
        async with ctx["sessionmaker"]() as session:
            for link in (await session.execute(select(ProfileShopLink).where(
                ProfileShopLink.profile_id == pid, ProfileShopLink.pending_create.is_not(None)
            ))).scalars():
                shop = await session.get(EtsyConnection, link.connection_id)
                why = (read_failure(exc, shop_name(shop)) if isinstance(exc, EtsyError)
                       else "creating it in this shop failed; try again")
                link.pending_create = None
                link.status = "error"
                link.notes = {**(link.notes or {}), "error": why}
            await session.commit()
        return "failed"


class PartialCreate(Exception):
    """The shipping profile was created, but Etsy refused one of its destinations or
    upgrades (``what``: "the destination US"). The profile is left as it is in the shop:
    the app deletes nothing there; the seller finishes it in Shop Manager."""

    def __init__(self, new_id: int, what: str, cause: BaseException) -> None:
        super().__init__(f"created {new_id}, then {what} failed")
        self.new_id, self.what, self.cause = new_id, what, cause


async def _create(client: EtsyApiClient, shop_id: int, outcome: L.Outcome, kw: dict[str, Any]) -> int | None:
    plan = outcome.create or {}
    if outcome.resource == L.SHIPPING:
        made = await client.create_shop_shipping_profile(shop_id, data=L.send(plan["profile"]), **kw)
        new_id = int(made["shipping_profile_id"])
        for destination in plan.get("destinations") or []:
            try:
                await client.create_shop_shipping_profile_destination(shop_id, new_id, data=L.send(destination), **kw)
            except (EtsyError, httpx.TransportError) as exc:
                raise PartialCreate(new_id, f"the destination {destination.get('label') or ''}".strip(), exc) from exc
        for upgrade in plan.get("upgrades") or []:
            try:
                await client.create_shop_shipping_profile_upgrade(shop_id, new_id, data=L.send(upgrade), **kw)
            except (EtsyError, httpx.TransportError) as exc:
                raise PartialCreate(new_id, upgrade.get("label") or "an upgrade", exc) from exc
        return new_id
    if outcome.resource == L.RETURNS:
        made = await client.create_shop_return_policy(shop_id, data=plan, **kw)
        return int(made["return_policy_id"])
    if outcome.resource == L.READINESS:
        made = await client.create_shop_readiness_state_definition(shop_id, data=plan, **kw)
        return int(made["readiness_state_id"])
    return None


async def _create_body(ctx: dict[str, Any], pid: uuid.UUID) -> str:
    async with ctx["sessionmaker"]() as session:
        profile = await session.get(ListingProfile, pid)
        if profile is None or not profile.cached_payload:
            return "missing"
        main = await session.get(EtsyConnection, profile.connection_id)
        todo = list((await session.execute(select(ProfileShopLink).where(
            ProfileShopLink.profile_id == pid, ProfileShopLink.pending_create.is_not(None)
        ))).scalars())
        if not todo or main is None or main.status is not ConnectionStatus.active:
            return "nothing"
        tenant = await session.get(Tenant, profile.tenant_id)
        payload = dict(profile.cached_payload)
        plan = await _plan_store(ctx, pid)
        async with httpx.AsyncClient(timeout=30.0) as http:
            source_client = _client(ctx, http, main.id, upkeep=True)
            kw = {"access_token": await _token(session, main), "tenant_id": profile.tenant_id,
                  "tenant_limit": limits.ceiling_limit(tenant) if tenant else None}
            source = await _settings(source_client, await _shop_id(session, source_client, main, kw), kw, currency=False)
            for link in todo:
                shop = await session.get(EtsyConnection, link.connection_id)
                if shop is None or shop.status is not ConnectionStatus.active or shop.tenant_id != profile.tenant_id:
                    link.pending_create = None
                    continue
                client = _client(ctx, http, shop.id, upkeep=True)
                tkw = {**kw, "access_token": await _token(session, shop)}
                shop_id = await _shop_id(session, client, shop, tkw)
                # Read again: what appeared since the plan is linked, not made twice.
                target = await _settings(client, shop_id, tkw, currency=True)
                outcomes = L.plan_shop(payload, source, target, set(shop.scopes) if shop.scopes is not None else None)
                confirmed = set(link.pending_create or [])
                failures: dict[str, dict[str, Any]] = {}
                for resource in L.RESOURCES:
                    outcome = outcomes[resource]
                    if resource in confirmed and outcome.creatable and outcome.linked is None:
                        try:
                            new_id = await _create(client, shop_id, outcome, tkw)
                        except RateLimitExceeded:
                            raise
                        except PartialCreate as part:
                            logger.warning("profile %s: %s in shop %s (%s)", pid, part, shop.id, part.cause)
                            failures[resource] = L.create_failure(
                                part.cause, resource, shop=shop_name(shop), main=shop_name(main),
                                source_label=outcome.source_label, made=part.what)
                            continue
                        except (EtsyError, httpx.TransportError) as exc:
                            # Etsy's body is in the client's own log line; the shape of what was sent here.
                            logger.warning("profile %s: creating %s in shop %s refused (%s); sent %s", pid, resource,
                                           shop.id, exc, getattr(exc, "sent", None))
                            failures[resource] = L.create_failure(
                                exc, resource, shop=shop_name(shop), main=shop_name(main),
                                source_label=outcome.source_label)
                            continue
                        outcome.linked, outcome.reason, outcome.how = new_id, None, "created"
                        outcome.label = outcome.source_label
                        outcome.options = [*outcome.options, {"id": new_id, "label": outcome.source_label or str(new_id)}]
                link.pending_create = None
                left = _apply(link, outcomes, L.currency_problem(payload.get("currency"), target.currency))
                if failures:
                    # Each refused copy says why, on its own setting; the shop is waiting for a
                    # choice (or a Refresh), not broken: the dialog shows every setting again.
                    notes = dict(link.notes or {})
                    for resource, failure in failures.items():
                        left.setdefault(resource, {})["failure"] = failure
                        notes[resource] = failure["reason"]
                    link.notes, link.status = notes, "incomplete"
                plan[str(shop.id)] = left
                await session.commit()  # each shop's ids as soon as they exist
        await session.commit()
    await _plan_save(ctx, pid, plan)
    return "created"


# --- refresh_shop_links (upkeep) -----------------------------------------------------------------


async def refresh_shop_links(ctx: dict[str, Any], connection_id: str) -> str:
    async with ctx["sessionmaker"]() as session:
        shop = await session.get(EtsyConnection, uuid.UUID(connection_id))
        if shop is None or shop.status is not ConnectionStatus.active:
            return "no-connection"
        tenant_id = shop.tenant_id
    return await _gated(ctx, "refresh_shop_links", connection_id, tenant_id, lambda: _refresh_shop_links(ctx, connection_id))


async def _refresh_shop_links(ctx: dict[str, Any], connection_id: str) -> str:
    cid = uuid.UUID(connection_id)
    async with ctx["sessionmaker"]() as session:
        shop = await session.get(EtsyConnection, cid)
        if shop is None:
            return "no-connection"
        rows = list((await session.execute(
            select(ProfileShopLink, ListingProfile)
            .join(ListingProfile, ListingProfile.id == ProfileShopLink.profile_id)
            .where(ProfileShopLink.connection_id == cid, ListingProfile.connection_id != cid,
                   ProfileShopLink.status.in_(("ready", "incomplete")))
        )).all())
        if not rows:
            return "nothing"
        async with httpx.AsyncClient(timeout=30.0) as http:
            client = _client(ctx, http, shop.id, upkeep=True)
            kw = {"access_token": await _token(session, shop), "tenant_id": shop.tenant_id, "tenant_limit": None}
            target = await _settings(client, await _shop_id(session, client, shop, kw), kw, currency=False)
        now = datetime.now(timezone.utc)
        for link, _profile in rows:
            notes = dict(link.notes or {})
            for resource in L.RESOURCES:
                key = L.PAYLOAD_KEY[resource]
                value = getattr(link, key)
                if value in (None, []):
                    continue
                ids = value if isinstance(value, list) else [value]
                if not all(target.find(resource, int(i)) for i in ids):
                    setattr(link, key, [] if resource == L.PARTNERS else None)
                    notes[resource] = "it is no longer in this shop; choose another or set it up again"
            link.notes = notes or None
            link.status = "incomplete" if notes else "ready"
            link.checked_at = now
        await session.commit()
    return "checked"


async def link_plan(ctx: dict[str, Any], profile_id: uuid.UUID) -> dict[str, Any]:
    """What is left for the seller, per shop (read by the API)."""
    return await _plan_store(ctx, profile_id)
