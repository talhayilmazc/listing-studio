"""Retention of Etsy-sourced content (CLAUDE.md cache rules, Etsy API ToU §1).

These are contractual limits, not tidiness. Each rule below is also a sentence in
the Privacy Policy, so the policy stays true only while this module runs:

* ``listing_snapshot`` — pre-change copies of the seller's listings, kept for
  rollback — are deleted **90 days** after they were taken.
* ``shop_listing_cache`` — the seller's own listings, including image URLs — is
  deleted once older than **6 hours**; the next view fetches it again.
* A profile's reference payload is held under two limits. Its image *links*
  are displayed in the profile card, so they follow the listing-display limit
  and are stripped after **6 hours**. The rest — taxonomy, attributes, price,
  shipping, variation shape, readiness state, the description used to write
  drafts, and each image's id, rank and size-chart classification — is never
  displayed and is held to provide the service; it is cleared after
  **24 hours**. The profile itself (name, template, prefix, chosen size
  charts) is the seller's own configuration and stays; a refresh repopulates
  the rest.

:func:`purge_expired` runs on a cron in the worker. :func:`purge_shop_etsy_content`
runs when a seller disconnects their shop, removing everything Etsy-sourced at
once rather than waiting for it to age out.

Payloads are cleared to SQL ``NULL`` via ``null()``, not Python ``None``: on a
JSON column ``None`` is stored as the JSON value ``null``, which ``IS NOT NULL``
still matches — every sweep would "clear" the same rows again, forever.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, null, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AiCall,
    InviteRequest,
    AdCharge,
    AdsDaily,
    AllowanceUse,
    DraftAttempt,
    LedgerDaily,
    LedgerSync,
    Job,
    ListingProfile,
    ListingPublication,
    ListingSnapshot,
    SaleLine,
    SalesDaily,
    SalesSync,
    StatementImport,
    StatementListingFee,
    StatementOrder,
    ShopListingCache,
)

logger = logging.getLogger(__name__)


async def _bulk(session: AsyncSession, statement: Any) -> Any:
    """Run a bulk DELETE/UPDATE, matching rows in the database rather than by
    re-evaluating the condition on loaded objects in Python (which compares a
    naive datetime from SQLite with an aware cutoff and fails)."""
    return await session.execute(statement.execution_options(synchronize_session="fetch"))


async def purge_expired_rows(session: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    """Delete or clear every Etsy-sourced row past its maximum age. Commits."""
    now = now or datetime.now(timezone.utc)

    snapshots = await _bulk(session, 
        delete(ListingSnapshot).where(
            ListingSnapshot.taken_at < now - timedelta(days=ListingSnapshot.RETENTION_DAYS)
        )
    )
    listings = await _bulk(session, 
        delete(ShopListingCache).where(
            ShopListingCache.fetched_at
            < now - timedelta(seconds=ShopListingCache.STALE_SECONDS)
        )
    )
    # Sales totals: 13 months (v7 §C1), long enough for
    # last year's season.
    oldest = (now - timedelta(days=SalesDaily.RETENTION_DAYS)).date()
    sales = await _bulk(session, delete(SalesDaily).where(SalesDaily.day < oldest))
    await _bulk(session, delete(LedgerDaily).where(LedgerDaily.day < oldest))
    # What "Import from Etsy" stored, and the order lines it is joined to: the same 13 months.
    await _bulk(session, delete(SaleLine).where(SaleLine.day < oldest))
    await _bulk(session, delete(AdsDaily).where(AdsDaily.day < oldest))
    await _bulk(session, delete(AdCharge).where(AdCharge.click_day < oldest))
    for table in (StatementImport, StatementOrder, StatementListingFee):
        await _bulk(session, delete(table).where(table.month < oldest.replace(day=1)))
    # Drafts that were started and never finished or retried (etsy/publisher.py).
    await _bulk(session, delete(DraftAttempt).where(DraftAttempt.created_at < now - timedelta(days=DraftAttempt.RETENTION_DAYS)))
    # Allowance usage: long past any period an allowance counts over.
    await _bulk(session, delete(AllowanceUse).where(AllowanceUse.at < now - timedelta(days=AllowanceUse.RETENTION_DAYS)))
    # Invite requests are personal data from people who are not users.
    await _bulk(session, 
        delete(InviteRequest).where(
            or_(
                InviteRequest.decided_at < now - timedelta(days=InviteRequest.DECIDED_RETENTION_DAYS),
                InviteRequest.created_at < now - timedelta(days=InviteRequest.PENDING_RETENTION_DAYS),
            )
        )
    )
    # Our own record of model calls: kept 25 months, so twelve months can be
    # compared with the twelve before them.
    await _bulk(session, delete(AiCall).where(AiCall.day < (now - timedelta(days=AiCall.RETENTION_DAYS)).date()))
    image_links = await _strip_display_fields(session, now)
    profiles = await _bulk(session, 
        update(ListingProfile)
        .where(
            ListingProfile.cached_payload.is_not(None),
            or_(
                ListingProfile.updated_at.is_(None),
                ListingProfile.updated_at
                < now - timedelta(seconds=ListingProfile.CACHE_MAX_AGE_SECONDS),
            ),
        )
        .values(cached_payload=null())
    )
    await session.commit()
    counts = {
        "snapshots": snapshots.rowcount or 0,
        "shop_listings": listings.rowcount or 0,
        "profile_image_links": image_links,
        "profile_payloads": profiles.rowcount or 0,
        "sales_days": sales.rowcount or 0,
    }
    if any(counts.values()):
        logger.info("retention: purged %s", counts)
    return counts


async def _strip_display_fields(session: AsyncSession, now: datetime) -> int:
    """Drop the image links from payloads past the 6-hour display limit.

    JSON surgery, so it runs in Python; there are a handful of profiles per
    tenant. A fresh dict is assigned rather than mutating in place, which the
    ORM would not notice. Returns how many profiles were changed.
    """
    cutoff = now - timedelta(seconds=ListingProfile.DISPLAY_MAX_AGE_SECONDS)
    # The links have their own clock since auto-refresh renews them alone (v6 §H).
    fetched = func.coalesce(ListingProfile.images_updated_at, ListingProfile.updated_at)
    rows = await session.execute(
        select(ListingProfile).where(
            ListingProfile.cached_payload.is_not(None),
            or_(fetched.is_(None), fetched < cutoff),
        )
    )
    keys = ListingProfile.DISPLAY_IMAGE_KEYS
    changed = 0
    for profile in rows.scalars():
        payload = profile.cached_payload or {}
        images = payload.get("images") or []
        if not any(key in image for image in images for key in keys):
            continue  # already stripped
        profile.cached_payload = {
            **payload,
            "images": [{k: v for k, v in image.items() if k not in keys} for image in images],
        }
        changed += 1
    await session.flush()
    return changed


async def purge_shop_etsy_content(session: AsyncSession, connection_id: uuid.UUID) -> dict[str, int]:
    """Remove everything Etsy-sourced for one shop (on disconnect). Does not commit.

    Deleted: that shop's cached listings, its links to the account's profiles
    (a profile whose main shop it was moves to another linked shop, and the Etsy
    data read from this shop's reference goes; a profile used in no other shop
    is deleted), the saved copies of its listings, and the links from generated
    content to its drafts (v5 §E, v8 §C). Kept: the seller's uploads and
    generated content, and everything belonging to the account's other shops.
    """
    shop_jobs = select(Job.id).where(Job.connection_id == connection_id)
    snapshots = await session.execute(
        delete(ListingSnapshot).where(ListingSnapshot.job_id.in_(shop_jobs))
    )
    listings = await session.execute(
        delete(ShopListingCache).where(ShopListingCache.connection_id == connection_id)
    )
    publications = await session.execute(
        delete(ListingPublication).where(ListingPublication.connection_id == connection_id)
    )
    # Profiles are the account's (v8 §C): only this shop's links go; a profile whose
    # main shop this was moves to another linked shop, or goes with its last one.
    from app.pipeline.profile_shops import detach_shop

    detached = await detach_shop(session, connection_id)
    sales = await session.execute(delete(SalesDaily).where(SalesDaily.connection_id == connection_id))
    await session.execute(delete(SalesSync).where(SalesSync.connection_id == connection_id))
    await session.execute(delete(LedgerDaily).where(LedgerDaily.connection_id == connection_id))
    await session.execute(delete(DraftAttempt).where(DraftAttempt.connection_id == connection_id))
    await session.execute(delete(LedgerSync).where(LedgerSync.connection_id == connection_id))
    # Imported statements and Ads reports, and the order lines: the shop's, so they go with it.
    for table in (SaleLine, AdsDaily, AdCharge, StatementImport, StatementOrder, StatementListingFee):
        await session.execute(delete(table).where(table.connection_id == connection_id))
    return {
        "sales_days": sales.rowcount or 0,
        "snapshots": snapshots.rowcount or 0,
        "shop_listings": listings.rowcount or 0,
        "publications": publications.rowcount or 0,
        "profiles": detached["profiles"],
        "profiles_moved": detached["profiles_moved"],
        "profile_links": detached["profile_links"],
    }


async def purge_expired(ctx: dict[str, Any]) -> dict[str, int]:
    """arq cron entry point."""
    async with ctx["sessionmaker"]() as session:
        return await purge_expired_rows(session)
