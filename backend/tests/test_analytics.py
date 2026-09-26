"""v7 §C: profit, the Etsy Ads CSV, classification and the Analytics endpoints.

All over the seller's own shop: their daily sales totals, the ads report they
uploaded, and the fees and costs they entered.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.db.models import AdSpend, EtsyConnection, SalesDaily, ShopListingCache, Tenant
from app.pipeline import ads_csv, profit
from app.pipeline.profit import AdRow, CostSettings, DaySales, ListingFacts, Metrics, Window
from tests.auth_support import authenticate, make_tenant, open_session
from tests.test_publish_api import _shop, ctx  # noqa: F401  (fixture)

TODAY = datetime.now(timezone.utc).date()
MONEY = profit.MoneyFormat("USD")


# --- Profit (C2, C3) ------------------------------------------------------------------


def test_profit_follows_the_sellers_own_rates() -> None:
    window = Window.last(30, TODAY)
    costs = CostSettings.from_stored(
        {"transaction_pct": "6.5", "payment_pct": "3", "payment_fixed": "0.25", "listing_fee": "0.20",
         "shipping_cost": "4", "product_cost": "9", "product_cost_by_profile": {"p1": "7"},
         "product_cost_by_sku": {"TEE01": "5"}}
    )
    unit_cost = costs.product_cost_for("tee01", "p1")  # SKU beats profile beats default
    assert unit_cost == Decimal("5")
    assert costs.product_cost_for(None, "p1") == Decimal("7") and costs.product_cost_for("X", None) == Decimal("9")

    sales = [
        DaySales(1, TODAY - timedelta(days=1), units=3, orders=2, revenue_minor=7500),
        DaySales(1, TODAY - timedelta(days=2), units=1, orders=1, revenue_minor=2500),
        DaySales(1, window.start - timedelta(days=1), units=9, orders=9, revenue_minor=99900),  # outside
    ]
    # Ten days of ad spend, five of them inside the window: half of it counts.
    ads = [AdRow(1, window.start - timedelta(days=5), window.start + timedelta(days=4), 1000, 2, 4000)]
    m = profit.listing_metrics(sales, ads, window, costs, unit_cost)

    assert (m.units, m.orders, m.revenue) == (4, 3, 10000)
    assert m.transaction_fee == 650  # 6.5% of $100
    assert m.payment_fee == 375  # 3% of $100 + $0.25 x 3 orders
    assert m.listing_fee == 80  # $0.20 x 4 items
    assert m.product_cost == 2000  # $5 x 4
    assert m.shipping_cost == 1200  # $4 x 3 orders
    assert (m.ad_spend, m.ad_orders, m.ad_revenue) == (500, 1, 2000)
    assert m.net == 10000 - (650 + 375 + 80 + 2000 + 1200 + 500) == 5195
    assert m.margin == pytest.approx(0.5195) and m.aov == 3333 and m.acos == pytest.approx(0.25)

    # Monthly fixed costs count at shop level, spread over the window's days.
    assert profit.fixed_costs(CostSettings.from_stored({"monthly_fixed": "36.50"}), window) == 3600


def test_cost_settings_are_checked_and_defaults_fill_the_rest() -> None:
    stored = profit.validate_costs({"transaction_pct": "7,5", "product_cost_by_sku": {"A1": "3", "B2": ""}})
    assert stored["transaction_pct"] == "7.5" and stored["listing_fee"] == "0.20"
    assert stored["product_cost_by_sku"] == {"A1": "3"}  # an emptied field removes the entry
    for bad in ({"payment_pct": "101"}, {"listing_fee": "-1"}, {"shipping_cost": "abc"}):
        with pytest.raises(ValueError):
            profit.validate_costs(bad)


# --- Classification (C4) --------------------------------------------------------------


def _facts(lid=1, *, units=0, net=0, spend=0, prev_units=0, long_units=None, age=None, ly=(0, 0)) -> ListingFacts:
    # Ad spend is the only cost here, so net = revenue - spend.
    cur = Metrics(units=units, orders=units, revenue=(net + spend) if units else 0, ad_spend=spend)
    return ListingFacts(
        listing_id=lid, current=cur, previous=Metrics(units=prev_units, orders=prev_units),
        units_long=units if long_units is None else long_units, age_days=age,
        last_year_current=ly[0], last_year_previous=ly[1],
    )


def _klass(f: ListingFacts, top: set[int] | None = None) -> str:
    return profit.classify(f, top or set(), 30, MONEY).klass


def test_ad_sink_needs_real_spend_and_no_sale_or_a_loss() -> None:
    assert _klass(_facts(spend=500, long_units=4)) == profit.AD_SINK
    assert _klass(_facts(spend=499, long_units=4)) == profit.STEADY  # under $5 is noise
    sink = _facts(units=1, net=-3100, spend=4700)
    v = profit.classify(sink, set(), 30, MONEY)
    assert v.klass == profit.AD_SINK
    assert v.reason == "This listing spent $47.00 on ads in 30 days, made 1 sale, net -$31.00."
    assert "turning its ad off" in v.action and v.links[0]["url"] == profit.ADS_URL
    assert _klass(_facts(units=2, net=900, spend=800)) != profit.AD_SINK  # ads that pay


def test_fading_at_half_the_previous_period() -> None:
    assert _klass(_facts(units=1, net=500, prev_units=3)) == profit.FADING
    assert _klass(_facts(units=2, net=500, prev_units=3)) == profit.STEADY  # 2 > half of 3
    assert _klass(_facts(units=0, prev_units=2, long_units=2)) == profit.STEADY  # too few before
    seasonal = profit.classify(_facts(units=0, prev_units=6, long_units=6, ly=(1, 7)), set(), 30, MONEY)
    assert seasonal.klass == profit.FADING and "may be seasonal" in seasonal.reason


def test_winners_are_the_top_earners_with_enough_sales() -> None:
    facts = [_facts(i, units=5, net=1000 * i) for i in range(1, 11)] + [_facts(99, units=2, net=90000)]
    top = profit.winners(facts)
    assert top == {10, 9}  # top 20% of the 11 selling listings by net, with 3+ sales
    assert _klass(facts[-2], top) == profit.WINNER and _klass(facts[0], top) == profit.STEADY


def test_losers_new_listings_and_losses() -> None:
    assert _klass(_facts(long_units=0)) == profit.LOSER
    assert _klass(_facts(long_units=0, age=89)) == profit.NEW
    assert _klass(_facts(long_units=0, age=90)) == profit.LOSER
    assert _klass(_facts(units=2, net=-400)) == profit.LOSER  # sells at a loss


# --- Etsy Ads CSV (C1) ----------------------------------------------------------------

CSV = (
    "Listing,Listing ID,Views,Clicks,Orders,Revenue,Spend\n"
    "Funny Nurse Tee,4001,100,9,1,$25.00,$12.40\n"
    "Teacher Shirt,,50,3,0,$0.00,\"$1,002.50\"\n"
    "Someone Else's Mug,9999999,10,1,0,0,$3.00\n"
    "Idle Listing,4003,5,0,0,0,$0.00\n"
    "Total,,165,13,1,$25.00,\"$1,017.90\"\n"
).encode()


def test_money_dates_and_mapping_guesses() -> None:
    assert ads_csv.parse_money("$1,234.56") == 123456
    assert ads_csv.parse_money("1.234,56 €") == 123456
    assert ads_csv.parse_money("12,5") == 1250 and ads_csv.parse_money("(3.00)") == -300
    assert ads_csv.parse_money("") is None
    assert ads_csv.parse_date("2026-09-01") == date(2026, 9, 1) == ads_csv.parse_date("09/01/2026")
    table = ads_csv.read_table(CSV)
    assert ads_csv.guess_mapping(table.headers) == {
        "listing_id": "Listing ID", "title": "Listing", "date": None,
        "spend": "Spend", "orders": "Orders", "revenue": "Revenue",
    }


def test_rows_match_only_the_sellers_own_listings() -> None:
    table = ads_csv.read_table(CSV)
    mapping = ads_csv.guess_mapping(table.headers)
    period = (TODAY - timedelta(days=29), TODAY)
    titles = ads_csv.own_title_index({4001: "Funny Nurse Tee", 4002: "Teacher  shirt", 4003: "Idle Listing"})
    out = ads_csv.parse_rows(table, mapping, {4001, 4002, 4003}, titles, period)
    assert [(r.listing_id, r.spend_minor, r.ad_orders, r.ad_revenue_minor) for r in out.rows] == [
        (4001, 1240, 1, 2500),
        (4002, 100250, 0, 0),  # matched by title (spacing and case ignored)
    ]
    assert [(u.line, u.why) for u in out.unmatched] == [(4, "not one of your shop's listings")]
    assert out.skipped == 2  # the zero row and the totals row

    # A title two of the seller's listings share matches neither.
    assert ads_csv.own_title_index({1: "Same", 2: "same"}) == {}
    with pytest.raises(ads_csv.CsvError):
        ads_csv.parse_rows(table, {**mapping, "spend": None}, set(), {}, period)
    with pytest.raises(ads_csv.CsvError):
        ads_csv.parse_rows(table, mapping, set(), {}, None)  # no date column and no period


# --- Endpoints --------------------------------------------------------------------------


async def _seed(ctx, *, scopes=("transactions_r",), cache_hours=1.0) -> uuid.UUID:  # noqa: F811
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
        conn = await s.get(EtsyConnection, shop)
        conn.scopes = list(scopes)
        fetched = datetime.now(timezone.utc) - timedelta(hours=cache_hours)
        for lid, title in ((4001, "Funny Nurse Tee"), (4002, "Teacher Shirt"), (4003, "Idle Listing")):
            s.add(ShopListingCache(tenant_id=ctx["tenant_id"], connection_id=shop, listing_id=lid, fetched_at=fetched,
                                   payload={"listing_id": lid, "state": "active", "title": title,
                                            "url": f"https://www.etsy.com/listing/{lid}", "skus": [f"SKU{lid}"],
                                            "original_creation_timestamp": 1_600_000_000}))
        for i in range(6):
            s.add(SalesDaily(connection_id=shop, listing_id=4001, day=TODAY - timedelta(days=i), tenant_id=ctx["tenant_id"],
                             units=1, orders=1, revenue_minor=2500, currency="USD"))
        s.add(SalesDaily(connection_id=shop, listing_id=4002, day=TODAY - timedelta(days=40), tenant_id=ctx["tenant_id"],
                         units=4, orders=4, revenue_minor=8000, currency="USD"))
        await s.commit()
    return shop


async def test_overview_ranks_the_shop_and_hides_expired_titles(ctx) -> None:  # noqa: F811
    await _seed(ctx)
    await ctx["client"].put("/api/analytics/costs", json={"product_cost_by_sku": {"SKU4001": "8"}})
    body = (await ctx["client"].get("/api/analytics/overview?days=30")).json()
    assert body["status"]["can_read_sales"] and body["status"]["currency"] == "USD"
    assert body["current"]["revenue"] == 15000 and body["current"]["units"] == 6
    # $150 - 6.5% - (3% + 6 x $0.25) - 6 x $0.20 - 6 x $8 product cost
    assert body["current"]["net"] == 15000 - 975 - (450 + 150) - 120 - 4800
    assert body["best"][0]["listing_id"] == 4001 and body["best"][0]["verdict"]["klass"] == "winner"
    assert body["best"][0]["title"] == "Funny Nurse Tee" and body["best"][0]["url"].endswith("/4001")

    rows = (await ctx["client"].get("/api/analytics/listings?days=30")).json()["listings"]
    assert {r["listing_id"]: r["verdict"]["klass"] for r in rows} == {4001: "winner", 4002: "fading", 4003: "loser"}
    assert (await ctx["client"].get("/api/analytics/listings?days=14")).status_code == 422


async def test_expired_listing_content_is_not_shown(ctx) -> None:  # noqa: F811
    await _seed(ctx, cache_hours=7)
    rows = (await ctx["client"].get("/api/analytics/listings")).json()
    # Shown by id, with the Etsy link, until the listings are read again.
    assert {r["listing_id"]: (r["title"], r["url"]) for r in rows["listings"]} == {
        4001: (None, "https://www.etsy.com/listing/4001"),
        4002: (None, "https://www.etsy.com/listing/4002"),
    }
    assert rows["status"]["titles_refreshing"]
    assert any(c[0] == "sync_shop_listings" for c in ctx["enqueuer"].calls)


async def test_reading_sales_needs_the_permission(ctx) -> None:  # noqa: F811
    await _seed(ctx, scopes=("listings_r",))
    r = await ctx["client"].post("/api/analytics/sales/refresh")
    assert r.status_code == 409 and "reconnect" in r.json()["detail"]
    await _seed_scope(ctx, ["listings_r", "transactions_r"])
    assert (await ctx["client"].post("/api/analytics/sales/refresh")).status_code == 202
    assert ctx["enqueuer"].calls[-1][0] == "sync_sales"


async def _seed_scope(ctx, scopes) -> None:  # noqa: F811
    async with ctx["sm"]() as s:
        (await s.get(EtsyConnection, await _shop(s, ctx["tenant_id"]))).scopes = scopes
        await s.commit()


async def test_ads_csv_upload_matches_and_replaces(ctx) -> None:  # noqa: F811
    await _seed(ctx)
    preview = (await ctx["client"].post("/api/analytics/ads/preview", files={"file": ("ads.csv", CSV, "text/csv")})).json()
    assert preview["rows"] == 5 and preview["mapping"]["spend"] == "Spend"

    form = {"mapping": json.dumps(preview["mapping"]),
            "period_start": str(TODAY - timedelta(days=29)), "period_end": str(TODAY)}
    first = (await ctx["client"].post("/api/analytics/ads/import", files={"file": ("ads.csv", CSV, "text/csv")}, data=form)).json()
    assert (first["matched"], first["unmatched_total"], first["spend"]) == (2, 1, 1240 + 100250)
    again = (await ctx["client"].post("/api/analytics/ads/import", files={"file": ("ads.csv", CSV, "text/csv")}, data=form)).json()
    assert again["replaced"] == 2  # the same period uploaded twice is not counted twice
    async with ctx["sm"]() as s:
        from sqlalchemy import func, select

        assert await s.scalar(select(func.count()).select_from(AdSpend)) == 2

    uploads = (await ctx["client"].get("/api/analytics/ads/uploads")).json()
    assert len(uploads) == 1 and uploads[0]["listings"] == 2

    # Teacher Shirt: $1,002.50 of ads and no sale in 30 days.
    rows = (await ctx["client"].get("/api/analytics/listings")).json()["listings"]
    sink = next(r for r in rows if r["listing_id"] == 4002)
    assert sink["verdict"]["klass"] == "ad_sink" and "$1,002.50" in sink["verdict"]["reason"]

    # Another account can't remove this upload.
    upload = uploads[0]["upload_id"]
    other = await make_tenant(ctx["sm"], "other@example.com")
    ctx["client"].cookies.clear()
    authenticate(ctx["client"], await open_session(ctx["redis"], other))
    assert (await ctx["client"].delete(f"/api/analytics/ads/uploads/{upload}")).status_code == 404


async def test_listing_detail_has_weeks_and_its_cost_source(ctx) -> None:  # noqa: F811
    await _seed(ctx)
    await ctx["client"].put("/api/analytics/costs", json={"product_cost": "2", "product_cost_by_sku": {"sku4001": "8"}})
    body = (await ctx["client"].get("/api/analytics/listings/4001")).json()
    assert body["unit_cost"] == "8" and body["unit_cost_source"] == "SKU SKU4001"
    assert len(body["weeks"]) == 56 and sum(w["units"] for w in body["weeks"]) == 6
    assert (await ctx["client"].get("/api/analytics/listings/12345")).status_code == 404


async def test_costs_round_trip_and_reject_bad_values(ctx) -> None:  # noqa: F811
    r = await ctx["client"].put("/api/analytics/costs", json={"transaction_pct": "6.5", "monthly_fixed": 20})
    assert r.status_code == 200 and r.json()["monthly_fixed"] == "20"
    assert (await ctx["client"].get("/api/analytics/costs")).json()["transaction_pct"] == "6.5"
    assert (await ctx["client"].put("/api/analytics/costs", json={"payment_pct": "150"})).status_code == 422
    async with ctx["sm"]() as s:
        assert (await s.get(Tenant, ctx["tenant_id"])).cost_settings["monthly_fixed"] == "20"


async def test_best_sellers_come_first_among_pattern_listings(ctx) -> None:  # noqa: F811
    await _seed(ctx)
    async with ctx["sm"]() as s:
        (await s.get(Tenant, ctx["tenant_id"])).features = {"own_patterns": True}
        await s.commit()
    rows = (await ctx["client"].get("/api/shop/pattern-listings")).json()
    assert [(r["listing_id"], r["units_90d"]) for r in rows] == [(4001, 6), (4002, 4), (4003, 0)]
