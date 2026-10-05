"""Where a profile can be used: its main shop and the shops it is linked to (v8 §C).

The database side of ``pipeline/links.py``: reading and keeping the links, and
what disconnecting a shop does to the profiles that use it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ListingProfile, ProfileShopLink
from app.pipeline import links as L

CHECKING = "being set up in this shop; it takes a minute"


@dataclass
class LinkState:
    connection_id: uuid.UUID
    main: bool
    ready: bool
    reason: str | None = None  # why it is not ready, one line
    missing: dict[str, str] = field(default_factory=dict)  # resource -> why
    status: str = "ready"


def state_of(profile: ListingProfile, link: ProfileShopLink | None) -> LinkState | None:
    """Is ``profile`` usable in the link's shop, and if not, why."""
    if link is None:
        return None
    if link.connection_id == profile.connection_id:
        return LinkState(link.connection_id, main=True, ready=True)
    if link.status == "checking":
        return LinkState(link.connection_id, False, False, CHECKING, status="checking")
    notes = link.notes or {}
    if notes.get("currency"):
        return LinkState(link.connection_id, False, False, str(notes["currency"]), status="incomplete")
    if link.status == "error":
        return LinkState(link.connection_id, False, False, str(notes.get("error") or "setting it up failed; try again"),
                         status="error")
    missing = L.missing(profile.cached_payload, link)
    if missing:
        first = next(iter(missing))
        return LinkState(link.connection_id, False, False, f"{L.LABEL[first]}: {missing[first]}", missing, "incomplete")
    return LinkState(link.connection_id, False, True)


async def links_of(session: AsyncSession, profile_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ProfileShopLink]]:
    out: dict[uuid.UUID, list[ProfileShopLink]] = {pid: [] for pid in profile_ids}
    if not profile_ids:
        return out
    rows = await session.execute(select(ProfileShopLink).where(ProfileShopLink.profile_id.in_(profile_ids)))
    for link in rows.scalars():
        out.setdefault(link.profile_id, []).append(link)
    return out


async def link_for(session: AsyncSession, profile: ListingProfile, connection_id: uuid.UUID) -> ProfileShopLink | None:
    rows = await session.execute(
        select(ProfileShopLink).where(
            ProfileShopLink.profile_id == profile.id, ProfileShopLink.connection_id == connection_id
        )
    )
    link = rows.scalar_one_or_none()
    if link is None and connection_id == profile.connection_id:
        # The main shop is always linked. Read paths never write: a stand-in, not
        # added to the session (fifty drafts at once must not race to insert it).
        link = ProfileShopLink(
            tenant_id=profile.tenant_id, profile_id=profile.id, connection_id=profile.connection_id, status="ready"
        )
    return link


async def ensure_main_link(session: AsyncSession, profile: ListingProfile) -> ProfileShopLink:
    rows = await session.execute(
        select(ProfileShopLink).where(
            ProfileShopLink.profile_id == profile.id, ProfileShopLink.connection_id == profile.connection_id
        )
    )
    link = rows.scalar_one_or_none()
    if link is None:
        link = ProfileShopLink(
            tenant_id=profile.tenant_id, profile_id=profile.id, connection_id=profile.connection_id, status="ready"
        )
        session.add(link)
        await session.flush()
    return link


async def shop_profiles(
    session: AsyncSession, connection_id: uuid.UUID, *, confirmed: bool = True
) -> list[ListingProfile]:
    """The profiles usable in one shop: its own (main) and those linked to it."""
    linked = select(ProfileShopLink.profile_id).where(ProfileShopLink.connection_id == connection_id)
    query = select(ListingProfile).where(
        (ListingProfile.connection_id == connection_id) | ListingProfile.id.in_(linked)
    )
    if confirmed:
        query = query.where(ListingProfile.confirmed.is_(True))
    rows = await session.execute(query.order_by(ListingProfile.name))
    return list(rows.scalars())


