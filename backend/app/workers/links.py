"""Worker jobs: set a profile up in other shops (v8 §C).

``link_profile``      sets a profile up in each shop asked for. Every shop setting its
                      reference uses (shipping profile, return policy, processing
                      profile) is resolved in order: the account's map
                      (``pipeline/setting_map.py``: one counterpart per setting and shop,
                      shared by every reference listing), the same name, the same terms,
                      then a copy made automatically. The seller is asked only when Etsy
                      refuses, two settings match, or nothing can be copied; then the
                      setup dialog says why. Production partners match by name.
``create_link_resources``  a copy the seller chose in that dialog: the same resolution.
``refresh_shop_links``  checks, in the background, that the ids a shop's links hold are
                      still in that shop; one that is gone (deleted on Etsy) is resolved
                      again automatically (upkeep).
``resolve_shop_settings_once``  once after the deploy that added the map: every link is
                      filled from it, and what is still missing is resolved again.

Every copy is made under a lock per setting and shop, after the map and the shop's own
list (read fresh) are checked again: two reference listings or two jobs never make two
copies of one setting in one shop. A shop's lists are kept for an hour (other Etsy
content, well inside its 24 hours): a hundred reference listings set up in a hundred
shops read each shop once. Refresh drops that copy.

Started by the seller, but their requests are the app's preparation for drafting in
those shops: they count toward the app-wide daily budget and not toward the seller's
own ceiling (``gate.UPKEEP``). Every request goes through the queue, the gate and the
client (CLAUDE.md). When Etsy refuses a copy, its own one-line reason is shown to that
shop's owner only, in plain words with the action that fixes it (``links.create_failure``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.etsy.connection import renewer_for as _renewer
from app.core import limits
from app.core.config import get_settings
from app.db.models import ConnectionStatus, EtsyConnection, ListingProfile, ProfileShopLink, Tenant
from app.etsy.api import EtsyApiClient, RateLimitExceeded
from app.etsy.errors import EtsyError
from app.pipeline import links as L
from app.pipeline import setting_map

logger = logging.getLogger(__name__)

#: Where a profile's open choices wait for the seller (shop -> resource -> choice).
PLAN_KEY = "link-plan:{profile_id}"
PLAN_TTL_SECONDS = 6 * 3600

FAILED = "setting it up in this shop failed; try again"

#: A shop's four lists (and its currency), kept an hour: other Etsy content (24 h at most).
LISTS_KEY = "shop-settings:{connection_id}"
LISTS_TTL_SECONDS = 3600
#: Copies Etsy left half made in a shop (id -> the seller's message), until Refresh.
PARTIAL_KEY = "shop-settings-partial:{connection_id}"
PARTIAL_TTL_SECONDS = 30 * 86400
#: One copy of one setting in one shop at a time.
COPY_LOCK = "setting-copy:{target}:{resource}:{source}:{source_id}"
COPY_LOCK_SECONDS = 300
COPY_WAIT_SECONDS = 120.0
COPY_POLL_SECONDS = 0.25
#: The one-time job after migration 0082 ran.
ONCE_KEY = "once:shop-setting-map:v2"


def shop_name(connection: EtsyConnection | None) -> str | None:
    if connection is None:
        return None
    return connection.display_name or connection.shop_name


def read_failure(exc: BaseException, shop: str | None) -> str:
    """Why a shop's lists could not be read, as the seller reads it."""
    here = shop or "this shop"
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return f"Etsy refused to show {here}'s settings; reconnect {here} on Shops & groups, then try again"
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
        renew=_renewer(ctx, shop),
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


# --- A shop's lists, kept a while (other Etsy content: well inside its 24 hours) -----------------


def _lists_key(connection_id: uuid.UUID) -> str:
    return LISTS_KEY.format(connection_id=connection_id)


async def _cached_lists(ctx: dict[str, Any], connection_id: uuid.UUID) -> dict[str, Any] | None:
    redis = ctx.get("redis")
    if redis is None:
        return None
    raw = await redis.get(_lists_key(connection_id))
    return json.loads(raw) if raw else None


