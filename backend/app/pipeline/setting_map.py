"""One map per account of shop settings across shops (v8 §C; ``shop_setting_map``).

(source shop, source shipping profile / return policy / processing profile id) ->
(target shop, that shop's own id). A profile's reference uses settings of its main shop;
in every other shop the profile's link holds that shop's counterparts, and those come
from here, so a setting is set up once per shop however many reference listings use it.

Before, each profile's link kept its own result for each shop, computed when that link
was checked: creating "MemoPaid" in PCS for one reference listing left a second reference
listing using the same shipping profile at "no match in this shop" until someone set it
up again (production, 2026-10-20).

Written the moment a copy is created, a setting matches by name or terms, or the seller
picks one (``remember``); every link in the shop is then filled from it (``apply``),
current and future reference listings alike. A target found missing from the shop's
current list (deleted on Etsy) is forgotten and resolved again (``workers/links.py``).

Database only: no Etsy request is made here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ListingProfile, ProfileShopLink, ShopSettingMap
from app.pipeline import links as L

#: The settings mapped across shops. Production partners are matched by name per profile
#: (a pick there is a list for the profile's own partners, not one setting to another).
MAPPED: tuple[str, ...] = (L.SHIPPING, L.RETURNS, L.READINESS)

Key = tuple[str, uuid.UUID, int, uuid.UUID]  # resource, source shop, source id, target shop


async def lookup(
    session: AsyncSession, tenant_id: uuid.UUID, resource: str, source: uuid.UUID, source_id: int, target: uuid.UUID
) -> ShopSettingMap | None:
    return (await session.execute(select(ShopSettingMap).where(
        ShopSettingMap.tenant_id == tenant_id, ShopSettingMap.resource == resource,
        ShopSettingMap.source_connection_id == source, ShopSettingMap.source_id == int(source_id),
        ShopSettingMap.target_connection_id == target,
    ))).scalar_one_or_none()


async def remember(
    session: AsyncSession, tenant_id: uuid.UUID, resource: str, source: uuid.UUID, source_id: int,
    target: uuid.UUID, target_id: int, *, how: str, label: str | None,
) -> ShopSettingMap:
    """Record (or change) the counterpart of a setting in a shop. Does not commit.

    The unique constraint keeps one row per setting and shop: when another job wrote it
    in the meantime, that row is updated, never doubled."""
    row = await lookup(session, tenant_id, resource, source, source_id, target)
    if row is None:
        row = ShopSettingMap(tenant_id=tenant_id, resource=resource, source_connection_id=source,
                             source_id=int(source_id), target_connection_id=target, target_id=int(target_id),
                             how=how, label=label)
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
            return row
        except IntegrityError:
            row = await lookup(session, tenant_id, resource, source, source_id, target)
            assert row is not None
    if (row.target_id, row.how, row.label) != (int(target_id), how, label):
        row.target_id, row.how, row.label = int(target_id), how, label
        row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return row


async def forget(session: AsyncSession, row: ShopSettingMap) -> None:
    """The target is no longer in its shop (deleted on Etsy): it is resolved again."""
    await session.delete(row)
    await session.flush()


async def forget_target(
    session: AsyncSession, tenant_id: uuid.UUID, resource: str, target: uuid.UUID, target_id: int
) -> int:
    """Every mapping onto one setting of a shop (it was deleted on Etsy)."""
    result = await session.execute(delete(ShopSettingMap).where(
        ShopSettingMap.tenant_id == tenant_id, ShopSettingMap.resource == resource,
        ShopSettingMap.target_connection_id == target, ShopSettingMap.target_id == int(target_id),
    ))
    return result.rowcount or 0


async def forget_shop(session: AsyncSession, connection_id: uuid.UUID) -> int:
    """A shop is disconnected: every mapping from or to it goes. Does not commit."""
    result = await session.execute(delete(ShopSettingMap).where(
        (ShopSettingMap.source_connection_id == connection_id) | (ShopSettingMap.target_connection_id == connection_id)
    ))
    return result.rowcount or 0


async def rows_for(session: AsyncSession, tenant_id: uuid.UUID, target: uuid.UUID | None = None) -> dict[Key, ShopSettingMap]:
    query = select(ShopSettingMap).where(ShopSettingMap.tenant_id == tenant_id)
    if target is not None:
        query = query.where(ShopSettingMap.target_connection_id == target)
    return {(r.resource, r.source_connection_id, int(r.source_id), r.target_connection_id): r
            for r in (await session.execute(query)).scalars()}


def fill(profile: ListingProfile, link: ProfileShopLink, maps: dict[Key, ShopSettingMap]) -> bool:
    """Set the link's ids from the map, for every setting the profile's reference uses
    there. A setting the map holds is no longer missing; the link's status follows what is
    still missing. Returns whether anything changed. Pure (no I/O)."""
    if link.connection_id == profile.connection_id:
        return False  # the main shop: its ids are its reference's own
    changed = False
    notes: dict[str, Any] = dict(link.notes or {})
    for resource in MAPPED:
        source_id = L.wanted(profile.cached_payload, resource)
        if source_id in (None, "", []):
            continue
        row = maps.get((resource, profile.connection_id, int(source_id), link.connection_id))
        if row is None:
            continue
        key = L.PAYLOAD_KEY[resource]
        if getattr(link, key) != int(row.target_id):
            setattr(link, key, int(row.target_id))
            changed = True
        if resource in notes:
            notes.pop(resource)
            changed = True
    if changed:
        link.notes = notes or None
        if link.status != "checking":
            still = L.missing(profile.cached_payload, link) or notes.get("currency") or notes.get("error")
            link.status = ("error" if notes.get("error") else "incomplete") if still else "ready"
    return changed


async def apply(
    session: AsyncSession, tenant_id: uuid.UUID, *, target: uuid.UUID | None = None,
    profile_ids: list[uuid.UUID] | None = None,
) -> int:
    """Fill every profile link of the account (in ``target``, or every shop) from the map.
    Database only. Does not commit. Returns how many links changed."""
    maps = await rows_for(session, tenant_id, target)
    if not maps:
        return 0
    query = (select(ProfileShopLink, ListingProfile)
             .join(ListingProfile, ListingProfile.id == ProfileShopLink.profile_id)
             .where(ProfileShopLink.tenant_id == tenant_id, ProfileShopLink.connection_id != ListingProfile.connection_id))
    if target is not None:
        query = query.where(ProfileShopLink.connection_id == target)
    if profile_ids:
        query = query.where(ProfileShopLink.profile_id.in_(profile_ids))
    changed = 0
    for link, profile in (await session.execute(query)).all():
        if fill(profile, link, maps):
            changed += 1
    await session.flush()
    return changed
