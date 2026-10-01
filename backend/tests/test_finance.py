"""The finance rules behind Analytics (pipeline/finance.py) and the ledger totals."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.pipeline import finance, ledger, profit
from app.pipeline.profit import AdRow, CostSettings, DaySales, Window

TODAY = date(2026, 9, 27)
MONEY = profit.MoneyFormat("USD")


def _shop(sales=None, ads=None, led=None, *, entered=None, sales_from=TODAY - timedelta(days=396),
          ledger_from=None, info=None, costs=None) -> finance.Shop:
    return finance.Shop(
        sales=sales or {}, ads=ads or {}, ledger=led or [], info=info or {},
        costs=costs or CostSettings.from_stored({}),
        entered=entered or {"product": False, "shipping": False, "fixed": False, "rates": False},
        currency="USD", sales_from=sales_from, ledger_from=ledger_from, ledger_to=TODAY if ledger_from else None,
    )


def _daily(lid: int, days: range, units: int = 1, price: int = 2500) -> list[DaySales]:
    return [DaySales(lid, TODAY - timedelta(days=d), units, units, units * price) for d in days]


def test_ledger_types_are_totalled_and_only_known_ones_are_costs() -> None:
    entries = [
        {"ledger_type": "prolist", "amount": -300, "currency": "USD", "created_timestamp": 1790000000},
        {"ledger_type": "prolist", "amount": -200, "currency": "USD", "created_timestamp": 1790000100},
        {"ledger_type": "DISBURSE", "amount": -9000, "currency": "USD", "created_timestamp": 1790000200},
        {"ledger_type": "offsite_ads_fee", "amount": -50, "buyer_user_id": 1, "created_timestamp": 1790000300},
    ]
    totals = ledger.aggregate(entries)
    day = date.fromtimestamp(1790000000)
    assert totals[(day, "prolist")].amount_minor == -500 and totals[(day, "prolist")].entries == 2
    assert ledger.category("prolist") == ledger.category("OFFSITE_ADS_FEE") == "ads"
    assert ledger.category("DISBURSE") is None  # a payout: listed, never counted as a cost
    assert ledger.cost_of(-500) == 500


def test_fees_come_from_the_ledger_when_it_covers_the_period_else_from_rates() -> None:
    sales = {1: _daily(1, range(0, 10))}  # $250 over 10 days
    led = [finance.LedgerDay(TODAY - timedelta(days=3), "transaction", -1625, 10),
           finance.LedgerDay(TODAY - timedelta(days=3), "prolist", -700, 3)]
    window = Window.last(30, TODAY)
    covered = finance.shop_totals(_shop(sales, led=led, ledger_from=TODAY - timedelta(days=90)), window)
    assert covered["lines"]["transaction_fees"] == {"value": 1625, "source": "ledger", "note": None}
    assert covered["lines"]["ads"]["value"] == 700
    estimated = finance.shop_totals(_shop(sales, led=led), window)  # the ledger isn't read
    assert estimated["lines"]["transaction_fees"]["source"] == "rates" and estimated["lines"]["transaction_fees"]["value"] == 1625
    assert estimated["lines"]["ads"]["value"] is None


def test_listing_fees_allocated_from_the_ledger_add_up_to_it() -> None:
    sales = {1: _daily(1, range(0, 3)), 2: _daily(2, range(0, 7), price=1000)}
    led = [finance.LedgerDay(TODAY, "transaction", -1001, 10), finance.LedgerDay(TODAY, "listing", -200, 10)]
    econ = finance.economics(_shop(sales, led=led, ledger_from=TODAY - timedelta(days=90)), Window.last(30, TODAY))
    assert sum(e.fees["transaction_fees"] for e in econ.values()) in (1000, 1001, 1002)  # rounding per listing only
    assert econ[1].fees["listing_fees"] == 60 and econ[2].fees["listing_fees"] == 140  # by items sold


def test_break_even_acos_is_the_margin_before_ads() -> None:
    sales = {1: _daily(1, range(0, 10))}  # $250, 10 items
    costs = CostSettings.from_stored({"product_cost": "10", "transaction_pct": "0", "payment_pct": "0",
                                      "payment_fixed": "0", "listing_fee": "0"})
    ads = {1: [AdRow(1, TODAY - timedelta(days=29), TODAY, 16000, 8, 20000)]}  # $160 spent for $200 of ad sales
    shop = _shop(sales, ads=ads, costs=costs, entered={"product": True, "shipping": False, "fixed": False, "rates": True})
    e = finance.economics(shop, Window.last(30, TODAY))[1]
    assert e.break_even_acos == pytest.approx(0.6)  # ($250 - $100 product) / $250
    assert e.acos == pytest.approx(0.8) and e.spend_per_sale == 2000
    [act] = finance.actions(shop, {1: e}, {}, Window.last(30, TODAY), TODAY, MONEY)
    assert act["kind"] == "ads_above_break_even" and act["stake"] == 4000  # 20 points of ACOS on $200 of ad sales


def test_a_change_of_direction_is_a_signal_a_spike_is_not() -> None:
    rising_then_falling = [DaySales(1, TODAY - timedelta(days=d), u, u, u * 100) for d, u in
                           [(80, 1), (75, 1), (50, 3), (45, 3), (40, 3), (35, 3), (20, 1)]]
    assert finance.trend(rising_then_falling, TODAY)["signal"] == "turned_down"
    spike = [DaySales(1, TODAY - timedelta(days=5), 1, 1, 100)]
    assert finance.trend(spike, TODAY)["signal"] == "too_few"


def test_concentration_and_cohorts() -> None:
    sales = {lid: _daily(lid, range(0, lid)) for lid in range(1, 13)}  # listing n sells n items
    info = {1: finance.ListingInfo(state="active", launched=TODAY - timedelta(days=400)),
            12: finance.ListingInfo(state="active", launched=TODAY - timedelta(days=120))}
    shop = _shop(sales, info=info)
    econ = finance.economics(shop, Window.last(30, TODAY))
    c = finance.concentration(econ)
    assert c["top_listings"][0] == 12 and c["top_share"] == pytest.approx(sum(range(3, 13)) / sum(range(1, 13)))
    groups = {g["label"]: g for g in finance.cohorts(shop, econ, Window.last(30, TODAY), TODAY)}
    launched = TODAY - timedelta(days=120)
    recent = groups[f"{launched.year} Q{(launched.month - 1) // 3 + 1}"]
    assert recent["listings"] == 1 and recent["first90_listings"] == 1 and recent["first90_revenue"] == 0
    assert "Launch date unknown" in groups


def test_a_year_on_year_comparison_needs_the_history() -> None:
    window = Window.last(30, TODAY)
    other, label, why = finance.comparison(_shop(sales_from=TODAY - timedelta(days=100)), window, "year")
    assert other is None and "13 months" in why
    other, _, why = finance.comparison(_shop(), window, "year")
    assert why is None and other.start == window.start - timedelta(days=364)