async def _cache_lists(ctx: dict[str, Any], connection_id: uuid.UUID, lists: L.ShopSettings) -> None:
    redis = ctx.get("redis")
    if redis is not None:
        data = {"shipping": lists.shipping, "returns": lists.returns, "readiness": lists.readiness,
                "partners": lists.partners, "currency": lists.currency}
        await redis.set(_lists_key(connection_id), json.dumps(data), ex=LISTS_TTL_SECONDS)


async def shop_lists(ctx: dict[str, Any], client: EtsyApiClient, shop_id: int, connection_id: uuid.UUID,
                     kw: dict[str, Any], *, currency: bool) -> L.ShopSettings:
    """A shop's shipping profiles, return policies, processing profiles and production
    partners: from the copy read in the last hour, else from Etsy (four requests, and one
    for the shop's currency). A hundred reference listings set up in a hundred shops read
    each shop once, not once per reference listing. Refresh drops the copy
    (``forget_lists``); a copy made here is added to it."""
    cached = await _cached_lists(ctx, connection_id)
    if cached is not None:
        lists = L.ShopSettings(shipping=cached.get("shipping") or [], returns=cached.get("returns") or [],
                               readiness=cached.get("readiness") or [], partners=cached.get("partners") or [],
                               currency=cached.get("currency"))
        if currency and lists.currency is None:
            lists.currency = (await client.get_shop(shop_id, **kw)).get("currency_code")
            await _cache_lists(ctx, connection_id, lists)
        return lists
    lists = await _settings(client, shop_id, kw, currency=currency)
    await _cache_lists(ctx, connection_id, lists)
    return lists


async def _put_rows(ctx: dict[str, Any], connection_id: uuid.UUID, resource: str, rows: list[dict[str, Any]]) -> None:
    """One list of a shop as just read (with a copy just made), into the kept copy."""
    cached = await _cached_lists(ctx, connection_id)
    if cached is None:
        return
    field = {L.SHIPPING: "shipping", L.RETURNS: "returns", L.READINESS: "readiness"}[resource]
    cached[field] = rows
    redis = ctx.get("redis")
    await redis.set(_lists_key(connection_id), json.dumps(cached), ex=LISTS_TTL_SECONDS)


async def forget_lists(redis: Any, connection_id: uuid.UUID) -> None:
    """Refresh: the shop's lists are read from Etsy again, and a copy left half made there
    may be used again (the seller finished it in Shop Manager)."""
    if redis is not None:
        await redis.delete(_lists_key(connection_id), PARTIAL_KEY.format(connection_id=connection_id))


async def _read_rows(client: EtsyApiClient, shop_id: int, resource: str, kw: dict[str, Any]) -> list[dict[str, Any]]:
    read = {L.SHIPPING: client.get_shop_shipping_profiles, L.RETURNS: client.get_shop_return_policies,
            L.READINESS: client.get_shop_readiness_state_definitions}[resource]
    return L.results(await read(shop_id, **kw))


async def _partials(ctx: dict[str, Any], connection_id: uuid.UUID) -> dict[int, dict[str, Any]]:
    """Copies Etsy left half made in a shop (made, then a destination or upgrade refused):
    never used, and never copied again, until the seller finishes one and presses Refresh."""
    redis = ctx.get("redis")
    if redis is None:
        return {}
    raw = await redis.hgetall(PARTIAL_KEY.format(connection_id=connection_id))
    return {int(k): json.loads(v) for k, v in (raw or {}).items()}


async def _mark_partial(ctx: dict[str, Any], connection_id: uuid.UUID, new_id: int, failure: dict[str, Any]) -> None:
    redis = ctx.get("redis")
    if redis is not None:
        key = PARTIAL_KEY.format(connection_id=connection_id)
        await redis.hset(key, str(new_id), json.dumps(failure))
        await redis.expire(key, PARTIAL_TTL_SECONDS)


# --- One copy per setting and shop ---------------------------------------------------------------


