"""The shops each listing goes to: chosen once for the batch, as groups or shops.

The review page's "Where and when" (B4) chooses where a batch's listings go: shop groups
and single shops (``upload_batch.send_to``). **Every approved listing goes to every chosen
shop** (the owner, 2026-10-22: no splitting; 6 listings and 2 shops are 12 drafts); several
groups give all their shops, each shop once. One listing can go elsewhere ("→ GROUP 1 ·
change": ``generated_content.send_to``). Nothing chosen: each listing to its profile's main
shop (B3). A group is read as its shops at that moment, so a shop joining the group later
gets the listings not yet sent.

The same answer serves the drafts, the Section offered per shop, the batch's statuses and
deciding when the group's image files are no longer needed (pipeline/group_state.py). Only
shops still connected count. The profile is never chosen here: a listing is built with its
own profile in every shop (pipeline/targets.py).
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Asset,
    EtsyConnection,
    GeneratedContent,
    ListingGroupSetting,
    ListingProfile,
    UploadBatch,
)
from app.etsy.shops import group_shops


async def own_shop(session: AsyncSession, content: GeneratedContent) -> EtsyConnection | None:
    """The shop a listing goes to unless the seller chose others: its profile's main shop
    (B3, 2026-10-22: no batch or group shop decides it any more). Only a listing with no
    profile falls back to its group's, else its batch's shop, as stored before."""
    shop_id = None
    if content.listing_profile_id is not None:
        profile = await session.get(ListingProfile, content.listing_profile_id)
        shop_id = profile.connection_id if profile is not None else None
    asset = await session.get(Asset, content.asset_id) if shop_id is None else None
    if asset is not None:
        setting = (await session.execute(select(ListingGroupSetting).where(
            ListingGroupSetting.batch_id == content.batch_id,
            ListingGroupSetting.group_key == (asset.group_key or ""),
        ))).scalar_one_or_none()
        shop_id = setting.connection_id if setting is not None else None
    if shop_id is None:
        batch = await session.get(UploadBatch, content.batch_id)
        shop_id = batch.connection_id if batch is not None else None
    if shop_id is None:
        return None
    connection = await session.get(EtsyConnection, shop_id)
    if connection is None or connection.tenant_id != content.tenant_id:
        return None
    return connection if connection.status.value == "active" else None


def destination(value: Any) -> dict[str, list[uuid.UUID]] | None:
    """A stored ``send_to`` as {"group_ids": [...], "connection_ids": [...]} of UUIDs, or None."""
    if not isinstance(value, dict):
        return None
    out: dict[str, list[uuid.UUID]] = {"group_ids": [], "connection_ids": []}
    for key in out:
        for raw in value.get(key) or []:
            try:
                item = uuid.UUID(str(raw))
            except (TypeError, ValueError):
                continue
            if item not in out[key]:
                out[key].append(item)
    return out


def stored_value(group_ids: Iterable[uuid.UUID], connection_ids: Iterable[uuid.UUID]) -> dict[str, list[str]]:
    """What ``send_to`` holds."""
    return {"group_ids": [str(g) for g in dict.fromkeys(group_ids)],
            "connection_ids": [str(c) for c in dict.fromkeys(connection_ids)]}


def shops_of(chosen: dict[str, list[uuid.UUID]], members: dict[uuid.UUID, list[uuid.UUID]]) -> list[uuid.UUID]:
    """The shops of a destination: each group's shops, then the single shops; each once."""
    out: list[uuid.UUID] = []
    for gid in chosen["group_ids"]:
        out += members.get(gid, [])
    out += chosen["connection_ids"]
    return list(dict.fromkeys(out))


async def members_of(session: AsyncSession, tenant_id: uuid.UUID) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Every group of the account -> its connected shops' ids."""
    return {gid: [c.id for c in shops] for gid, shops in (await group_shops(session, tenant_id)).items()}


async def chosen_for(session: AsyncSession, content: GeneratedContent) -> tuple[dict[str, list[uuid.UUID]] | None, bool]:
    """The listing's destination and whether it is its own exception (else its batch's);
    (None, False) when nothing was chosen (its profile's main shop)."""
    own = destination(content.send_to)
    if own is not None:
        return own, True
    batch = await session.get(UploadBatch, content.batch_id)
    return (destination(batch.send_to) if batch is not None else None), False


