"""The check an hour before a scheduled run: will it fail? Read only, through the rate limiter, at most
once per shop per hour (upkeep, not the seller's budget). What it finds goes to the seller's "Needs
your attention" with the fix in plain words, and to us (monitor/rules.py). A setting the app made
itself is made again when the draft needs it, so that alone is not a failure.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import limits
from app.core.config import get_settings
from app.db.models import (
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    ListingProfile,
    ListingPublication,
    PlannedSlot,
    ProfileShopLink,
    ShopSettingMap,
    Tenant,
)
from app.etsy.errors import EtsyClientError
from app.monitor.rules import PREFLIGHT_TENANTS, preflight_key
from app.pipeline import links as L
from app.pipeline import shop_listing
from app.workers.links import _cache_lists, _settings
from app.workers.profiles import ShopAccessLost, _build_client, _connection_service, _enqueue_job, _resolve_shop_id, _run_gated, _token
from app.workers.reconcile import spread_seconds

logger = logging.getLogger(__name__)

#: "One hour before": the look runs every 10 minutes, so a slot is seen once it is this close.
WINDOW = timedelta(minutes=70)
CHECKED = "preflight:checked:{shop}"
ONCE_PER_SECONDS = 3600
#: A result stays shown this long if nothing checks again (its slots have passed by then).
KEEP_SECONDS = 3 * 3600
RESOURCES = (L.SHIPPING, L.RETURNS, L.READINESS)


def _plural(n: int, what: str) -> str:
    return f"{n} {what}{'' if n == 1 else 's'}"


def _problem(shop: EtsyConnection, kind: str, line: str, href: str, count: int) -> dict[str, Any]:
    return {"shop": str(shop.id), "shop_name": _name(shop), "kind": kind, "line": line, "href": href, "count": count}


def _name(shop: EtsyConnection) -> str:
    return shop.display_name or shop.shop_name or "this shop"


def not_connected(shop: EtsyConnection, count: int) -> dict[str, Any]:
    if shop.status is ConnectionStatus.expired:
        return token_refused(shop, count)
    return _problem(shop, "disconnected", (
        f"{_name(shop)} is not connected, so {_plural(count, 'scheduled listing')} there will not go. Connect it again on "
        "Shops & groups."), "/connect", count)


def token_refused(shop: EtsyConnection, count: int) -> dict[str, Any]:
    return _problem(shop, "token", (
        f"Etsy no longer accepts the sign-in of {_name(shop)}, so {_plural(count, 'scheduled listing')} there will not go. "
        "Connect the shop again on Shops & groups."), "/connect", count)


async def save(redis: Any, tenant_id: Any, shop_id: Any, problems: list[dict[str, Any]], now: float | None = None) -> None:
    key = preflight_key(tenant_id)
    if not problems:
        await redis.hdel(key, str(shop_id))
        return
    await redis.hset(key, str(shop_id), json.dumps({"at": time.time() if now is None else now, "problems": problems}))
    await redis.expire(key, KEEP_SECONDS)
    await redis.sadd(PREFLIGHT_TENANTS, str(tenant_id))


async def forget(redis: Any, tenant_id: Any, shop_id: Any) -> None:
    """The seller fixed it (reconnected, chose another setting): the next look decides afresh."""
    await redis.hdel(preflight_key(tenant_id), str(shop_id))
    await redis.delete(CHECKED.format(shop=shop_id))


async def due_by_shop(session: AsyncSession, now: datetime, until: datetime) -> dict[uuid.UUID, list[tuple[uuid.UUID | None, bool]]]:
    """Per shop: (listing, has a draft already) of each run due before ``until``."""
    from app.pipeline.schedule_pause import is_paused, paused_shops

    out: dict[uuid.UUID, list[tuple[uuid.UUID | None, bool]]] = {}
    tenants: dict[uuid.UUID, uuid.UUID] = {}
    rows = await session.execute(select(
        ListingPublication.tenant_id, ListingPublication.connection_id, ListingPublication.content_id).where(
        ListingPublication.scheduled_for >= now, ListingPublication.scheduled_for < until,
        ListingPublication.schedule_job_id.is_(None), ListingPublication.schedule_note.is_(None),
        ListingPublication.state == "draft"))
    for tenant, shop, content in rows.all():
        tenants[shop] = tenant
        out.setdefault(shop, []).append((content, True))
    rows = await session.execute(select(PlannedSlot.tenant_id, PlannedSlot.connection_id, PlannedSlot.content_id).where(
        PlannedSlot.publish_at >= now, PlannedSlot.publish_at < until, PlannedSlot.state.in_(("waiting", "drafting"))))
    for tenant, shop, content in rows.all():
        tenants[shop] = tenant
        out.setdefault(shop, []).append((content, False))
    if not out:
        return out
    paused = await paused_shops(session, set(tenants.values()))
    return {shop: runs for shop, runs in out.items() if not is_paused(tenants[shop], shop, paused)}


async def preflight_scheduled(ctx: dict[str, Any]) -> int:
    """Cron, every 10 minutes: a check for each shop with a run coming, once an hour per shop."""
    redis = ctx["redis"]
    now = datetime.now(timezone.utc)
    queued: list[str] = []
    async with ctx["sessionmaker"]() as session:
        due = await due_by_shop(session, now, now + WINDOW)
        for shop_id, runs in due.items():
            shop = await session.get(EtsyConnection, shop_id)
            if shop is None:
                continue
            if shop.status is not ConnectionStatus.active:  # known without asking Etsy
                await save(redis, shop.tenant_id, shop.id, [not_connected(shop, len(runs))])
                continue
            if await redis.set(CHECKED.format(shop=shop_id), "1", nx=True, ex=ONCE_PER_SECONDS):
                queued.append(str(shop_id))
    for n, shop_id in enumerate(queued):
        await _enqueue_job(ctx, "preflight_shop", shop_id, _defer_by=spread_seconds(n, len(queued)) / 10,
                           _job_id=f"preflight:{shop_id}:{int(now.timestamp()) // ONCE_PER_SECONDS}")
    return len(queued)


async def preflight_shop(ctx: dict[str, Any], connection_id: str) -> str:
    async with ctx["sessionmaker"]() as session:
        shop = await session.get(EtsyConnection, uuid.UUID(connection_id))
        if shop is None:
            return "no-connection"
        tenant_id = shop.tenant_id
    return await _run_gated(ctx, "preflight_shop", connection_id, tenant_id, lambda: _check(ctx, connection_id))


async def _check(ctx: dict[str, Any], connection_id: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    async with ctx["sessionmaker"]() as session:
        shop = await session.get(EtsyConnection, uuid.UUID(connection_id))
        if shop is None:
            return "no-connection"
        runs = (await due_by_shop(session, now, now + WINDOW)).get(shop.id, [])
        if not runs:
            await save(ctx["redis"], shop.tenant_id, shop.id, [])
            return "nothing-due"
        if shop.status is not ConnectionStatus.active:
            await save(ctx["redis"], shop.tenant_id, shop.id, [not_connected(shop, len(runs))])
            return "not-connected"
        try:
            token = await _token(_connection_service(settings), session, shop)
        except ShopAccessLost:
            await session.refresh(shop)
            if shop.status is ConnectionStatus.expired:  # Etsy said no; anything else is tried at the next look
                await save(ctx["redis"], shop.tenant_id, shop.id, [token_refused(shop, len(runs))])
                return "token"
            await forget_turn(ctx, shop.id)
            return "unknown"
        tenant = await session.get(Tenant, shop.tenant_id)
        kw = {"access_token": token, "tenant_id": shop.tenant_id, "tenant_limit": limits.ceiling_limit(tenant) if tenant else None}
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                client = _build_client(ctx, http, settings, shop=shop.id)
                shop_id = await _resolve_shop_id(session, client, shop, kw)
                lists = await _settings(client, shop_id, kw, currency=False)
        except EtsyClientError as exc:
            if exc.status_code in (401, 403):
                await save(ctx["redis"], shop.tenant_id, shop.id, [token_refused(shop, len(runs))])
                return "token"
            await forget_turn(ctx, shop.id)
            return "unknown"
        except (httpx.HTTPError, ShopAccessLost):
            await forget_turn(ctx, shop.id)  # says nothing about the shop: the next look asks again
            return "unknown"
        await _cache_lists(ctx, shop.id, lists)
        problems = await findings(session, shop, runs, lists)
        await session.commit()
    await save(ctx["redis"], shop.tenant_id, shop.id, problems)
    return f"checked:{len(problems)}"


async def forget_turn(ctx: dict[str, Any], shop_id: Any) -> None:
    await ctx["redis"].delete(CHECKED.format(shop=shop_id))


async def findings(session: AsyncSession, shop: EtsyConnection, runs: list[tuple[uuid.UUID | None, bool]],
                   lists: L.ShopSettings) -> list[dict[str, Any]]:
    """What will fail in this shop, given its lists as Etsy has them now."""
    no_profile = 0
    by_profile: Counter[uuid.UUID] = Counter()
    drafted: Counter[uuid.UUID] = Counter()
    for content_id, has_draft in runs:
        content = await session.get(GeneratedContent, content_id) if content_id else None
        profile_id = (shop_listing.profile_in(content, shop.id) or content.listing_profile_id) if content else None
        if profile_id is None or await session.get(ListingProfile, profile_id) is None:
            if not has_draft:  # a draft on Etsy goes live without its profile
                no_profile += 1
            continue
        by_profile[profile_id] += 1
        drafted[profile_id] += has_draft
    out: list[dict[str, Any]] = []
    if no_profile:
        out.append(_problem(shop, "profile", (
            f"{_plural(no_profile, 'scheduled listing')} in {_name(shop)} no longer {'has a profile' if no_profile == 1 else 'have a profile'}: "
            "the profile was deleted, so no draft can be made. Choose a profile for the listing on its batch page, or take it "
            "off the schedule."), "/scheduled", no_profile))
    for profile_id, count in by_profile.items():
        profile = await session.get(ListingProfile, profile_id)
        link = (await session.execute(select(ProfileShopLink).where(
            ProfileShopLink.profile_id == profile_id, ProfileShopLink.connection_id == shop.id))).scalar_one_or_none()
        if profile.connection_id == shop.id:
            ids = {r: L.wanted(profile.cached_payload, r) for r in RESOURCES}
        elif link is not None:
            ids = {L.SHIPPING: link.shipping_profile_id, L.RETURNS: link.return_policy_id, L.READINESS: link.readiness_state_id}
        else:
            continue  # the draft sets the shop up first ("Kurulum yoktur")
        for resource, wanted in ids.items():
            if wanted in (None, "") or any(L.id_of(resource, row) == int(wanted) for row in lists.rows(resource)):
                continue
            if link is not None and not drafted[profile_id] and await _remade_by_the_draft(session, shop, resource, int(wanted)):
                continue
            out.append(_problem(shop, f"setting:{resource}:{profile.id}", (
                f"The {L.LABEL[resource].lower()} that {profile.name} uses in {_name(shop)} is no longer on Etsy, so "
                f"{_plural(count, 'scheduled listing')} may fail. Choose another with \"change\" on the profile's card."),
                "/profiles", count))
    return out


async def _remade_by_the_draft(session: AsyncSession, shop: EtsyConnection, resource: str, target: int) -> bool:
    row = (await session.execute(select(ShopSettingMap).where(
        ShopSettingMap.tenant_id == shop.tenant_id, ShopSettingMap.resource == resource,
        ShopSettingMap.target_connection_id == shop.id, ShopSettingMap.target_id == target))).scalar_one_or_none()
    return row is not None and row.how == "created"
