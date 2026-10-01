"""The shop's payment account ledger, totalled per day and entry type (v7 §C).

Each ledger entry is read for four things only: ``ledger_type``, ``amount``,
``currency`` and the date (``created_timestamp``). The totals are ours; the
entries are discarded in the same job.

Etsy's API spec lists no ``ledger_type`` values, so nothing here assumes the
list is complete. Types are put in a category only when their meaning is
known; every other type is still totalled and shown to the seller under its
own name, but not counted as a cost, because some ledger debits are not costs
at all (a payout to the seller's bank, for one).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

# Category of each known ledger type (lower-cased). "ads" is the one the product
# brief names outright: prolist is Etsy Ads, offsite_ads_fee is Offsite Ads.
CATEGORIES: dict[str, str] = {
    "prolist": "ads",
    "offsite_ads_fee": "ads",
    "listing": "listing_fees",
    "renew_sold": "listing_fees",
    "renew_sold_auto": "listing_fees",
    "renew_expired": "listing_fees",
    "renew_manual": "listing_fees",
    "transaction": "transaction_fees",
    "transaction_quantity": "transaction_fees",
    "shipping_transaction": "transaction_fees",
    "payment_processing_fee": "processing_fees",
    "shipping_labels": "shipping_labels",
    "postage": "shipping_labels",
}

#: Seller-facing names, in the order a statement lists them.
CATEGORY_LABELS: dict[str, str] = {
    "listing_fees": "Listing fees",
    "transaction_fees": "Transaction fees",
    "processing_fees": "Payment processing",
    "ads": "Ads (Etsy Ads and Offsite Ads)",
    "shipping_labels": "Shipping labels",
}

#: Categories whose meaning comes straight from the product brief; the rest are
#: Etsy's usual names for its fees, applied only when that type actually appears.
CONFIRMED = frozenset({"ads"})


def category(ledger_type: str) -> str | None:
    return CATEGORIES.get((ledger_type or "").strip().lower())


@dataclass
class TypeDay:
    amount_minor: int = 0
    entries: int = 0
    currency: str | None = None


def entry_day(entry: dict[str, Any]) -> date | None:
    stamp = entry.get("created_timestamp") or entry.get("create_date")
    if not stamp:
        return None
    return datetime.fromtimestamp(int(stamp), tz=timezone.utc).date()


def aggregate(entries: list[dict[str, Any]]) -> dict[tuple[date, str], TypeDay]:
    """Totals per (day, ledger type) from a page of ledger entries."""
    out: dict[tuple[date, str], TypeDay] = {}
    for e in entries:
        day = entry_day(e)
        kind = str(e.get("ledger_type") or "unknown")
        if day is None:
            continue
        t = out.setdefault((day, kind), TypeDay())
        t.amount_minor += int(e.get("amount") or 0)
        t.entries += 1
        t.currency = t.currency or e.get("currency")
    return out


def cost_of(amount_minor: int) -> int:
    """A fee is a debit (negative on the ledger); as a cost it is positive.
    A credit on a fee type (a fee refunded) lowers the cost."""
    return -amount_minor