async def _drop_temporary(client: EtsyApiClient, shop_id: int, profile_id: int, country: str,
                          made: dict[str, Any], kw: dict[str, Any]) -> None:
    """An "everywhere else"-only copy was created with the ship-from country as a temporary
    destination (Etsy's create call needs a country or a region); "everywhere else" is in
    now, so that destination goes. If it cannot be deleted the copy still works the same:
    the ship-from country is charged exactly what "everywhere else" is."""
    def find(profile: dict[str, Any]) -> int | None:
        for d in profile.get("shipping_profile_destinations") or []:
            if str(d.get("destination_country_iso") or "").upper() == country and d.get("shipping_profile_destination_id"):
                return int(d["shipping_profile_destination_id"])
        return None
    try:
        destination = find(made) or find(await client.get_shop_shipping_profile(shop_id, profile_id, **kw))
        if destination is not None:
            await client.delete_shop_shipping_profile_destination(shop_id, profile_id, destination, **kw)
    except (EtsyError, httpx.TransportError) as exc:
        logger.warning("shipping copy %s: the temporary %s destination stays (%s)", profile_id, country, exc)


class CopyBusy(Exception):
    """Another setup is making this copy in this shop right now: it is used once it exists."""


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
        temporary = (plan.get("profile") or {}).get(L.TEMPORARY)
        if temporary:
            await _drop_temporary(client, shop_id, new_id, str(temporary), made, kw)
        return new_id
    if outcome.resource == L.RETURNS:
        made = await client.create_shop_return_policy(shop_id, data=plan, **kw)
        return int(made["return_policy_id"])
    if outcome.resource == L.READINESS:
        made = await client.create_shop_readiness_state_definition(shop_id, data=plan, **kw)
        return int(made["readiness_state_id"])
    return None


async def ensure_copy(
    ctx: dict[str, Any], session: Any, client: EtsyApiClient, shop_id: int, kw: dict[str, Any], *,
    tenant_id: uuid.UUID, source: uuid.UUID, source_id: int, source_row: dict[str, Any], target: uuid.UUID,
    outcome: L.Outcome,
) -> tuple[int, str, str | None]:
    """The shop's counterpart of one setting, made at most once.

    Under a lock per setting and shop (two reference listings, or two jobs, setting the
    same shop up at once wait for each other): the map again (the other may have made it
    meanwhile), then the shop's own list read fresh from Etsy, where a setting with the same
    name (shipping) or the same terms (returns, processing) is used; only then a copy. The
    map is written and committed before the lock is released. Returns (id, how, label)."""
    redis = ctx.get("redis")
    key = COPY_LOCK.format(target=target, resource=outcome.resource, source=source, source_id=source_id)
    token = uuid.uuid4().hex
    held = False
    if redis is not None:
        waited = 0.0
        while not (held := bool(await redis.set(key, token, nx=True, ex=COPY_LOCK_SECONDS))):
            if waited >= COPY_WAIT_SECONDS:
                raise CopyBusy()
            await asyncio.sleep(COPY_POLL_SECONDS)
            waited += COPY_POLL_SECONDS
    try:
        row = await setting_map.lookup(session, tenant_id, outcome.resource, source, source_id, target)
        if row is not None:
            return int(row.target_id), row.how, row.label
        rows = await _read_rows(client, shop_id, outcome.resource, kw)
        partial = await _partials(ctx, target)
        found, why = L.match_in(outcome.resource, source_row, rows)
        if found is not None and found in partial:
            raise HalfMade(partial[found])  # never used, never copied again, until it is finished
        if found is not None:
            new_id, how = found, "same name" if outcome.resource == L.SHIPPING else "same terms"
            label = L.label_of(outcome.resource, next(r for r in rows if L.id_of(outcome.resource, r) == found))
        elif why == L.AMBIGUOUS:
            raise Ambiguous()
        else:
            id_key = {L.SHIPPING: "shipping_profile_id", L.RETURNS: "return_policy_id",
                      L.READINESS: "readiness_state_id"}[outcome.resource]
            try:
                made = await _create(client, shop_id, outcome, kw)
            except PartialCreate as part:
                # In the shop now, half made: the kept list says so, so nothing copies it again.
                await _put_rows(ctx, target, outcome.resource,
                                [*rows, {"title": source_row.get("title"), id_key: part.new_id}])
                raise
            assert made is not None
            new_id, how, label = made, "created", outcome.source_label
            rows = [*rows, {**({"title": source_row.get("title")} if outcome.resource == L.SHIPPING else source_row),
                            {L.SHIPPING: "shipping_profile_id", L.RETURNS: "return_policy_id",
                             L.READINESS: "readiness_state_id"}[outcome.resource]: new_id}]
        await setting_map.remember(session, tenant_id, outcome.resource, source, source_id, target, new_id,
                                   how=how, label=label)
        await session.commit()
        await _put_rows(ctx, target, outcome.resource, rows)
        return new_id, how, label
    finally:
        if held and redis is not None and await redis.get(key) in (token, token.encode()):
            await redis.delete(key)


