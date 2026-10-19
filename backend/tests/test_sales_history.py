"""Sales records before the Commercial Access review (Part B3).

* disconnecting a shop or deleting the account deletes its sales lines and daily totals at once;
* "Delete my sales history" (pipeline/sales_history.py, the profit report): with confirmation,
  only the caller's own shop, and later reads store nothing from before that day;
* every night the last 30 days of sales and of Etsy's ledger are read again and replaced, so a
  refund or a change shows within a day; the report says when it was last updated.
Synthetic sales only; Etsy is a fake that serves pages.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.core.crypto import TokenCipher
from app.db.models import (
    AdsDaily,
    AuditLog,
    EtsyConnection,
    LedgerDaily,
    LedgerSync,
    SaleLine,
    SalesDaily,
    SalesSync,
    StatementImport,
    Tenant,
)
from app.etsy.connection import ConnectionService
from app.pipeline import sales_history
from app.pipeline.sales import REFRESH_DAYS
from app.workers import accounts as account_jobs
from app.workers import ledger as ledger_worker
from app.workers import sales as sales_worker
from tests.test_ledger import LedgerShop
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_sales import TODAY, EtsyShop, _ctx, _patch, _sale, _shop, _ts

ROOT = Path(__file__).resolve().parents[2]


async def _with_history(sm, shop: uuid.UUID, tenant: uuid.UUID, *, days: tuple[int, ...] = (1, 40, 200)) -> None:
    """Sales lines, daily sales and fee totals and an imported month for one shop."""
    async with sm() as s:
        for i, back in enumerate(days):
            day = TODAY - timedelta(days=back)
            s.add(SalesDaily(connection_id=shop, listing_id=1, day=day, tenant_id=tenant, units=2, orders=1, revenue_minor=5000, currency="USD"))
            s.add(SaleLine(connection_id=shop, transaction_id=1000 + i, tenant_id=tenant, receipt_id=50 + i, listing_id=1, day=day,
                           quantity=2, price_minor=5000, shipping_minor=0, currency="USD"))
            s.add(LedgerDaily(connection_id=shop, day=day, ledger_type="transaction", tenant_id=tenant, amount_minor=-325, entries=1, currency="USD"))
        month = TODAY.replace(day=1)
        s.add(StatementImport(connection_id=shop, month=month, tenant_id=tenant, rows=3, first_day=month, last_day=month, net_minor=1200))
        s.add(AdsDaily(connection_id=shop, day=TODAY, tenant_id=tenant, spend_minor=300))
        await s.commit()


async def _count(sm, table, shop: uuid.UUID) -> int:
    async with sm() as s:
        return int(await s.scalar(select(func.count()).select_from(table).where(table.connection_id == shop)) or 0)


# --- disconnecting and deleting the account ----------------------------------------------------------


async def test_disconnecting_or_deleting_the_account_deletes_sales_lines_and_totals_at_once(async_sm) -> None:
    service = ConnectionService(TokenCipher(Fernet.generate_key()), client_id="k", token_url="https://t")
    tid, shop = await _shop(async_sm)
    await _with_history(async_sm, shop, tid)
    async with async_sm() as s:
        (await s.get(EtsyConnection, shop)).sales_history_from = TODAY - timedelta(days=300)
        await s.commit()
    assert await _count(async_sm, SaleLine, shop) == 3 and await _count(async_sm, LedgerDaily, shop) == 3
    async with async_sm() as s:
        await service.disconnect(s, await s.get(EtsyConnection, shop))
    for table in (SaleLine, SalesDaily, LedgerDaily, StatementImport, AdsDaily):
        assert await _count(async_sm, table, shop) == 0, table.__name__
    async with async_sm() as s:
        assert (await s.get(EtsyConnection, shop)).sales_history_from is None  # a reconnected shop starts fresh

    # Deleting the account: every shop is disconnected the same way, at once.
    tid2, shop2 = await _shop(async_sm)
    async with async_sm() as s:
        (await s.get(EtsyConnection, shop2)).etsy_user_id = 78  # one Etsy account, one shop
        await s.commit()
    await _with_history(async_sm, shop2, tid2)
    async with async_sm() as s:
        await account_jobs.request_deletion(s, await s.get(Tenant, tid2), service)
    for table in (SaleLine, SalesDaily, LedgerDaily):
        assert await _count(async_sm, table, shop2) == 0, table.__name__


# --- "Delete my sales history" -----------------------------------------------------------------------


async def test_delete_my_sales_history_needs_confirmation_and_touches_only_that_shop(world) -> None:  # noqa: F811
    a1, a2, b1 = world["a1"], world["a2"], world["b1"]
    for shop, tenant in ((a1, world["alice"]), (a2, world["alice"]), (b1, world["bob"])):
        await _with_history(world["sm"], shop, tenant)
    url = "/api/analytics/sales/history/delete"
    # Without the seller's confirmation: refused, nothing deleted.
    assert (await world["a"].post(f"{url}?shop={a1}", json={})).status_code == 422
    assert (await world["a"].post(f"{url}?shop={a1}", json={"confirm": False})).status_code == 422
    assert await _count(world["sm"], SaleLine, a1) == 3
    # Another account's shop: 404, and its history stays.
    assert (await world["a"].post(f"{url}?shop={b1}", json={"confirm": True})).status_code == 404
    assert await _count(world["sm"], SaleLine, b1) == 3

    res = await world["a"].post(f"{url}?shop={a1}", json={"confirm": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deleted"]["sale_lines"] == 3 and body["deleted"]["sales_days"] == 3 and body["deleted"]["fee_days"] == 3
    assert body["deleted"]["statement_months"] == 1 and body["deleted"]["ads_days"] == 1
    assert body["history_from"] == TODAY.isoformat() and body["status"]["history_from"] == TODAY.isoformat()
    for table in (SaleLine, SalesDaily, LedgerDaily, StatementImport, AdsDaily):
        assert await _count(world["sm"], table, a1) == 0, table.__name__
    # The account's other shop and the other account keep theirs.
    assert await _count(world["sm"], SaleLine, a2) == 3 and await _count(world["sm"], SaleLine, b1) == 3
    async with world["sm"]() as s:
        entry = (await s.execute(select(AuditLog).where(AuditLog.action == "sales_history.deleted"))).scalars().one()
        assert entry.details["shop_id"] == str(a1) and entry.details["counts"]["sale_lines"] == 3 and entry.details["by"] == "seller"
    # The status says so: the report starts today.
    status = (await world["a"].get(f"/api/analytics/sales/status?shop={a1}")).json()
    assert status["history_from"] == TODAY.isoformat()


def _rsale(listing_id: int, days_ago: int, hour: int = 12) -> dict:
    """A sale with its order number (so it also becomes an order line)."""
    t = _sale(listing_id, days_ago, hour=hour)
    t["receipt_id"] = t["transaction_id"] + 1
    return t


async def test_after_deleting_later_reads_store_nothing_from_before(async_sm, monkeypatch) -> None:
    tid, shop = await _shop(async_sm)
    old = [_rsale(3, 90), _rsale(3, 10)]
    fake = EtsyShop(old)
    _patch(monkeypatch, tid, fake)
    ctx = _ctx(async_sm)
    async with async_sm() as s:
        s.add(SalesSync(connection_id=shop, tenant_id=tid, state="reading", window_start=TODAY - timedelta(days=395),
                        has_lines=True, started_at=datetime.now(timezone.utc)))
        s.add(LedgerSync(connection_id=shop, tenant_id=tid, state="complete", window_start=_ts(TODAY - timedelta(days=90), 0),
                         window_end=_ts(TODAY, 0), synced_until=_ts(TODAY, 0), backfill_state="reading"))
        await s.commit()
        done = await sales_history.delete_history(s, await s.get(EtsyConnection, shop))
        await s.commit()
    assert done.history_from == TODAY
    async with async_sm() as s:
        sync, ledger = await s.get(SalesSync, shop), await s.get(LedgerSync, shop)
        # The history read stops; the ledger's backward history has nothing left to fill.
        assert sync.state == "complete" and sync.window_start == TODAY
        assert ledger.backfill_state == "complete" and ledger.covered_from == ledger.backfill_target == _ts(TODAY, 0)
    # A read that brings older sales in (here the nightly one) stores only today's.
    fake.add([_rsale(4, 0, hour=1)])
    await sales_worker.sync_sales(ctx, str(shop))
    async with async_sm() as s:
        days = {r.day for r in (await s.execute(select(SalesDaily).where(SalesDaily.connection_id == shop))).scalars()}
        lines = {r.day for r in (await s.execute(select(SaleLine).where(SaleLine.connection_id == shop))).scalars()}
    assert days == {TODAY} and lines == {TODAY}
    # The ledger likewise: an entry from before is dropped, today's is kept.
    async with async_sm() as s:
        connection = await s.get(EtsyConnection, shop)
        await ledger_worker._add(s, connection, [
            {"ledger_type": "transaction", "amount": -65, "currency": "USD", "created_timestamp": _ts(TODAY - timedelta(days=5))},
            {"ledger_type": "transaction", "amount": -40, "currency": "USD", "created_timestamp": _ts(TODAY, 1)},
        ])
        await s.commit()
        assert {r.day for r in (await s.execute(select(LedgerDaily).where(LedgerDaily.connection_id == shop))).scalars()} == {TODAY}


# --- the nightly 30-day re-read ---------------------------------------------------------------------


async def _complete_read(sm, monkeypatch, sales):  # noqa: ANN001, ANN202
    tid, shop = await _shop(sm)
    fake = EtsyShop(sales)
    _patch(monkeypatch, tid, fake)
    async with sm() as s:
        s.add(SalesSync(connection_id=shop, tenant_id=tid, state="complete", direction="desc", has_lines=True, reread="done",
                        window_start=TODAY - timedelta(days=395), started_at=datetime.now(timezone.utc)))
        await s.commit()
        connection = await s.get(EtsyConnection, shop)
        await sales_worker._add_totals(s, connection, sales, TODAY - timedelta(days=395))
        await s.commit()
    return tid, shop, fake


async def test_the_last_30_days_are_read_again_every_night_and_replaced(async_sm, monkeypatch) -> None:
    recent = [_sale(1, d, hour=h) for d in range(0, 29) for h in (9, 15)]  # 58 sales in the last 30 days
    older = [_sale(2, 60), _sale(2, 120)]
    tid, shop, fake = await _complete_read(async_sm, monkeypatch, recent + older)
    # Etsy changed two recent sales since they were read (one cancelled, one with a new quantity).
    cancelled, changed = recent[3], recent[10]
    fake.sales = [t for t in fake.sales if t["transaction_id"] != cancelled["transaction_id"]]
    for t in fake.sales:
        if t["transaction_id"] == changed["transaction_id"]:
            t["quantity"] = 3
    fake.calls.clear()
    result = await sales_worker.sync_sales(_ctx(async_sm), str(shop))
    assert result.startswith("refreshed:")
    # One page holds the 57 recent sales and the first older one: the read stops there.
    assert fake.calls == [(0, 100)]
    async with async_sm() as s:
        rows = (await s.execute(select(SalesDaily).where(SalesDaily.connection_id == shop))).scalars().all()
        sync = await s.get(SalesSync, shop)
        connection = await s.get(EtsyConnection, shop)
        lines = (await s.execute(select(SaleLine).where(SaleLine.connection_id == shop))).scalars().all()
    recent_units = sum(r.units for r in rows if r.day >= TODAY - timedelta(days=REFRESH_DAYS))
    assert recent_units == 58 - 1 + 2  # the cancelled sale is gone, the changed one counts 3
    assert {r.day for r in rows if r.listing_id == 2} == {TODAY - timedelta(days=60), TODAY - timedelta(days=120)}  # older days kept
    assert cancelled["transaction_id"] not in {line.transaction_id for line in lines}
    assert sync.last_update_requests == 1 and connection.sales_synced_at is not None
    # Read again with nothing changed: the same totals (replaced, not added twice).
    await sales_worker.sync_sales(_ctx(async_sm), str(shop))
    async with async_sm() as s:
        again = (await s.execute(select(SalesDaily).where(SalesDaily.connection_id == shop))).scalars().all()
    assert sum(r.units for r in again) == sum(r.units for r in rows)


async def test_a_shop_with_more_recent_sales_than_tonights_pages_takes_only_its_new_sales(async_sm, monkeypatch) -> None:
    sales = [_sale(1, d % 20, hour=d % 24) for d in range(450)]
    tid, shop, fake = await _complete_read(async_sm, monkeypatch, sales)
    async with async_sm() as s:
        sync = await s.get(SalesSync, shop)
        top = max(sales, key=lambda t: (t["created_timestamp"], t["transaction_id"]))
        sync.newest_ts, sync.newest_id = top["created_timestamp"], top["transaction_id"]
        await s.commit()
    monkeypatch.setattr(sales_worker, "REFRESH_PAGES", 2)
    fake.add([_sale(5, 0, hour=23)])
    fake.calls.clear()
    assert await sales_worker.sync_sales(_ctx(async_sm), str(shop)) == "new-only:1"
    assert len(fake.calls) == 2
    async with async_sm() as s:
        assert sum(r.units for r in (await s.execute(select(SalesDaily).where(SalesDaily.connection_id == shop))).scalars()) == 451


async def test_the_ledger_rereads_its_last_30_days_every_night(async_sm, monkeypatch) -> None:
    tid, shop = await _shop(async_sm)
    now = int(datetime.now(timezone.utc).timestamp())
    old = {"ledger_type": "transaction", "amount": -100, "currency": "USD", "created_timestamp": now - 50 * 86400}
    recent = [{"ledger_type": "transaction", "amount": -65, "currency": "USD", "created_timestamp": now - d * 86400} for d in (2, 5, 9)]
    fake = LedgerShop([old, *recent])
    _patch(monkeypatch, tid, fake)
    monkeypatch.setattr(sales_worker, "_build_client", lambda ctx, http, settings, **_: fake)
    async with async_sm() as s:
        s.add(LedgerSync(connection_id=shop, tenant_id=tid, state="complete", window_start=now - 90 * 86400, window_end=now - 3600,
                         synced_until=now - 3600, backfill_state="complete"))
        await s.commit()
        connection = await s.get(EtsyConnection, shop)
        await ledger_worker._add(s, connection, [old, *recent])
        await s.commit()
    # A refund lands on a recent day, and one fee Etsy had recorded is gone.
    fake.entries = [old, recent[0], recent[1], {"ledger_type": "REFUND", "amount": 2500, "currency": "USD", "created_timestamp": now - 5 * 86400}]
    fake.calls.clear()
    assert await ledger_worker.sync_ledger(_ctx(async_sm), str(shop)) == "refreshed:3"
    since = fake.calls[0][0]
    assert since == ledger_worker._midnight(now - REFRESH_DAYS * 86400)  # the last 30 days, from midnight
    async with async_sm() as s:
        rows = (await s.execute(select(LedgerDaily).where(LedgerDaily.connection_id == shop))).scalars().all()
        sync = await s.get(LedgerSync, shop)
    by = {(r.day, r.ledger_type): r.amount_minor for r in rows}
    day = lambda back: datetime.fromtimestamp(now - back * 86400, tz=timezone.utc).date()  # noqa: E731
    assert by[(day(50), "transaction")] == -100  # older than 30 days: kept as it was
    assert (day(9), "transaction") not in by  # the fee Etsy no longer lists is gone
    assert by[(day(5), "REFUND")] == 2500 and by[(day(2), "transaction")] == -65
    assert sync.synced_until >= now - 5


async def test_the_report_says_when_it_was_last_updated(world) -> None:  # noqa: F811
    a1 = world["a1"]
    then = datetime.now(timezone.utc) - timedelta(hours=3)
    async with world["sm"]() as s:
        c = await s.get(EtsyConnection, a1)
        c.scopes = ["listings_r", "listings_w", "shops_r", "shops_w", "transactions_r"]
        c.sales_synced_at = then
        s.add(SalesSync(connection_id=a1, tenant_id=world["alice"], state="complete"))
        s.add(LedgerSync(connection_id=a1, tenant_id=world["alice"], state="complete", synced_until=int((then - timedelta(hours=1)).timestamp())))
        await s.commit()
    status = (await world["a"].get(f"/api/analytics/sales/status?shop={a1}")).json()
    updated = datetime.fromisoformat(status["updated_at"].replace("Z", "+00:00"))
    assert abs((updated - (then - timedelta(hours=1))).total_seconds()) < 2  # the older of the two reads


def test_the_policy_and_the_compliance_notes_say_all_three() -> None:
    privacy = (ROOT / "frontend" / "app" / "(marketing)" / "privacy" / "page.tsx").read_text(encoding="utf-8")
    flat = " ".join(privacy.split())
    assert "R.salesRefreshDays" in privacy and "so a refund or a change shows within a day" in flat
    assert "Deleted at once when you disconnect the shop or delete your account" in flat
    assert "<em>Delete my sales history</em>" in privacy
    notes = " ".join((ROOT / "docs" / "etsy-compliance.md").read_text(encoding="utf-8").split())
    assert "Delete my sales history" in notes and "last 30 days" in notes and "disconnect" in notes.lower()
    # The confirmation says what is deleted in the server's own words.
    ui = (ROOT / "frontend" / "lib" / "salesHistory.ts").read_text(encoding="utf-8")
    assert f'"{sales_history.WHAT_IS_DELETED}"' in ui
