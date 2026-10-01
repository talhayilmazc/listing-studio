"""The seller's shop finances, as Analytics shows them (v7 §C, reworked).

Pure functions over what the app holds for one shop: daily sales totals per
listing, the payment ledger's daily totals per entry type, the Etsy Ads
reports the seller uploaded, the listing cache (launch dates, SKUs) and the
seller's own rates and costs. Money is in minor units of the shop's currency.

Every figure says where it comes from (``source``) and, when it is estimated,
partial or unavailable, why (``note``). A figure with nothing behind it is
``None`` (shown blank with its note), never a zero that looks like a fact.

Sources:
  "sales"      the shop's own sales, read from Etsy (units, orders, revenue)
  "ledger"     Etsy's payment account ledger: the fees and ad spend Etsy charged
  "allocated"  a ledger total shared out to listings (by revenue or items sold)
  "rates"      estimated from the fee rates the seller entered
  "costs"      the seller's own product, shipping and fixed costs
  "report"     an Etsy Ads report the seller uploaded (per listing)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable

from app.pipeline import profit
from app.pipeline.ledger import CATEGORY_LABELS, category, cost_of
from app.pipeline.profit import AdRow, CostSettings, DaySales, Window

FEES = ("listing_fees", "transaction_fees", "processing_fees")
YEAR = 364  # same weekdays a year earlier


# --- inputs ------------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerDay:
    day: date
    ledger_type: str
    amount_minor: int
    entries: int


@dataclass
class ListingInfo:
    title: str | None = None
    state: str | None = None
    url: str | None = None
    thumbnail_url: str | None = None
    sku: str | None = None
    profile_id: str | None = None
    profile_name: str | None = None
    launched: date | None = None


@dataclass
class Shop:
    """Everything the app holds for one shop, for one report."""

    sales: dict[int, list[DaySales]]
    ads: dict[int, list[AdRow]]
    ledger: list[LedgerDay]
    info: dict[int, ListingInfo]
    costs: CostSettings
    entered: dict[str, bool]  # which of the seller's costs are set at all
    currency: str | None
    sales_from: date | None  # first day the sales read covers
    ledger_from: date | None  # first day the ledger read covers
    ledger_to: date | None  # last day the ledger covers
    cache_fresh: bool = True


def entered_costs(stored: dict[str, Any] | None) -> dict[str, bool]:
    """Which costs the seller has actually entered (a zero default isn't a cost)."""
    s = stored or {}

    def positive(v: Any) -> bool:
        try:
            return Decimal(str(v)) > 0
        except Exception:  # noqa: BLE001
            return False

    return {
        "product": positive(s.get("product_cost")) or bool(s.get("product_cost_by_profile")) or bool(s.get("product_cost_by_sku")),
        "shipping": positive(s.get("shipping_cost")),
        "fixed": positive(s.get("monthly_fixed")),
        "rates": any(k in s for k in ("listing_fee", "transaction_pct", "payment_pct", "payment_fixed")),
    }


# --- figures --------------------------------------------------------------------------------


@dataclass
class Figure:
    value: int | float | None
    source: str
    note: str | None = None

    def out(self) -> dict[str, Any]:
        return {"value": self.value, "source": self.source, "note": self.note}


def _sum_sales(rows: Iterable[DaySales], window: Window) -> tuple[int, int, int]:
    units = orders = revenue = 0
    for s in rows:
        if s.day in window:
            units += s.units
            orders += s.orders
            revenue += s.revenue_minor
    return units, orders, revenue


def _ads_in(rows: Iterable[AdRow], window: Window) -> tuple[Decimal, Decimal, Decimal]:
    spend = orders = revenue = Decimal(0)
    for a in rows:
        inside = window.overlap_days(a.period_start, a.period_end)
        if inside:
            share = Decimal(inside) / Decimal((a.period_end - a.period_start).days + 1)
            spend += a.spend_minor * share
            orders += a.ad_orders * share
            revenue += a.ad_revenue_minor * share
    return spend, orders, revenue


def _r(v: Decimal | float | int) -> int:
    return int(Decimal(str(v)).quantize(Decimal("1")))


def ledger_covers(shop: Shop, window: Window) -> bool:
    return shop.ledger_from is not None and shop.ledger_from <= window.start and (shop.ledger_to or window.end) >= window.end - timedelta(days=1)


def report_covers(shop: Shop, window: Window) -> bool:
    """Some uploaded Ads report overlaps the window."""
    return any(window.overlap_days(a.period_start, a.period_end) for rows in shop.ads.values() for a in rows)


def ledger_by_category(shop: Shop, window: Window) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Costs per category in the window, and every ledger type seen (counted or not)."""
    cats: dict[str, int] = defaultdict(int)
    types: dict[str, dict[str, Any]] = {}
    for e in shop.ledger:
        if e.day not in window:
            continue
        cat = category(e.ledger_type)
        t = types.setdefault(e.ledger_type, {"ledger_type": e.ledger_type, "category": cat, "amount": 0, "entries": 0})
        t["amount"] += e.amount_minor
        t["entries"] += e.entries
        if cat is not None:
            cats[cat] += cost_of(e.amount_minor)
    rows = sorted(types.values(), key=lambda t: t["amount"])
    for t in rows:
        t["counted"] = t["category"] is not None
        t["label"] = CATEGORY_LABELS.get(t["category"] or "", "Not counted as a cost (a payout, a sale, tax, or a type this app doesn't recognise)")
    return dict(cats), rows


def _rate_fees(costs: CostSettings, units: int, orders: int, revenue: int) -> dict[str, int]:
    rev = Decimal(revenue)
    return {
        "listing_fees": _r(costs.listing_fee * 100 * units),
        "transaction_fees": _r(rev * costs.transaction_pct / 100),
        "processing_fees": _r(rev * costs.payment_pct / 100 + costs.payment_fixed * 100 * orders),
    }


def _unit_cost(shop: Shop, lid: int) -> tuple[Decimal, str]:
    info = shop.info.get(lid) or ListingInfo()
    c = shop.costs
    if info.sku and info.sku.strip().casefold() in {k.strip().casefold() for k in c.product_cost_by_sku}:
        return c.product_cost_for(info.sku, None), f"SKU {info.sku}"
    if info.profile_id and info.profile_id in c.product_cost_by_profile:
        return c.product_cost_by_profile[info.profile_id], f"profile {info.profile_name or ''}".strip()
    return c.product_cost, "default"


# --- the shop ---------------------------------------------------------------------------------


def shop_totals(shop: Shop, window: Window) -> dict[str, Any]:
    """The shop's income statement for one window, each line with its source."""
    units = orders = revenue = 0
    for rows in shop.sales.values():
        u, o, r = _sum_sales(rows, window)
        units, orders, revenue = units + u, orders + o, revenue + r

    notes: list[str] = []
    if shop.sales_from is None:
        rev_note = "Your sales haven't been read yet."
    elif shop.sales_from > window.start:
        rev_note = f"Sales are read from {shop.sales_from:%b %d, %Y}; days before that aren't included."
    else:
        rev_note = "Item prices only: what buyers paid for shipping isn't included, so this is lower than Etsy's sales total."
    have_sales = shop.sales_from is not None

    covered = ledger_covers(shop, window)
    cats, types = ledger_by_category(shop, window) if covered else ({}, [])
    rates = _rate_fees(shop.costs, units, orders, revenue)
    fees: dict[str, Figure] = {}
    for key in FEES:
        if covered:
            fees[key] = Figure(cats.get(key, 0), "ledger")
        elif have_sales:
            fees[key] = Figure(rates[key], "rates", "Estimated from your fee rates; Etsy's ledger isn't read for this period."
                               + (" Per-order fixed fees are counted per listing, so multi-item orders are slightly over-charged." if key == "processing_fees" else ""))
        else:
            fees[key] = Figure(None, "none", "Needs your sales.")

    report_spend = sum((_ads_in(rows, window)[0] for rows in shop.ads.values()), Decimal(0))
    if covered:
        ads = Figure(cats.get("ads", 0), "ledger", "Etsy Ads and Offsite Ads, from Etsy's ledger.")
    elif report_covers(shop, window):
        ads = Figure(_r(report_spend), "report", "Only the listings in the Ads reports you uploaded; Etsy's ledger isn't read for this period.")
    else:
        ads = Figure(None, "none", "Not available: Etsy's ledger isn't read for this period and no Ads report covers it.")

    if covered and cats.get("shipping_labels"):
        shipping = Figure(cats["shipping_labels"], "ledger", "Shipping labels bought on Etsy.")
    elif shop.entered["shipping"] and have_sales:
        shipping = Figure(_r(shop.costs.shipping_cost * 100 * orders), "costs", "Your shipping cost per order line.")
    else:
        shipping = Figure(None, "none", "No shipping cost entered, and no Etsy shipping labels in the ledger.")

    if shop.entered["product"] and have_sales:
        product = Figure(sum(_r(_unit_cost(shop, lid)[0] * 100 * _sum_sales(rows, window)[0]) for lid, rows in shop.sales.items()), "costs")
    else:
        product = Figure(None, "none", "Enter your product costs (Fees & costs) to include them.")

    fixed = (Figure(profit.fixed_costs(shop.costs, window), "costs", "Your monthly fixed costs, spread over the days.")
             if shop.entered["fixed"] else Figure(None, "none", "No fixed costs entered."))

    lines = {**fees, "ads": ads, "shipping": shipping, "product": product, "fixed": fixed}
    known = sum(f.value for f in lines.values() if f.value is not None)
    missing = [LABELS[k] for k, f in lines.items() if f.value is None and k != "fixed"]
    net = revenue - known if have_sales else None
    return {
        "revenue": Figure(revenue if have_sales else None, "sales", rev_note).out(),
        "units": units if have_sales else None,
        "orders": orders if have_sales else None,
        "aov": round(revenue / orders) if orders else None,
        "lines": {k: f.out() for k, f in lines.items()},
        "costs": known,
        "net": Figure(net, "computed", ("Leaves out: " + ", ".join(missing) + ".") if missing else None).out(),
        "margin": (net / revenue) if (net is not None and revenue) else None,
        "net_excludes": missing,
        "ads_unattributed": (max(0, cats.get("ads", 0) - _r(report_spend)) if covered else None),
        "ledger_types": types,
        "notes": notes,
    }


LABELS = {
    "listing_fees": "listing fees",
    "transaction_fees": "transaction fees",
    "processing_fees": "payment processing",
    "ads": "ad spend",
    "shipping": "shipping",
    "product": "product cost",
    "fixed": "fixed costs",
}


def comparison(shop: Shop, window: Window, mode: str) -> tuple[Window | None, str, str | None]:
    """The window compared with, its label, and why not when unavailable."""
    days = window.days
    if mode == "year":
        other = window.shifted(YEAR)
        if shop.sales_from is None or shop.sales_from > other.start:
            return None, f"Last {days} days vs the same {days} days a year earlier", (
                "A year-earlier comparison needs 13 months of sales history, which isn't read yet."
            )
        return other, f"Last {days} days vs the same {days} days a year earlier", None
    return window.previous(), f"Last {days} days vs the previous {days}", None


# --- listings -----------------------------------------------------------------------------------


def weekly(rows: Iterable[DaySales], today: date, weeks: int, field_: str = "units") -> list[int]:
    """Totals per week, oldest first, the last week ending today."""
    out = [0] * weeks
    first = today - timedelta(days=7 * weeks - 1)
    for s in rows:
        i = (s.day - first).days // 7
        if 0 <= i < weeks:
            out[i] += getattr(s, field_)
    return out


def trend(rows: list[DaySales], today: date) -> dict[str, Any]:
    """Direction over 12 weeks, as 4-week averages: level and change of direction.

    "turned_down": it was rising and now falls; "turned_up": the reverse. That is
    the signal worth acting on, not a single day's spike.
    """
    w = weekly(rows, today, 12)
    a, b, c = sum(w[0:4]) / 4, sum(w[4:8]) / 4, sum(w[8:12]) / 4
    total = sum(w)
    if total < 4:
        signal = "too_few"
    else:
        step = max(0.5, 0.2 * (total / 12))
        d1, d2 = b - a, c - b
        if d1 > step and d2 < -step:
            signal = "turned_down"
        elif d1 < -step and d2 > step:
            signal = "turned_up"
        elif d2 > step:
            signal = "rising"
        elif d2 < -step:
            signal = "falling"
        else:
            signal = "steady"
    return {"signal": signal, "weekly_units": w, "avg4": [round(a, 2), round(b, 2), round(c, 2)]}


@dataclass
class Economics:
    lid: int
    units: int
    orders: int
    revenue: int
    fees: dict[str, int]
    fees_source: str
    product: int | None
    unit_cost: str | None
    unit_cost_source: str | None
    shipping: int | None
    ads: int | None
    ad_orders: float | None
    ad_revenue: int | None
    net: int
    net_excludes: list[str] = field(default_factory=list)

    @property
    def fees_total(self) -> int:
        return sum(self.fees.values())

    @property
    def margin(self) -> float | None:
        return self.net / self.revenue if self.revenue else None

    @property
    def contribution(self) -> int:
        """Revenue less fees, product and shipping: what's left before ads."""
        return self.revenue - self.fees_total - (self.product or 0) - (self.shipping or 0)

    @property
    def break_even_acos(self) -> float | None:
        """The ACOS at which ads eat this listing's whole margin before ads."""
        return self.contribution / self.revenue if self.revenue else None

    @property
    def acos(self) -> float | None:
        return (self.ads / self.ad_revenue) if (self.ads is not None and self.ad_revenue) else None

    @property
    def spend_per_sale(self) -> int | None:
        if self.ads is None:
            return None
        sales = self.ad_orders if self.ad_orders else None
        return round(self.ads / sales) if sales else None

    def out(self) -> dict[str, Any]:
        return {
            "units": self.units, "orders": self.orders, "revenue": self.revenue,
            "fees": self.fees, "fees_total": self.fees_total, "fees_source": self.fees_source,
            "product": self.product, "unit_cost": self.unit_cost, "unit_cost_source": self.unit_cost_source,
            "shipping": self.shipping, "ads": self.ads,
            "ad_orders": None if self.ad_orders is None else round(self.ad_orders, 1),
            "ad_revenue": self.ad_revenue,
            "net": self.net, "margin": self.margin, "net_per_unit": round(self.net / self.units) if self.units else None,
            "net_excludes": self.net_excludes,
            "contribution": self.contribution, "break_even_acos": self.break_even_acos,
            "acos": self.acos, "spend_per_sale": self.spend_per_sale,
        }


def economics(shop: Shop, window: Window) -> dict[int, Economics]:
    """Unit economics of every listing with sales or ad spend in the window.

    Fees: with Etsy's ledger for the window, its totals are shared out to
    listings (listing fees by items sold, transaction and processing fees by
    revenue), so the listings add up to the ledger; otherwise the seller's rates.
    """
    covered = ledger_covers(shop, window)
    cats = ledger_by_category(shop, window)[0] if covered else {}
    per: dict[int, tuple[int, int, int]] = {}
    for lid, rows in shop.sales.items():
        u, o, r = _sum_sales(rows, window)
        if u or r:
            per[lid] = (u, o, r)
    report = report_covers(shop, window)
    for lid, rows in shop.ads.items():
        if _ads_in(rows, window)[0] and lid not in per:
            per[lid] = (0, 0, 0)
    tot_u = sum(v[0] for v in per.values()) or 1
    tot_r = sum(v[2] for v in per.values()) or 1
    tot_o = sum(v[1] for v in per.values()) or 1

    out: dict[int, Economics] = {}
    for lid, (u, o, r) in per.items():
        if covered:
            fees = {
                "listing_fees": _r(Decimal(cats.get("listing_fees", 0)) * u / tot_u),
                "transaction_fees": _r(Decimal(cats.get("transaction_fees", 0)) * r / tot_r),
                "processing_fees": _r(Decimal(cats.get("processing_fees", 0)) * r / tot_r),
            }
            source = "allocated"
        else:
            fees, source = _rate_fees(shop.costs, u, o, r), "rates"
        excludes: list[str] = []
        if shop.entered["product"]:
            uc, ucs = _unit_cost(shop, lid)
            product, unit_cost, unit_src = _r(uc * 100 * u), str(uc), ucs
        else:
            product, unit_cost, unit_src = None, None, None
            excludes.append("product cost")
        if covered and cats.get("shipping_labels"):
            shipping = _r(Decimal(cats["shipping_labels"]) * o / tot_o)
        elif shop.entered["shipping"]:
            shipping = _r(shop.costs.shipping_cost * 100 * o)
        else:
            shipping = None
            excludes.append("shipping")
        if report:
            spend, ad_o, ad_r = _ads_in(shop.ads.get(lid, []), window)
            ads, ad_orders, ad_revenue = _r(spend), float(ad_o), _r(ad_r)
        else:
            ads = ad_orders = ad_revenue = None
            excludes.append("ad spend (per listing needs an uploaded Ads report)")
        net = r - sum(fees.values()) - (product or 0) - (shipping or 0) - (ads or 0)
        out[lid] = Economics(lid, u, o, r, fees, source, product, unit_cost, unit_src, shipping, ads, ad_orders, ad_revenue, net, excludes)
    return out


def concentration(econ: dict[int, Economics], top: int = 10) -> dict[str, Any]:
    ranked = sorted(econ.values(), key=lambda e: e.revenue, reverse=True)
    total = sum(e.revenue for e in ranked)
    top_rev = sum(e.revenue for e in ranked[:top])
    running, pareto = 0, 0
    for e in ranked:
        if total and running >= total * 0.8:
            break
        running += e.revenue
        pareto += 1
    return {
        "total": total,
        "top_n": top,
        "top_share": (top_rev / total) if total else None,
        "top_listings": [e.lid for e in ranked[:top]],
        "listings_for_80pct": pareto if total else None,
        "selling_listings": sum(1 for e in ranked if e.revenue > 0),
    }


def cohorts(shop: Shop, econ: dict[int, Economics], window: Window, today: date) -> list[dict[str, Any]]:
    """Listings grouped by launch quarter: a listing launched last month and one
    launched two years ago aren't comparable on raw revenue, so each cohort also
    shows revenue per listing and, where the sales history reaches, what a
    listing earned in its first 90 days."""
    groups: dict[str, dict[str, Any]] = {}

    def key(d: date | None) -> tuple[str, str]:
        if d is None:
            return "zz", "Launch date unknown"
        if d < today - timedelta(days=730):
            return "00", "More than two years ago"
        q = (d.month - 1) // 3 + 1
        return f"{d.year}Q{q}", f"{d.year} Q{q}"

    universe = set(econ) | {lid for lid, i in shop.info.items() if i.state == "active"}
    for lid in universe:
        info = shop.info.get(lid) or ListingInfo()
        k, label = key(info.launched)
        g = groups.setdefault(k, {"key": k, "label": label, "listings": 0, "selling": 0, "revenue": 0, "units": 0,
                                  "first90_revenue": 0, "first90_listings": 0})
        g["listings"] += 1
        e = econ.get(lid)
        if e is not None and e.units:
            g["selling"] += 1
            g["revenue"] += e.revenue
            g["units"] += e.units
        # First 90 days since launch, when the whole span is inside the sales history.
        if info.launched and shop.sales_from and info.launched >= shop.sales_from and info.launched + timedelta(days=89) <= today:
            span = Window(info.launched, info.launched + timedelta(days=89))
            g["first90_revenue"] += _sum_sales(shop.sales.get(lid, []), span)[2]
            g["first90_listings"] += 1
    out = sorted(groups.values(), key=lambda g: g["key"], reverse=True)
    for g in out:
        g["revenue_per_listing"] = round(g["revenue"] / g["listings"]) if g["listings"] else None
        g["first90_per_listing"] = round(g["first90_revenue"] / g["first90_listings"]) if g["first90_listings"] else None
    return out


def daily_series(shop: Shop, window: Window, other: Window | None) -> dict[str, Any]:
    """Revenue per day with 7- and 28-day rolling averages (no single-day spikes)."""
    by_day: dict[date, int] = defaultdict(int)
    for rows in shop.sales.values():
        for s in rows:
            by_day[s.day] += s.revenue_minor

    def series(w: Window) -> list[dict[str, Any]]:
        out = []
        for i in range(w.days):
            d = w.start + timedelta(days=i)
            def avg(n: int) -> float:
                return sum(by_day.get(d - timedelta(days=j), 0) for j in range(n)) / n
            out.append({"day": d.isoformat(), "revenue": by_day.get(d, 0), "avg7": round(avg(7)), "avg28": round(avg(28))})
        return out

    return {"current": series(window), "comparison": series(other) if other else None}


# --- what to do today ---------------------------------------------------------------------------

AD_SINK_MIN = 500


def actions(shop: Shop, econ: dict[int, Economics], prev: dict[int, Economics], window: Window,
            today: date, money: profit.MoneyFormat) -> list[dict[str, Any]]:
    """Things worth doing, each with the money at stake per 30 days, largest first.

    Advice only: there is no Ads endpoint, so every action is for the seller to
    take in Shop Manager (CLAUDE.md rule 4).
    """
    per30 = Decimal(30) / Decimal(window.days)
    out: list[dict[str, Any]] = []

    def add(lid: int, kind: str, stake: int, reason: str, action: str, links: list[dict[str, str]]) -> None:
        out.append({"listing_id": lid, "kind": kind, "stake": stake, "reason": reason, "action": action, "links": links})

    # Listings that sold before but not now are exactly the ones fading.
    for lid in set(econ) | set(prev):
        e = econ.get(lid) or Economics(lid, 0, 0, 0, {k: 0 for k in FEES}, "rates", None, None, None, None, None, None, None, 0)
        editor = {"label": "Edit in Shop Manager", "url": profit.editor_url(lid)}
        ads_link = {"label": "Etsy Ads in Shop Manager", "url": profit.ADS_URL}
        # Ads that bring no sale at all; ads that bring sales at a loss are the next case.
        if e.ads is not None and e.ads >= AD_SINK_MIN and (e.units == 0 or e.ad_orders == 0):
            add(lid, "ad_sink", _r(Decimal(e.ads) * per30),
                f"{money(e.ads)} of ads in {window.days} days, {e.units} sold, net {money(e.net)}.",
                "Turn its ad off in Shop Manager.", [ads_link, editor])
            continue
        acos, be = e.acos, e.break_even_acos
        if acos is not None and be is not None and e.ad_revenue and acos > max(be, 0):
            lost = _r(Decimal(e.ad_revenue) * Decimal(str(acos - max(be, 0))) * per30)
            add(lid, "ads_above_break_even", lost,
                f"ACOS {acos:.0%} against a break-even of {max(be, 0):.0%} (this listing's margin before ads): "
                f"each ad sale loses money.", "Lower its ad budget in Shop Manager, or raise its price.", [ads_link, editor])
            continue
        if e.product is not None and e.units and e.net < 0:
            add(lid, "selling_at_loss", _r(Decimal(-e.net) * per30),
                f"Sold {e.units} for {money(e.revenue)} and lost {money(-e.net)} after fees and costs.",
                "Raise its price or lower its cost.", [editor])
            continue
        p = prev.get(lid)
        if p and p.revenue >= 2000 and e.revenue <= p.revenue * 0.5:
            add(lid, "fading", _r(Decimal(p.revenue - e.revenue) * per30),
                f"Revenue {money(e.revenue)} against {money(p.revenue)} in the previous {window.days} days.",
                "Refresh its title, tags and photos; check whether its season has passed.", [editor])
            continue
        t = trend(shop.sales.get(lid, []), today)
        if t["signal"] == "turned_down":
            a, b, c = t["avg4"]
            price = Decimal(e.revenue) / Decimal(e.units) if e.units else Decimal(0)
            # The weekly drop, over 30 days, at its selling price.
            add(lid, "turned_down", _r((Decimal(str(b)) - Decimal(str(c))) * price * 30 / 7),
                f"Weekly sales rose to {b:g} and have turned down to {c:g} (4-week averages).",
                "Look at it now, before the decline sets in: refresh its photos or title.", [editor])
            continue
        if acos is not None and be is not None and e.ad_revenue and acos < be / 2 and e.net > 0:
            # What its ads earn after their own cost: the measure of what more of the same could bring,
            # not the listing's whole profit.
            earned = _r(Decimal(e.ad_revenue) * Decimal(str(be - acos)))
            add(lid, "room_to_advertise", _r(Decimal(earned) * per30),
                f"Ads return at {acos:.0%} ACOS, well under its {be:.0%} break-even: they earned about {money(earned)} "
                f"after their own cost.",
                "Consider a higher ad budget for it in Shop Manager; returns usually fall as spend rises.", [ads_link, editor])
    out.sort(key=lambda a: a["stake"], reverse=True)
    return out