class Ambiguous(Exception):
    """Read fresh, the shop has two settings that match: the seller chooses."""


class HalfMade(Exception):
    """Read fresh, the shop's match is a copy Etsy left half made: the seller finishes it."""

    def __init__(self, failure: dict[str, Any]) -> None:
        super().__init__("half-made copy")
        self.failure = failure


# --- Resolving one profile in one shop -----------------------------------------------------------


def _notice(resource: str, here: str, lost: str | None, how: str, label: str | None) -> str | None:
    what = L.LABEL[resource]
    if lost:
        now = "a new copy was made" if how == "created" else f"now using \"{label}\" ({how})"
        return f"{what} \"{lost}\" is no longer in {here}: {now}."
    if how == "created":
        return f"{what} \"{label}\" was created in {here}, a copy of the main shop's."
    return None


async def _resolve(
    ctx: dict[str, Any], session: Any, client: EtsyApiClient, shop_id: int, kw: dict[str, Any], *,
    profile: ListingProfile, payload: dict[str, Any], main: EtsyConnection, shop: EtsyConnection,
    link: ProfileShopLink, source: L.ShopSettings, target: L.ShopSettings,
) -> dict[str, Any]:
    """Every shop setting of one profile in one shop, in order: the account's map (a choice
    the seller made, or what was found before), the same name, the same terms, then a copy
    made at once. A reference listing uses its main shop's settings in every shop, with no
    question (since 21 October 2026). Never made again: a setting the seller made themselves
    that was deleted on Etsy (a tombstone in the map, ``setting_map.GONE``, until the seller
    picks another). Returns what is left for the seller (shown as one line on the card)."""
    granted = set(shop.scopes) if shop.scopes is not None else None
    outcomes = L.plan_shop(payload, source, target, granted)
    currency_note = L.currency_problem(payload.get("currency"), target.currency)
    here = shop_name(shop) or "this shop"
    failures: dict[str, dict[str, Any]] = {}
    waiting: dict[str, str] = {}
    notices: list[str] = []
    partial = await _partials(ctx, shop.id)
    for resource in setting_map.MAPPED:
        outcome = outcomes[resource]
        wanted = L.wanted(payload, resource)
        if not outcome.needed or wanted in (None, "", []):
            continue
        source_id = int(wanted)
        source_row = source.find(resource, source_id)
        lost: str | None = None
        gone: str | None = None  # a seller-made setting deleted on Etsy: matched again, never remade
        mapped = await setting_map.lookup(session, profile.tenant_id, resource, main.id, source_id, shop.id)
        if mapped is not None and mapped.how == setting_map.GONE:
            gone = mapped.label or "it"
        elif mapped is not None:
            row = target.find(resource, int(mapped.target_id))
            if row is not None and not row.get("is_deleted"):
                outcome.linked, outcome.how, outcome.reason = int(mapped.target_id), mapped.how, None
                outcome.label = L.label_of(resource, row)
                continue
            # Deleted on Etsy: resolved again below. The app's own copy is made again; a
            # setting the seller made is not (a tombstone keeps that until they pick another).
            lost = mapped.label or (L.label_of(resource, row) if row else None) or f"#{mapped.target_id}"
            if mapped.how == "created":
                await setting_map.forget(session, mapped)
            else:
                gone = lost
                await setting_map.remember(session, profile.tenant_id, resource, main.id, source_id, shop.id, 0,
                                           how=setting_map.GONE, label=lost)
        if outcome.linked not in (None, []):
            if int(outcome.linked) in partial:  # the half-made copy: the seller finishes it first
                failures[resource] = partial[int(outcome.linked)]
                outcome.linked = None
                continue
            await setting_map.remember(session, profile.tenant_id, resource, main.id, source_id, shop.id,
                                       int(outcome.linked), how=outcome.how or "same name", label=outcome.label)
            if note := _notice(resource, here, lost, outcome.how or "same name", outcome.label):
                notices.append(note)
            continue
        if gone is not None:
            # Theirs, deleted in Shop Manager: not made again. One line on the card.
            outcome.reason, outcome.creatable = (f"\"{gone}\" was deleted in {here}'s Shop Manager; choose another "
                                                 "of the shop's with change"), False
            continue
        if not outcome.creatable or currency_note or source_row is None:
            continue  # nothing can be copied (the reason is the outcome's): the seller picks with change
        try:
            new_id, how, label = await ensure_copy(
                ctx, session, client, shop_id, kw, tenant_id=profile.tenant_id, source=main.id,
                source_id=source_id, source_row=source_row, target=shop.id, outcome=outcome)
        except RateLimitExceeded:
            raise
        except CopyBusy:
            waiting[resource] = f"being set up in {here} by another setup; it is used as soon as it exists"
            continue
        except Ambiguous:
            outcome.reason, outcome.creatable = L.AMBIGUOUS, False
            continue
        except HalfMade as half:
            failures[resource] = half.failure
            continue
        except PartialCreate as part:
            logger.warning("profile %s: %s in shop %s (%s)", profile.id, part, shop.id, part.cause)
            failures[resource] = L.create_failure(part.cause, resource, shop=here, main=shop_name(main),
                                                  source_label=outcome.source_label, made=part.what)
            await _mark_partial(ctx, shop.id, part.new_id, failures[resource])
            continue
        except (EtsyError, httpx.TransportError) as exc:
            # Etsy's body is in the client's own log line; the shape of what was sent here.
            logger.warning("profile %s: creating %s in shop %s refused (%s); sent %s", profile.id, resource,
                           shop.id, exc, getattr(exc, "sent", None))
            failures[resource] = L.create_failure(exc, resource, shop=here, main=shop_name(main),
                                                  source_label=outcome.source_label)
            continue
        outcome.linked, outcome.how, outcome.label, outcome.reason = new_id, how, label, None
        if all(o.get("id") != new_id for o in outcome.options):
            outcome.options = [*outcome.options, {"id": new_id, "label": label or str(new_id)}]
        if note := _notice(resource, here, lost, how, label):
            notices.append(note)
    # The shop's ids for every mapped setting are what was just resolved, never an older
    # per-link value (one the shop no longer has, or another reference listing's choice).
    for resource in setting_map.MAPPED:
        if outcomes[resource].needed:
            setattr(link, L.PAYLOAD_KEY[resource], outcomes[resource].linked)
    link.pending_create = None
    left = _apply(link, outcomes, currency_note)
    if failures or waiting or notices:
        notes = dict(link.notes or {})
        for resource, failure in failures.items():
            left.setdefault(resource, {})["failure"] = failure
            notes[resource] = failure["reason"]
        notes.update(waiting)
        if notices:
            notes["notice"] = " ".join(notices)
        link.notes = notes
        if failures or waiting:
            link.status = "incomplete"
    return left


