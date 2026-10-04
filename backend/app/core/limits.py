"""The three numbers that limit work, each defined once.

Every screen and every endpoint that shows or enforces one of these reads it
from here, so two screens cannot disagree about the same account at the same
moment.

1. **Listings generated** (the plan's allowance): ``core/allowance.py::status``.
   Counts designs written, once each whatever the number of shops; drafts and
   publishing never count. Per day, week or month in the seller's own time
   zone, so it resets at their local midnight (or on the 1st).

2. **Etsy requests today** (the account's ceiling): :func:`etsy_ceiling`.
   Requests made for the seller's own work in all their shops: creating
   drafts, publishing, replacing images. An account follows the default
   (``ACCOUNT_DAILY_CEILING``, 4,500) unless an admin sets its own number.
   The app's upkeep (shop sync, profile refresh, sales and ledger reads) is
   not counted against it. Resets at 00:00 UTC, because Etsy's day does.

3. **The app's Etsy budget** (admin only): :func:`app_budget`. Every request by
   everyone, upkeep included, against Etsy's 5,000 a day; new work pauses at
   90% so work already running can finish. Resets at 00:00 UTC. A seller never
   sees its figures, only, when it is what stops their work, that it did.

:func:`spendable` is what may still start for a seller right now: the smaller
of what their ceiling and the app's budget leave, and which of the two that is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.core.timezones import valid_zone, zone_abbreviation
from app.db.models import Tenant


def utc_reset(now: datetime | None = None) -> datetime:
    """The next 00:00 UTC: when both Etsy counters start again."""
    now = now or datetime.now(timezone.utc)
    tomorrow = (now.astimezone(timezone.utc) + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)


def zone_of(tenant: Tenant | None) -> str:
    return tenant.time_zone if tenant is not None and valid_zone(tenant.time_zone) else "UTC"


def time_label(instant: datetime, zone: str) -> str:
    """ "7:00 PM CDT": a time of day in the seller's zone."""
    local = instant.astimezone(ZoneInfo(zone))
    return f"{local.hour % 12 or 12}:{local:%M %p} {zone_abbreviation(instant, zone)}"


def ceiling_default() -> int:
    return get_settings().account_daily_ceiling


def ceiling_limit(tenant: Tenant) -> int:
    """The account's Etsy requests per day: its own number if an admin set one,
    else the default. The one place this is decided."""
    own = tenant.etsy_ceiling_override
    return ceiling_default() if own is None else own


def follows_default(tenant: Tenant) -> bool:
    return tenant.etsy_ceiling_override is None


@dataclass
class EtsyCeiling:
    limit: int
    used: int
    follows_default: bool
    default: int
    #: Requests today keeping the account's shops and profiles current. Not part
    #: of ``used``: they count against the app's budget only.
    upkeep: int
    resets_at: datetime
    time_zone: str

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def resets_label(self) -> str:
        return time_label(self.resets_at, self.time_zone)

    def out(self) -> dict[str, Any]:
        return {
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "follows_default": self.follows_default,
            "default": self.default,
            "upkeep": self.upkeep,
            "resets_at": self.resets_at,
            "resets_label": self.resets_label,
        }


async def etsy_ceiling(quota: Any, tenant: Tenant, now: datetime | None = None) -> EtsyCeiling:
    used, _ = await quota.usage(tenant.id)
    return EtsyCeiling(
        limit=ceiling_limit(tenant),
        used=used,
        follows_default=follows_default(tenant),
        default=ceiling_default(),
        upkeep=await quota.upkeep_usage(tenant.id),
        resets_at=utc_reset(now),
        time_zone=zone_of(tenant),
    )


@dataclass
class AppBudget:
    limit: int  # Etsy's own: 5,000 a day for the whole app
    pause_at: int  # new work waits from here (90%)
    used: int  # everyone's requests today, upkeep included
    resets_at: datetime

    @property
    def remaining(self) -> int:
        """What may still be spent before new work pauses."""
        return max(0, self.pause_at - self.used)

    @property
    def paused(self) -> bool:
        return self.used >= self.pause_at


async def app_budget(quota: Any, now: datetime | None = None) -> AppBudget:
    return AppBudget(limit=quota.global_limit, pause_at=quota.pause_at, used=await quota.global_usage(), resets_at=utc_reset(now))


@dataclass
class Spendable:
    """What a seller's new work may still spend right now."""

    amount: int
    #: "account": their own ceiling is the smaller; "app": the app's budget is.
    limited_by: str
    ceiling: EtsyCeiling
    app_remaining: int


async def spendable(quota: Any, tenant: Tenant, now: datetime | None = None) -> Spendable:
    ceiling = await etsy_ceiling(quota, tenant, now)
    app = await app_budget(quota, now)
    by_app = app.remaining < ceiling.remaining
    return Spendable(
        amount=min(ceiling.remaining, app.remaining),
        limited_by="app" if by_app else "account",
        ceiling=ceiling,
        app_remaining=app.remaining,
    )


def app_budget_message(tenant: Tenant | None, ceiling: EtsyCeiling | None = None) -> str:
    """What a seller is told when the app's budget, not their own ceiling, stops
    their work. It says exactly that, and when work resumes in their own time."""
    reset = utc_reset()
    when = time_label(reset, zone_of(tenant))
    own = (
        f" It is not your own limit: you have {ceiling.remaining:,} of your {ceiling.limit:,} Etsy requests left today."
        if ceiling is not None
        else " It is not your own limit."
    )
    return (
        "The app's shared Etsy budget for today is used up, so new work waits."
        + own
        + f" It resumes by itself at {when} (00:00 UTC)."
    )


def ceiling_message(tenant: Tenant | None, limit: int) -> str:
    when = time_label(utc_reset(), zone_of(tenant))
    return (
        f"Your account has used its {limit:,} Etsy requests for today. "
        f"New work waits, then resumes by itself at {when} (00:00 UTC)."
    )
