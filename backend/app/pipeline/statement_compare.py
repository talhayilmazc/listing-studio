"""An imported statement beside the app's own calculation, line by line.

The statement is the authority for a month. What the app knew before it was
imported comes from two reads of Etsy's API: the payment ledger (fees and ad
charges, totalled per day and type) and the sales (order lines). This puts the
two side by side and says, for every line that differs, why, or that the reason
is not known. A figure the app does not have is "no data", never zero.

Each line's ``status``:
  * ``match``: the two agree to the cent
  * ``explained``: they differ, and the whole difference has a stated cause
  * ``unexplained``: they differ and the cause is not established; a likely one
    is named as such
  * ``statement_only``: the app has no figure of its own for this line
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.pipeline import ledger as ledger_rules
from app.pipeline.attribution import OrderJoin

EDGE = (
    "Likely cause: the ledger dates each entry in UTC and the statement in the shop's own time, so entries "
    "in the first and last hours of the month can fall in the other month. Not established."
)


@dataclass
class Line:
    key: str
    label: str
    statement_minor: int | None
    ours_minor: int | None
    ours_source: str | None
    status: str
    reason: str

    @property
    def difference_minor(self) -> int | None:
        if self.statement_minor is None or self.ours_minor is None:
            return None
        return self.statement_minor - self.ours_minor


def _ledger_by_category(ledger: dict[str, int]) -> dict[str, int]:
    """Ledger types summed into the app's categories (signed as Etsy signs them)."""
    out: dict[str, int] = {}
    for kind, amount in ledger.items():
        category = ledger_rules.category(kind)
        if category is not None:
            out[category] = out.get(category, 0) + int(amount)
    return out


def compare(
    totals: dict[str, int],
    credits: dict[str, int],
    *,
    ledger: dict[str, int] | None,
    ledger_note: str | None,
    join: OrderJoin | None,
    sales_note: str | None,
) -> list[Line]:
    """``totals``/``credits``: the statement's, in minor units.
    ``ledger``: ledger type → signed minor units for the statement's days, or
    None when the ledger has nothing for them (``ledger_note`` says why).
    ``join``: the statement's orders against the sales read, or None when no
    order lines are stored (``sales_note`` says why)."""
    t = lambda *names: sum(int(totals.get(n) or 0) for n in names)  # noqa: E731
    c = lambda name: int(credits.get(name) or 0)  # noqa: E731
    lines: list[Line] = []

    # --- revenue: the statement's orders against the sales read ---------------------
    revenue = t("sales", "sales_tax", "buyer_fees")
    if join is None or join.matched == 0:
        lines.append(Line("revenue", "Revenue (sales less sales tax and buyer-paid state fees)", revenue, None, None,
                          "statement_only", sales_note or "The sales read has no orders for this month yet."))
    else:
        ours = join.items_minor + join.shipping_minor
        parts = []
        if join.unmatched:
            parts.append(f"{join.unmatched} of {join.orders} orders ({_money(join.unmatched_minor)}) are not in the sales read")
        if join.differing:
            parts.append(
                f"{len(join.differing)} orders differ by {_money(join.difference_minor)} in all between what the buyer paid "
                "and items plus shipping (discounts are the usual reason; they are not read from Etsy)"
            )
        # Orders the sales read lacks are a known cause; an order whose two
        # amounts differ has only a likely one.
        lines.append(Line(
            "revenue", "Revenue (sales less sales tax and buyer-paid state fees)", revenue, ours,
            "sales read: items plus shipping paid by buyers",
            "match" if revenue == ours else ("unexplained" if join.differing else "explained"),
            "; ".join(parts) + "." if parts else "Every order matches the sales read to the cent.",
        ))

    # --- fees and ads: the statement against the ledger ---------------------------------
    by_category = _ledger_by_category(ledger) if ledger else {}
    pairs = [
        ("listing_fees", "Listing fees (less credits)", t("listing_fee") + c("listing"), "listing_fees"),
        ("transaction_fees", "Transaction fees on items and shipping (less credits)",
         t("transaction_fee_items", "transaction_fee_shipping") + c("transaction_items") + c("transaction_shipping"), "transaction_fees"),
        ("processing_fees", "Processing fees (less credits)", t("processing_fee") + c("processing"), "processing_fees"),
        ("ads", "Etsy Ads and Offsite Ads (less credits)", t("etsy_ads", "offsite_ads", "offsite_ads_credit", "other_marketing"), "ads"),
        ("shipping_labels", "Shipping labels and adjustments", t("shipping"), "shipping_labels"),
    ]
    for key, label, statement, category in pairs:
        if not ledger:
            lines.append(Line(key, label, statement, None, None, "statement_only",
                              ledger_note or "The ledger has not been read for these days."))
            continue
        ours = by_category.get(category, 0)
        if statement == ours:
            lines.append(Line(key, label, statement, ours, "Etsy ledger (API)", "match", "The ledger agrees to the cent."))
        else:
            lines.append(Line(key, label, statement, ours, "Etsy ledger (API)", "unexplained",
                              (ledger_note + " " if ledger_note else "") + EDGE))

    # --- what only the statement has ---------------------------------------------------
    only = [
        ("fee_tax", "Tax on Etsy's fees (less credits)", t("transaction_tax", "transaction_tax_credit", "other_tax"),
         "The app's ledger reading counts only fee types it knows; the tax Etsy adds to its fees is not one of them."),
        ("refunds", "Refunds (less the sales tax returned with them)", t("refunds", "sales_tax_refund"),
         "Refunds are not read from Etsy's API; the statement is the only source."),
        ("other", "Other fees and rows not recognised", t("other_fees", "unrecognised") + c("other"),
         "Rows the statement reader has no rule for; listed with the import."),
    ]
    for key, label, statement, reason in only:
        if statement:
            lines.append(Line(key, label, statement, None, None, "statement_only", reason))
    return lines


def _money(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}{abs(minor) // 100:,}.{abs(minor) % 100:02d}"


def summary(lines: list[Line]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for line in lines:
        counts[line.status] = counts.get(line.status, 0) + 1
    return counts