# --- link_profile / create_link_resources --------------------------------------------------------


async def link_profile(ctx: dict[str, Any], profile_id: str) -> str:
    tenant_id = await _profile_tenant(ctx, profile_id)
    if tenant_id is None:
        return "missing"
    return await _gated(ctx, "link_profile", profile_id, tenant_id, lambda: _link_profile(ctx, profile_id))


async def create_link_resources(ctx: dict[str, Any], profile_id: str) -> str:
    """A copy the seller chose in the setup dialog (after Etsy refused one, say): the same
    resolution as ``link_profile``, which makes the copy if the shop still has none."""
    tenant_id = await _profile_tenant(ctx, profile_id)
    if tenant_id is None:
        return "missing"
    return await _gated(ctx, "create_link_resources", profile_id, tenant_id, lambda: _link_profile(ctx, profile_id))


async def _create_link_resources(ctx: dict[str, Any], profile_id: str) -> str:
    return await _link_profile(ctx, profile_id)


async def _fail_checking(ctx: dict[str, Any], profile_id: uuid.UUID, why: str) -> None:
    async with ctx["sessionmaker"]() as session:
        for link in (await session.execute(select(ProfileShopLink).where(
            ProfileShopLink.profile_id == profile_id, ProfileShopLink.status == "checking"
        ))).scalars():
            link.status, link.notes, link.pending_create = "error", {"error": why}, None
        await session.commit()


