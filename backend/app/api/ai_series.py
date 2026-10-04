"""AI cost over time, per seller (admin only; `core/ai_meter.py` writes the calls).

A period is a run of buckets in **Istanbul wall-clock time**: hours, days, weeks
(from Monday) or months, the last one still running. The provider's console
counts in UTC days, so the daily view also carries each date's UTC-day figures
(`AiCall.day`) for reconciling.

"Previous" is the same span one period earlier, cut at the same point: the last
24 hours up to now against the 24 hours before that, up to this time yesterday.
It is compared only when the records reach back that far (metering has a first
day, and calls are kept 25 months: enough for twelve months against the twelve
before): a period we have no records for is unknown, not free.

Counts and cost only: nothing of a seller's work is read here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import and_, case, exists, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AiCall, Tenant

ZONE_NAME = "Europe/Istanbul"
ZONE = ZoneInfo(ZONE_NAME)

Period = Literal["24h", "48h", "daily", "weekly", "monthly"]
#: period -> (bucket, how many, the last one being the current one)
PRESETS: dict[str, tuple[str, int]] = {
    "24h": ("hour", 24),
    "48h": ("hour", 48),
    "daily": ("day", 31),
    "weekly": ("week", 12),
    "monthly": ("month", 12),
}
#: Sellers with a colour of their own in the chart; the palette has eight hues
#: and a ninth is never generated. Later accounts share "Other sellers".
SLOTS = 8
NO_SELLER = "none"

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class AiCell(BaseModel):
    """Calls, listings and cost of one seller (or all) in one stretch of time."""

    calls: int = 0
    failed: int = 0
    listings: int = 0
    cost_usd: str = "0.000000"
    #: Some of these calls used a model with no price: the cost is a floor.
    unpriced: bool = False
    cost_per_listing_usd: str | None = None


class AiBucket(BaseModel):
    start: datetime  # Istanbul time, with its offset
    end: datetime
    label: str  # short, for the axis
    title: str  # in full, for the readout, the table and the export
    partial: bool  # still running
    #: Daily view only: the same date as the provider's console counts it (a UTC
    #: day runs 03:00 to 03:00 in Istanbul), and what was spent in it.
    utc_day: str | None = None
    utc: AiCell | None = None
    total: AiCell  # every seller shown


class AiSeriesSeller(BaseModel):
    id: str  # the account, or "none": our own runs and deleted accounts
    email: str | None
    #: The seller's colour, fixed per account whatever the period or filter.
    slot: int | None
    total: AiCell
    #: Of the period's cost for all sellers (0..1), whatever the filter.
    share: str | None
    cells: list[AiCell]  # one per bucket


class AiSellerOption(BaseModel):
    id: str
    email: str | None
    slot: int | None


class AiChange(BaseModel):
    """Percent against the previous period; None where that had nothing."""

    cost: str | None = None
    listings: str | None = None
    cost_per_listing: str | None = None


class AiSeriesOut(BaseModel):
    period: Period
    seller: str  # "all", "none" or an account id
    time_zone: str
    as_of: datetime
    start: datetime
    end: datetime
    buckets: list[AiBucket]
    sellers: list[AiSeriesSeller]
    total: AiCell  # the sellers shown
    all_sellers: AiCell  # the period, whatever the filter
    previous: AiCell
    previous_start: datetime
    previous_end: datetime
    #: False when the records do not reach back to the previous period: `change`
    #: is then empty, because what was spent then is unknown.
    previous_covered: bool
    records_from: str | None  # the first UTC day with a recorded call
    change: AiChange
    options: list[AiSellerOption]  # who the filter can pick in this period


@dataclass
class _Sum:
    calls: int = 0
    failed: int = 0
    listings: int = 0
    cost: Decimal = field(default_factory=Decimal)
    unpriced: bool = False

    def add(self, row: Any) -> None:
        self.calls += int(row["calls"] or 0)
        self.failed += int(row["failed"] or 0)
        self.listings += int(row["listings"] or 0)
        self.cost += Decimal(str(row["cost"] or 0))
        self.unpriced = self.unpriced or bool(row["unpriced"])

    def take(self, other: _Sum) -> None:
        self.calls += other.calls
        self.failed += other.failed
        self.listings += other.listings
        self.cost += other.cost
        self.unpriced = self.unpriced or other.unpriced

    @property
    def per_listing(self) -> Decimal | None:
        return self.cost / self.listings if self.listings else None

    def out(self) -> AiCell:
        per = self.per_listing
        return AiCell(calls=self.calls, failed=self.failed, listings=self.listings, cost_usd=f"{self.cost:.6f}",
                      unpriced=self.unpriced, cost_per_listing_usd=None if per is None else f"{per:.6f}")


def _floor(local: datetime, unit: str) -> datetime:
    if unit == "hour":
        return local.replace(minute=0, second=0, microsecond=0)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == "day":
        return day
    if unit == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def _months(local: datetime, n: int) -> datetime:
    """`n` months on, the day of the month kept where that month has it."""
    index = local.year * 12 + local.month - 1 + n
    year, month = divmod(index, 12)
    first_of_next = date(year + (month == 11), (month + 1) % 12 + 1, 1)
    last = (first_of_next - timedelta(days=1)).day
    return local.replace(year=year, month=month + 1, day=min(local.day, last))


def _step(local: datetime, unit: str, n: int) -> datetime:
    if unit == "month":
        return _months(local, n)
    return local + {"hour": timedelta(hours=n), "day": timedelta(days=n), "week": timedelta(weeks=n)}[unit]


def _labels(start: datetime, unit: str) -> tuple[str, str]:
    day = f"{start.day} {_MONTHS[start.month - 1][:3]}"
    if unit == "hour":
        until = start + timedelta(hours=1)
        short = day if start.hour == 0 else f"{start:%H}:00"
        return short, f"{_DAYS[start.weekday()]} {day}, {start:%H}:00 to {until:%H}:00"
    if unit == "day":
        return day, f"{_DAYS[start.weekday()]} {day} {start.year}"
    if unit == "week":
        last = start + timedelta(days=6)
        return day, f"{day} to {last.day} {_MONTHS[last.month - 1][:3]} {last.year}"
    name = _MONTHS[start.month - 1]
    return (f"{name[:3]} {start.year}" if start.month == 1 else name[:3]), f"{name} {start.year}"


def window(period: str, now: datetime) -> tuple[str, list[datetime], datetime]:
    """The bucket unit, each bucket's start and the end of the last one, as
    Istanbul wall-clock times (naive)."""
    unit, count = PRESETS[period]
    current = _floor(now.astimezone(ZONE).replace(tzinfo=None), unit)
    starts = [_step(current, unit, i - count + 1) for i in range(count)]
    return unit, starts, _step(current, unit, 1)


def _aware(local: datetime) -> datetime:
    return local.replace(tzinfo=ZONE)


def _utc(local: datetime) -> datetime:
    """The instant of an Istanbul wall-clock time, as the database compares it."""
    return _aware(local).astimezone(timezone.utc)


_FIGURES = (
    func.count().label("calls"),
    func.sum(case((AiCall.ok.is_(False), 1), else_=0)).label("failed"),
    func.sum(AiCall.listings).label("listings"),
    func.sum(AiCall.cost_usd).label("cost"),
    # A call with tokens but no cost: its model had no price.
    func.sum(case((and_(AiCall.cost_usd.is_(None), AiCall.input_tokens + AiCall.output_tokens > 0), 1), else_=0)).label("unpriced"),
)


def _between(start: datetime, end: datetime) -> Any:
    """Calls in [start, end). `day` (the UTC day, indexed) narrows the scan first."""
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    return and_(AiCall.day >= start.date(), AiCall.day <= end.date(), AiCall.at >= start, AiCall.at < end)


def _pct(now: Decimal | None, before: Decimal | None) -> str | None:
    if now is None or not before:
        return None
    return f"{(now - before) / before * 100:.1f}"


def parse_seller(value: str) -> str:
    """ "all", "none", or an account id; anything else raises ValueError."""
    if value in ("all", NO_SELLER):
        return value
    return str(uuid.UUID(value))


async def build(session: AsyncSession, period: str, seller: str = "all", now: datetime | None = None) -> AiSeriesOut:
    now = now or datetime.now(timezone.utc)
    unit, starts, end_local = window(period, now)
    start, end = _aware(starts[0]), _aware(end_local)
    key = lambda tenant_id: str(tenant_id) if tenant_id else NO_SELLER  # noqa: E731
    shown = lambda who: seller in ("all", who)  # noqa: E731

    # A call's bucket is the first one whose end it comes before. The boundaries
    # are instants, so the buckets are Istanbul's whatever the database's zone.
    ends = [*starts[1:], end_local]
    bucket = case(*[(AiCall.at < _utc(until), i) for i, until in enumerate(ends)], else_=len(ends) - 1).label("bucket")
    rows = (
        await session.execute(
            select(bucket, AiCall.tenant_id, *_FIGURES).where(_between(start, end)).group_by(literal_column("bucket"), AiCall.tenant_id)
        )
    ).mappings().all()

    cells: dict[str, list[_Sum]] = {}
    everyone = _Sum()
    for row in rows:
        everyone.add(row)
        cells.setdefault(key(row["tenant_id"]), [_Sum() for _ in starts])[int(row["bucket"])].add(row)

    # The same span, one period earlier, up to the same point in it.
    if unit == "month":
        before_start, before_end = _months(starts[0], -len(starts)), _months(now.astimezone(ZONE).replace(tzinfo=None), -len(starts))
    else:
        span = end_local - starts[0]
        before_start, before_end = starts[0] - span, now.astimezone(ZONE).replace(tzinfo=None) - span
    first_day = (await session.execute(select(func.min(AiCall.day)))).scalar()
    covered = first_day is not None and _utc(before_start).date() >= first_day
    previous = _Sum()
    for row in (
        await session.execute(
            select(AiCall.tenant_id, *_FIGURES).where(_between(_aware(before_start), _aware(before_end))).group_by(AiCall.tenant_id)
        )
    ).mappings():
        if shown(key(row["tenant_id"])):
            previous.add(row)

    utc: dict[date, _Sum] = {}
    if unit == "day":
        for row in (
            await session.execute(
                select(AiCall.day, AiCall.tenant_id, *_FIGURES)
                .where(AiCall.day >= starts[0].date(), AiCall.day <= starts[-1].date())
                .group_by(AiCall.day, AiCall.tenant_id)
            )
        ).mappings():
            if shown(key(row["tenant_id"])):
                utc.setdefault(row["day"], _Sum()).add(row)

    # A colour belongs to an account: the first eight that have ever had a call,
    # oldest account first, whatever the period and whoever is filtered out.
    accounts = (
        await session.execute(
            select(Tenant.id, Tenant.email)
            .where(exists().where(AiCall.tenant_id == Tenant.id))
            .order_by(Tenant.created_at, Tenant.id)
        )
    ).all()
    slot = {str(a.id): (i if i < SLOTS else None) for i, a in enumerate(accounts)}
    email = {str(a.id): a.email for a in accounts}
    order = {who: i for i, who in enumerate([*email, NO_SELLER])}

    sellers = []
    total = _Sum()
    columns = [_Sum() for _ in starts]
    for who in sorted(cells, key=lambda w: order.get(w, len(order))):
        if not shown(who):
            continue
        whole = _Sum()
        for column, cell in zip(columns, cells[who], strict=True):
            whole.take(cell)
            column.take(cell)
        total.take(whole)
        sellers.append(
            AiSeriesSeller(
                id=who, email=email.get(who), slot=slot.get(who), total=whole.out(),
                share=f"{whole.cost / everyone.cost:.4f}" if everyone.cost else None,
                cells=[c.out() for c in cells[who]],
            )
        )

    buckets = []
    for i, local in enumerate(starts):
        label, title = _labels(local, unit)
        until = _step(local, unit, 1)
        day = local.date() if unit == "day" else None
        buckets.append(
            AiBucket(
                start=_aware(local), end=_aware(until), label=label, title=title, partial=_aware(until) > now,
                utc_day=day.isoformat() if day else None,
                utc=utc.get(day, _Sum()).out() if day else None,
                total=columns[i].out(),
            )
        )

    return AiSeriesOut(
        period=period,  # type: ignore[arg-type]
        seller=seller,
        time_zone=ZONE_NAME,
        as_of=now,
        start=start,
        end=end,
        buckets=buckets,
        sellers=sellers,
        total=total.out(),
        all_sellers=everyone.out(),
        previous=previous.out(),
        previous_start=_aware(before_start),
        previous_end=_aware(before_end),
        previous_covered=covered,
        records_from=first_day.isoformat() if first_day else None,
        change=AiChange(
            cost=_pct(total.cost, previous.cost),
            listings=_pct(Decimal(total.listings), Decimal(previous.listings)),
            cost_per_listing=_pct(total.per_listing, previous.per_listing),
        ) if covered else AiChange(),
        options=[AiSellerOption(id=who, email=email.get(who), slot=slot.get(who)) for who in sorted(cells, key=lambda w: order.get(w, len(order)))],
    )
