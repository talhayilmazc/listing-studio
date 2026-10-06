"""Views, favourites and orders of the listings the app published (Part D).

Two readers over ``listing_stat_daily`` (workers/listing_stats.py):

- :func:`month_traffic`: each listing's views and favourites in a month, for
  Analytics → Listings, with conversion = orders ÷ views.
- :func:`compare_styles`: listings published in the same period, by title style
  ("short" / "long", from their content version), compared on views per listing
  per day, favourites per view and orders per view, each with its sample size
  and a 95% interval. Below :data:`MIN_LISTINGS` listings or :data:`MIN_VIEWS`
  views for a style it says "not enough data yet" and shows no rate.

These are listing page views on Etsy, **not search impressions**: Etsy's API has
no impressions, search terms or traffic sources (docs/analytics.md). Nothing is
ever rewritten from these numbers.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ContentVersion, ListingPublication, ListingStatDaily, SalesDaily

LABEL = "listing views on Etsy, not search impressions"
MIN_LISTINGS = 30
MIN_VIEWS = 1000
Z95 = 1.96
NOT_MEASURED = "Not measured yet: views are read once a day for listings published with the app."
NOT_APP = "Only listings published with the app are measured."


@dataclass
class Traffic:
    views: int | None
    favorites: int | None
    days: int  # days with a known reading in the range

    def out(self, orders: int) -> dict[str, Any]:
        return {
            "views": self.views,
            "favorites": self.favorites,
            "conversion": (orders / self.views) if self.views else None,
            "views_days": self.days,
            "traffic_note": None if self.days else NOT_MEASURED,
        }


async def app_listings(session: AsyncSession, connection_id: uuid.UUID) -> dict[int, datetime]:
    rows = await session.execute(
        select(ListingPublication.etsy_listing_id, ListingPublication.published_at).where(
            ListingPublication.connection_id == connection_id, ListingPublication.published_at.is_not(None)
        )
    )
    return {int(lid): at for lid, at in rows.all()}


async def month_traffic(
    session: AsyncSession, connection_id: uuid.UUID, start: date, end: date
) -> dict[int, Traffic]:
    """Every app-published listing's views and favourites in ``start``..``end``."""
    out = {lid: Traffic(None, None, 0) for lid in await app_listings(session, connection_id)}
    for row in (await session.execute(
        select(ListingStatDaily).where(
            ListingStatDaily.connection_id == connection_id, ListingStatDaily.day >= start, ListingStatDaily.day <= end
        )
    )).scalars():
        t = out.setdefault(row.listing_id, Traffic(None, None, 0))
        if row.views is None:
            continue
        t.views = (t.views or 0) + row.views
        t.favorites = (t.favorites or 0) + (row.favorites or 0)
        t.days += 1
    return out


# --- title-style comparison ----------------------------------------------------------------------------


@dataclass
class Unit:
    """One listing in the comparison: what it had while it carried its style."""

    listing_id: int
    style: str
    days: int = 0
    views: int = 0
    favorites: int = 0
    orders: int = 0


def ratio(units: list[Unit], num: str, den: str) -> tuple[float | None, float | None, float | None]:
    """Σnum / Σden with a 95% interval clustered by listing (the ratio estimator's
    delta-method variance), so one busy listing does not pass for many."""
    n = len(units)
    total_d = sum(getattr(u, den) for u in units)
    if n < 2 or total_d <= 0:
        return None, None, None
    r = sum(getattr(u, num) for u in units) / total_d
    mean_d = total_d / n
    var = sum((getattr(u, num) - r * getattr(u, den)) ** 2 for u in units) / (n * (n - 1) * mean_d**2)
    se = math.sqrt(var)
    return r, max(0.0, r - Z95 * se), r + Z95 * se


@dataclass
class StyleResult:
    style: str
    listings: int
    views: int
    enough: bool
    note: str | None
    rates: dict[str, Any] = field(default_factory=dict)

    def out(self) -> dict[str, Any]:
        return {"style": self.style, "listings": self.listings, "views": self.views, "enough": self.enough,
                "note": self.note, **self.rates}


RATES = (("views_per_listing_day", "views", "days"), ("favorites_per_view", "favorites", "views"),
         ("orders_per_view", "orders", "views"))


