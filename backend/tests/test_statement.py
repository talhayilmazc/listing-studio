"""Etsy's monthly statement and Ads report, read into exact totals.

The fixtures are synthetic: the structure and every edge case of the real files
(byte-order mark, "--", HTML entities, quoted commas, a refund with its credits,
deposits, the previous month's last ad day, a day billed differently), with
invented orders, listings and amounts.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from app.pipeline import ads_report, statement
from app.pipeline.statement import StatementError

FIXTURES = Path(__file__).parent / "fixtures"
STATEMENT = (FIXTURES / "etsy_statement_synthetic.csv").read_bytes()
ADS = (FIXTURES / "etsy_ads_synthetic.csv").read_bytes()

HEADER = "Date,Type,Title,Info,Currency,Amount,Fees & Taxes,Net,Tax Details\n"


def test_every_row_lands_in_one_category_and_they_add_up_to_the_net_total() -> None:
    assert STATEMENT[:3] == b"\xef\xbb\xbf"  # as Etsy writes it
    s = statement.parse(STATEMENT)
    assert (s.rows, s.first_day, s.last_day, s.currency) == (67, date(2031, 9, 1), date(2031, 9, 30), "USD")
    assert s.totals == {
        "sales": D("1331.74"),
        "sales_tax": D("-39.43"),
        "sales_tax_refund": D("1.50"),
        "buyer_fees": D("-0.31"),
        "refunds": D("-26.50"),
        "transaction_fee_items": D("-83.40"),
        "transaction_fee_shipping": D("-0.92"),
        "processing_fee": D("-40.90"),
        "listing_fee": D("-0.60"),
        "fee_credits": D("3.16"),
        "transaction_tax": D("-0.10"),
        "transaction_tax_credit": D("0.05"),
        "etsy_ads": D("-405.61"),
        "offsite_ads": D("-147.00"),
        "offsite_ads_credit": D("24.00"),
        "shipping": D("-1.15"),
    }
    assert s.net_total == D("614.53") == sum(s.totals.values())
    assert set(s.totals) <= set(statement.CATEGORIES)
    assert s.counts["sales"] == s.sale_orders == 4
    assert (s.counts["transaction_fee_items"], s.counts["listing_fee"], s.counts["etsy_ads"], s.counts["transaction_tax"]) == (5, 3, 30, 2)
    assert s.unrecognised == [] and s.notes == []


def test_revenue_is_never_the_raw_sales_figure() -> None:
    s = statement.parse(STATEMENT)
    # What buyers paid includes the sales tax and the state fee collected from them.
    assert s.total("sales") == D("1331.74")
    assert s.revenue == D("1292.00")  # 1331.74 - 39.43 - 0.31
    assert s.refunds_net == D("-25.00")  # the refund, less the sales tax that went back with it
    assert s.group("pass_through") == D("-38.24")
    assert s.group("etsy_fees") == D("-122.71") and s.group("ads") == D("-528.61") and s.group("shipping") == D("-1.15")
    assert s.group("sales") + s.group("pass_through") + s.group("refunds") + s.group("etsy_fees") + s.group("ads") + s.group("shipping") + s.group("other") == s.net_total


def test_deposits_are_transfers_not_income_or_cost() -> None:
    s = statement.parse(STATEMENT)
    assert s.deposits == [(date(2031, 9, 21), D("1050.25")), (date(2031, 9, 7), D("40.00"))]
    assert s.deposits_total == D("1090.25")
    assert "deposits" not in s.totals and s.net_total == D("614.53")


def test_orders_carry_what_attribution_needs_and_nothing_about_a_buyer() -> None:
    s = statement.parse(STATEMENT)
    assert set(s.orders) == {9000000001, 9000000002, 9000000003, 9000000004, 8999999999}
    two_items = s.orders[9000000002]
    assert two_items.day == date(2031, 9, 10)
    # Two items: their two transaction fees are one figure per order; splitting it
    # by item needs the receipt, never the truncated titles.
    assert two_items.amounts == {
        "sales": D("1234.56"), "transaction_fee_items": D("-78.00"), "processing_fee": D("-37.29"),
        "sales_tax": D("-34.56"), "offsite_ads": D("-144.00"), "offsite_ads_credit": D("24.00"),
    }
    refunded = s.orders[9000000003]
    assert refunded.amounts["refunds"] == D("-26.50") and refunded.amounts["sales_tax_refund"] == D("1.50")
    assert refunded.amounts["fee_credits"] == D("2.63") and refunded.amounts["buyer_fees"] == D("-0.31")
    assert sum(refunded.amounts.values()) == D("0.05")  # the state fee stays collected; a little fee tax comes back
    # Sold the month before: only its Offsite Ads fee is here, and no sale day.
    earlier = s.orders[8999999999]
    assert earlier.day is None and earlier.amounts == {"offsite_ads": D("-3.00")}
    # Nothing but ids, a day and amounts is kept: no title text reaches an order.
    assert set(vars(two_items)) == {"receipt_id", "day", "amounts"}


def test_listing_fees_are_tied_to_the_listing_id_etsy_gives() -> None:
    s = statement.parse(STATEMENT)
    assert s.listing_fees == {7000000001: [2, D("-0.40"), D("0")], 7000000002: [1, D("-0.20"), D("0.20")]}
    # What cannot be tied to an order or a listing: Etsy Ads (shop level) and the label adjustment.
    tied = sum((v for o in s.orders.values() for v in o.amounts.values()), D("0")) + sum((f[1] + f[2] for f in s.listing_fees.values()), D("0"))
    assert s.net_total - tied == s.total("etsy_ads", "shipping") == D("-406.76")


def test_an_ad_charge_belongs_to_its_click_day_and_is_posted_the_next() -> None:
    s = statement.parse(STATEMENT)
    assert len(s.ad_charges) == 30
    first = min(s.ad_charges, key=lambda c: c.click_day)
    assert (first.click_day, first.posted, first.amount) == (date(2031, 8, 31), date(2031, 9, 1), D("7.77"))
    assert all((c.posted - c.click_day).days == 1 for c in s.ad_charges)
    assert date(2031, 9, 30) not in {c.click_day for c in s.ad_charges}  # billed in October


def test_the_ads_report_is_shop_level_daily_totals() -> None:
    report = ads_report.parse(ADS)
    assert (report.first_day, report.last_day, len(report.days), report.currency) == (date(2031, 9, 1), date(2031, 9, 30), 30, "USD")
    assert (report.spend, report.revenue, report.orders, report.clicks, report.views) == (D("416.25"), D("645.0"), 30, 1065, 34650)
    assert round(report.roas, 4) == D("1.5495") and round(report.click_rate * 100, 2) == D("3.07")
    assert not hasattr(report.days[0], "listing_id")
    with pytest.raises(ads_report.AdsReportError, match="listing column"):
        ads_report.parse("Date (ET),Listing,Views,Clicks,Orders,Revenue (USD),Spend (USD)\n\"Sep 1, 2031\",1,1,1,1,1,1\n")
    with pytest.raises(ads_report.AdsReportError, match="no column for spend"):
        ads_report.parse("Date (ET),Views,Clicks,Orders,Revenue (USD)\n")
    with pytest.raises(ads_report.AdsReportError, match="appears twice"):
        ads_report.parse(ADS.decode("utf-8-sig") + '\r\n"Sep 1, 2031",1,1,1,1,1,1,1,1')


def test_charged_by_etsy_and_spend_for_clicks_differ_by_exactly_the_explained_days() -> None:
    s = statement.parse(STATEMENT)
    rec = ads_report.reconcile(s.ad_charges, ads_report.parse(ADS))
    assert (rec.reported_total, rec.charged_total) == (D("416.25"), D("405.61"))
    assert rec.matched_days == 28
    assert [(d.day, d.reported, d.charged) for d in rec.billed_later] == [(date(2031, 9, 30), D("17.5"), None)]
    assert [(d.day, d.reported, d.charged) for d in rec.billed_from_before] == [(date(2031, 8, 31), None, D("7.77"))]
    assert [(d.day, d.reported, d.charged, d.difference) for d in rec.billed_differently] == [(date(2031, 9, 16), D("14"), D("13.09"), D("-0.91"))]
    # 405.61 = 416.25 - 17.50 + 7.77 - 0.91
    assert rec.explained == rec.charged_total and rec.exact
    assert "next day" in rec.billed_later[0].reason and "month before" in rec.billed_from_before[0].reason


def test_money_and_dates_as_etsy_writes_them() -> None:
    assert statement.parse_money("$1,234.56") == D("1234.56")
    assert statement.parse_money("-$0.20") == D("-0.20")
    assert statement.parse_money("--") == 0 and statement.parse_money("") == 0
    assert statement.parse_money("$0.00") == 0
    assert statement.parse_money("-€12,50".replace(",", ".")) == D("-12.50")  # another currency's sign is not the amount
    assert statement.parse_day("September 30, 2031") == date(2031, 9, 30)
    assert statement.parse_day("Sep 1, 2031") == date(2031, 9, 1)
    for bad in ("12.3.4", "abc", "$"):
        with pytest.raises(StatementError):
            statement.parse_money(bad)
    for bad in ("30 September 2031", "Smarch 1, 2031", "September 31, 2031", ""):
        with pytest.raises(StatementError):
            statement.parse_day(bad)


def test_a_row_the_parser_has_no_rule_for_is_counted_and_listed_never_dropped() -> None:
    text = HEADER + (
        '"September 2, 2031",Sale,Payment for Order #1,,USD,$10.00,--,$10.00,--\n'
        '"September 2, 2031",Fee,Renewal fee for something new,Listing #5,USD,--,-$0.20,-$0.20,--\n'
        '"September 3, 2031",Tax,VAT: on a new thing,Order #1,USD,--,-$0.30,-$0.30,--\n'
        '"September 3, 2031",Marketing,Credit for Etsy Ads,,USD,--,$1.00,$1.00,--\n'
        '"September 4, 2031",Mystery,Something Etsy added later,,USD,--,-$2.00,-$2.00,--\n'
    )
    s = statement.parse(text)
    assert s.totals == {"sales": D("10.00"), "other_fees": D("-0.20"), "other_tax": D("-0.30"), "other_marketing": D("1.00"), "unrecognised": D("-2.00")}
    assert s.net_total == D("8.50") == sum(s.totals.values())
    assert [(u.type, u.category, u.net) for u in s.unrecognised] == [
        ("Fee", "other_fees", D("-0.20")), ("Tax", "other_tax", D("-0.30")), ("Marketing", "other_marketing", D("1.00")), ("Mystery", "unrecognised", D("-2.00")),
    ]
    assert s.group("other") == D("-2.00")


def test_an_item_fee_is_never_matched_to_a_listing_by_its_title() -> None:
    # Item names that read like other rows' titles: the category comes from the fixed start only.
    text = HEADER + (
        '"September 2, 2031",Fee,"Transaction fee: Listing fee Mug, Processing fee Edit...",Order #7,USD,--,-$1.00,-$1.00,--\n'
        '"September 2, 2031",Fee,Transaction fee: Shipping,Order #7,USD,--,-$0.10,-$0.10,--\n'
        '"September 2, 2031",Fee,"Transaction fee: Shipping Box Sticker, Fragile ...",Order #8,USD,--,-$0.50,-$0.50,--\n'
    )
    s = statement.parse(text)
    # Only the exact title is the shipping fee: a product whose name starts with "Shipping" is an item.
    assert s.totals == {"transaction_fee_items": D("-1.50"), "transaction_fee_shipping": D("-0.10")}
    assert s.listing_fees == {}


def test_files_that_are_not_a_statement_are_refused_with_a_reason() -> None:
    with pytest.raises(StatementError, match="empty"):
        statement.parse(b"")
    with pytest.raises(StatementError, match="not an Etsy monthly statement.*'Net'"):
        statement.parse("Date,Type,Title,Info,Currency,Amount,Fees & Taxes\n")
    with pytest.raises(StatementError, match="not an Etsy monthly statement"):
        statement.parse(ADS)
    with pytest.raises(StatementError, match="no rows"):
        statement.parse(HEADER)
    with pytest.raises(StatementError, match="line 2: an amount could not be read"):
        statement.parse(HEADER + '"September 2, 2031",Sale,Payment for Order #1,,USD,ten,--,$10.00,--\n')
    with pytest.raises(StatementError, match="line 2: a date could not be read"):
        statement.parse(HEADER + '2031-09-02,Sale,Payment for Order #1,,USD,$10.00,--,$10.00,--\n')
    with pytest.raises(StatementError, match="mixes currencies"):
        statement.parse(HEADER + '"September 2, 2031",Sale,Payment for Order #1,,USD,$10.00,--,$10.00,--\n'
                                 '"September 2, 2031",Sale,Payment for Order #2,,EUR,$10.00,--,$10.00,--\n')
    with pytest.raises(UnicodeDecodeError):
        statement.parse(b"\xff\xfe\x00not utf-8")


def test_a_row_whose_parts_do_not_add_up_is_noted_and_its_net_is_what_counts() -> None:
    s = statement.parse(HEADER + '"September 2, 2031",Sale,Payment for Order #1,,USD,$10.00,-$1.00,$8.50,--\n')
    assert s.net_total == D("8.50") and len(s.notes) == 1 and "line 2" in s.notes[0]
