"""One month of a shop's money, as Analytics shows it (the rebuilt screens).

Pure functions over what the app holds for the month. Money is in minor units,
signed the way it moves the result: income positive, costs negative.

**Where a month's totals come from**: the imported statement, else Etsy's
ledger as read through the API, else nothing. Per listing: the statement's
order rows joined to the sales read by order number (never by title); what
belongs to an order as a whole is split by each item's share of the price, to
the cent (pipeline/attribution.py). Etsy Ads is a shop-level cost and is never
given to a listing, not even in proportion: listing results are *before ads*.
What cannot be tied to a listing is reported as such, with its amount.

**Every figure says how it was arrived at** (``basis``):

* ``exact``       Etsy's own figure, taken as it is (statement, ledger, sales).
* ``calculated``  worked out by us from exact figures (a split, a difference,
                  the seller's own cost times units sold).
* ``estimated``   rests on an assumption, because the exact figure isn't there.

A figure with nothing behind it is ``None`` with the reason, never zero.
Product cost is only ever what the seller entered: until it is, the result is
"profit before product cost" and the month is marked incomplete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.pipeline import attribution
from app.pipeline.ledger import CATEGORIES as LEDGER_CATEGORIES

EXACT, CALCULATED, ESTIMATED = "exact", "calculated", "estimated"

#: Statement categories (pipeline/statement.py) behind each line of the receipt.
LINES: dict[str, tuple[str, ...]] = {
    "refunds": ("refunds", "sales_tax_refund"),
    "transaction_fee_items": ("transaction_fee_items",),
    "transaction_fee_shipping": ("transaction_fee_shipping",),
    "processing_fee": ("processing_fee",),
    "listing_fee": ("listing_fee",),
    "fee_credits": ("fee_credits",),
    "other_fees": ("other_fees",),
    "fee_taxes": ("transaction_tax", "transaction_tax_credit", "other_tax"),
    "etsy_ads": ("etsy_ads",),
    "offsite_ads": ("offsite_ads", "offsite_ads_credit"),
    "other_marketing": ("other_marketing",),
    "shipping_labels": ("shipping",),
    "unrecognised": ("unrecognised",),
}
REVENUE = ("sales", "sales_tax", "buyer_fees")

#: The receipt, top to bottom: (section, its lines). Section totals are "<section>_total".
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("revenue", ("items", "shipping_paid", "refunds")),
    ("etsy_fees", ("transaction_fee_items", "transaction_fee_shipping", "processing_fee", "listing_fee",
                   "fee_credits", "other_fees", "fee_taxes")),
    ("marketing", ("etsy_ads", "offsite_ads", "other_marketing")),
    ("other", ("shipping_labels", "unrecognised")),
    ("your_costs", ("product_cost", "provider_shipping")),
)

#: How a statement order's categories are grouped on a listing's row.
ROW_GROUPS: dict[str, tuple[str, ...]] = {
    "revenue": REVENUE,
    "refunds": LINES["refunds"],
    "fees": ("transaction_fee_items", "transaction_fee_shipping", "processing_fee", "fee_credits",
             "transaction_tax", "transaction_tax_credit", "other_fees", "other_tax", "listing_fee"),
    "offsite_ads": LINES["offsite_ads"],
    "labels": ("shipping",),
    "other": ("unrecognised", "other_marketing"),
}
_GROUP_OF = {category: group for group, categories in ROW_GROUPS.items() for category in categories}

# --- product cost: only what the seller entered ----------------------------------------


def _money(value: Any) -> int | None:
    """An entered amount in minor units; None when nothing (valid) was entered."""
    if value is None or str(value).strip() == "":
        return None
    try:
        d = Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation:
        return None
    if not d.is_finite() or d < 0 or d > 1_000_000:
        return None
    return int((d * 100).to_integral_value())


def size_key(size: str | None) -> str:
    return " ".join((size or "").split()).casefold()


@dataclass(frozen=True)
class UnitCost:
    """What one item costs the seller: to make, and what the provider charges to ship it."""

    production: int | None = None
    shipping: int | None = None

    @property
    def complete(self) -> bool:
        return self.production is not None and self.shipping is not None


@dataclass
class ProductCosts:
    """Per profile, with optional per-size overrides (``tenant.cost_settings``)."""

    profiles: dict[str, UnitCost] = field(default_factory=dict)
    sizes: dict[str, dict[str, UnitCost]] = field(default_factory=dict)

    @classmethod
    def from_stored(cls, stored: dict[str, Any] | None) -> "ProductCosts":
        stored = stored or {}
        out = cls()
        # What was entered per profile before there were two parts: the production cost.
        for pid, value in (stored.get("product_cost_by_profile") or {}).items():
            if (minor := _money(value)) is not None:
                out.profiles[str(pid)] = UnitCost(production=minor)
        for pid, entry in (stored.get("profile_costs") or {}).items():
            if not isinstance(entry, dict):
                continue
            out.profiles[str(pid)] = UnitCost(_money(entry.get("production")), _money(entry.get("shipping")))
            for size, override in (entry.get("sizes") or {}).items():
                if isinstance(override, dict) and size_key(size):
                    out.sizes.setdefault(str(pid), {})[size_key(size)] = UnitCost(
                        _money(override.get("production")), _money(override.get("shipping")))
        return out

    def unit(self, profile_id: str | None, size: str | None) -> UnitCost:
        """The profile's cost, each part replaced by the size's own when one is set."""
        if not profile_id or profile_id not in self.profiles:
            return UnitCost()
        base = self.profiles[profile_id]
        override = self.sizes.get(profile_id, {}).get(size_key(size)) if size else None
        if override is None:
            return base
        return UnitCost(
            override.production if override.production is not None else base.production,
            override.shipping if override.shipping is not None else base.shipping,
        )


def validate_profile_costs(body: dict[str, Any]) -> dict[str, Any]:
    """``{profile id: {production, shipping, sizes: {size: {production, shipping}}}}`` to
    store, as strings. An emptied field is "not set", never zero."""

    def amount(value: Any, name: str) -> str | None:
        if value is None or str(value).strip() == "":
            return None
        if _money(value) is None:
            raise ValueError(f"{name}: enter an amount of zero or more")
        return str(Decimal(str(value).strip().replace(",", ".")))

    if len(body) > 2000:
        raise ValueError("too many profiles")
    out: dict[str, Any] = {}
    for pid, entry in body.items():
        if not isinstance(entry, dict):
            raise ValueError("each profile needs its costs")
        sizes: dict[str, Any] = {}
        raw_sizes = entry.get("sizes") or {}
        if not isinstance(raw_sizes, dict) or len(raw_sizes) > 60:
            raise ValueError("sizes: at most 60 per profile")
        for size, override in raw_sizes.items():
            name = " ".join(str(size).split())
            if not name or len(name) > 40 or not isinstance(override, dict):
                raise ValueError("sizes: each needs a name of 1-40 characters")
            one = {"production": amount(override.get("production"), f"{name} production cost"),
                   "shipping": amount(override.get("shipping"), f"{name} shipping cost")}
            if one["production"] is not None or one["shipping"] is not None:
                sizes[name] = one
        one = {"production": amount(entry.get("production"), "production cost"),
               "shipping": amount(entry.get("shipping"), "shipping cost"), "sizes": sizes}
        if one["production"] is not None or one["shipping"] is not None or sizes:
            out[str(pid)] = one
    return out


# --- the month's inputs ---------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """One sold line from the sales read: ids, amounts, the size. Nothing about the buyer."""

    receipt_id: int
    listing_id: int
    day: date
    quantity: int
    price_minor: int
    shipping_minor: int
    size: str | None = None


@dataclass
class MonthInput:
    month: date  # the 1st
    #: The imported statement's totals per category, or None.
    statement: dict[str, int] | None = None
    #: Statement amounts per order number.
    orders: dict[int, dict[str, int]] = field(default_factory=dict)
    #: Listing fees (and their credits) the statement ties to a listing id.
    listing_fees: dict[int, int] = field(default_factory=dict)
    deposits_minor: int = 0
    #: Etsy's ledger for the month, per ledger type, or None; and what to say about its reach.
    ledger: dict[str, int] | None = None
    ledger_note: str | None = None
    #: Sold lines: for a statement month, those of its orders; else those dated in the month.
    lines: list[Line] = field(default_factory=list)
    #: Why there are no lines, when there should be.
    sales_note: str | None = None
    #: The Ads report ("ad spend for clicks this month"): spend, revenue, days covered.
    ads_spend: int | None = None
    ads_revenue: int | None = None
    ads_days: int = 0
    costs: ProductCosts = field(default_factory=ProductCosts)
    profile_of: dict[int, str] = field(default_factory=dict)

    @property
    def last_day(self) -> date:
        return (self.month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


@dataclass
class Figure:
    key: str
    minor: int | None
    basis: str | None = None
    #: "statement" | "ledger" | "sales" | "costs" | "ads_report"
    source: str | None = None
    note: str | None = None
    parts: dict[str, int] | None = None

    def out(self) -> dict[str, Any]:
        return {"key": self.key, "minor": self.minor, "basis": self.basis if self.minor is not None else None,
                "source": self.source, "note": self.note, "parts": self.parts}


def _name(month: date) -> str:
    return f"{month:%B} {month.year}"


# --- per listing --------------------------------------------------------------------------------


@dataclass
class ListingRow:
    listing_id: int
    units: int = 0
    orders: int = 0
    revenue: int = 0  # items + shipping, after sales tax
    shipping_paid: int = 0
    refunds: int = 0
    fees: int = 0  # Etsy's fees on the listing's orders, and its listing fees
    offsite_ads: int = 0
    labels: int = 0
    other: int = 0
    product_cost: int = 0
    provider_shipping: int = 0
    #: Units with no cost entered, and how many were costed at the profile's base
    #: cost because the sale's size isn't known.
    uncosted_units: int = 0
    size_unknown_units: int = 0
    #: Etsy's fees per order are known (a statement month).
    fees_known: bool = True

    @property
    def items(self) -> int:
        return self.revenue - self.shipping_paid

    @property
    def costed(self) -> bool:
        return self.uncosted_units == 0

    @property
    def before_cost(self) -> int | None:
        """What the listing's sales left after Etsy's own charges, before Etsy Ads and product cost."""
        if not self.fees_known:
            return None
        return self.revenue + self.refunds + self.fees + self.offsite_ads + self.labels + self.other

    @property
    def result(self) -> int | None:
        """Profit before ads: after Etsy's charges and the seller's product cost."""
        if self.before_cost is None or not self.costed:
            return None
        return self.before_cost - self.product_cost - self.provider_shipping

    @property
    def best(self) -> int | None:
        """The furthest the figures go: profit before ads, else before product cost."""
        return self.result if self.result is not None else self.before_cost


def _cost_lines(row: ListingRow, lines: list[Line], data: MonthInput) -> None:
    profile = data.profile_of.get(row.listing_id)
    for line in lines:
        unit = data.costs.unit(profile, line.size)
        if not unit.complete:
            row.uncosted_units += line.quantity
            continue
        row.product_cost += (unit.production or 0) * line.quantity
        row.provider_shipping += (unit.shipping or 0) * line.quantity
        if not line.size and profile in data.costs.sizes:
            row.size_unknown_units += line.quantity


def listing_rows(data: MonthInput) -> tuple[dict[int, ListingRow], dict[str, Any]]:
    """Each listing's month, and what could not be given to a listing.

    A statement month: every order's amounts go to its listings by order number.
    Otherwise: the lines dated in the month (revenue only; Etsy's fees per
    listing need the statement).
    """
    rows: dict[int, ListingRow] = {}
    by_receipt: dict[int, list[Line]] = {}
    for line in data.lines:
        by_receipt.setdefault(line.receipt_id, []).append(line)

    def row(listing_id: int) -> ListingRow:
        return rows.setdefault(listing_id, ListingRow(listing_id))

    if data.statement is None:
        for receipt_id, lines in by_receipt.items():
            shipping = attribution.split(sum(x.shipping_minor for x in lines), [max(0, x.price_minor) for x in lines])
            seen: set[int] = set()
            for line, share in zip(lines, shipping, strict=True):
                r = row(line.listing_id)
                r.fees_known = False
                r.units += line.quantity
                r.revenue += line.price_minor + share
                r.shipping_paid += share
                if line.listing_id not in seen:
                    r.orders += 1
                    seen.add(line.listing_id)
        for r in rows.values():
            _cost_lines(r, [x for x in data.lines if x.listing_id == r.listing_id], data)
        return rows, {"groups": {}, "orders": 0, "orders_minor": 0, "total": None}

    given: dict[str, int] = {g: 0 for g in ROW_GROUPS}
    unmatched = unmatched_minor = 0
    for receipt_id, amounts in data.orders.items():
        lines = by_receipt.get(receipt_id)
        sold_here = bool(amounts.get("sales"))
        if not lines:
            if sold_here:
                unmatched += 1
                unmatched_minor += attribution.order_revenue(amounts)
            continue
        weights = [max(0, x.price_minor) for x in lines]
        for listing_id, parts in attribution.attribute({k: int(v or 0) for k, v in amounts.items()}, lines).items():
            r = row(listing_id)
            for category, part in parts.items():
                group = _GROUP_OF.get(category)
                if group is None:
                    continue  # not an order-level amount we group (Etsy Ads is never on an order)
                setattr(r, group, getattr(r, group) + part)
                given[group] += part
        if sold_here:
            # Shipping the buyer paid: the order's own, by each item's share of the price.
            shares = attribution.split(sum(x.shipping_minor for x in lines), weights)
            seen = set()
            for line, share in zip(lines, shares, strict=True):
                r = row(line.listing_id)
                r.units += line.quantity
                r.shipping_paid += share
                if line.listing_id not in seen:
                    r.orders += 1
                    seen.add(line.listing_id)
            for listing_id in seen:
                _cost_lines(row(listing_id), [x for x in lines if x.listing_id == listing_id], data)
    for listing_id, amount in data.listing_fees.items():
        row(listing_id).fees += amount
        given["fees"] += amount

    totals = {g: sum(int(data.statement.get(c) or 0) for c in categories) for g, categories in ROW_GROUPS.items()}
    left = {g: totals[g] - given[g] for g in ROW_GROUPS if totals[g] - given[g]}
    return rows, {"groups": left, "orders": unmatched, "orders_minor": unmatched_minor, "total": sum(left.values())}


# --- the receipt ----------------------------------------------------------------------------------


def receipt(data: MonthInput, rows: dict[int, ListingRow] | None = None) -> dict[str, Any]:
    """The month laid out line by line. ``source`` says where its totals come from."""
    if rows is None:
        rows, _ = listing_rows(data)
    name = _name(data.month)
    f: dict[str, Figure] = {}
    units = sum(r.units for r in rows.values())
    uncosted = sum(r.uncosted_units for r in rows.values())
    size_unknown = sum(r.size_unknown_units for r in rows.values())
    shipping = sum(r.shipping_paid for r in rows.values())

    if data.statement is not None:
        source = "statement"
        s = data.statement
        revenue = sum(int(s.get(c) or 0) for c in REVENUE)
        have = {x.receipt_id for x in data.lines}
        matched = sum(1 for o, a in data.orders.items() if a.get("sales") and o in have)
        sold = sum(1 for a in data.orders.values() if a.get("sales"))
        nothing_sold = sold == 0
        if data.lines and matched:
            missing = sold - matched
            note = (f"{missing} of {sold} orders are not in the sales read yet: the shipping their buyers paid is inside Items."
                    if missing else None)
            f["shipping_paid"] = Figure("shipping_paid", shipping, EXACT, "sales", note)
            f["items"] = Figure("items", revenue - shipping, CALCULATED, "statement")
        else:
            why = data.sales_note or "The sales read has no order lines for this month's orders yet."
            f["shipping_paid"] = Figure("shipping_paid", None, note=why + " Until it does, shipping paid by buyers is inside Items.")
            f["items"] = Figure("items", revenue, EXACT, "statement", "Includes the shipping buyers paid.")
        for key, categories in LINES.items():
            f[key] = Figure(key, sum(int(s.get(c) or 0) for c in categories), EXACT, "statement")
        f["refunds"].parts = {"refunded": int(s.get("refunds") or 0), "tax_returned": int(s.get("sales_tax_refund") or 0)}
        net = Figure("net_etsy", sum(int(v or 0) for v in s.values()), EXACT, "statement")
    elif data.ledger is not None or data.lines:
        source = "ledger" if data.ledger is not None else "sales"
        nothing_sold = False
        by_category: dict[str, int] = {}
        for kind, amount in (data.ledger or {}).items():
            by_category[kind] = by_category.get(kind, 0) + int(amount or 0)
        need = "Only on the statement: import it for this month."
        if data.lines:
            f["items"] = Figure("items", sum(r.items for r in rows.values()), CALCULATED, "sales",
                                "Listed prices times quantity, before any discount. The statement has the exact figure.")
            f["shipping_paid"] = Figure("shipping_paid", shipping, EXACT, "sales")
        else:
            why = data.sales_note or "The shop's sales have not been read for this month."
            f["items"], f["shipping_paid"] = Figure("items", None, note=why), Figure("shipping_paid", None, note=why)
        for key in LINES:
            f[key] = Figure(key, None, note=need)
        if data.ledger is not None:
            def led(*kinds: str) -> int:
                return sum(by_category.get(k, 0) for k in kinds)

            reach = data.ledger_note
            f["transaction_fee_items"] = Figure("transaction_fee_items", led("transaction", "transaction_quantity"), EXACT, "ledger", reach)
            f["transaction_fee_shipping"] = Figure("transaction_fee_shipping", led("shipping_transaction"), EXACT, "ledger", reach)
            f["processing_fee"] = Figure("processing_fee", led("payment_processing_fee"), EXACT, "ledger", reach)
            f["listing_fee"] = Figure("listing_fee", led(*[k for k, c in LEDGER_CATEGORIES.items() if c == "listing_fees"]), EXACT, "ledger", reach)
            f["etsy_ads"] = Figure("etsy_ads", led("prolist"), EXACT, "ledger", reach)
            f["offsite_ads"] = Figure("offsite_ads", led("offsite_ads_fee"), EXACT, "ledger", reach)
            f["shipping_labels"] = Figure("shipping_labels", led("shipping_labels", "postage"), EXACT, "ledger", reach)
        known = [x.minor for x in f.values() if x.minor is not None]
        net = Figure("net_etsy", sum(known) if f["items"].minor is not None and data.ledger is not None else None, CALCULATED, source,
                     f"No statement for {name}: refunds, fee credits and any charge Etsy's ledger names differently are not in this figure. "
                     "Import the statement for the exact one."
                     if f["items"].minor is not None and data.ledger is not None
                     else f"Needs both the sales and Etsy's ledger for {name}, or the month's statement.")
    else:
        source = "none"
        nothing_sold = False
        why = f"Nothing has been read or imported for {name}."
        for key in ("items", "shipping_paid", *LINES):
            f[key] = Figure(key, None, note=why)
        net = Figure("net_etsy", None, note=why)

    # The seller's own costs: only what was entered.
    if nothing_sold:
        f["product_cost"] = Figure("product_cost", 0, CALCULATED, "costs", "Nothing was sold this month.")
        f["provider_shipping"] = Figure("provider_shipping", 0, CALCULATED, "costs", "Nothing was sold this month.")
    elif not rows or units == 0:
        why = f["shipping_paid"].note or "No sales are tied to listings for this month."
        f["product_cost"], f["provider_shipping"] = Figure("product_cost", None, note=why), Figure("provider_shipping", None, note=why)
    else:
        costed = units - uncosted
        note = None
        if uncosted:
            note = (f"Set for {costed:,} of {units:,} items sold. The other {uncosted:,} have no product cost entered."
                    if costed else "No product cost is entered yet.")
        elif size_unknown:
            note = f"{size_unknown:,} items were costed at their profile's base cost: the size sold isn't known yet."
        has = costed > 0
        f["product_cost"] = Figure("product_cost", -sum(r.product_cost for r in rows.values()) if has else None,
                                   CALCULATED, "costs", note)
        f["provider_shipping"] = Figure("provider_shipping", -sum(r.provider_shipping for r in rows.values()) if has else None,
                                        CALCULATED, "costs", note)

    complete = nothing_sold or (bool(rows) and units > 0 and uncosted == 0)
    if net.minor is not None and complete:
        profit = Figure("profit", net.minor + (f["product_cost"].minor or 0) + (f["provider_shipping"].minor or 0),
                        CALCULATED, source, net.note)
    else:
        profit = Figure("profit", None, note="Needs a product cost for every item sold." if net.minor is not None else net.note)

    def total(section: str, keys: tuple[str, ...]) -> Figure:
        have = [f[k] for k in keys if f[k].minor is not None]
        if not have:
            return Figure(f"{section}_total", None, note=f[keys[0]].note)
        bases = {x.basis for x in have}
        basis = ESTIMATED if ESTIMATED in bases else CALCULATED if (CALCULATED in bases or len(have) < len(keys)) else EXACT
        if source == "statement" and section != "your_costs":
            basis = EXACT  # the parts are the statement's own rows
        return Figure(f"{section}_total", sum(x.minor or 0 for x in have), basis, have[0].source)

    sections = []
    for section, keys in SECTIONS:
        sections.append({"key": section, "lines": [f[k].out() for k in keys], "total": total(section, keys).out()})

    # Break-even ROAS: what each unit of ad spend must bring in sales for Etsy Ads
    # to stop losing money, after Etsy's fees and (once entered) product cost.
    revenue_total = (f["items"].minor or 0) + (f["shipping_paid"].minor or 0) if f["items"].minor is not None else None
    ads = f["etsy_ads"].minor
    break_even: dict[str, Any] = {"roas": None, "margin": None, "basis": None, "note": None, "complete": complete,
                                  "actual_roas": None, "actual_note": None}
    if net.minor is None or not revenue_total or revenue_total <= 0:
        break_even["note"] = net.note or "Needs the month's revenue and Etsy's fees."
    else:
        left = net.minor - (ads or 0)  # ads are negative: this is the result before Etsy Ads
        if complete:
            left += (f["product_cost"].minor or 0) + (f["provider_shipping"].minor or 0)
        margin = left / revenue_total
        break_even["margin"] = round(margin, 4)
        if margin <= 0:
            break_even["note"] = "Sales don't cover their own costs even without ads, so no amount of ad sales breaks even."
        else:
            break_even["roas"] = round(1 / margin, 2)
            break_even["basis"] = CALCULATED if complete else ESTIMATED
            if not complete:
                break_even["note"] = "Before product cost: the real break-even is higher. Enter product costs to get it."
    if data.ads_spend and data.ads_revenue is not None and data.ads_days:
        break_even["actual_roas"] = round(data.ads_revenue / data.ads_spend, 2)
        full = data.ads_days >= data.last_day.day
        break_even["actual_note"] = None if full else f"From the {data.ads_days} days of {name} the Ads report covers."
    else:
        break_even["actual_note"] = f"Import the Etsy Ads report for {name} to see your actual ROAS."

    incomplete: list[dict[str, str]] = []
    if source != "statement":
        incomplete.append({"key": "no_statement", "text": f"No statement is imported for {name}, so its figures are not Etsy's final ones."
                           if source != "none" else f"Nothing has been read or imported for {name}."})
    if source != "none" and not complete:
        incomplete.append({"key": "product_cost", "text": (f["product_cost"].note or "Product cost is not entered.")
                           + " Until it is, the result is profit before product cost."})
    if source == "statement" and f["shipping_paid"].minor is None:
        incomplete.append({"key": "sales_lines", "text": f["shipping_paid"].note or ""})

    return {
        "month": data.month,
        "source": source,
        "sections": sections,
        "net_etsy": net.out(),
        "profit": profit.out(),
        "revenue": Figure("revenue", revenue_total, EXACT if source == "statement" else CALCULATED, source).out()
        if revenue_total is not None else Figure("revenue", None, note=f["items"].note).out(),
        "deposits_minor": data.deposits_minor if source == "statement" else None,
        "ads_report": {"spend_minor": -data.ads_spend if data.ads_spend is not None else None,
                       "revenue_minor": data.ads_revenue, "days": data.ads_days, "month_days": data.last_day.day},
        "break_even": break_even,
        "units": units if rows else None,
        "complete": complete and source == "statement",
        "incomplete": incomplete,
    }


# --- classes, with their reasons ------------------------------------------------------------------

NEW, WINNER, STEADY, FADING, LOSING = "new", "winner", "steady", "fading", "losing"
#: Days before the end of the month within which a listing is too new to judge.
NEW_DAYS = 45
FADING_MIN_BEFORE = 3
WINNER_MIN_UNITS = 3
WINNER_SHARE = 0.2


def winners(rows: dict[int, ListingRow]) -> set[int]:
    """The top fifth by result, among listings that sold at least three and earned something."""
    ranked = sorted((r for r in rows.values() if r.units >= WINNER_MIN_UNITS and (r.best or 0) > 0),
                    key=lambda r: -(r.best or 0))
    return {r.listing_id for r in ranked[: max(1, round(len(ranked) * WINNER_SHARE))]} if ranked else set()


def classify(row: ListingRow, before: int, launched: date | None, month_end: date, top: set[int], money: Any) -> tuple[str, str]:
    """The listing's class for the month and, in a sentence, why."""
    what = "after Etsy's fees and product cost" if row.result is not None else "after Etsy's fees, before product cost"
    if launched is not None and (month_end - launched).days < NEW_DAYS:
        age = max(0, (month_end - launched).days)
        return NEW, f"Listed {age} days before the month ended: too early to judge."
    best = row.best
    if best is not None and best < 0:
        return LOSING, f"Lost {money(-best)} on {row.units} sold, {what}."
    if before >= FADING_MIN_BEFORE and row.units * 2 <= before:
        return FADING, f"Sold {row.units} this month, {before} the month before."
    if row.listing_id in top and best is not None:
        return WINNER, f"In the top fifth: {money(best)} from {row.units} sold, {what}."
    if best is None:
        return STEADY, f"Sold {row.units}; {before} the month before. Its fees per order need the month's statement."
    return STEADY, f"Sold {row.units}; {before} the month before. Left {money(best)}, {what}."