def summarise(units: list[Unit], style: str, *, orders_known: bool) -> StyleResult:
    views = sum(u.views for u in units)
    enough = len(units) >= MIN_LISTINGS and views >= MIN_VIEWS
    note = None if enough else (
        f"Not enough data yet: {len(units)} of {MIN_LISTINGS} listings and {views:,} of {MIN_VIEWS:,} views."
    )
    rates: dict[str, Any] = {}
    for key, num, den in RATES:
        if not enough or (key == "orders_per_view" and not orders_known):
            rates[key] = None
            continue
        r, low, high = ratio(units, num, den)
        rates[key] = {"value": r, "low": low, "high": high}
    return StyleResult(style, len(units), views, enough, note, rates)


def difference(a: StyleResult, b: StyleResult, units: dict[str, list[Unit]]) -> dict[str, Any] | None:
    """short − long for each rate, with a 95% interval; only when both have enough."""
    if not (a.enough and b.enough):
        return None
    out: dict[str, Any] = {}
    for key, num, den in RATES:
        ra, rb = ratio(units[a.style], num, den), ratio(units[b.style], num, den)
        if a.rates.get(key) is None or b.rates.get(key) is None or None in (ra[0], rb[0]):
            out[key] = None
            continue
        se_a, se_b = (ra[2] - ra[0]) / Z95, (rb[2] - rb[0]) / Z95
        d = ra[0] - rb[0]
        half = Z95 * math.sqrt(se_a**2 + se_b**2)
        out[key] = {"value": d, "low": d - half, "high": d + half, "clear": d - half > 0 or d + half < 0}
    return out


async def compare_styles(
    session: AsyncSession, connection_id: uuid.UUID, start: date, end: date, today: date, *, orders_known: bool
) -> dict[str, Any]:
    """Listings published with the app in ``start``..``end``, by title style, measured
    from going live to ``today`` (only while their first version was live)."""
    pubs = (await session.execute(
        select(ListingPublication).where(
            ListingPublication.connection_id == connection_id,
            ListingPublication.published_at.is_not(None),
        )
    )).scalars().all()
    units: dict[str, list[Unit]] = defaultdict(list)
    until: dict[int, date] = {}
    for p in pubs:
        live = p.published_at.replace(tzinfo=timezone.utc) if p.published_at.tzinfo is None else p.published_at
        if not start <= live.date() <= end:
            continue
        first = (await session.execute(
            select(ContentVersion).where(ContentVersion.publication_id == p.id)
            .order_by(ContentVersion.created_at).limit(1)
        )).scalar_one_or_none()
        if first is None:  # published before versions were kept: its style is unknown
            continue
        unit = Unit(p.etsy_listing_id, first.title_style)
        # A listing whose text was later replaced counts only while its first text was live.
        ended = first.active_to.date() if first.active_to is not None else today
        until[p.etsy_listing_id] = ended
        units[first.title_style].append(unit)
    by_id = {u.listing_id: u for us in units.values() for u in us}
    if by_id:
        for row in (await session.execute(
            select(ListingStatDaily).where(
                ListingStatDaily.connection_id == connection_id, ListingStatDaily.listing_id.in_(list(by_id)),
                ListingStatDaily.day >= start,
            )
        )).scalars():
            u = by_id[row.listing_id]
            if row.views is None or row.day > until[row.listing_id]:
                continue
            u.days += 1
            u.views += row.views
            u.favorites += row.favorites or 0
        if orders_known:
            for row in (await session.execute(
                select(SalesDaily).where(
                    SalesDaily.connection_id == connection_id, SalesDaily.listing_id.in_(list(by_id)),
                    SalesDaily.day >= start,
                )
            )).scalars():
                if row.day <= until[row.listing_id]:
                    by_id[row.listing_id].orders += row.orders
    # A listing with no measured day yet tells nothing about its rate.
    for style in list(units):
        units[style] = [u for u in units[style] if u.days > 0]
    short = summarise(units.get("short", []), "short", orders_known=orders_known)
    long = summarise(units.get("long", []), "long", orders_known=orders_known)
    return {
        "label": LABEL,
        "period": {"start": start, "end": end},
        "styles": [short.out(), long.out()],
        "difference": difference(short, long, {"short": units.get("short", []), "long": units.get("long", [])}),
        "orders_note": None if orders_known else "Orders need the shop's sales read (Analytics → Sales).",
        "thresholds": {"listings": MIN_LISTINGS, "views": MIN_VIEWS},
        "caveats": [
            "Etsy gives new and renewed listings a small temporary boost; compare listings published in the same period.",
            "Views are listing page views on Etsy, not search impressions; Etsy's API has no impressions, search terms or traffic sources.",
            "Listings published before content versions were kept have no recorded style and are left out.",
        ],
    }