async def _link_profile(ctx: dict[str, Any], profile_id: str) -> str:
    pid = uuid.UUID(profile_id)
    try:
        return await _link_profile_body(ctx, pid)
    except RateLimitExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - the seller sees a sentence, never a trace
        logger.warning("profile %s: linking failed", profile_id, exc_info=True)
        await _fail_checking(ctx, pid, read_failure(exc, None) if isinstance(exc, EtsyError) else FAILED)
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
                source_shop = await _shop_id(session, source_client, main, kw)
                source = await shop_lists(ctx, source_client, source_shop, main.id, kw, currency=False)
            except EtsyError as exc:
                logger.warning("profile %s: reading the main shop's settings failed (%s)", pid, exc)
                why = read_failure(exc, shop_name(main))
                for link in pending:
                    link.status, link.notes, link.pending_create = "error", {"error": why}, None
                await session.commit()
                return "failed"
            for link in pending:
                shop = await session.get(EtsyConnection, link.connection_id)
                if shop is None or shop.status is not ConnectionStatus.active or shop.tenant_id != profile.tenant_id:
                    link.status, link.notes, link.pending_create = "error", {"error": "this shop is not connected"}, None
                    continue
                client = _client(ctx, http, shop.id, upkeep=True)
                try:
                    tkw = {**kw, "access_token": await _token(session, shop)}
                    shop_id = await _shop_id(session, client, shop, tkw)
                    target = await shop_lists(ctx, client, shop_id, shop.id, tkw, currency=True)
                except EtsyError as exc:
                    # One shop's refusal does not stop the others.
                    logger.warning("profile %s: reading shop %s failed (%s)", pid, shop.id, exc)
                    link.status, link.notes, link.pending_create = "error", {"error": read_failure(exc, shop_name(shop))}, None
                    continue
                plan[str(shop.id)] = await _resolve(
                    ctx, session, client, shop_id, tkw, profile=profile, payload=payload, main=main, shop=shop,
                    link=link, source=source, target=target)
                await session.commit()  # each shop's ids as soon as they exist
                # Every other reference listing in this shop that uses the same settings, at once.
                await setting_map.apply(session, profile.tenant_id, target=shop.id)
                await session.commit()
        await session.commit()
    await _plan_save(ctx, pid, plan)
    return "linked"


# --- refresh_shop_links (upkeep) -----------------------------------------------------------------


async def refresh_shop_links(ctx: dict[str, Any], connection_id: str) -> str:
    async with ctx["sessionmaker"]() as session:
        shop = await session.get(EtsyConnection, uuid.UUID(connection_id))
        if shop is None or shop.status is not ConnectionStatus.active:
            return "no-connection"
        tenant_id = shop.tenant_id
    return await _gated(ctx, "refresh_shop_links", connection_id, tenant_id, lambda: _refresh_shop_links(ctx, connection_id))


