"""Retention of Etsy-sourced content (CLAUDE.md cache rules, Etsy API ToU §1).

These are contractual limits, not tidiness. Each rule below is also a sentence in
the Privacy Policy, so the policy stays true only while this module runs:

* ``listing_snapshot`` — pre-change copies of the seller's listings, kept for
  rollback — are deleted **90 days** after they were taken.
* ``shop_listing_cache`` — the seller's own listings, including image URLs — is
  deleted once older than **6 hours**; the next view fetches it again.
* A profile's ``cached_payload`` — the reference listing's category, price,
  variations, description and image list — is cleared once older than
  **24 hours**. The profile itself (name, template, prefix, chosen size charts)
  is the seller's own configuration and stays; a refresh repopulates the rest.

:func:`purge_expired` runs on a cron in the worker. :func:`purge_tenant_etsy_content`
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

from sqlalchemy import delete, null, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ListingProfile, ListingSnapshot, ShopListingCache

logger = logging.getLogger(__name__)


async def purge_expired_rows(session: AsyncSession, *, now: datetime | None = None) -> dict[str, int]:
    """Delete or clear every Etsy-sourced row past its maximum age. Commits."""
    now = now or datetime.now(timezone.utc)

    snapshots = await session.execute(
        delete(ListingSnapshot).where(
            ListingSnapshot.taken_at < now - timedelta(days=ListingSnapshot.RETENTION_DAYS)
        )
    )
    listings = await session.execute(
        delete(ShopListingCache).where(
            ShopListingCache.fetched_at
            < now - timedelta(seconds=ShopListingCache.STALE_SECONDS)
        )
    )
    profiles = await session.execute(
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
        "profile_payloads": profiles.rowcount or 0,
    }
    if any(counts.values()):
        logger.info("retention: purged %s", counts)
    return counts


async def purge_tenant_etsy_content(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    """Remove everything Etsy-sourced for one tenant (on disconnect). Does not commit.

    Kept: the seller's uploads, the drafts we generated for them, and their
    profile *settings*. Those are the seller's own work, not Etsy's content.
    """
    snapshots = await session.execute(
        delete(ListingSnapshot).where(ListingSnapshot.tenant_id == tenant_id)
    )
    listings = await session.execute(
        delete(ShopListingCache).where(ShopListingCache.tenant_id == tenant_id)
    )
    profiles = await session.execute(
        update(ListingProfile)
        .where(ListingProfile.tenant_id == tenant_id, ListingProfile.cached_payload.is_not(None))
        .values(cached_payload=null())
    )
    return {
        "snapshots": snapshots.rowcount or 0,
        "shop_listings": listings.rowcount or 0,
        "profile_payloads": profiles.rowcount or 0,
    }


async def purge_expired(ctx: dict[str, Any]) -> dict[str, int]:
    """arq cron entry point."""
    async with ctx["sessionmaker"]() as session:
        return await purge_expired_rows(session)
