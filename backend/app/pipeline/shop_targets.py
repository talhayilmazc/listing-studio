"""The shops each listing is meant for: what the seller ticked on the review page.

A listing starts with one shop, the one it was written for (its group's shop,
else its batch's, else its profile's main shop). On the review page the seller
ticks the shops it should become a draft in; the choice is kept on the listing
(``generated_content.target_shops``), so it is the same after a reload, before
and after approval, for the drafts ("Create drafts" sends each listing to its
ticked shops), for the Section offered per shop, and for deciding when the
group's image files are no longer needed (pipeline/group_state.py).

Only shops still connected count. The profile is never chosen here: a listing
is built with its own profile in every shop (pipeline/targets.py).
"""

from __future__ import annotations

import uuid
from typing import Iterable

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


async def own_shop(session: AsyncSession, content: GeneratedContent) -> EtsyConnection | None:
    """The shop the listing was written for: its group's shop, else its batch's,
    else its profile's main shop (a profile is the account's since v8 §C)."""
    asset = await session.get(Asset, content.asset_id)
    shop_id = None
    if asset is not None:
        setting = (await session.execute(select(ListingGroupSetting).where(
            ListingGroupSetting.batch_id == content.batch_id,
            ListingGroupSetting.group_key == (asset.group_key or ""),
        ))).scalar_one_or_none()
        shop_id = setting.connection_id if setting is not None else None
    if shop_id is None:
        batch = await session.get(UploadBatch, content.batch_id)
        shop_id = batch.connection_id if batch is not None else None
    if shop_id is None and content.listing_profile_id is not None:
        profile = await session.get(ListingProfile, content.listing_profile_id)
        shop_id = profile.connection_id if profile is not None else None
    if shop_id is None:
        return None
    connection = await session.get(EtsyConnection, shop_id)
    if connection is None or connection.tenant_id != content.tenant_id:
        return None
    return connection if connection.status.value == "active" else None


def stored(content: GeneratedContent) -> list[uuid.UUID] | None:
    """The shops the seller ticked, or None when they never changed the default."""
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
    session: AsyncSession, content: GeneratedContent, shops: Iterable[EtsyConnection]
) -> list[EtsyConnection]:
    """The connected shops this listing is meant for, in the account's shop order."""
    by_id = {s.id: s for s in shops}
    chosen = stored(content)
    if chosen is None:
        own = await own_shop(session, content)
        chosen = [own.id] if own is not None else []
    return [by_id[s] for s in by_id if s in chosen]


async def set_shop(
    session: AsyncSession, content: GeneratedContent, shops: Iterable[EtsyConnection], shop_id: uuid.UUID, on: bool
) -> bool:
    """Tick or untick one shop for one listing. True when it changed."""
    shops = list(shops)
    current = [s.id for s in await wanted(session, content, shops)]
    if (shop_id in current) == on:
        if content.target_shops is None:
            return False  # already so by default: nothing to store
        changed = False
    else:
        changed = True
    updated = [s for s in current if s != shop_id] + ([shop_id] if on else [])
    order = [s.id for s in shops]
    content.target_shops = [str(s) for s in sorted(updated, key=order.index)]
    return changed