async def _refresh_shop_links(ctx: dict[str, Any], connection_id: str) -> str:
    """The shop's lists read fresh; a setting a link uses that is no longer there (deleted
    on Etsy) is resolved again automatically: matched, or copied again (``link_profile``),
    and the card says so in one line."""
    from app.workers.profiles import _enqueue_job

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
        cached = await _cached_lists(ctx, cid)
        target.currency = (cached or {}).get("currency")
        await _cache_lists(ctx, cid, target)  # read just now: fresh for the setups that follow
        now = datetime.now(timezone.utc)
        again: set[uuid.UUID] = set()
        for link, profile in rows:
            notes = dict(link.notes or {})
            gone = False
            for resource in L.RESOURCES:
                key = L.PAYLOAD_KEY[resource]
                value = getattr(link, key)
                if value in (None, []):
                    continue
                ids = value if isinstance(value, list) else [value]
                if all((row := target.find(resource, int(i))) and not row.get("is_deleted") for i in ids):
                    continue
                if resource in setting_map.MAPPED:
                    gone = True  # resolved again: the map is checked against this list there
                else:
                    setattr(link, key, [])
                    notes[resource] = "it is no longer in this shop; choose another or set it up again"
            link.notes = notes or None
            link.checked_at = now
            if gone:
                link.status = "checking"
                again.add(profile.id)
            else:
                link.status = "incomplete" if L.missing(profile.cached_payload, link) or notes else "ready"
        await session.commit()
    for profile_id in sorted(again, key=str):
        await _enqueue_job(ctx, "link_profile", str(profile_id))
    return f"checked:{len(again)} re-resolved"


async def link_plan(ctx: dict[str, Any], profile_id: uuid.UUID) -> dict[str, Any]:
    """What is left for the seller, per shop (read by the API)."""
    return await _plan_store(ctx, profile_id)


# --- A draft found a mapped setting gone (Etsy's 404 for that id) -------------------------------

#: Etsy's refusal names the setting (``errors.foreign_setting``), as the seller knows it.
_GONE = {"shipping profile": L.SHIPPING, "return policy": L.RETURNS, "processing profile": L.READINESS}


async def setting_gone(session: Any, redis: Any, job: Any, exc: BaseException) -> uuid.UUID | None:
    """A draft in a linked shop was refused because that shop does not have the setting the
    link holds ("Could not find shipping_profile_id='…' associated with shop"): it was
    deleted on Etsy. The mapping goes, the shop's kept lists go, and the profile is set up
    there again (matched, or copied again); returns the profile to queue ``link_profile``
    for. Does not commit."""
    from app.db.models import GeneratedContent
    from app.etsy.errors import foreign_setting
    from app.pipeline import profile_shops

    resource = _GONE.get(foreign_setting(getattr(exc, "detail", None)) or "")
    content_id = (job.payload or {}).get("content_id")
    if resource is None or job.connection_id is None or not content_id:
        return None
    content = await session.get(GeneratedContent, uuid.UUID(str(content_id)))
    chosen = (job.payload or {}).get("profile_id") or (content.listing_profile_id if content else None)
    profile = await session.get(ListingProfile, uuid.UUID(str(chosen))) if chosen else None
    if profile is None or profile.tenant_id != job.tenant_id or profile.connection_id == job.connection_id:
        return None
    link = await profile_shops.link_for(session, profile, job.connection_id)
    if link is None:
        return None
    held = getattr(link, L.PAYLOAD_KEY[resource])
    refused = re.search(r"=\s*'?(\d+)'?", getattr(exc, "detail", None) or "")
    if held is None or (refused is not None and int(refused.group(1)) != int(held)):
        return None  # not the id this shop's link holds (another shop's id was sent): nothing was deleted
    await setting_map.forget_target(session, job.tenant_id, resource, job.connection_id, int(held))
    link.status, link.pending_create = "checking", None
    await forget_lists(redis, job.connection_id)
    return profile.id


# --- Once after deploy: every account's links from the map ---------------------------------------


