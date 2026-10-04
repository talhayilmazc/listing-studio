"""Turning the seller's own sales into daily totals per listing (v7 §C1).

This is the only code that reads Etsy's transaction objects. It reads, from
each: ``listing_id``, ``quantity``, ``price`` and the date
(``created_timestamp``) for the totals; the ``transaction_id`` as a position
marker, so a resumed read never counts a sale twice; and, for attribution
(:func:`lines`), the order's number (``receipt_id``) and the shipping the buyer
paid for the line (``shipping_cost``). Everything else in the response — buyer
ids, names, addresses, messages, coupons, variations — is never read and never
stored (CLAUDE.md rule 2); the caller discards the raw response in the same
job. Receipts themselves (getShopReceipts) are not read at all: they carry the
buyer's name and address.
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


def _money(value: Any) -> int:
    """An Etsy Money object in minor units; anything else is 0."""
    if not isinstance(value, dict) or not value.get("divisor"):
        return 0
    amount = Decimal(int(value.get("amount") or 0)) / Decimal(int(value["divisor"]))
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass
class Line:
    """One line of one order: what attribution needs, and nothing about the buyer."""

    transaction_id: int
    receipt_id: int
    listing_id: int
    day: date
    quantity: int
    price_minor: int  # unit price x quantity
    shipping_minor: int  # the shipping the buyer paid for this line
    currency: str | None


def lines(transactions: list[dict[str, Any]], since: date) -> list[Line]:
    """The order lines among these sales, on or after ``since``. A sale with no
    order number or no listing cannot be attributed and is left out."""
    out: list[Line] = []
    for t in transactions:
        listing_id, receipt_id, transaction_id = t.get("listing_id"), t.get("receipt_id"), t.get("transaction_id")
        day = sale_day(t)
        if listing_id is None or receipt_id is None or transaction_id is None or day is None or day < since:
            continue
        quantity = int(t.get("quantity") or 0)
        price, currency = _minor(t.get("price"), quantity)
        out.append(Line(
            transaction_id=int(transaction_id), receipt_id=int(receipt_id), listing_id=int(listing_id), day=day,
            quantity=quantity, price_minor=price, shipping_minor=_money(t.get("shipping_cost")), currency=currency,
        ))
    return out


def sale_key(transaction: dict[str, Any]) -> tuple[int, int]:
    """Where a sale sits in Etsy's order: (created time, transaction id)."""
    stamp = transaction.get("created_timestamp") or transaction.get("create_timestamp") or 0
    return int(stamp), int(transaction.get("transaction_id") or 0)


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
