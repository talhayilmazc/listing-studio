"""Turning the seller's own sales into daily totals per listing (v7 §C1).

This is the only code that reads Etsy's transaction objects, and it reads four
fields from each: ``listing_id``, ``quantity``, ``price`` and the date
(``created_timestamp``). Everything else in the response — buyer ids, names,
addresses, messages, coupons, variations — is never read and never stored
(CLAUDE.md rule 2); the caller discards the raw response in the same job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


@dataclass
class DayTotal:
    units: int = 0
    orders: int = 0
    revenue_minor: int = 0
    currency: str | None = None


def _minor(price: Any, quantity: int) -> tuple[int, str | None]:
    """Line revenue in minor units (cents), and the currency."""
    if not isinstance(price, dict) or not price.get("divisor"):
        return 0, None
    per_unit = Decimal(int(price.get("amount") or 0)) / Decimal(int(price["divisor"]))
    minor = (per_unit * quantity * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(minor), price.get("currency_code")


def sale_day(transaction: dict[str, Any]) -> date | None:
    stamp = transaction.get("created_timestamp") or transaction.get("create_timestamp")
    if not stamp:
        return None
    return datetime.fromtimestamp(int(stamp), tz=timezone.utc).date()


def aggregate(transactions: list[dict[str, Any]], since: date) -> dict[tuple[int, date], DayTotal]:
    """Daily totals per listing for sales on or after ``since``."""
    totals: dict[tuple[int, date], DayTotal] = {}
    for t in transactions:
        listing_id = t.get("listing_id")
        day = sale_day(t)
        if listing_id is None or day is None or day < since:
            continue
        quantity = int(t.get("quantity") or 0)
        revenue, currency = _minor(t.get("price"), quantity)
        total = totals.setdefault((int(listing_id), day), DayTotal())
        total.units += quantity
        total.orders += 1
        total.revenue_minor += revenue
        total.currency = total.currency or currency
    return totals
