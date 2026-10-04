"""The Etsy Ads report, and how it squares with the statement.

The report is what a seller downloads from Shop Manager → Marketing → Etsy Ads
with the date range set to a whole month. It has one row per day **for the
whole shop** and no listing column: views, clicks, orders, revenue, spend,
ROAS, click rate (a percent: 1.8 means 1.8%) and the budget left that day.
Days are Eastern Time. Ad spend therefore cannot be tied to a listing from
this file, and nothing here pretends it can.

Two honest figures for "ads this month", and why they differ:
  * **Ad spend for clicks this month**: the report, day by day.
  * **Charged by Etsy this month**: the statement. Each day's clicks are billed
    the next day, so a month's statement holds the last day of the month before
    and not its own last day. Now and then a day is billed a little differently
    from what the report shows.
``reconcile`` lays the two side by side per click day and states the difference
exactly: charged = report − days billed later + earlier days billed now + the
days that were billed differently.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from app.pipeline.statement import AdCharge, StatementError, parse_day


class AdsReportError(ValueError):
    """The file is not an Etsy Ads report this module can read (safe to show)."""


@dataclass
class AdsDay:
    day: date
    views: int
    clicks: int
    orders: int
    revenue: Decimal
    spend: Decimal


@dataclass
class AdsReport:
    days: list[AdsDay] = field(default_factory=list)
    currency: str | None = None

    @property
    def first_day(self) -> date | None:
        return min((d.day for d in self.days), default=None)

    @property
    def last_day(self) -> date | None:
        return max((d.day for d in self.days), default=None)

    def _sum(self, name: str):
        return sum((getattr(d, name) for d in self.days), Decimal("0") if name in ("revenue", "spend") else 0)

    @property
    def spend(self) -> Decimal:
        return self._sum("spend")

    @property
    def revenue(self) -> Decimal:
        return self._sum("revenue")

    @property
    def orders(self) -> int:
        return self._sum("orders")

    @property
    def clicks(self) -> int:
        return self._sum("clicks")

    @property
    def views(self) -> int:
        return self._sum("views")

    @property
    def roas(self) -> Decimal | None:
        """Revenue per unit of spend over the whole report; None with no spend."""
        return (self.revenue / self.spend) if self.spend else None

    @property
    def click_rate(self) -> Decimal | None:
        """Clicks per view, as a fraction (0.018 is 1.8%)."""
        return (Decimal(self.clicks) / Decimal(self.views)) if self.views else None


_CURRENCY = re.compile(r"\(([A-Z]{3})\)")


def _number(text: str, line: int, what: str) -> Decimal:
    value = (text or "").strip().replace(",", "").replace("$", "")
    if value in ("", "--"):
        return Decimal("0")
    try:
        return Decimal(value)
    except InvalidOperation:
        raise AdsReportError(f"line {line}: {what} could not be read: {text!r}") from None


def parse(data: bytes | str) -> AdsReport:
    text = data.decode("utf-8-sig", errors="strict") if isinstance(data, bytes) else data.lstrip("﻿")
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise AdsReportError("the file is empty") from None

    def column(prefix: str) -> int | None:
        return next((i for i, h in enumerate(header) if h.lower().startswith(prefix)), None)

    at = {name: column(name) for name in ("date", "views", "clicks", "orders", "revenue", "spend")}
    missing = [name for name, i in at.items() if i is None]
    if missing:
        raise AdsReportError("this is not an Etsy Ads report: no column for " + ", ".join(missing))
    if any(h.lower().startswith(("listing", "title")) for h in header):
        # A per-listing export would be a different file with a different meaning.
        raise AdsReportError("this file has a listing column; the shop-level daily report is expected")

    out = AdsReport()
    currency = _CURRENCY.search(header[at["spend"]])
    out.currency = currency.group(1) if currency else None
    seen: set[date] = set()
    for line, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        try:
            day = parse_day(row[at["date"]])
        except (StatementError, IndexError):
            raise AdsReportError(f"line {line}: the date could not be read") from None
        if day in seen:
            raise AdsReportError(f"line {line}: {day.isoformat()} appears twice")
        seen.add(day)
        cell = lambda name: row[at[name]] if at[name] < len(row) else ""  # noqa: E731
        out.days.append(AdsDay(
            day=day,
            views=int(_number(cell("views"), line, "views")),
            clicks=int(_number(cell("clicks"), line, "clicks")),
            orders=int(_number(cell("orders"), line, "orders")),
            revenue=_number(cell("revenue"), line, "revenue"),
            spend=_number(cell("spend"), line, "spend"),
        ))
    if not out.days:
        raise AdsReportError("the report has no days")
    out.days.sort(key=lambda d: d.day)
    return out


@dataclass
class DayDifference:
    day: date
    reported: Decimal | None  # the report's spend for that day's clicks; None: day not in the report
    charged: Decimal | None  # what the statement billed for that day's clicks; None: not billed in it
    reason: str

    @property
    def difference(self) -> Decimal:
        return (self.charged or Decimal("0")) - (self.reported or Decimal("0"))


@dataclass
class AdsReconciliation:
    reported_total: Decimal  # "Ad spend for clicks this month"
    charged_total: Decimal  # "Charged by Etsy this month"
    matched_days: int
    #: Days in the report whose clicks this statement does not bill (billed next month).
    billed_later: list[DayDifference]
    #: Charges on this statement for clicks outside the report (the month before).
    billed_from_before: list[DayDifference]
    #: Days both have, with different amounts.
    billed_differently: list[DayDifference]

    @property
    def explained(self) -> Decimal:
        """The charged total rebuilt from the report and the differences."""
        return (
            self.reported_total
            - sum((d.reported or Decimal("0") for d in self.billed_later), Decimal("0"))
            + sum((d.charged or Decimal("0") for d in self.billed_from_before), Decimal("0"))
            + sum((d.difference for d in self.billed_differently), Decimal("0"))
        )

    @property
    def exact(self) -> bool:
        return self.explained == self.charged_total


def reconcile(charges: list[AdCharge], report: AdsReport) -> AdsReconciliation:
    """Line the statement's Etsy Ads charges up with the report, per click day."""
    charged: dict[date, Decimal] = {}
    for c in charges:
        charged[c.click_day] = charged.get(c.click_day, Decimal("0")) + c.amount
    reported = {d.day: d.spend for d in report.days}
    later, before, differently = [], [], []
    matched = 0
    for day in sorted(set(charged) | set(reported)):
        r, c = reported.get(day), charged.get(day)
        if r is not None and c is not None:
            if r == c:
                matched += 1
            else:
                differently.append(DayDifference(day, r, c, "Etsy billed this day's clicks differently from what the Ads report shows"))
        elif c is None:
            if r:
                later.append(DayDifference(day, r, None, "clicks are billed the next day: this day is on the next statement"))
            else:
                matched += 1  # nothing spent, nothing billed
        elif report.first_day is not None and day < report.first_day:
            before.append(DayDifference(day, None, c, "clicks on the last day of the month before, billed on this statement"))
        else:
            before.append(DayDifference(day, None, c, "the Ads report does not cover this day; the statement bills it"))
    return AdsReconciliation(
        reported_total=report.spend,
        charged_total=sum(charged.values(), Decimal("0")),
        matched_days=matched,
        billed_later=later,
        billed_from_before=before,
        billed_differently=differently,
    )