async def detach_shop(session: AsyncSession, connection_id: uuid.UUID) -> dict[str, int]:
    """A shop is disconnected: only its links go (v8 §C). Does not commit.

    A profile whose main shop it was survives while another shop is linked: that
    shop becomes the main one. The Etsy data read from the old shop's reference
    goes with the old shop (CLAUDE.md), so the profile's reference payload is
    cleared and it needs a reference listing in its new main shop before its
    shared data can be read again. A profile with no other shop is deleted.
    """
    links = await session.execute(delete(ProfileShopLink).where(ProfileShopLink.connection_id == connection_id))
    owned = list((await session.execute(
        select(ListingProfile).where(ListingProfile.connection_id == connection_id)
    )).scalars())
    remaining = await links_of(session, [p.id for p in owned])
    moved = deleted = 0
    for profile in owned:
        others = sorted(
            (link for link in remaining.get(profile.id, []) if link.connection_id != connection_id),
            key=lambda link: link.created_at,
        )
        if not others:
            await session.delete(profile)
            deleted += 1
            continue
        heir = others[0]
        profile.connection_id = heir.connection_id
        profile.reference_listing_id = None
        profile.cached_payload = None
        profile.fixed_image_ids = None
        profile.updated_at = profile.images_updated_at = None
        profile.refresh_error = "Its main shop was disconnected. Choose a reference listing in this shop to keep using it."
        # The new main shop's ids are its reference's own: the link keeps none.
        heir.shipping_profile_id = heir.return_policy_id = heir.readiness_state_id = None
        heir.production_partner_ids = None
        heir.notes, heir.status = None, "ready"
        moved += 1
    await session.flush()
    return {"profile_links": links.rowcount or 0, "profiles_moved": moved, "profiles": deleted}


#: Every column that names a profile, as (table, column): repointed when two merge.
PROFILE_REFERENCES: tuple[tuple[str, str], ...] = (
    ("upload_batch", "size_chart_profile_id"),
    ("listing_group_setting", "profile_id"),
    ("listing_group_setting", "size_chart_profile_id"),
    ("generated_content", "listing_profile_id"),
    ("listing_publication", "profile_id"),
)


async def absorb(session: AsyncSession, keep: ListingProfile, other: ListingProfile) -> dict[str, int]:
    """Make ``other`` (same name, another main shop) part of ``keep`` (v8 §C).

    ``other``'s main shop becomes one of ``keep``'s linked shops, holding the ids
    ``other``'s reference had there; its other links move too unless ``keep``
    already has that shop. Listings, groups, batches and drafts that named
    ``other`` name ``keep``; its product costs give way to ``keep``'s. Then
    ``other`` is deleted. Does not commit.
    """
    from sqlalchemy import update

    from app.db import models

    if keep.tenant_id != other.tenant_id or keep.id == other.id:
        raise ValueError("not two profiles of one account")
    have = {link.connection_id for link in (await links_of(session, [keep.id]))[keep.id]}
    have.add(keep.connection_id)
    moved = 0
    payload = other.cached_payload or {}
    if other.connection_id not in have:
        link = ProfileShopLink(
            tenant_id=keep.tenant_id, profile_id=keep.id, connection_id=other.connection_id,
            shipping_profile_id=payload.get("shipping_profile_id"),
            return_policy_id=payload.get("return_policy_id"),
            readiness_state_id=payload.get("readiness_state_id"),
            production_partner_ids=list(payload.get("production_partner_ids") or []),
            status="ready" if payload else "checking",
        )
        session.add(link)
        have.add(other.connection_id)
        moved += 1
    for link in (await links_of(session, [other.id]))[other.id]:
        if link.connection_id in have:
            await session.delete(link)
        else:
            link.profile_id = keep.id
            have.add(link.connection_id)
            moved += 1
    repointed = 0
    tables = {t.name: t for t in models.Base.metadata.sorted_tables}
    for table_name, column in PROFILE_REFERENCES:
        table = tables[table_name]
        result = await session.execute(
            update(table).where(table.c[column] == other.id).values({column: keep.id})
        )
        repointed += result.rowcount or 0
    await session.flush()
    await session.delete(other)
    await session.flush()
    return {"links": moved, "repointed": repointed}
