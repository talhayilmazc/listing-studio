"""When a reference profile needs refreshing, and who asks for it.

A profile holds Etsy data under two limits (6 hours for the image links, 24 for
the rest). Profiles in use are kept warm in the background: the worker's cron
renews each clock ahead of its limit. "In use" means a listing was written or
a draft created with it in the last :data:`USED_WITHIN` (two weeks); a profile
nobody publishes with is not refreshed in the background, since that would
spend the day's Etsy budget on data nobody reads. It is refreshed on demand
instead: when the Profiles page opens, when it is chosen for a batch, and when
content is generated with it.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import GeneratedContent, ListingProfile, ListingPublication

#: A profile used this recently is kept warm in the background.
USED_WITHIN = timedelta(days=14)

FULL = "refresh_profile"
IMAGES = "refresh_profile_images"


def _older(stamp: datetime | None, cutoff: datetime) -> bool:
    if stamp is None:
        return True
    if stamp.tzinfo is None:  # SQLite hands back naive datetimes
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp <= cutoff


def refresh_due(profile: ListingProfile, now: datetime | None = None) -> str | None:
    """Which refresh this profile needs now (full, images only), or None.

    A refresh that failed recently waits before the next try, so a gone
    reference listing is not asked for again and again.
    """
    now = now or datetime.now(timezone.utc)
    retry_after = now - timedelta(seconds=ListingProfile.AUTO_REFRESH_RETRY_SECONDS)
    if profile.refresh_failed_at is not None and not _older(profile.refresh_failed_at, retry_after):
        return None
    if not profile.cached_payload or _older(
        profile.updated_at, now - timedelta(seconds=ListingProfile.AUTO_REFRESH_SECONDS)
    ):
        return FULL
    if _older(
        profile.images_updated_at, now - timedelta(seconds=ListingProfile.AUTO_REFRESH_IMAGES_SECONDS)
    ):
        return IMAGES
    return None


def used_recently(now: datetime | None = None) -> ColumnElement[bool]:
    """SQL: the profile wrote a listing or made a draft within USED_WITHIN."""
    cutoff = (now or datetime.now(timezone.utc)) - USED_WITHIN
    return or_(
        exists().where(
            GeneratedContent.listing_profile_id == ListingProfile.id,
            GeneratedContent.created_at >= cutoff,
        ),
        exists().where(
            ListingPublication.profile_id == ListingProfile.id,
            ListingPublication.created_at >= cutoff,
        ),
    )


async def in_use(session: AsyncSession, profile_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """Which of these profiles are kept warm in the background."""
    ids = list(profile_ids)
    if not ids:
        return set()
    rows = await session.execute(
        select(ListingProfile.id).where(ListingProfile.id.in_(ids), used_recently())
    )
    return set(rows.scalars())


Enqueue = Callable[..., Awaitable[None]]


async def request_refresh(enqueue: Enqueue, profile: ListingProfile, *, origin: str) -> bool:
    """Queue the refresh this profile needs, if any; True if one was queued.

    One queued job per profile and kind, however often it is asked for.
    """
    function = refresh_due(profile)
    if function is None:
        return False
    await enqueue(function, str(profile.id), _job_id=f"{origin}:{function}:{profile.id}")
    return True
