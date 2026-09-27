"""The product allowance: listings generated and drafts created per period.

This is our product's allowance, set per seller in the admin panel (with a
system default): an amount and a period, daily, weekly or monthly. It counts
the seller's work, a listing's text written (generation, regeneration,
Replace images) and a draft made on Etsy, not raw Etsy requests. The Etsy
request quota (``tenant.daily_quota``, the rate limiter) is separate and stays
daily, because Etsy resets it daily.

Periods follow the seller's own time zone: a day starts at midnight, a week on
Monday 00:00 and a month on the 1st. Usage is recorded as timestamped events
(``allowance_use``) and a period is a window over them, so changing a seller's
amount or period applies at once without losing what they have used.

Work is checked before it starts (never failing silently halfway); drafts that
are queued but not yet made count as used, so the queue can't go past the limit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.timezones import valid_zone, zone_abbreviation
from app.db.models import AllowanceUse, AppSetting, Job, JobStatus, JobType, Tenant

PERIODS = ("daily", "weekly", "monthly")
GENERATION = "generation"
DRAFT = "draft"
DEFAULT_KEY = "allowance_default"

_PERIOD_WORD = {"daily": "today", "weekly": "this week", "monthly": "this month"}


class AllowanceExceeded(Exception):
    """The work would go past the seller's allowance (message is for the seller)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _zone(tenant: Tenant) -> str:
    return tenant.time_zone if valid_zone(tenant.time_zone) else "UTC"


def period_bounds(period: str, zone: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """The current period's start and its reset, as UTC instants, in ``zone``'s calendar."""
    tz = ZoneInfo(zone)
    local = (now or _now()).astimezone(tz)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    if period == "daily":
        start, end = day, day + timedelta(days=1)
    elif period == "weekly":
        start = day - timedelta(days=day.weekday())  # Monday
        end = start + timedelta(days=7)
    else:  # monthly
        start = day.replace(day=1)
        end = (start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1))
    # Wall-clock midnights in the zone (a date's own offset: DST kept), to UTC.
    return start.replace(tzinfo=tz).astimezone(timezone.utc), end.replace(tzinfo=tz).astimezone(timezone.utc)


def reset_label(reset: datetime, zone: str) -> str:
    """"Thu, Oct 1, 12:00 AM CDT": when an allowance resets, for messages."""
    local = reset.astimezone(ZoneInfo(zone))
    hour = local.hour % 12 or 12
    return f"{local:%a, %b} {local.day}, {hour}:{local:%M %p} {zone_abbreviation(reset, zone)}"


async def system_default(session: AsyncSession) -> tuple[int, str]:
    """The allowance for sellers without their own: set in the admin panel, else config."""
    row = await session.get(AppSetting, DEFAULT_KEY)
    settings = get_settings()
    amount, period = settings.allowance_default_amount, settings.allowance_default_period
    if row is not None:
        amount = int(row.value.get("amount", amount))
        period = str(row.value.get("period", period))
    return amount, period if period in PERIODS else "monthly"


@dataclass
class AllowanceStatus:
    amount: int
    period: str
    custom: bool  # the seller has their own; else the system default
    generations: int
    drafts: int
    pending: int  # drafts and Replace images queued, not yet done: counted as used
    period_start: datetime
    resets_at: datetime
    time_zone: str

    @property
    def used(self) -> int:
        return self.generations + self.drafts + self.pending

    @property
    def remaining(self) -> int:
        return max(0, self.amount - self.used)

    def message(self, wanted: int = 1) -> str:
        when = reset_label(self.resets_at, self.time_zone)
        word = _PERIOD_WORD.get(self.period, "this period")
        if self.remaining <= 0:
            return (
                f"You've used your allowance of {self.amount} listings and drafts {word}. "
                f"It resets on {when}."
            )
        return (
            f"This needs {wanted} of your allowance, and {self.remaining} of {self.amount} "
            f"{'is' if self.remaining == 1 else 'are'} left {word}. It resets on {when}."
        )


async def status(session: AsyncSession, tenant: Tenant, now: datetime | None = None) -> AllowanceStatus:
    default_amount, default_period = await system_default(session)
    custom = tenant.allowance_amount is not None or tenant.allowance_period is not None
    amount = tenant.allowance_amount if tenant.allowance_amount is not None else default_amount
    period = tenant.allowance_period if tenant.allowance_period in PERIODS else default_period
    zone = _zone(tenant)
    start, reset = period_bounds(period, zone, now)
    rows = await session.execute(
        select(AllowanceUse.kind, func.count())
        .where(AllowanceUse.tenant_id == tenant.id, AllowanceUse.at >= start, AllowanceUse.at < reset)
        .group_by(AllowanceUse.kind)
    )
    counts = dict(rows.all())
    pending = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.tenant_id == tenant.id,
            Job.type.in_((JobType.create_draft, JobType.replace_images)),
            Job.status.in_((JobStatus.queued, JobStatus.running)),
        )
    )
    return AllowanceStatus(
        amount=amount,
        period=period,
        custom=custom,
        generations=int(counts.get(GENERATION, 0)),
        drafts=int(counts.get(DRAFT, 0)),
        pending=int(pending or 0),
        period_start=start,
        resets_at=reset,
        time_zone=zone,
    )


async def check(session: AsyncSession, tenant: Tenant, wanted: int = 1) -> AllowanceStatus:
    """Raise AllowanceExceeded (with the reset date) unless ``wanted`` units fit."""
    current = await status(session, tenant)
    if wanted > current.remaining:
        raise AllowanceExceeded(current.message(wanted))
    return current


def record(session: AsyncSession, tenant_id: uuid.UUID, kind: str, count: int = 1) -> None:
    """Add used units (the caller commits with the work they stand for)."""
    for _ in range(count):
        session.add(AllowanceUse(id=uuid.uuid4(), tenant_id=tenant_id, kind=kind))


def status_out(s: AllowanceStatus) -> dict[str, Any]:
    return {
        "amount": s.amount,
        "period": s.period,
        "custom": s.custom,
        "used": s.used,
        "generations": s.generations,
        "drafts": s.drafts,
        "pending": s.pending,
        "remaining": s.remaining,
        "period_start": s.period_start,
        "resets_at": s.resets_at,
        "resets_label": reset_label(s.resets_at, s.time_zone),
        "time_zone": s.time_zone,
    }