async def resolve_all(session: Any, *, apply: bool, email: str | None = None) -> dict[str, Any]:
    """Put every account's reference listings right, after a deploy:

    * a profile whose reference listing is, by the account's own listing cache, in another
      of its shops gets that shop as its main shop (``profile_shops.move_main``; no Etsy
      request; its ids are that listing's own);
    * every profile link is filled from the map (database only);
    * links still lacking a shipping profile, return policy or processing profile are
      resolved again (``link_profile``: the map, the same name or terms, else a copy made at
      once; each shop's lists read at most once an hour).

    Without ``apply`` nothing is written. Returns the counts and the profiles to queue."""
    from app.db.models import ShopListingCache
    from app.pipeline import profile_shops

    query = select(Tenant.id)
    if email:
        query = query.where(Tenant.email == email.strip().lower())
    tenants = list((await session.execute(query)).scalars())
    moved: list[uuid.UUID] = []
    wrong = (await session.execute(
        select(ListingProfile, ShopListingCache.connection_id)
        .join(ShopListingCache, (ShopListingCache.tenant_id == ListingProfile.tenant_id)
              & (ShopListingCache.listing_id == ListingProfile.reference_listing_id))
        .join(EtsyConnection, EtsyConnection.id == ShopListingCache.connection_id)
        .where(ListingProfile.tenant_id.in_(tenants), ShopListingCache.connection_id != ListingProfile.connection_id,
               EtsyConnection.status == ConnectionStatus.active)
    )).all()
    for profile, shop in wrong:
        await profile_shops.move_main(session, profile, shop)
        moved.append(profile.id)
    filled = 0
    for tenant_id in tenants:
        filled += await setting_map.apply(session, tenant_id)
    again: list[uuid.UUID] = []
    links = 0
    rows = (await session.execute(
        select(ProfileShopLink, ListingProfile)
        .join(ListingProfile, ListingProfile.id == ProfileShopLink.profile_id)
        .where(ProfileShopLink.tenant_id.in_(tenants), ProfileShopLink.connection_id != ListingProfile.connection_id,
               ProfileShopLink.status.in_(("incomplete", "error")), ListingProfile.confirmed.is_(True))
    )).all()
    for link, profile in rows:
        if not profile.cached_payload or (link.notes or {}).get("currency"):
            continue
        missing = L.missing(profile.cached_payload, link)
        if link.status == "incomplete" and not any(r in missing for r in setting_map.MAPPED):
            continue  # only a production partner is left: the seller's choice
        link.status, link.pending_create = "checking", [L.COPY_ALL]
        links += 1
        if profile.id not in again:
            again.append(profile.id)
    for profile_id in moved:  # their links were set to be resolved by the move
        if profile_id not in again:
            again.append(profile_id)
    if apply:
        await session.commit()
    else:
        await session.rollback()
    return {"accounts": len(tenants), "links_filled": filled, "links_to_resolve": links, "profiles": again,
            "main_shops_corrected": len(moved), "refresh": moved}


async def resolve_shop_settings_once(ctx: dict[str, Any]) -> str:
    """Cron, at worker start: once after the deploy that added the map (migration 0082),
    every account's links are filled from it and what is still missing is resolved again.
    A second run changes nothing (the map is already applied); the Redis marker keeps it to
    one. Not run with ETSY_CALLS_BLOCKED (a development machine's real accounts stay as
    they are; ``python -m app.cli resolve-shop-settings`` does it by hand)."""
    from app.workers.profiles import _enqueue_job

    if get_settings().etsy_calls_blocked:
        logger.info("resolve_shop_settings_once: skipped (ETSY_CALLS_BLOCKED)")
        return "skipped"
    redis = ctx.get("redis")
    if redis is not None and await redis.get(ONCE_KEY):
        return "done-before"
    async with ctx["sessionmaker"]() as session:
        result = await resolve_all(session, apply=True)
    for profile_id in result["refresh"]:
        await _enqueue_job(ctx, "refresh_profile", str(profile_id))
    for profile_id in result["profiles"]:
        await _enqueue_job(ctx, "link_profile", str(profile_id))
    if redis is not None:
        await redis.set(ONCE_KEY, datetime.now(timezone.utc).isoformat())
    logger.info("resolve_shop_settings_once: %s links filled from the map, %s to resolve (%s profiles)",
                result["links_filled"], result["links_to_resolve"], len(result["profiles"]))
    return f"filled:{result['links_filled']} resolve:{result['links_to_resolve']}"
