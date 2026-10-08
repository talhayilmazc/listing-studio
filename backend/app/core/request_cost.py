"""How many Etsy requests one draft costs: measured, with a margin, not the worst case.

The worst case (``gate.JOB_COST["run_publish_job"]``, 45) is what one draft
*can* make: every attribute, ten images, every retry. Planning with it blocks
real work: a 16-shop seller sending ~240 drafts a day would be told 10,800
requests where ~3,600 are spent. So every estimate that decides whether work
fits (the gate's admission of a draft job, the publish preview, group
schedules) uses the **measured average** of the last :data:`WINDOW_DAYS`:
requests spent in the "drafts" category (``api_usage.categories``, all accounts)
÷ draft jobs that finished, plus :data:`MARGIN`, rounded up, never above the
worst case. Failed and retried attempts are in the requests, so the average
already carries them. With fewer than :data:`MIN_DRAFTS` measured drafts it is
:data:`DEFAULT_PER_DRAFT`. A draft that turns out dearer than the estimate is
not lost: it stops at the wall and carries on after the reset (drafts resume,
etsy/publisher.py).
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiUsage, Job, JobStatus, JobType

WINDOW_DAYS = 14
MIN_DRAFTS = 20
MARGIN = 0.25
#: Until enough drafts are measured (the planning figure the app used before).
DEFAULT_PER_DRAFT = 15
#: What one draft can make at most (gate.JOB_COST; kept in step by a test).
WORST_CASE = 45
CATEGORY = "drafts"
#: Seconds an estimate is reused (it is read before every draft job).
CACHE_SECONDS = 600

_cache: tuple[float, "DraftEstimate"] | None = None


@dataclass(frozen=True)
class DraftEstimate:
    per_draft: int  # what plans and the gate use
    source: str  # "measured" | "default"
    measured_mean: float | None
    drafts: int  # finished draft jobs in the window
    requests: int  # "drafts" requests in the window
    since: date | None
    worst_case: int = WORST_CASE
    margin: float = MARGIN

    def out(self) -> dict[str, Any]:
        return asdict(self)


def estimate_from(requests: int, drafts: int, since: date | None) -> DraftEstimate:
    if drafts < MIN_DRAFTS:
        mean = (requests / drafts) if drafts else None
        return DraftEstimate(DEFAULT_PER_DRAFT, "default", mean, drafts, requests, since)
    mean = requests / drafts
    per = min(WORST_CASE, max(1, math.ceil(mean * (1 + MARGIN))))
    return DraftEstimate(per, "measured", mean, drafts, requests, since)


async def measure(session: AsyncSession, today: date | None = None) -> DraftEstimate:
    """The last :data:`WINDOW_DAYS` complete UTC days (today is still running)."""
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=WINDOW_DAYS)
    rows = (await session.execute(
        select(ApiUsage.usage_date, ApiUsage.categories).where(
            ApiUsage.usage_date >= start, ApiUsage.usage_date < today, ApiUsage.categories.is_not(None)
        )
    )).all()
    if not rows:
        return estimate_from(0, 0, None)
    since = min(day for day, _ in rows)
    requests = sum(int((cats or {}).get(CATEGORY, 0)) for _, cats in rows)
    begin = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
    end = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    drafts = int(await session.scalar(
        select(func.count()).select_from(Job).where(
            Job.type == JobType.create_draft, Job.status == JobStatus.succeeded,
            Job.finished_at >= begin, Job.finished_at < end,
        )
    ) or 0)
    return estimate_from(requests, drafts, since)


async def draft_estimate(session: AsyncSession) -> DraftEstimate:
    """:func:`measure`, reused for :data:`CACHE_SECONDS`."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    estimate = await measure(session)
    _cache = (now, estimate)
    return estimate


def forget() -> None:
    """Drop the cached estimate (tests)."""
    global _cache
    _cache = None
