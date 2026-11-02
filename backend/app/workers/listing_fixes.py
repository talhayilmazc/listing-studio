"""Listing health's jobs: read a listing's attributes, write a suggested title, apply a fix, undo it.

Applying is the only thing here that changes a live listing, and it runs only for a fix the seller approved
(api/listing_health.py). Each run reads the listing first and writes only if it is still as the preview
showed it: a seller who edited it in Shop Manager in the meantime keeps their edit, and is told. Then it reads
back and checks what Etsy kept. A run cut short is safe to repeat: a field already at its new value is left.

Undo writes back the copy kept when the fix was made, and only where the listing is still as the fix left it.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.core import ai_meter, allowance, audit, limits, llm_status
from app.core.config import get_settings
from app.core.llm_status import LLMUnavailable
from app.db.models import ListingFix, ListingPublication, ShopListingCache, Tenant
from app.etsy.api import RateLimitExceeded
from app.etsy.errors import EtsyError, explain
from app.pipeline import listing_fixes as F
from app.pipeline import versions
from app.pipeline.llm import client_for
from app.workers import recovery
from app.workers.profiles import _active_shop, _build_client, _connection_service, _resolve_shop_id, _run_gated

logger = logging.getLogger(__name__)

#: One screen of listings at a time.
READ_AT_ONCE = 25
CHANGED = "This listing was changed on Etsy after the preview, so it was left as it is. Preview the fix again if you still want it."
NOT_ACTIVE = "This listing is no longer active on Etsy, so it was left as it is."
UNDO_CHANGED = "This listing was changed on Etsy after the fix, so it was left as it is."
WORDS = {"title": "the title", "tags": "the tags"}


def read_spec(connection_id: uuid.UUID | str, listing_ids: list[int]) -> str:
    return json.dumps({"connection_id": str(connection_id), "listing_ids": listing_ids[:READ_AT_ONCE]})


async def _shop_work(ctx: dict[str, Any], session: Any, connection: Any, tenant: Tenant, service: Any, http: httpx.AsyncClient) -> tuple[Any, dict[str, Any], int]:
    token = await service.get_valid_access_token(session, connection)
    client = _build_client(ctx, http, get_settings(), shop=connection.id)
    kw = {"access_token": token, "tenant_id": tenant.id, "tenant_limit": limits.ceiling_limit(tenant)}
    shop_id = await _resolve_shop_id(session, client, connection, kw)
    return client, kw, shop_id


async def read_listing_properties(ctx: dict[str, Any], spec: str) -> str:
    """A listing's attributes aren't in the shop sync. Read for the listings on screen, one request each
    (upkeep), and kept on the same cached row: they go when the sync replaces it, within six hours."""
    wanted = json.loads(spec)
    connection_id = uuid.UUID(wanted["connection_id"])
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, connection_id)
        if connection is None:
            return "no-connection"
        tenant_id = connection.tenant_id
    return await _run_gated(ctx, "read_listing_properties", spec, tenant_id, lambda: _read(ctx, connection_id, wanted["listing_ids"]))


async def _read(ctx: dict[str, Any], connection_id: uuid.UUID, listing_ids: list[int]) -> str:
    service = _connection_service(get_settings())
    done = 0
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, connection_id)
        if connection is None:
            return "no-connection"
        tenant = await session.get(Tenant, connection.tenant_id)
        async with httpx.AsyncClient(timeout=30.0) as http:
            client, kw, shop_id = await _shop_work(ctx, session, connection, tenant, service, http)
            for listing_id in listing_ids[:READ_AT_ONCE]:
                row = await session.get(ShopListingCache, (tenant.id, int(listing_id)))
                if row is None or row.connection_id != connection.id:
                    continue  # only this shop's own listings, as the sync read them
                answer = await client.get_listing_properties(shop_id, int(listing_id), **kw)
                row = await session.get(ShopListingCache, (tenant.id, int(listing_id)))
                if row is None:
                    continue
                row.payload = {**row.payload, "_properties": list(answer.get("results") or [])}
                await session.commit()
                done += 1
    return f"read:{done}"


async def write_listing_fix_text(ctx: dict[str, Any], fix_id: str) -> str:
    """A suggested title and tags by the content model. Nothing goes to Etsy here: it becomes a preview.
    Under the allowance's rewrite rule, recorded only when there is something to show."""
    settings = get_settings()
    async with ctx["sessionmaker"]() as session:
        fix = await session.get(ListingFix, uuid.UUID(fix_id))
        if fix is None or fix.state != "writing":
            return "not-writing"
        row = await session.get(ShopListingCache, (fix.tenant_id, fix.listing_id))
        if row is None:
            fix.state, fix.note = "could_not_write", "This listing is no longer in the shop's copy; open the page again."
            await session.commit()
            return "no-listing"
        listing, tenant_id = dict(row.payload), fix.tenant_id
        redis = ctx.get("redis")
        written = None
        try:
            async with ai_meter.scope(tenant_id, session):
                with ai_meter.purpose("listing_fix"):
                    written = await F.rewrite(client_for(settings, "content"), listing)
        except LLMUnavailable as exc:
            if redis is not None:
                await llm_status.report(redis, exc)
            fix = await session.get(ListingFix, uuid.UUID(fix_id))
            fix.state, fix.note = "could_not_write", "Writing is paused for a few minutes on our side. Nothing was counted; try again shortly."
            await session.commit()
            return "paused"
        except Exception as exc:  # noqa: BLE001 - the provider's words are never shown to sellers
            logger.warning("listing fix: the model call failed: %s", type(exc).__name__)
        fix = await session.get(ListingFix, uuid.UUID(fix_id))
        if written is None:
            fix.state, fix.note = "could_not_write", "No title that meets the rules came back. Nothing was counted; try again."
            await session.commit()
            return "unusable"
        fix.after = {**fix.after, **written}
        fix.state = "proposed"
        allowance.record(session, tenant_id, fix.allowance_kind or allowance.GENERATION, design=fix.design)
        await session.commit()
        return "written"


