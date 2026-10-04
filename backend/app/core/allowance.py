"""The listing allowance: listings generated per period ("Listings generated").

This is the plan's limit, set per seller in the admin panel (with a system
default): an amount and a period, daily, weekly or monthly. It counts designs
written: a listing's text generated, regenerated, or rewritten by Replace
images. A design counts once however many shops it is sent to: **creating
drafts and publishing never count**. Etsy requests are a different number with
a different reset (``core/limits.py``).

Periods follow the seller's own time zone: a day starts at their midnight, a
week on Monday 00:00 and a month on the 1st. Usage is recorded as timestamped
events (``allowance_use``) and a period is a window over them, so changing a
seller's amount or period applies at once without losing what they have used.

An account's own allowance is an amount **and** a period together; with either
missing it follows the system default entirely (never one's amount with the
other's period).

Work is checked before it starts; a Replace images job that is queued but has
not yet written its listing counts as used, so the queue can't go past the limit.
:func:`status` is the one place the number is computed: the seller's screens,
the checks and the admin panel all read it.
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
#: What every screen calls this number.
LABEL = "Listings generated"
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
    pending: int  # Replace images queued, their listing not yet written: counted as used
    period_start: datetime
    resets_at: datetime
    time_zone: str

    @property
    def used(self) -> int:
        return self.generations + self.pending

    @property
    def remaining(self) -> int:
        return max(0, self.amount - self.used)

    def message(self, wanted: int = 1) -> str:
        when = reset_label(self.resets_at, self.time_zone)
        word = _PERIOD_WORD.get(self.period, "this period")
        if self.remaining <= 0:
            return (
                f"You've generated your {self.amount:,} listings {word}. "
                f"More can be generated from {when}. Drafts and publishing are not affected."
            )
        return (
            f"This would generate {wanted} listings, and {self.remaining:,} of your {self.amount:,} "
            f"{'is' if self.remaining == 1 else 'are'} left {word}. It resets on {when}."
        )


async def status(session: AsyncSession, tenant: Tenant, now: datetime | None = None) -> AllowanceStatus:
    default_amount, default_period = await system_default(session)
    # The account's own allowance is the pair; half of one is not an override.
    custom = tenant.allowance_amount is not None and tenant.allowance_period in PERIODS
    amount = tenant.allowance_amount if custom else default_amount
    period = tenant.allowance_period if custom else default_period
    zone = _zone(tenant)
    start, reset = period_bounds(period, zone, now)
    rows = await session.execute(
        select(AllowanceUse.kind, func.count())
        .where(AllowanceUse.tenant_id == tenant.id, AllowanceUse.at >= start, AllowanceUse.at < reset)
        .group_by(AllowanceUse.kind)
    )
    counts = dict(rows.all())
    # Only listings written count. Rows of the old "draft" kind are still in the
    # table from when drafts counted; they are not read.
    pending = await session.scalar(
        select(func.count())
        .select_from(Job)
        .where(
            Job.tenant_id == tenant.id,
            Job.type == JobType.replace_images,
            Job.status.in_((JobStatus.queued, JobStatus.running)),
        )
    )
    return AllowanceStatus(
        amount=amount,
        period=period,
        custom=custom,
        generations=int(counts.get(GENERATION, 0)),
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
        "label": LABEL,
        "used": s.used,
        "generations": s.generations,
        "pending": s.pending,
        "remaining": s.remaining,
        "period_start": s.period_start,
        "resets_at": s.resets_at,
        "resets_label": reset_label(s.resets_at, s.time_zone),
        "time_zone": s.time_zone,
    }