async def last_destination(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, list[str]] | None:
    """Where the account's latest batch with a choice was sent, its groups and shops that still
    exist (a new batch starts from it), or None."""
    from app.db.models import ConnectionStatus, ShopGroup

    latest = (await session.execute(
        select(UploadBatch.send_to).where(UploadBatch.tenant_id == tenant_id, UploadBatch.send_to.is_not(None))
        .order_by(UploadBatch.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    chosen = destination(latest)
    if chosen is None:
        return None
    groups = set((await session.execute(select(ShopGroup.id).where(
        ShopGroup.tenant_id == tenant_id, ShopGroup.id.in_(chosen["group_ids"])))).scalars()) if chosen["group_ids"] else set()
    shops = set((await session.execute(select(EtsyConnection.id).where(
        EtsyConnection.tenant_id == tenant_id, EtsyConnection.status == ConnectionStatus.active,
        EtsyConnection.id.in_(chosen["connection_ids"])))).scalars()) if chosen["connection_ids"] else set()
    kept_groups = [g for g in chosen["group_ids"] if g in groups]
    kept_shops = [c for c in chosen["connection_ids"] if c in shops]
    return stored_value(kept_groups, kept_shops) if kept_groups or kept_shops else None


def stored(content: GeneratedContent) -> list[uuid.UUID] | None:
    """Old matrix ticks a migration did not move (0084 moved them all), or None."""
    if content.target_shops is None:
        return None
    out: list[uuid.UUID] = []
    for value in content.target_shops:
        try:
            shop = uuid.UUID(str(value))
        except (TypeError, ValueError):
            continue
        if shop not in out:
            out.append(shop)
    return out


async def wanted(
    session: AsyncSession, content: GeneratedContent, shops: Iterable[EtsyConnection],
    members: dict[uuid.UUID, list[uuid.UUID]] | None = None,
) -> list[EtsyConnection]:
    """The connected shops this listing goes to, in the account's shop order. ``members``: the
    account's groups, read once by a caller asking for many listings."""
    by_id = {s.id: s for s in shops}
    chosen_dest, _ = await chosen_for(session, content)
    if chosen_dest is not None:
        chosen = shops_of(chosen_dest, members if members is not None else await members_of(session, content.tenant_id))
    else:
        chosen = stored(content)
        if chosen is None:
            own = await own_shop(session, content)
            chosen = [own.id] if own is not None else []
    return [by_id[s] for s in by_id if s in chosen]


async def destination_shops(
    session: AsyncSession, tenant_id: uuid.UUID, contents: Iterable[GeneratedContent]
) -> dict[uuid.UUID, list[uuid.UUID] | None]:
    """Per listing: the shops it goes to, or None when nothing was chosen (the reader's own
    default: its profile's main shop). Not filtered by connection: callers do that."""
    contents = list(contents)
    batch_ids = {c.batch_id for c in contents}
    batches = {b.id: destination(b.send_to) for b in (await session.execute(
        select(UploadBatch).where(UploadBatch.id.in_(batch_ids)))).scalars()} if batch_ids else {}
    members = await members_of(session, tenant_id) if contents else {}
    out: dict[uuid.UUID, list[uuid.UUID] | None] = {}
    for c in contents:
        chosen = destination(c.send_to) or batches.get(c.batch_id)
        out[c.id] = shops_of(chosen, members) if chosen is not None else stored(c)
    return out


async def set_shop(
    session: AsyncSession, content: GeneratedContent, shops: Iterable[EtsyConnection], shop_id: uuid.UUID, on: bool
) -> bool:
    """Add or take away one shop for one listing, as its own exception. True when it changed."""
    shops = list(shops)
    current = [s.id for s in await wanted(session, content, shops)]
    if (shop_id in current) == on:
        return False
    updated = [s for s in current if s != shop_id] + ([shop_id] if on else [])
    order = [s.id for s in shops]
    content.send_to = stored_value([], sorted(updated, key=order.index))
    return True
