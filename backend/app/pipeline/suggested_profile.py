"""The suggested profile: no profile wall for a new seller (fix/onboarding-followups, Part E).

Before: a seller who connected a shop could not have a single listing written until they found the
Profiles page, picked a reference listing and confirmed it; nothing on Home said so.

Now, when a shop with active listings is read for the first time and the account has no profile yet,
one **unconfirmed** profile is made from the shop's most recent active listing (the newest by its
creation time on Etsy), from the shop's own listing cache: no Etsy request of its own, nothing changed
on Etsy. Its reference data is then read like any profile's (``refresh_profile``, a read). Home's
checklist shows "Confirm your listing settings" with one button; until the seller confirms it, the
profile is used for nothing (``confirmed`` false, as for a detected profile). The seller may change
anything on the Profiles page first; confirming keeps what they changed.

A shop with no active listings gets no suggestion: the step says why in one sentence and links to the
Profiles page (today's flow). A seller who deleted a profile is never given one back.
Publishing is unchanged: a confirmed suggested profile is a profile like any other.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, EtsyConnection, ListingProfile, ShopListingCache
from app.pipeline import profile_shops
from app.pipeline.clustering import heuristic_name
from app.pipeline.reference import decode_etsy_text

SOURCE = "suggested"
#: Where the step's link goes for a shop with nothing to copy from.
NO_LISTINGS = ("Your shop has no active listings yet, so there are no settings to copy. Make a profile from a "
               "listing once you have one.")


def _created(row: dict[str, Any]) -> int:
    for key in ("original_creation_timestamp", "created_timestamp", "creation_tsz", "state_timestamp"):
        value = row.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def newest_active(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The shop's most recent active listing, by its creation time on Etsy (ties: the higher id)."""
    active = [r for r in rows if r.get("state") == "active" and r.get("listing_id") is not None]
    if not active:
        return None
    return max(active, key=lambda r: (_created(r), int(r["listing_id"])))


async def wanted(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """No profile yet, and none ever deleted: a suggestion helps. Otherwise the seller has chosen."""
    has_profile = await session.scalar(select(exists().where(ListingProfile.tenant_id == tenant_id)))
    if has_profile:
        return False
    deleted = await session.scalar(select(exists().where(
        AuditLog.target_tenant_id == tenant_id, AuditLog.action == "profile.deleted")))
    return not deleted


async def suggest(session: AsyncSession, connection: EtsyConnection) -> ListingProfile | None:
    """Make the suggested profile for this shop from its cached listings, if one is wanted. Not committed."""
    if not await wanted(session, connection.tenant_id):
        return None
    rows = list((await session.execute(
        select(ShopListingCache.payload).where(ShopListingCache.connection_id == connection.id)
    )).scalars())
    newest = newest_active(rows)
    if newest is None:
        return None
    same_kind = [decode_etsy_text(str(r.get("title") or "")) for r in rows
                 if r.get("state") == "active" and r.get("taxonomy_id") == newest.get("taxonomy_id")]
    name = heuristic_name(same_kind or [decode_etsy_text(str(newest.get("title") or ""))])
    profile = ListingProfile(
        tenant_id=connection.tenant_id,
        connection_id=connection.id,
        name=name if name != "Profile" else "My listings",
        reference_listing_id=int(newest["listing_id"]),
        source=SOURCE,
        confirmed=False,  # used for nothing until the seller confirms it
    )
    session.add(profile)
    await session.flush()
    await profile_shops.ensure_main_link(session, profile)
    return profile


async def pending(session: AsyncSession, tenant_id: uuid.UUID) -> ListingProfile | None:
    """The account's suggested profile still waiting for the seller."""
    return await session.scalar(
        select(ListingProfile).where(ListingProfile.tenant_id == tenant_id, ListingProfile.source == SOURCE,
                                     ListingProfile.confirmed.is_(False))
        .order_by(ListingProfile.created_at).limit(1)
    )


async def has_confirmed(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    return bool(await session.scalar(select(exists().where(
        ListingProfile.tenant_id == tenant_id, ListingProfile.confirmed.is_(True)))))