# --- what needs attention ---------------------------------------------------------------------------


def attention(data: MonthInput, sheet: dict[str, Any], rows: dict[int, ListingRow], classes: dict[int, tuple[str, str]],
              before: dict[int, int], unattributed: dict[str, Any], money: Any, limit: int = 5) -> list[dict[str, Any]]:
    """The few things worth acting on, by the money at stake. Suggestions point
    to Shop Manager; nothing on Etsy is changed from here."""
    items: list[dict[str, Any]] = []

    def add(kind: str, stake: int | None, title: str, why: str, do: str, basis: str | None, listing_id: int | None = None) -> None:
        items.append({"kind": kind, "stake_minor": stake, "basis": basis, "title": title, "why": why, "do": do, "listing_id": listing_id})

    for lid, (cls, reason) in classes.items():
        r = rows[lid]
        if cls == LOSING and r.best is not None:
            add("losing", -r.best, "Selling at a loss", reason,
                "Raise its price or lower its cost in Shop Manager; until then every sale costs you money.",
                CALCULATED, lid)
        elif cls == FADING:
            per = (r.best / r.units) if r.units and r.best is not None else None
            lost = round((before.get(lid, 0) - r.units) * per) if per and per > 0 else None
            add("fading", lost, "Sales fell by half or more", reason,
                "Check its photos, price and whether it is still in stock and listed.",
                ESTIMATED if lost is not None else None, lid)
        if r.units and r.revenue > 0 and -r.refunds * 5 >= r.revenue and -r.refunds >= 1000:
            add("refunds", -r.refunds, "Refunds are a fifth or more of its sales",
                f"{money(-r.refunds)} refunded against {money(r.revenue)} sold.",
                "Look at why buyers returned it: sizing, print quality or the photos setting the wrong expectation.",
                EXACT if sheet["source"] == "statement" else CALCULATED, lid)

    uncosted_revenue = sum(r.revenue for r in rows.values() if r.uncosted_units)
    uncosted = sum(r.uncosted_units for r in rows.values())
    if uncosted:
        add("product_cost", uncosted_revenue, "Product cost is missing",
            f"{uncosted:,} items sold ({money(uncosted_revenue)} of sales) have no product cost, so the result is before product cost.",
            "Enter what each profile costs to make and ship under Product costs.", CALCULATED)

    be = sheet["break_even"]
    if be["roas"] and be["actual_roas"] is not None and data.ads_spend and data.ads_revenue is not None and be["actual_roas"] < be["roas"]:
        earned = round(data.ads_revenue * (be["margin"] or 0))
        add("ads", data.ads_spend - earned, "Etsy Ads cost more than their sales earned",
            f"Your ads brought {be['actual_roas']:.2f} in sales per 1 spent; they break even at {be['roas']:.2f}.",
            "Lower the daily budget or remove the weakest listings from the campaign in Shop Manager > Marketing > Etsy Ads.",
            be["basis"])

    if unattributed.get("orders"):
        add("unattributed", unattributed["orders_minor"], "Orders not tied to a listing",
            f"{unattributed['orders']:,} orders ({money(unattributed['orders_minor'])}) on the statement are not in the sales read yet.",
            "Nothing to do: they are tied to their listings once the shop's sales are read again.", EXACT)

    if sheet["source"] != "statement":
        add("statement", None, f"Import the statement for {_name(data.month)}",
            "Without it there are no refunds, no fee credits and no fees per listing for the month.",
            "Download it in Shop Manager > Finances > Monthly statements and import it here.", None)

    items.sort(key=lambda i: -(i["stake_minor"] if i["stake_minor"] is not None else -1))
    return items[:limit]
