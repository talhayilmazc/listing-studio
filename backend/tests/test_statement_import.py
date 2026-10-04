"""Import from Etsy: the statement and the Ads report are read in memory, stored
as totals and per-order amounts, and shown beside the app's own calculation
with every difference and its reason. Synthetic files only."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select

from app.db.models import (
    AdCharge,
    AdsDaily,
    LedgerDaily,
    SaleLine,
    SalesDaily,
    SalesSync,
    StatementImport,
    StatementListingFee,
    StatementOrder,
)
from app.pipeline import attribution
from app.pipeline import sales as sales_rules
from app.workers import sales as sales_worker
from app.workers.retention import purge_expired_rows, purge_shop_etsy_content
from tests.auth_support import authenticate, make_tenant, open_session
from tests.test_publish_api import _shop, ctx  # noqa: F401  (fixture)

FIXTURES = Path(__file__).parent / "fixtures"
STATEMENT = (FIXTURES / "etsy_statement_synthetic.csv").read_bytes()
ADS = (FIXTURES / "etsy_ads_synthetic.csv").read_bytes()
SEP = date(2031, 9, 1)


async def _upload(ctx, kind: str, data: bytes, name: str = "file.csv"):  # noqa: F811
    return await ctx["client"].post(f"/api/analytics/import/{kind}", files={"file": (name, data, "text/csv")})


async def _count(ctx, model) -> int:  # noqa: F811
    async with ctx["sm"]() as s:
        return int(await s.scalar(select(func.count()).select_from(model)) or 0)


async def test_a_statement_is_read_into_totals_and_only_those_are_kept(ctx) -> None:  # noqa: F811
    res = await _upload(ctx, "statement", STATEMENT)
    assert res.status_code == 200, res.text
    body = res.json()
    st = body["statement"]
    assert (body["month"], st["rows"], st["first_day"], st["last_day"], st["currency"]) == ("2031-09-01", 67, "2031-09-01", "2031-09-30", "USD")
    by = {c["key"]: c for c in st["categories"]}
    assert (by["sales"]["minor"], by["sales"]["rows"], by["sales"]["group"]) == (133174, 4, "sales")
    assert (by["etsy_ads"]["minor"], by["etsy_ads"]["rows"]) == (-40561, 30)
    assert by["fee_credits"]["minor"] == 316 and by["shipping"]["minor"] == -115
    # Every row is in one category, and they add up to the file's net total.
    assert st["net_minor"] == 61453 == sum(c["minor"] for c in st["categories"])
    # Revenue is never the raw sales figure: sales tax and the state fee are taken out.
    assert st["revenue_minor"] == 129200 and by["sales"]["minor"] != st["revenue_minor"]
    # Refunds without the tax that went back with them, and the two parts for the tooltip.
    assert (st["refunds_net_minor"], st["refunded_minor"], st["tax_returned_minor"]) == (-2500, -2650, 150)
    assert (st["etsy_fees_minor"], st["ads_minor"], st["shipping_minor"], st["pass_through_minor"]) == (-12271, -52861, -115, -3824)
    assert st["revenue_minor"] + st["refunds_net_minor"] + st["etsy_fees_minor"] + st["ads_minor"] + st["shipping_minor"] == st["net_minor"]
    # Deposits are transfers to the bank: shown, not counted.
    assert st["deposits_minor"] == 109025 and len(st["deposits"]) == 2 and "deposits" not in by
    assert st["unrecognised"] == [] and st["notes"] == []
    assert body["listing_fees"] == {"listings": 2, "fees": 3, "minor": -60, "credits_minor": 20}

    # What is stored: one row of totals, the per-order and per-listing amounts, the ad charges. No file.
    assert (await _count(ctx, StatementImport), await _count(ctx, StatementOrder), await _count(ctx, StatementListingFee), await _count(ctx, AdCharge)) == (1, 5, 2, 30)
    async with ctx["sm"]() as s:
        order = (await s.execute(select(StatementOrder).where(StatementOrder.receipt_id == 9000000002))).scalar_one()
        assert order.day == date(2031, 9, 10) and order.amounts["transaction_fee_items"] == -7800
        # Ids, a day and amounts: nothing else is on the row (no titles, no buyer).
        assert {c.name for c in StatementOrder.__table__.columns} == {"connection_id", "month", "receipt_id", "tenant_id", "day", "amounts"}
        charge = (await s.execute(select(AdCharge).order_by(AdCharge.click_day))).scalars().first()
        assert (charge.click_day, charge.posted, charge.amount_minor) == (date(2031, 8, 31), date(2031, 9, 1), 777)

    # Importing the month again replaces it.
    assert (await _upload(ctx, "statement", STATEMENT)).status_code == 200
    assert (await _count(ctx, StatementImport), await _count(ctx, StatementOrder), await _count(ctx, AdCharge)) == (1, 5, 30)


async def test_with_nothing_of_our_own_yet_every_line_says_so_instead_of_showing_zero(ctx) -> None:  # noqa: F811
    body = (await _upload(ctx, "statement", STATEMENT)).json()
    lines = {c["key"]: c for c in body["comparison"]}
    assert set(lines) >= {"revenue", "listing_fees", "transaction_fees", "processing_fees", "ads", "shipping_labels", "fee_tax", "refunds"}
    for key in ("revenue", "listing_fees", "transaction_fees", "processing_fees", "ads", "shipping_labels"):
        assert (lines[key]["ours_minor"], lines[key]["difference_minor"], lines[key]["status"]) == (None, None, "statement_only"), key
    assert "ledger has not been read" in lines["ads"]["reason"]
    assert "Sales have not been read" in lines["revenue"]["reason"]
    assert lines["refunds"]["statement_minor"] == -2500 and "not read from Etsy" in lines["refunds"]["reason"]
    assert lines["fee_tax"]["statement_minor"] == -5
    assert (body["orders"]["orders"], body["orders"]["matched"], body["orders"]["unmatched"]) == (4, 0, 4)
    # Only what Etsy charged is known for ads until the report is imported.
    assert (body["ads"]["charged_minor"], body["ads"]["reported_minor"], body["ads"]["exact"]) == (40561, None, None)
    assert "Ads report for this month is not imported" in body["ads"]["note"]


async def test_the_ads_report_is_shop_level_and_squares_with_the_statement_per_click_day(ctx) -> None:  # noqa: F811
    first = await _upload(ctx, "ads", ADS)
    assert first.status_code == 200, first.text
    (month,) = first.json()
    ads = month["ads"]
    assert (month["month"], ads["report_days"], ads["month_days"], ads["reported_minor"], ads["report_orders"], ads["clicks"], ads["views"]) == (
        "2031-09-01", 30, 30, 41625, 30, 1065, 34650,
    )
    assert ads["charged_minor"] is None and "statement for this month is not imported" in ads["note"]
    assert await _count(ctx, AdsDaily) == 30
    async with ctx["sm"]() as s:
        # One row a day for the whole shop: there is no listing to tie it to.
        assert "listing_id" not in {c.name for c in AdsDaily.__table__.columns}

    month = (await _upload(ctx, "statement", STATEMENT)).json()
    ads = month["ads"]
    # "Ad spend for clicks this month" and "Charged by Etsy this month".
    assert (ads["reported_minor"], ads["charged_minor"], ads["matched_days"], ads["exact"]) == (41625, 40561, 28, True)
    assert [(d["day"], d["reported_minor"], d["charged_minor"]) for d in ads["billed_later"]] == [("2031-09-30", 1750, None)]
    assert [(d["day"], d["reported_minor"], d["charged_minor"]) for d in ads["billed_from_before"]] == [("2031-08-31", None, 777)]
    assert [(d["day"], d["reported_minor"], d["charged_minor"]) for d in ads["billed_differently"]] == [("2031-09-16", 1400, 1309)]
    assert "billed the next day" in ads["note"]
    # 40,561 = 41,625 - 1,750 + 777 - 91
    assert ads["charged_minor"] == ads["reported_minor"] - 1750 + 777 - 91

    status = (await ctx["client"].get("/api/analytics/import/status")).json()
    sep = next(m for m in status["months"] if m["month"] == "2031-09-01")
    assert (sep["state"], sep["statement_rows"], sep["statement_net_minor"], sep["ads_days"], sep["month_days"]) == ("complete", 67, 61453, 30, 30)
    assert len(status["months"]) == 14 and status["months"][0]["month"] == "2031-09-01"
    assert all(m["state"] == "nothing" for m in status["months"][1:])
    again = (await ctx["client"].get("/api/analytics/import/months/2031-09")).json()
    assert again["ads"] == ads and again["statement"]["net_minor"] == 61453

    # Importing the same days again replaces them; removing the month removes both files' data.
    assert (await _upload(ctx, "ads", ADS)).status_code == 200 and await _count(ctx, AdsDaily) == 30
    assert (await ctx["client"].delete("/api/analytics/import/months/2031-09")).status_code == 204
    assert (await _count(ctx, AdsDaily), await _count(ctx, StatementImport), await _count(ctx, StatementOrder), await _count(ctx, AdCharge)) == (0, 0, 0, 0)


async def _lines(ctx, rows: list[tuple[int, int, int, int, int]]) -> None:  # noqa: F811
    """(transaction, receipt, listing, price, shipping) as the sales read stores them."""
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
        for transaction_id, receipt_id, listing_id, price, shipping in rows:
            s.add(SaleLine(connection_id=shop, transaction_id=transaction_id, tenant_id=ctx["tenant_id"], receipt_id=receipt_id,
                           listing_id=listing_id, day=date(2031, 9, 3), quantity=1, price_minor=price, shipping_minor=shipping, currency="USD"))
        await s.commit()


async def test_orders_are_tied_to_listings_by_receipt_id_and_the_two_sources_are_checked(ctx) -> None:  # noqa: F811
    await _upload(ctx, "statement", STATEMENT)
    # Three of the four orders are in the sales read; each agrees to the cent (items + shipping).
    await _lines(ctx, [
        (1, 9000000001, 501, 4000, 900),  # 52.37 paid, 3.37 of it sales tax
        (2, 9000000002, 502, 80000, 0), (3, 9000000002, 503, 40000, 0),  # two items
        (4, 9000000003, 501, 2500, 0),  # 26.81 paid: 1.50 tax and a 0.31 state fee
    ])
    body = (await ctx["client"].get("/api/analytics/import/months/2031-09")).json()
    o = body["orders"]
    assert (o["orders"], o["matched"], o["unmatched"], o["exact"], o["differing"]) == (4, 3, 1, 3, 0)
    assert (o["statement_minor"], o["items_minor"], o["shipping_minor"], o["difference_minor"], o["unmatched_minor"]) == (127400, 126500, 900, 0, 1800)
    revenue = next(c for c in body["comparison"] if c["key"] == "revenue")
    # The whole difference is the order the sales read lacks: a stated cause.
    assert (revenue["statement_minor"], revenue["ours_minor"], revenue["difference_minor"], revenue["status"]) == (129200, 127400, 1800, "explained")
    assert "1 of 4 orders (18.00) are not in the sales read" in revenue["reason"]

    # The fourth order: the buyer paid 18.00, the sales read says 15.00 + 5.00.
    await _lines(ctx, [(5, 9000000004, 504, 1500, 500)])
    body = (await ctx["client"].get("/api/analytics/import/months/2031-09")).json()
    o = body["orders"]
    assert (o["matched"], o["exact"], o["differing"], o["difference_minor"]) == (4, 3, 1, -200)
    assert o["largest"] == [{"receipt_id": 9000000004, "statement_minor": 1800, "items_minor": 1500, "shipping_minor": 500, "difference_minor": -200}]
    revenue = next(c for c in body["comparison"] if c["key"] == "revenue")
    # Reported, not hidden; the cause is only a likely one, and the line says so.
    assert (revenue["difference_minor"], revenue["status"]) == (-200, "unexplained") and "discounts are the usual reason" in revenue["reason"]


def test_an_orders_amounts_are_split_by_each_items_share_of_the_price_to_the_cent() -> None:
    assert attribution.split(-3729, [80000, 40000]) == [-2486, -1243]
    assert attribution.split(100, [1, 1, 1]) == [34, 33, 33] and sum(attribution.split(100, [1, 1, 1])) == 100
    assert attribution.split(-1, [3, 3]) == [-1, 0]
    assert attribution.split(500, [0, 0]) == [250, 250]  # no prices to go by: evenly
    assert attribution.split(7, []) == [] and attribution.split(0, [5, 5]) == [0, 0]
    for amount in (-3729, 1, 999, -10001):
        for weights in ([1, 2, 3], [80000, 40000, 1], [7]):
            assert sum(attribution.split(amount, weights)) == amount
    line = lambda listing, price: SimpleNamespace(listing_id=listing, price_minor=price, shipping_minor=0, quantity=1)  # noqa: E731
    # The synthetic two-item order: 800.00 and 400.00.
    amounts = {"sales": 123456, "transaction_fee_items": -7800, "processing_fee": -3729, "sales_tax": -3456, "offsite_ads": -14400, "offsite_ads_credit": 2400}
    out = attribution.attribute(amounts, [line(502, 80000), line(503, 40000)])
    assert out[502] == {"sales": 82304, "transaction_fee_items": -5200, "processing_fee": -2486, "sales_tax": -2304, "offsite_ads": -9600, "offsite_ads_credit": 1600}
    for category, whole in amounts.items():
        assert out[502][category] + out[503][category] == whole
    # Two lines of one listing are that listing's.
    assert attribution.attribute({"sales": 300}, [line(1, 100), line(1, 200)]) == {1: {"sales": 300}}
    assert attribution.attribute({"sales": 300}, []) == {}


async def test_fees_are_set_beside_the_ledger_and_a_difference_is_named_not_smoothed(ctx) -> None:  # noqa: F811
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
        for kind, amount in (("listing", -40), ("transaction", -8236), ("payment_processing_fee", -3900), ("prolist", -40561),
                             ("offsite_ads_fee", -12300), ("sales_tax", -99999)):
            s.add(LedgerDaily(connection_id=shop, day=date(2031, 9, 12), ledger_type=kind, tenant_id=ctx["tenant_id"], amount_minor=amount, entries=1, currency="USD"))
        # The month before: not this statement's days.
        s.add(LedgerDaily(connection_id=shop, day=date(2031, 8, 31), ledger_type="listing", tenant_id=ctx["tenant_id"], amount_minor=-500, entries=1, currency="USD"))
        await s.commit()
    body = (await _upload(ctx, "statement", STATEMENT)).json()
    lines = {c["key"]: c for c in body["comparison"]}
    # Listing fees less their credit, transaction fees less theirs, and ads: the ledger agrees to the cent.
    for key, amount in (("listing_fees", -40), ("transaction_fees", -8236), ("ads", -52861)):
        assert (lines[key]["statement_minor"], lines[key]["ours_minor"], lines[key]["status"], lines[key]["ours_source"]) == (amount, amount, "match", "Etsy ledger (API)"), key
    # Processing differs by 0.90: shown with its size and an honest reason.
    p = lines["processing_fees"]
    assert (p["statement_minor"], p["ours_minor"], p["difference_minor"], p["status"]) == (-3990, -3900, -90, "unexplained")
    assert p["reason"].startswith("Likely cause:") and p["reason"].endswith("Not established.")
    # A type the ledger reading does not count is not our figure for anything.
    assert lines["shipping_labels"]["ours_minor"] == 0 and lines["shipping_labels"]["status"] == "unexplained"
    assert lines["fee_tax"]["ours_minor"] is None


async def test_files_that_are_not_what_they_should_be_are_refused_and_nothing_is_stored(ctx) -> None:  # noqa: F811
    wrong = await _upload(ctx, "statement", ADS)
    assert wrong.status_code == 422 and "not an Etsy monthly statement" in wrong.json()["detail"]
    wrong = await _upload(ctx, "ads", STATEMENT)
    assert wrong.status_code == 422 and "not an Etsy Ads report" in wrong.json()["detail"]
    two_months = STATEMENT.decode("utf-8-sig").replace('"September 30, 2031",Sale', '"October 1, 2031",Sale', 1).encode()
    wrong = await _upload(ctx, "statement", two_months)
    assert wrong.status_code == 422 and "a monthly statement covers one month" in wrong.json()["detail"]
    assert (await _upload(ctx, "statement", b"\xff\xfe\x00\x00")).status_code == 422
    assert (await _upload(ctx, "statement", b"x" * (8 * 1024 * 1024 + 1))).status_code == 413
    assert (await ctx["client"].get("/api/analytics/import/months/september")).status_code == 422
    assert (await _count(ctx, StatementImport), await _count(ctx, StatementOrder), await _count(ctx, AdsDaily)) == (0, 0, 0)


async def test_another_account_cannot_read_or_write_a_shops_imports(ctx) -> None:  # noqa: F811
    await _upload(ctx, "statement", STATEMENT)
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
    other = await make_tenant(ctx["sm"], "other@example.com")
    client = ctx["client"]
    mine = client.cookies.get("session")
    authenticate(client, await open_session(ctx["redis"], other))
    try:
        assert (await client.get(f"/api/analytics/import/months/2031-09?shop={shop}")).status_code == 404
        assert (await client.get(f"/api/analytics/import/status?shop={shop}")).status_code == 404
        assert (await client.delete(f"/api/analytics/import/months/2031-09?shop={shop}")).status_code == 404
        assert (await client.post(f"/api/analytics/import/statement?shop={shop}", files={"file": ("f.csv", STATEMENT, "text/csv")})).status_code == 404
        # With no shop of their own there is nothing to import into either.
        assert (await client.post("/api/analytics/import/statement", files={"file": ("f.csv", STATEMENT, "text/csv")})).status_code == 409
    finally:
        client.cookies.set("session", mine)
    assert await _count(ctx, StatementImport) == 1


async def test_imports_are_kept_13_months_and_go_with_the_shop(ctx) -> None:  # noqa: F811
    await _upload(ctx, "statement", STATEMENT)
    await _upload(ctx, "ads", ADS)
    await _lines(ctx, [(1, 9000000001, 501, 4000, 900)])
    counts = lambda: [_count(ctx, m) for m in (StatementImport, StatementOrder, StatementListingFee, AdCharge, AdsDaily, SaleLine)]  # noqa: E731
    async with ctx["sm"]() as s:
        await purge_expired_rows(s, now=datetime(2032, 9, 20, tzinfo=timezone.utc))  # twelve months on: still there
        await s.commit()
    assert [await c for c in counts()] == [1, 5, 2, 30, 30, 1]
    async with ctx["sm"]() as s:
        await purge_expired_rows(s, now=datetime(2032, 12, 1, tzinfo=timezone.utc))  # past 13 months
        await s.commit()
    assert [await c for c in counts()] == [0, 0, 0, 0, 0, 0]

    await _upload(ctx, "statement", STATEMENT)
    await _upload(ctx, "ads", ADS)
    await _lines(ctx, [(1, 9000000001, 501, 4000, 900)])
    async with ctx["sm"]() as s:
        await purge_shop_etsy_content(s, await _shop(s, ctx["tenant_id"]))
        await s.commit()
    assert [await c for c in counts()] == [0, 0, 0, 0, 0, 0]


def _transaction(transaction_id: int, receipt_id: int, listing_id: int, when: datetime, *, price: int, shipping: int, qty: int = 1) -> dict:
    return {
        "transaction_id": transaction_id, "receipt_id": receipt_id, "listing_id": listing_id, "quantity": qty,
        "created_timestamp": int(when.timestamp()),
        "price": {"amount": price, "divisor": 100, "currency_code": "USD"},
        "shipping_cost": {"amount": shipping, "divisor": 100, "currency_code": "USD"},
        # What Etsy also sends, and nothing here reads:
        "buyer_user_id": 424242, "title": "A title", "variations": [{"formatted_name": "Size"}], "buyer_coupon": 0.0,
    }


def test_an_order_line_is_ids_a_day_and_amounts_and_nothing_about_the_buyer() -> None:
    when = datetime(2031, 9, 3, 12, tzinfo=timezone.utc)
    out = sales_rules.lines([
        _transaction(1, 9000000001, 501, when, price=2000, shipping=900, qty=2),
        {**_transaction(2, 9000000002, 502, when, price=1000, shipping=0), "receipt_id": None},  # no order number: cannot be tied
        _transaction(3, 9000000003, 503, when - timedelta(days=500), price=1000, shipping=0),  # before the window
    ], since=date(2031, 1, 1))
    assert len(out) == 1
    line = out[0]
    assert (line.transaction_id, line.receipt_id, line.listing_id, line.day, line.quantity, line.price_minor, line.shipping_minor, line.currency) == (
        1, 9000000001, 501, date(2031, 9, 3), 2, 4000, 900, "USD",
    )
    assert set(vars(line)) == {"transaction_id", "receipt_id", "listing_id", "day", "quantity", "price_minor", "shipping_minor", "currency"}
    assert {c.name for c in SaleLine.__table__.columns} == {
        "connection_id", "transaction_id", "tenant_id", "receipt_id", "listing_id", "day", "quantity", "price_minor", "shipping_minor", "currency",
    }


async def test_the_sales_read_keeps_order_lines_and_reads_once_more_where_it_had_none(ctx) -> None:  # noqa: F811
    when = datetime.now(timezone.utc) - timedelta(days=3)
    async with ctx["sm"]() as s:
        from app.db.models import EtsyConnection

        connection = await s.get(EtsyConnection, await _shop(s, ctx["tenant_id"]))
        sales = [_transaction(1, 77, 501, when, price=2000, shipping=500), _transaction(2, 77, 502, when, price=1000, shipping=0)]
        await sales_worker._add_totals(s, connection, sales, when.date() - timedelta(days=30))
        await sales_worker._add_totals(s, connection, sales[:1], when.date() - timedelta(days=30))  # a page seen twice
        await s.commit()
        lines = (await s.execute(select(SaleLine).order_by(SaleLine.transaction_id))).scalars().all()
        assert [(line.receipt_id, line.listing_id, line.price_minor, line.shipping_minor) for line in lines] == [(77, 501, 2000, 500), (77, 502, 1000, 0)]

        # A shop read before lines were kept: the nightly round starts its read again, once.
        connection.scopes = ["transactions_r"]
        s.add(SalesSync(connection_id=connection.id, tenant_id=ctx["tenant_id"], state="complete", has_lines=False,
                        start_offset=0, next_offset=40, read_count=40, updated_at=datetime.now(timezone.utc)))
        await s.commit()

    queued: list[tuple] = []

    async def enqueue(ctx_, function, *args, **options):  # noqa: ANN001
        queued.append((function, args))

    original = sales_worker._enqueue_job
    sales_worker._enqueue_job = enqueue
    try:
        assert await sales_worker.sync_all_sales({"sessionmaker": ctx["sm"]}) == 1
        async with ctx["sm"]() as s:
            sync = (await s.execute(select(SalesSync))).scalar_one()
            assert (sync.state, sync.has_lines, sync.next_offset, sync.read_count) == ("reading", True, 0, 0)
            assert "tie each order to its listings" in sync.note
            # The read starts clean: the totals and the lines it rebuilds.
            assert (await s.scalar(select(func.count()).select_from(SaleLine)), await s.scalar(select(func.count()).select_from(SalesDaily))) == (0, 0)
        assert queued[0][0] == "sync_sales"
    finally:
        sales_worker._enqueue_job = original
