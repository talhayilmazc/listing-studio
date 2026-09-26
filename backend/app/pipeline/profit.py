"""Profit per listing and per shop, and what to do about each listing (v7 §C2–C4).

Pure functions over the seller's own daily sales totals (``sales_daily``), the
Etsy Ads spend they uploaded (``ad_spend``) and the fee rates and costs they
entered (``tenant.cost_settings``). Money is in the shop's currency, in minor
units (cents), and rounded once per figure.

Nothing here calls Etsy, and the recommendations only point at Shop Manager:
there is no Ads endpoint (CLAUDE.md rule 4), so "turn its ad off" is advice for
the seller to act on, never something the app does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Iterable

# --- Cost settings (C2) ------------------------------------------------------------

#: Etsy's published rates as defaults; every one is editable because they change.
DEFAULT_COSTS: dict[str, Any] = {
    "listing_fee": "0.20",  # per item sold (a sale renews the listing)
    "transaction_pct": "6.5",  # of the sale price
    "payment_pct": "3",  # payment processing, of the sale price
    "payment_fixed": "0.25",  # payment processing, per order
    "shipping_cost": "0",  # what the seller pays to ship, per order
    "monthly_fixed": "0",  # other fixed costs per month (shop-level only)
    "product_cost": "0",  # per item, when no profile or SKU cost applies
    "product_cost_by_profile": {},
    "product_cost_by_sku": {},
}

_AMOUNT_KEYS = (
    "listing_fee",
    "transaction_pct",
    "payment_pct",
    "payment_fixed",
    "shipping_cost",
    "monthly_fixed",
    "product_cost",
)
MAX_AMOUNT = Decimal("1000000")
MAX_PCT = Decimal("100")
MAX_COST_ENTRIES = 2000


def _amount(value: Any, key: str) -> Decimal:
    try:
        d = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, AttributeError):
        raise ValueError(f"{key}: not a number") from None
    if not d.is_finite() or d < 0:
        raise ValueError(f"{key}: must be zero or more")
    limit = MAX_PCT if key.endswith("_pct") else MAX_AMOUNT
    if d > limit:
        raise ValueError(f"{key}: must be at most {limit}")
    return d


@dataclass(frozen=True)
class CostSettings:
    listing_fee: Decimal
    transaction_pct: Decimal
    payment_pct: Decimal
    payment_fixed: Decimal
    shipping_cost: Decimal
    monthly_fixed: Decimal
    product_cost: Decimal
    product_cost_by_profile: dict[str, Decimal] = field(default_factory=dict)
    product_cost_by_sku: dict[str, Decimal] = field(default_factory=dict)

    @classmethod
    def from_stored(cls, stored: dict[str, Any] | None) -> "CostSettings":
        """Stored settings over the defaults; a bad stored value falls back to its default."""
        merged = {**DEFAULT_COSTS, **(stored or {})}
        values: dict[str, Any] = {}
        for key in _AMOUNT_KEYS:
            try:
                values[key] = _amount(merged[key], key)
            except ValueError:
                values[key] = _amount(DEFAULT_COSTS[key], key)
        for key in ("product_cost_by_profile", "product_cost_by_sku"):
            table: dict[str, Decimal] = {}
            for k, v in (merged.get(key) or {}).items():
                try:
                    table[str(k)] = _amount(v, key)
                except ValueError:
                    continue
            values[key] = table
        return cls(**values)

    def product_cost_for(self, sku: str | None, profile_id: str | None) -> Decimal:
        """Per item: the SKU's cost, else the profile's, else the default."""
        if sku and sku.strip().casefold() in self._sku_index:
            return self._sku_index[sku.strip().casefold()]
        if profile_id and profile_id in self.product_cost_by_profile:
            return self.product_cost_by_profile[profile_id]
        return self.product_cost

    @property
    def _sku_index(self) -> dict[str, Decimal]:
        return {k.strip().casefold(): v for k, v in self.product_cost_by_sku.items()}


def validate_costs(body: dict[str, Any]) -> dict[str, Any]:
    """The settings to store, as strings, or ValueError naming the bad field."""
    out: dict[str, Any] = {}
    for key in _AMOUNT_KEYS:
        out[key] = str(_amount(body.get(key, DEFAULT_COSTS[key]), key))
    for key in ("product_cost_by_profile", "product_cost_by_sku"):
        table = body.get(key) or {}
        if not isinstance(table, dict):
            raise ValueError(f"{key}: expected a mapping")
        if len(table) > MAX_COST_ENTRIES:
            raise ValueError(f"{key}: at most {MAX_COST_ENTRIES} entries")
        clean: dict[str, str] = {}
        for k, v in table.items():
            name = str(k).strip()
            if not name or len(name) > 100:
                raise ValueError(f"{key}: each entry needs a name of 1-100 characters")
            if v is None or str(v).strip() == "":
                continue  # an emptied field removes the entry
            clean[name] = str(_amount(v, f"{key} {name}"))
        out[key] = clean
    return out


def _round(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --- Inputs ------------------------------------------------------------------------


@dataclass(frozen=True)
class DaySales:
    listing_id: int
    day: date
    units: int
    orders: int
    revenue_minor: int


@dataclass(frozen=True)
class AdRow:
    listing_id: int
    period_start: date
    period_end: date
    spend_minor: int
    ad_orders: int
    ad_revenue_minor: int
    ad_views: int = 0


@dataclass(frozen=True)
class Window:
    start: date
    end: date  # inclusive

    @classmethod
    def last(cls, days: int, today: date) -> "Window":
        return cls(today - timedelta(days=days - 1), today)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def previous(self) -> "Window":
        return Window(self.start - timedelta(days=self.days), self.start - timedelta(days=1))

    def shifted(self, days: int) -> "Window":
        return Window(self.start - timedelta(days=days), self.end - timedelta(days=days))

    def overlap_days(self, start: date, end: date) -> int:
        return max(0, (min(self.end, end) - max(self.start, start)).days + 1)

    def __contains__(self, day: object) -> bool:
        return isinstance(day, date) and self.start <= day <= self.end


# --- Metrics (C3) ------------------------------------------------------------------


@dataclass
class Metrics:
    units: int = 0
    orders: int = 0
    revenue: int = 0
    transaction_fee: int = 0
    payment_fee: int = 0
    listing_fee: int = 0
    product_cost: int = 0
    shipping_cost: int = 0
    ad_spend: int = 0
    ad_orders: int = 0
    ad_revenue: int = 0

    @property
    def fees(self) -> int:
        return self.transaction_fee + self.payment_fee + self.listing_fee

    @property
    def costs(self) -> int:
        return self.fees + self.product_cost + self.shipping_cost + self.ad_spend

    @property
    def net(self) -> int:
        return self.revenue - self.costs

    @property
    def margin(self) -> float | None:
        return self.net / self.revenue if self.revenue else None

    @property
    def aov(self) -> int | None:
        return round(self.revenue / self.orders) if self.orders else None

    @property
    def acos(self) -> float | None:
        """Ad spend over the revenue the ads brought in."""
        return self.ad_spend / self.ad_revenue if self.ad_revenue else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "units": self.units,
            "orders": self.orders,
            "revenue": self.revenue,
            "transaction_fee": self.transaction_fee,
            "payment_fee": self.payment_fee,
            "listing_fee": self.listing_fee,
            "fees": self.fees,
            "product_cost": self.product_cost,
            "shipping_cost": self.shipping_cost,
            "ad_spend": self.ad_spend,
            "ad_orders": self.ad_orders,
            "ad_revenue": self.ad_revenue,
            "costs": self.costs,
            "net": self.net,
            "margin": self.margin,
            "aov": self.aov,
            "acos": self.acos,
        }


def listing_metrics(
    sales: Iterable[DaySales],
    ads: Iterable[AdRow],
    window: Window,
    costs: CostSettings,
    unit_cost: Decimal,
) -> Metrics:
    """One listing's figures over ``window``.

    Fees follow the seller's own rates: transaction and payment percentages of
    the sale price, the payment fixed fee and shipping per order line, the
    listing fee and product cost per item. Ad spend uploaded for a period that
    only partly overlaps the window counts in proportion to the days inside it.
    """
    units = orders = revenue = 0
    for s in sales:
        if s.day in window:
            units += s.units
            orders += s.orders
            revenue += s.revenue_minor
    spend = ad_orders = ad_revenue = Decimal(0)
    for a in ads:
        inside = window.overlap_days(a.period_start, a.period_end)
        if not inside:
            continue
        share = Decimal(inside) / Decimal((a.period_end - a.period_start).days + 1)
        spend += a.spend_minor * share
        ad_orders += a.ad_orders * share
        ad_revenue += a.ad_revenue_minor * share
    rev = Decimal(revenue)
    return Metrics(
        units=units,
        orders=orders,
        revenue=revenue,
        transaction_fee=_round(rev * costs.transaction_pct / 100),
        payment_fee=_round(rev * costs.payment_pct / 100 + costs.payment_fixed * 100 * orders),
        listing_fee=_round(costs.listing_fee * 100 * units),
        product_cost=_round(unit_cost * 100 * units),
        shipping_cost=_round(costs.shipping_cost * 100 * orders),
        ad_spend=_round(spend),
        ad_orders=_round(ad_orders),
        ad_revenue=_round(ad_revenue),
    )


def fixed_costs(costs: CostSettings, window: Window) -> int:
    """The shop's monthly fixed costs spread over the window's days."""
    return _round(costs.monthly_fixed * 100 * Decimal(window.days) * 12 / Decimal(365))


def change(current: float | int, previous: float | int) -> float | None:
    """Relative change against the previous period; None when there was nothing before."""
    if not previous:
        return None
    return (current - previous) / abs(previous)


# --- Classification (C4) -------------------------------------------------------------

WINNER = "winner"
STEADY = "steady"
FADING = "fading"
AD_SINK = "ad_sink"
LOSER = "loser"
NEW = "new"

#: Ad spend in the window below this is noise, not a sink ($5).
AD_SINK_MIN_SPEND = 500
#: Fading: at least this many sold in the previous period...
FADING_MIN_PREVIOUS_UNITS = 3
#: ...and the current period at most this share of it.
FADING_SHARE = 0.5
#: Winner: at least this many sold in the window, net positive, and among the
#: top share of the shop's listings by net profit.
WINNER_MIN_UNITS = 3
WINNER_TOP_SHARE = 0.2
#: Loser: no sale in this many days.
LOSER_DAYS = 90
#: New: a listing with no sale is not judged until it has had a fair chance:
#: this many days live...
NEW_FAIR_DAYS = 45
#: ...or this many once it has had ad spend or ad views (from an uploaded report).
NEW_FAIR_DAYS_WITH_TRAFFIC = 30

ADS_URL = "https://www.etsy.com/your/shops/me/advertising"


def editor_url(listing_id: int) -> str:
    return f"https://www.etsy.com/your/shops/me/listing-editor/edit/{listing_id}"


@dataclass
class ListingFacts:
    """What classification needs to know about one listing."""

    listing_id: int
    current: Metrics
    previous: Metrics
    units_long: int  # units in the last LOSER_DAYS days
    age_days: int | None = None  # days live (since it last went active), when known
    ad_spend_total: int = 0  # all uploaded ad spend for it, any period
    ad_views_total: int = 0  # all uploaded ad views for it, any period
    last_year_current: int = 0  # units in the same weeks last year
    last_year_previous: int = 0  # units in the weeks before those, last year


def too_new(f: ListingFacts) -> bool:
    """Not yet had a fair chance: under 30 days live, or under 45 with no ad traffic."""
    if f.age_days is None:
        return False
    if f.age_days < NEW_FAIR_DAYS_WITH_TRAFFIC:
        return True
    return f.age_days < NEW_FAIR_DAYS and not (f.ad_spend_total or f.ad_views_total)


@dataclass
class Verdict:
    klass: str
    reason: str
    action: str
    links: list[dict[str, str]]
    priority: float  # higher = look at it sooner


def winners(facts: list[ListingFacts]) -> set[int]:
    """Listings among the top ``WINNER_TOP_SHARE`` by net profit (at least one)."""
    earning = [f for f in facts if f.current.net > 0 and f.current.units >= WINNER_MIN_UNITS]
    if not earning:
        return set()
    selling = sum(1 for f in facts if f.current.units > 0)
    keep = max(1, int(selling * WINNER_TOP_SHARE))
    earning.sort(key=lambda f: f.current.net, reverse=True)
    return {f.listing_id for f in earning[:keep]}


def classify(f: ListingFacts, top: set[int], days: int, money: "MoneyFormat") -> Verdict:
    """The listing's class, why, and what the seller could do in Shop Manager."""
    cur, prev = f.current, f.previous
    editor = {"label": "Edit in Shop Manager", "url": editor_url(f.listing_id)}
    ads = {"label": "Etsy Ads in Shop Manager", "url": ADS_URL}
    sales = _plural(cur.units, "sale") if cur.units else "no sales"

    # Checked first: a listing that hasn't had a fair chance gets no advice to
    # remove, renew or stop advertising it, only that it is too early to judge.
    if f.units_long == 0 and too_new(f):
        assert f.age_days is not None
        spent = f" and {money(f.ad_spend_total)} of ads" if f.ad_spend_total else ""
        return Verdict(
            NEW,
            f"Live {_plural(f.age_days, 'day')} with no sale yet{spent}.",
            f"Too early to judge. It is judged from day {NEW_FAIR_DAYS} live, or day "
            f"{NEW_FAIR_DAYS_WITH_TRAFFIC} once it has ad views or ad spend.",
            [],
            0,
        )
    if cur.ad_spend >= AD_SINK_MIN_SPEND and (cur.units == 0 or cur.net < 0):
        return Verdict(
            AD_SINK,
            f"This listing spent {money(cur.ad_spend)} on ads in {days} days, made {sales}, "
            f"net {money(cur.net)}.",
            "Consider turning its ad off in Shop Manager.",
            [ads, editor],
            3_000_000 + cur.ad_spend - cur.net,
        )
    if prev.units >= FADING_MIN_PREVIOUS_UNITS and cur.units <= prev.units * FADING_SHARE:
        season = ""
        if f.last_year_previous or f.last_year_current:
            season = (
                f" Last year it sold {f.last_year_current} in these same weeks and "
                f"{f.last_year_previous} in the weeks before"
                + (", so the drop may be seasonal." if f.last_year_current < f.last_year_previous else ".")
            )
        return Verdict(
            FADING,
            f"Sold {prev.units} in the previous {days} days and {cur.units} in the last {days}.{season}",
            "Refresh its title and tags, and check whether its season is over.",
            [editor],
            2_000_000 + max(0, prev.revenue - cur.revenue),
        )
    if f.listing_id in top:
        margin = f" ({cur.margin:.0%} margin)" if cur.margin is not None else ""
        return Verdict(
            WINNER,
            f"Net {money(cur.net)} from {sales} in {days} days{margin}, one of your top earners.",
            "Consider more ad budget for it in Shop Manager, and making similar designs.",
            [ads, editor],
            1_000_000 + cur.net,
        )
    if cur.units > 0 and cur.net < 0:
        return Verdict(
            LOSER,
            f"Sold {cur.units} in {days} days but lost {money(-cur.net)} after fees and costs.",
            "Check its price and product cost.",
            [editor],
            1_500_000 - cur.net,
        )
    if f.units_long == 0:
        live = f.age_days is not None and f.age_days < LOSER_DAYS
        return Verdict(
            LOSER,
            f"No sale in the {f.age_days} days it has been live." if live else f"No sale in the last {LOSER_DAYS} days.",
            "Refresh its photos, title and tags, or deactivate it in Shop Manager.",
            [editor],
            500_000,
        )
    return Verdict(
        STEADY,
        (
            f"{sales.capitalize()} in {days} days, net {money(cur.net)}."
            if cur.units
            else f"{_plural(f.units_long, 'sale').capitalize()} in the last {LOSER_DAYS} days."
        ),
        "Leave it as it is.",
        [editor],
        100_000 + cur.net,
    )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


class MoneyFormat:
    """Minor units to text in the shop's currency ("$47.00", "-$31.00", "€12.50")."""

    SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$", "TRY": "₺"}

    def __init__(self, currency: str | None) -> None:
        self.currency = currency or "USD"

    def __call__(self, minor: int) -> str:
        sign = "-" if minor < 0 else ""
        body = f"{abs(minor) / 100:,.2f}"
        symbol = self.SYMBOLS.get(self.currency)
        return f"{sign}{symbol}{body}" if symbol else f"{sign}{body} {self.currency}"
