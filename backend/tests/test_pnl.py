"""The month view: totals from the statement to the cent, orders tied to listings
by order number, Etsy Ads kept at shop level, product cost only as entered,
every figure labelled, and a blank (never a zero) where there is nothing."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import (
    AdsDaily,
    EtsyConnection,
    LedgerDaily,
    ListingProfile,
    SaleLine,
    SalesDaily,
    SalesSync,
    StatementImport,
    StatementListingFee,
    StatementOrder,
)
from app.pipeline import pnl
from app.pipeline.profit import MoneyFormat
from tests.auth_support import authenticate, make_tenant, open_session
from tests.test_publish_api import _shop, ctx  # noqa: F401  (fixture)

TODAY = datetime.now(timezone.utc).date()
#: The month before this one: always inside the 13 kept.
MONTH = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)
BEFORE = (MONTH - timedelta(days=1)).replace(day=1)
money = MoneyFormat("USD")

# Three orders sold in the month, one refunded from an earlier month, one the sales read lacks.
ORDERS = {
    1: {"sales": 5400, "sales_tax": -400, "transaction_fee_items": -260, "transaction_fee_shipping": -33, "processing_fee": -175, "offsite_ads": -150},
    2: {"sales": 2700, "sales_tax": -200, "transaction_fee_items": -163, "processing_fee": -100},
    3: {"refunds": -1500, "sales_tax_refund": 90, "fee_credits": 97},
    4: {"sales": 1100, "sales_tax": -100, "transaction_fee_items": -65, "processing_fee": -55},
}
LISTING_FEES = {501: (-40, 20), 502: (-20, 0)}
SHOP_LEVEL = {"etsy_ads": -800, "shipping": -75, "unrecognised": -11, "other_marketing": -9}


def _totals() -> dict[str, int]:
    totals: dict[str, int] = dict(SHOP_LEVEL)
    for amounts in ORDERS.values():
        for k, v in amounts.items():
            totals[k] = totals.get(k, 0) + v
    for amount, credit in LISTING_FEES.values():
        totals["listing_fee"] = totals.get("listing_fee", 0) + amount
        totals["fee_credits"] = totals.get("fee_credits", 0) + credit
    return totals


def _lines() -> list[pnl.Line]:
    d = MONTH + timedelta(days=4)
    return [
        # Order 1: two listings; the buyer paid 5.00 shipping, all on the first line.
        pnl.Line(1, 501, d, 1, 3000, 500), pnl.Line(1, 502, d, 1, 1500, 0),
        pnl.Line(2, 501, d, 1, 2500, 0),
        # Order 3 was sold the month before; only its refund is in this month.
        pnl.Line(3, 502, BEFORE + timedelta(days=20), 1, 1500, 0),
    ]


def _input(**over) -> pnl.MonthInput:
    return pnl.MonthInput(month=MONTH, statement=_totals(), orders=ORDERS, lines=_lines(),
                          listing_fees={k: a + c for k, (a, c) in LISTING_FEES.items()}, deposits_minor=4000, **over)


def _line(sheet: dict, key: str) -> dict:
    return next(line for s in sheet["sections"] for line in s["lines"] if line["key"] == key)


def test_a_statement_month_adds_up_to_its_net_to_the_cent_and_every_line_says_how() -> None:
    data = _input()
    sheet = pnl.receipt(data)
    net = sum(_totals().values())
    assert sheet["source"] == "statement" and sheet["net_etsy"] == {
        "key": "net_etsy", "minor": net, "basis": "exact", "source": "statement", "note": None, "parts": None}
    etsy_side = [line for s in sheet["sections"] if s["key"] != "your_costs" for line in s["lines"]]
    assert sum(line["minor"] for line in etsy_side) == net  # every category is on exactly one line
    assert all(line["basis"] in ("exact", "calculated") for line in etsy_side)

    # Revenue is after sales tax; the shipping buyers paid is its own line, from the sales read.
    revenue = 5400 - 400 + 2700 - 200 + 1100 - 100
    assert sheet["revenue"]["minor"] == revenue
    assert (_line(sheet, "shipping_paid")["minor"], _line(sheet, "shipping_paid")["basis"], _line(sheet, "shipping_paid")["source"]) == (500, "exact", "sales")
    assert (_line(sheet, "items")["minor"], _line(sheet, "items")["basis"]) == (revenue - 500, "calculated")
    assert "1 of 3 orders are not in the sales read yet" in _line(sheet, "shipping_paid")["note"]
    # Refunds are net of the sales tax Etsy returned, and say both parts.
    assert _line(sheet, "refunds")["minor"] == -1410 and _line(sheet, "refunds")["parts"] == {"refunded": -1500, "tax_returned": 90}
    # Deposits are transfers: beside the receipt, not in it.
    assert sheet["deposits_minor"] == 4000


def test_orders_go_to_listings_by_order_number_and_what_cannot_is_named_with_its_amount() -> None:
    data = _input()
    rows, left = pnl.listing_rows(data)
    a, b = rows[501], rows[502]
    # Order 1 (50.00 after tax) splits 2:1 by item price; its 5.00 shipping too.
    assert (a.units, a.orders, b.units, b.orders) == (2, 2, 1, 1)
    assert (a.revenue, b.revenue) == (3333 + 2500, 1667)
    assert (a.shipping_paid, b.shipping_paid) == (333, 167) and a.items == 5500
    # The refund of an order sold earlier goes to its listing, without counting a sale this month.
    assert b.refunds == -1410 and a.refunds == 0
    # Order-level fees by price share, plus the listing fees the statement ties to the listing itself.
    assert a.fees + b.fees == (-260 - 33 - 175) + (-163 - 100) + 97 + (-40 + 20 - 20)
    assert a.offsite_ads + b.offsite_ads == -150  # billed per order, so it is the order's listings'

    # Everything else is "not attributed": the order the sales read lacks, and shop-level rows.
    assert left["orders"] == 1 and left["orders_minor"] == 1000
    assert left["groups"] == {"revenue": 1000, "fees": -120, "labels": -75, "other": -20}
    # Listings + not attributed + Etsy Ads (shop level, never a listing's) = the statement's net.
    assert sum(r.before_cost for r in rows.values()) + left["total"] + SHOP_LEVEL["etsy_ads"] == sum(_totals().values())
    assert not any(hasattr(r, "etsy_ads") or hasattr(r, "ad_spend") for r in rows.values())


def test_product_cost_is_only_what_was_entered_and_until_then_it_is_profit_before_product_cost() -> None:
    profile_of = {501: "p1", 502: "p2"}
    bare = pnl.receipt(_input(profile_of=profile_of))
    assert bare["profit"]["minor"] is None and bare["complete"] is False
    assert _line(bare, "product_cost")["minor"] is None and "No product cost is entered yet" in _line(bare, "product_cost")["note"]
    assert [i["key"] for i in bare["incomplete"]] == ["product_cost"]
    assert bare["break_even"]["basis"] == "estimated" and "Before product cost" in bare["break_even"]["note"]

    # One profile set: the part that is known is shown, the result still is not.
    some = pnl.ProductCosts.from_stored({"profile_costs": {"p1": {"production": "8.00", "shipping": "4.00"}}})
    partial = pnl.receipt(_input(profile_of=profile_of, costs=some))
    assert _line(partial, "product_cost")["minor"] == -1600 and _line(partial, "provider_shipping")["minor"] == -800
    assert "Set for 2 of 3 items sold" in _line(partial, "product_cost")["note"] and partial["profit"]["minor"] is None

    # A production cost alone is not a cost: shipping must be entered too (0 is an answer, blank is not).
    half = pnl.ProductCosts.from_stored({"profile_costs": {"p1": {"production": "8.00"}, "p2": {"production": "6", "shipping": "0"}}})
    assert pnl.receipt(_input(profile_of=profile_of, costs=half))["profit"]["minor"] is None

    full = pnl.ProductCosts.from_stored({"profile_costs": {
        "p1": {"production": "8.00", "shipping": "4.00"}, "p2": {"production": "6.50", "shipping": "3.75"}}})
    data = _input(profile_of=profile_of, costs=full, ads_spend=800, ads_revenue=2400, ads_days=pnl.MonthInput(month=MONTH).last_day.day)
    done = pnl.receipt(data)
    net = sum(_totals().values())
    assert done["profit"] == {"key": "profit", "minor": net - 2 * 1200 - 1025, "basis": "calculated", "source": "statement", "note": None, "parts": None}
    assert done["complete"] is True and done["incomplete"] == []

    # Break-even ROAS: 1 / (what a unit of sales leaves before Etsy Ads, after fees and product cost).
    margin = (net + 800 - 3425) / 8500
    assert done["break_even"]["roas"] == round(1 / margin, 2) and done["break_even"]["basis"] == "calculated"
    assert done["break_even"]["actual_roas"] == 3.0 and done["break_even"]["actual_note"] is None


def test_a_size_has_its_own_cost_where_one_is_set_and_an_unknown_size_is_said_so() -> None:
    costs = pnl.ProductCosts.from_stored({"profile_costs": {"p1": {"production": "8", "shipping": "4", "sizes": {"2XL": {"production": "10.50"}}}}})
    assert costs.unit("p1", "2xl") == pnl.UnitCost(1050, 400)  # the size's production, the profile's shipping
    assert costs.unit("p1", "M") == pnl.UnitCost(800, 400) and costs.unit("nope", "M") == pnl.UnitCost()
    d = MONTH + timedelta(days=2)
    data = pnl.MonthInput(month=MONTH, statement={"sales": 6000}, orders={1: {"sales": 6000}}, costs=costs, profile_of={501: "p1"},
                          lines=[pnl.Line(1, 501, d, 2, 4000, 0, "2XL"), pnl.Line(1, 501, d, 1, 2000, 0, None)])
    rows, _ = pnl.listing_rows(data)
    assert rows[501].product_cost == 2 * 1050 + 800 and rows[501].size_unknown_units == 1
    assert "1 items were costed at their profile's base cost" in _line(pnl.receipt(data, rows), "product_cost")["note"]
    # Nothing valid entered is "not set", never zero.
    assert pnl.validate_profile_costs({"p1": {"production": "", "shipping": None, "sizes": {}}}) == {}
    try:
        pnl.validate_profile_costs({"p1": {"production": "-1"}})
    except ValueError as exc:
        assert "zero or more" in str(exc)
    else:
        raise AssertionError("a negative cost was accepted")


def test_without_a_statement_the_ledger_and_sales_are_used_and_what_they_lack_is_blank_not_zero() -> None:
    d = MONTH + timedelta(days=3)
    data = pnl.MonthInput(month=MONTH, ledger={"transaction": -400, "payment_processing_fee": -210, "prolist": -300, "listing": -40},
                          lines=[pnl.Line(1, 501, d, 2, 5000, 600)])
    rows, left = pnl.listing_rows(data)
    sheet = pnl.receipt(data, rows)
    assert sheet["source"] == "ledger" and left["total"] is None
    assert (_line(sheet, "items")["minor"], _line(sheet, "items")["basis"]) == (5000, "calculated")
    assert (_line(sheet, "transaction_fee_items")["minor"], _line(sheet, "etsy_ads")["minor"]) == (-400, -300)
    assert _line(sheet, "refunds")["minor"] is None and "Only on the statement" in _line(sheet, "refunds")["note"]
    assert sheet["net_etsy"]["basis"] == "calculated" and "No statement" in sheet["net_etsy"]["note"]
    assert rows[501].before_cost is None  # fees per listing need the statement
    assert [i["key"] for i in sheet["incomplete"]] == ["no_statement", "product_cost"]

    empty = pnl.receipt(pnl.MonthInput(month=MONTH))
    assert empty["source"] == "none" and empty["net_etsy"]["minor"] is None and empty["revenue"]["minor"] is None
    assert all(line["minor"] is None and line["note"] for s in empty["sections"] for line in s["lines"])


def test_each_class_comes_with_its_reason_and_attention_is_ranked_by_the_money_at_stake() -> None:
    end = pnl.MonthInput(month=MONTH).last_day
    win = pnl.ListingRow(1, units=9, revenue=20000, fees=-2500)
    lose = pnl.ListingRow(2, units=4, revenue=4000, fees=-600, product_cost=4200)
    fade = pnl.ListingRow(3, units=2, revenue=3000, fees=-300)
    new = pnl.ListingRow(4, units=1, revenue=1500, fees=-150)
    back = pnl.ListingRow(5, units=3, revenue=6000, refunds=-2000, fees=-500)
    rows = {r.listing_id: r for r in (win, lose, fade, new, back)}
    before = {1: 8, 2: 4, 3: 7, 4: 0, 5: 3}
    top = pnl.winners(rows)
    classes = {i: pnl.classify(r, before[i], end - timedelta(days=10) if i == 4 else end - timedelta(days=400), end, top, money) for i, r in rows.items()}
    assert [classes[i][0] for i in range(1, 6)] == ["winner", "losing", "fading", "new", "steady"]
    assert classes[1][1] == "In the top fifth: $175.00 from 9 sold, after Etsy's fees and product cost."
    assert classes[2][1] == "Lost $8.00 on 4 sold, after Etsy's fees and product cost."
    assert classes[3][1] == "Sold 2 this month, 7 the month before."
    assert classes[4][1] == "Listed 10 days before the month ended: too early to judge."

    data = pnl.MonthInput(month=MONTH, statement={"sales": 34500}, ads_spend=5000, ads_revenue=6000, ads_days=30)
    sheet = {"source": "statement", "break_even": {"roas": 2.5, "margin": 0.4, "actual_roas": 1.2, "basis": "calculated"}}
    items = pnl.attention(data, sheet, rows, classes, before, {"orders": 2, "orders_minor": 900}, money)
    assert len(items) == 5 and [i["kind"] for i in items] == ["fading", "ads", "refunds", "unattributed", "losing"]
    assert [i["stake_minor"] for i in items] == [6750, 2600, 2000, 900, 800]
    assert items[0]["basis"] == "estimated" and items[0]["listing_id"] == 3
    assert "break even at 2.50" in items[1]["why"] and "Shop Manager" in items[1]["do"]
    # Nothing here acts on Etsy: every suggestion is for the seller to do in Shop Manager, or to enter a cost.
    assert all(i["do"] for i in items)


# --- through the API -------------------------------------------------------------------------------------


async def _seed(ctx) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:  # noqa: F811
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
        tid = ctx["tenant_id"]
        profiles = []
        for name, ref in (("Standard Tee", 501), ("Hoodie", 502)):
            p = ListingProfile(tenant_id=tid, connection_id=shop, name=name, reference_listing_id=ref, content_template="apparel",
                               confirmed=True, cached_payload={}, updated_at=datetime.now(timezone.utc))
            s.add(p)
            profiles.append(p)
        s.add(StatementImport(connection_id=shop, month=MONTH, tenant_id=tid, currency="USD", rows=20, first_day=MONTH,
                              last_day=MONTH + timedelta(days=27), net_minor=sum(_totals().values()), totals=_totals(),
                              counts={}, credits={}, deposits=[{"day": MONTH.isoformat(), "minor": 4000}], unrecognised=[], notes=[]))
        for receipt, amounts in ORDERS.items():
            s.add(StatementOrder(connection_id=shop, month=MONTH, receipt_id=receipt, tenant_id=tid, day=MONTH, amounts=amounts))
        for listing, (amount, credit) in LISTING_FEES.items():
            s.add(StatementListingFee(connection_id=shop, month=MONTH, listing_id=listing, tenant_id=tid, fees=1, amount_minor=amount, credits_minor=credit))
        for n, line in enumerate(_lines()):
            s.add(SaleLine(connection_id=shop, transaction_id=900 + n, tenant_id=tid, receipt_id=line.receipt_id, listing_id=line.listing_id,
                           day=line.day, quantity=line.quantity, price_minor=line.price_minor, shipping_minor=line.shipping_minor, currency="USD"))
        # The daily totals behind the trend: 502 sold 6 the month before, 1 this month.
        s.add_all([
            SalesDaily(connection_id=shop, listing_id=501, day=MONTH + timedelta(days=4), tenant_id=tid, units=2, orders=2, revenue_minor=5500, currency="USD"),
            SalesDaily(connection_id=shop, listing_id=502, day=MONTH + timedelta(days=4), tenant_id=tid, units=1, orders=1, revenue_minor=1500, currency="USD"),
            SalesDaily(connection_id=shop, listing_id=502, day=BEFORE + timedelta(days=9), tenant_id=tid, units=6, orders=6, revenue_minor=9000, currency="USD"),
        ])
        s.add(SalesSync(connection_id=shop, tenant_id=tid, state="complete", has_lines=True))
        s.add(AdsDaily(connection_id=shop, day=MONTH + timedelta(days=1), tenant_id=tid, views=900, clicks=40, orders=3, revenue_minor=2400, spend_minor=800))
        await s.commit()
        return shop, profiles[0].id, profiles[1].id


async def test_the_month_view_gives_the_receipt_its_comparisons_the_listings_and_what_needs_attention(ctx) -> None:  # noqa: F811
    shop, tee, hoodie = await _seed(ctx)
    res = await ctx["client"].get("/api/analytics/month")
    assert res.status_code == 200, res.text
    view = res.json()
    # With no month asked for, it opens on the newest month that has a statement.
    assert view["month"] == MONTH.isoformat() and view["currency"] == "USD"
    assert len(view["months"]) == 13 and [m["month"] for m in view["months"] if m["statement"]] == [MONTH.isoformat()]
    sheet = view["sheet"]
    assert sheet["source"] == "statement" and sheet["net_etsy"]["minor"] == sum(_totals().values())
    assert sheet["profit"]["minor"] is None and [i["key"] for i in sheet["incomplete"]] == ["product_cost"]

    # Beside it: last month (sales only, nothing imported) and the same month last year (nothing at all).
    assert view["last_month"]["source"] == "sales" and view["last_month"]["net_etsy"]["minor"] is None
    assert view["last_year"]["source"] == "none" and view["last_year"]["revenue"]["minor"] is None

    rows = {r["listing_id"]: r for r in view["listings"]}
    assert set(rows) == {501, 502}
    assert (rows[501]["units"], rows[501]["revenue_minor"], rows[501]["shipping_paid_minor"], rows[501]["profile_name"]) == (2, 5833, 333, "Standard Tee")
    assert rows[502]["class"] == "fading" and rows[502]["reason"] == "Sold 1 this month, 6 the month before."
    assert rows[502]["trend"][-2:] == [6, 1] and rows[502]["units_before"] == 6
    assert rows[501]["result_minor"] is None and rows[501]["before_cost_minor"] is not None and rows[501]["costed"] is False
    assert all(r["url"].startswith("https://www.etsy.com/") for r in rows.values())  # the back link, always
    assert view["unattributed"]["orders"] == 1 and view["unattributed"]["etsy_ads_minor"] == -800
    kinds = [i["kind"] for i in view["attention"]]
    assert "product_cost" in kinds and "unattributed" in kinds and "statement" not in kinds

    # Product costs: per profile, for the caller's own profiles only.
    costs = (await ctx["client"].get("/api/analytics/product-costs")).json()
    assert {p["name"]: (p["production"], p["shipping"]) for p in costs["profiles"] if p["name"] in ("Standard Tee", "Hoodie")} == {
        "Standard Tee": (None, None), "Hoodie": (None, None)}
    saved = await ctx["client"].put("/api/analytics/product-costs", json={"profiles": {
        str(tee): {"production": "8.00", "shipping": "4"}, str(hoodie): {"production": "6.50", "shipping": "3.75"}}})
    assert saved.status_code == 200, saved.text
    assert (await ctx["client"].put("/api/analytics/product-costs", json={"profiles": {str(tee): {"production": "-3"}}})).status_code == 422
    assert (await ctx["client"].put("/api/analytics/product-costs", json={"profiles": {str(uuid.uuid4()): {"production": "1"}}})).status_code == 404

    view = (await ctx["client"].get(f"/api/analytics/month?month={MONTH:%Y-%m}")).json()
    sheet = view["sheet"]
    assert sheet["profit"]["minor"] == sum(_totals().values()) - 2 * 1200 - 1025 and sheet["profit"]["basis"] == "calculated"
    assert sheet["complete"] is True and sheet["break_even"]["roas"] is not None and sheet["break_even"]["actual_roas"] == 3.0
    rows = {r["listing_id"]: r for r in view["listings"]}
    assert rows[501]["product_cost_minor"] == -2400 and rows[501]["result_minor"] == rows[501]["before_cost_minor"] - 2400
    assert rows[501]["per_unit_minor"] == round(rows[501]["result_minor"] / 2)
    # Saving the fee rates (the older settings) does not drop the product costs.
    assert (await ctx["client"].put("/api/analytics/costs", json={})).status_code == 200
    assert (await ctx["client"].get("/api/analytics/month")).json()["sheet"]["profit"]["minor"] == sheet["profit"]["minor"]

    assert (await ctx["client"].get("/api/analytics/month?month=2019-01")).status_code == 422
    assert (await ctx["client"].get("/api/analytics/month?month=soon")).status_code == 422


async def test_another_account_sees_none_of_it(ctx) -> None:  # noqa: F811
    shop, tee, _ = await _seed(ctx)
    other = await make_tenant(ctx["sm"], "other@example.com")
    async with ctx["sm"]() as s:
        from app.db.models import ConnectionStatus

        s.add(EtsyConnection(tenant_id=other, status=ConnectionStatus.active, etsy_user_id=4242, shop_id=4242))
        await s.commit()
    client = ctx["client"]
    mine = client.cookies.get("session")
    authenticate(client, await open_session(ctx["redis"], other))
    try:
        assert (await client.get(f"/api/analytics/month?shop={shop}")).status_code == 404
        theirs = (await client.get("/api/analytics/month")).json()
        assert theirs["sheet"]["source"] == "none" and theirs["listings"] == [] and theirs["attention"][0]["kind"] == "statement"
        body = {"profiles": {str(tee): {"production": "1", "shipping": "1"}}}
        assert (await client.put(f"/api/analytics/product-costs?shop={shop}", json=body)).status_code == 404
        assert (await client.put("/api/analytics/product-costs", json=body)).status_code == 404
        assert (await client.get("/api/analytics/product-costs")).json()["profiles"] == []
    finally:
        client.cookies.set("session", mine)


def test_the_ledger_types_a_month_without_a_statement_reads_are_ones_the_ledger_reader_knows() -> None:
    from app.pipeline.ledger import CATEGORIES

    for kind in ("transaction", "transaction_quantity", "shipping_transaction", "payment_processing_fee", "prolist", "offsite_ads_fee", "shipping_labels", "postage"):
        assert kind in CATEGORIES
    assert LedgerDaily.__tablename__ == "ledger_daily"