def _current(listing: dict[str, Any], properties: list[dict[str, Any]]) -> dict[str, Any]:
    return {**F.text_of(listing), "attributes": F.attributes_by_id(properties)}


def _field_is(current: dict[str, Any], name: str, value: Any) -> bool:
    if name == "title":
        return F.same_title(current["title"], value)
    if name == "tags":
        return F.same_tags(current["tags"], value)
    raise KeyError(name)


def _attrs_of(side: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    return {int(a["property_id"]): a for a in (side or {}).get("attributes") or []}


async def _write(client: Any, shop_id: int, listing_id: int, current: dict[str, Any], target: dict[str, Any], kw: dict[str, Any]) -> bool:
    """Set what still differs. An attribute with nothing in the target was empty before: it is cleared."""
    wrote = False
    updates = {name: target[name] for name in ("title", "tags") if name in target and not _field_is(current, name, target[name])}
    if updates:
        await client.update_listing(shop_id, listing_id, updates=updates, **kw)
        wrote = True
    for property_id, value in _attrs_of(target).items():
        if F.same_attribute(current["attributes"].get(property_id), value):
            continue
        if value.get("value_ids") or value.get("values"):
            await client.update_listing_property(shop_id, listing_id, property_id, value_ids=value.get("value_ids") or None,
                                                 values=value.get("values") or None, scale_id=value.get("scale_id"), **kw)
        else:
            await client.delete_listing_property(shop_id, listing_id, property_id, **kw)
        wrote = True
    return wrote


def _not_kept(current: dict[str, Any], target: dict[str, Any]) -> list[str]:
    """What Etsy did not keep as sent, read back."""
    off = [WORDS[name] for name in ("title", "tags") if name in target and not _field_is(current, name, target[name])]
    off += [str(value.get("property_name") or property_id) for property_id, value in _attrs_of(target).items()
            if not F.same_attribute(current["attributes"].get(property_id), value)]
    return off


async def _update_copy(session: Any, fix: ListingFix, listing: dict[str, Any], properties: list[dict[str, Any]]) -> None:
    """The page shows the listing as it now is, for the rest of the copy's six hours."""
    row = await session.get(ShopListingCache, (fix.tenant_id, fix.listing_id))
    if row is not None:
        row.payload = {**row.payload, "title": listing.get("title"), "tags": listing.get("tags"), "_properties": properties}


async def _record_version(session: Any, fix: ListingFix, at: datetime, text: dict[str, Any]) -> None:
    """A listing the app published keeps its history of what was on Etsy (pipeline/versions.py)."""
    publication = (await session.execute(select(ListingPublication).where(
        ListingPublication.tenant_id == fix.tenant_id, ListingPublication.connection_id == fix.connection_id,
        ListingPublication.etsy_listing_id == fix.listing_id,
    ))).scalars().first()
    if publication is None:
        return
    publication.title = text["title"]
    await versions.health_fix(session, publication, at, title=text["title"], tags=text["tags"])


async def apply_listing_fix(ctx: dict[str, Any], fix_id: str) -> str:
    async with recovery.holding(ctx, f"fix:{fix_id}") as held:
        if not held:
            return "busy"
        async with ctx["sessionmaker"]() as session:
            fix = await session.get(ListingFix, uuid.UUID(fix_id))
            if fix is None or fix.state != "approved":
                return "not-approved"
            tenant_id = fix.tenant_id
        return await _run_gated(ctx, "apply_listing_fix", fix_id, tenant_id, lambda: _apply(ctx, uuid.UUID(fix_id)))


async def _apply(ctx: dict[str, Any], fix_id: uuid.UUID) -> str:
    service = _connection_service(get_settings())
    async with ctx["sessionmaker"]() as session:
        fix = await session.get(ListingFix, fix_id)
        if fix is None or fix.state != "approved":
            return "not-approved"
        connection = await _active_shop(session, fix.connection_id)
        if connection is None or connection.tenant_id != fix.tenant_id:
            fix.state, fix.note = "failed", "This shop is no longer connected, so nothing was changed."
            await session.commit()
            return "no-connection"
        tenant = await session.get(Tenant, fix.tenant_id)
        name = connection.shop_name
        async with httpx.AsyncClient(timeout=30.0) as http:
            try:
                client, kw, shop_id = await _shop_work(ctx, session, connection, tenant, service, http)
                listing = await client.get_listing(fix.listing_id, **kw)
                properties = list((await client.get_listing_properties(shop_id, fix.listing_id, **kw)).get("results") or [])
                current = _current(listing, properties)
                if str(listing.get("state") or "active") != "active":
                    fix.state, fix.note = "changed", NOT_ACTIVE
                    await session.commit()
                    return "not-active"
                after, before = fix.after or {}, fix.before or {}
                # Each field must be as the preview showed it, or already at its new value (a repeat).
                for field in ("title", "tags"):
                    if field in after and not _field_is(current, field, before.get(field)) and not _field_is(current, field, after[field]):
                        fix.state, fix.note = "changed", CHANGED
                        await session.commit()
                        return "changed"
                before_attrs = _attrs_of(before)
                for property_id, value in _attrs_of(after).items():
                    now = current["attributes"].get(property_id)
                    if not F.same_attribute(now, before_attrs.get(property_id)) and not F.same_attribute(now, value):
                        fix.state, fix.note = "changed", CHANGED
                        await session.commit()
                        return "changed"
                if fix.written_at is None:
                    now_at = datetime.now(timezone.utc)
                    fix.written_at, fix.undo_until = now_at, now_at + timedelta(days=F.UNDO_DAYS)
                    await session.commit()
                await _write(client, shop_id, fix.listing_id, current, after, kw)
                listing = await client.get_listing(fix.listing_id, **kw)
                properties = list((await client.get_listing_properties(shop_id, fix.listing_id, **kw)).get("results") or [])
            except RateLimitExceeded:
                raise  # the day's budget: _run_gated runs it again after the reset, from the top
            except EtsyError as exc:
                await session.rollback()
                fix = await session.get(ListingFix, fix_id)
                fix.state, fix.note = "failed", explain(exc, name)
                await session.commit()
                return "failed"
        back = _current(listing, properties)
        off = _not_kept(back, after)
        fix = await session.get(ListingFix, fix_id)
        at = datetime.now(timezone.utc)
        await _update_copy(session, fix, listing, properties)
        if off:
            fix.state, fix.note = "failed", f"Etsy did not keep {', '.join(off)} as sent. You can undo what did change."
            await session.commit()
            return "not-kept"
        fix.state, fix.applied_at, fix.note = "applied", at, None
        await _record_version(session, fix, at, F.text_of(listing))
        audit.record(session, "listing.health_fix_applied", actor=tenant, target_tenant_id=fix.tenant_id, shop_id=str(fix.connection_id),
                     etsy_listing_id=fix.listing_id, kinds=list(fix.kinds or []), trigger="seller_click")
        await session.commit()
        return "applied"


async def undo_listing_fix(ctx: dict[str, Any], fix_id: str) -> str:
    async with recovery.holding(ctx, f"fix:{fix_id}") as held:
        if not held:
            return "busy"
        async with ctx["sessionmaker"]() as session:
            fix = await session.get(ListingFix, uuid.UUID(fix_id))
            if fix is None or fix.state != "undoing":
                return "not-undoing"
            tenant_id = fix.tenant_id
        return await _run_gated(ctx, "undo_listing_fix", fix_id, tenant_id, lambda: _undo(ctx, uuid.UUID(fix_id)))


async def _undo(ctx: dict[str, Any], fix_id: uuid.UUID) -> str:
    service = _connection_service(get_settings())
    async with ctx["sessionmaker"]() as session:
        fix = await session.get(ListingFix, fix_id)
        if fix is None or fix.state != "undoing":
            return "not-undoing"
        connection = await _active_shop(session, fix.connection_id)
        if connection is None or connection.tenant_id != fix.tenant_id:
            fix.state, fix.note = "undo_failed", "This shop is no longer connected, so nothing was changed."
            await session.commit()
            return "no-connection"
        tenant = await session.get(Tenant, fix.tenant_id)
        name = connection.shop_name
        async with httpx.AsyncClient(timeout=30.0) as http:
            try:
                client, kw, shop_id = await _shop_work(ctx, session, connection, tenant, service, http)
                listing = await client.get_listing(fix.listing_id, **kw)
                properties = list((await client.get_listing_properties(shop_id, fix.listing_id, **kw)).get("results") or [])
                current = _current(listing, properties)
                after, before = fix.after or {}, fix.before or {}
                # Back only what is still as the fix left it, or already back (a repeat); else hands off.
                target: dict[str, Any] = {}
                for field in ("title", "tags"):
                    if field not in after:
                        continue
                    if _field_is(current, field, before.get(field)):
                        continue
                    if not _field_is(current, field, after[field]):
                        fix.state, fix.note = "undo_failed", UNDO_CHANGED
                        await session.commit()
                        return "changed"
                    target[field] = before.get(field)
                before_attrs = _attrs_of(before)
                back_attrs = []
                for property_id, value in _attrs_of(after).items():
                    now = current["attributes"].get(property_id)
                    old = before_attrs.get(property_id)
                    if F.same_attribute(now, old):
                        continue
                    if not F.same_attribute(now, value):
                        fix.state, fix.note = "undo_failed", UNDO_CHANGED
                        await session.commit()
                        return "changed"
                    back_attrs.append(old or {"property_id": property_id, "property_name": value.get("property_name"), "value_ids": [], "values": []})
                target["attributes"] = back_attrs
                await _write(client, shop_id, fix.listing_id, current, target, kw)
                listing = await client.get_listing(fix.listing_id, **kw)
                properties = list((await client.get_listing_properties(shop_id, fix.listing_id, **kw)).get("results") or [])
            except RateLimitExceeded:
                raise
            except EtsyError as exc:
                await session.rollback()
                fix = await session.get(ListingFix, fix_id)
                fix.state, fix.note = "undo_failed", explain(exc, name)
                await session.commit()
                return "failed"
        off = _not_kept(_current(listing, properties), target)
        fix = await session.get(ListingFix, fix_id)
        at = datetime.now(timezone.utc)
        await _update_copy(session, fix, listing, properties)
        if off:
            fix.state, fix.note = "undo_failed", f"Etsy did not keep {', '.join(off)} as sent. Check this listing in Shop Manager."
            await session.commit()
            return "not-kept"
        fix.state, fix.undone_at, fix.note = "undone", at, None
        await _record_version(session, fix, at, F.text_of(listing))
        audit.record(session, "listing.health_fix_undone", actor=tenant, target_tenant_id=fix.tenant_id, shop_id=str(fix.connection_id),
                     etsy_listing_id=fix.listing_id, trigger="seller_click")
        await session.commit()
        return "undone"
