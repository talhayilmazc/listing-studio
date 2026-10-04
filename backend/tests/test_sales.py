"""The seller's own sales as daily totals (v7 §C1).

Only listing id, quantity, price and date are read from each transaction;
nothing about buyers is stored, and totals are kept 13 months.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker

import pytest

from app.db.models import ConnectionStatus, EtsyConnection, SalesDaily, SalesSync, Tenant
from app.pipeline.sales import aggregate
from app.workers import sales as sales_worker
from app.workers.retention import purge_expired_rows, purge_shop_etsy_content
from tests.test_workers_profiles import FakeService


def _ts(d: date, hour: int = 12) -> int:
    return int(datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc).timestamp())


TODAY = datetime.now(timezone.utc).date()


def _tx(listing_id: int, d: date, qty: int = 1, amount: int = 2500) -> dict[str, Any]:
    # What Etsy sends includes buyer fields; only four are ever read.
    return {
        "transaction_id": 1, "listing_id": listing_id, "quantity": qty,
        "price": {"amount": amount, "divisor": 100, "currency_code": "USD"},
        "created_timestamp": _ts(d), "buyer_user_id": 999, "title": "x",
        "variations": [{"formatted_name": "Size", "formatted_value": "M"}],
    }


def test_sales_become_daily_totals_per_listing() -> None:
    d1, d2 = TODAY - timedelta(days=1), TODAY - timedelta(days=2)
    totals = aggregate([_tx(1, d1, 2), _tx(1, d1), _tx(2, d2, 1, 1999), _tx(1, TODAY - timedelta(days=50))], since=d2)
    assert set(totals) == {(1, d1), (2, d2)}
    assert (totals[(1, d1)].units, totals[(1, d1)].orders, totals[(1, d1)].revenue_minor) == (3, 2, 7500)
    assert totals[(2, d2)].revenue_minor == 1999 and totals[(2, d2)].currency == "USD"


def test_nothing_about_buyers_has_anywhere_to_go() -> None:
    columns = {c.key for c in inspect(SalesDaily).columns}
    assert columns == {"connection_id", "listing_id", "day", "tenant_id", "units", "orders", "revenue_minor", "currency"}


async def _shop(sm, *, scopes=("listings_r", "transactions_r")) -> tuple[uuid.UUID, uuid.UUID]:
    async with sm() as s:
        t = Tenant(email=f"{uuid.uuid4()}@e.com", password_hash="x", etsy_ceiling_override=2000)
        s.add(t)
        await s.flush()
        c = EtsyConnection(tenant_id=t.id, status=ConnectionStatus.active, etsy_user_id=77, shop_id=77, scopes=list(scopes))
        s.add(c)
        await s.commit()
        return t.id, c.id


def _patch(monkeypatch, tenant_id, fake) -> None:
    from app.workers import profiles as pw

    monkeypatch.setattr(pw, "_connection_service", lambda settings: FakeService(tenant_id))
    monkeypatch.setattr(sales_worker, "_connection_service", lambda settings: FakeService(tenant_id))
    monkeypatch.setattr(sales_worker, "_build_client", lambda ctx, http, settings, **_: fake)


class EtsyShop:
    """A shop's sales as Etsy serves them: ``count`` and pages by offset, in one order."""

    def __init__(self, sales: list[dict[str, Any]], *, newest_first: bool = True) -> None:
        self.newest_first = newest_first
        self.sales = sorted(sales, key=lambda t: (t["created_timestamp"], t["transaction_id"]), reverse=newest_first)
        self.calls: list[tuple[int, int]] = []
        self.fail_on: int | None = None

    def add(self, new: list[dict[str, Any]]) -> None:
        self.sales = sorted(self.sales + new, key=lambda t: (t["created_timestamp"], t["transaction_id"]), reverse=self.newest_first)

    async def get_shop_transactions(self, shop_id: int, *, limit: int, offset: int, **_: Any) -> dict[str, Any]:
        self.calls.append((offset, limit))
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise RuntimeError("connection reset")
        return {"count": len(self.sales), "results": self.sales[offset: offset + limit]}


_ids = iter(range(10_000_000, 99_999_999))


def _sale(listing_id: int, days_ago: int, qty: int = 1, hour: int = 12) -> dict[str, Any]:
    t = _tx(listing_id, TODAY - timedelta(days=days_ago), qty)
    t["created_timestamp"] = _ts(TODAY - timedelta(days=days_ago), hour)
    t["transaction_id"] = next(_ids)
    return t


def _shop_sales(inside: int, outside: int) -> list[dict[str, Any]]:
    """``inside`` sales spread over the 13 months, ``outside`` older than that."""
    out = [_sale(1 + i % 7, i % 390, hour=i % 24) for i in range(inside)]
    out += [_sale(9, 400 + i % 300) for i in range(outside)]
    return out


async def _units(sm) -> int:
    async with sm() as s:
        return sum(r.units for r in (await s.execute(select(SalesDaily))).scalars())


async def _sync_row(sm, shop) -> SalesSync:
    async with sm() as s:
        return await s.get(SalesSync, shop)


async def _estimated(sm, ctx, shop) -> SalesSync:
    async with sm() as s:
        c = await s.get(EtsyConnection, shop)
        s.add(SalesSync(connection_id=shop, tenant_id=c.tenant_id, state="estimating"))
        await s.commit()
    assert (await sales_worker.estimate_sales(ctx, str(shop))).startswith("estimate:")
    return await _sync_row(sm, shop)


async def _start(sm, shop) -> None:
    async with sm() as s:
        await sales_worker.begin_first_read(s, await s.get(SalesSync, shop))
        await s.commit()


class _Queue(list):
    async def __call__(self, function, *args, **kwargs):  # noqa: ANN001, ANN204
        self.append((function, args, kwargs))


def _ctx(sm) -> dict[str, Any]:
    return {"sessionmaker": sm, "bucket": None, "quota": None, "enqueue": _Queue()}


async def test_the_estimate_costs_a_few_requests_and_finds_the_13_month_edge(async_sm, monkeypatch) -> None:
    tid, shop = await _shop(async_sm)
    fake = EtsyShop(_shop_sales(inside=2400, outside=600))
    _patch(monkeypatch, tid, fake)
    sync = await _estimated(async_sm, _ctx(async_sm), shop)
    assert (sync.total_count, sync.window_count, sync.direction) == (3000, 2400, "desc")
    # One request per 100 sales, plus one to see the edge (2400 is a page boundary).
    assert sync.pages_estimate == 25
    # The first page, then a binary search: at most 1 + ceil(log2(3001)) = 13 requests.
    assert sync.requests_used == len(fake.calls) <= 13


async def test_the_first_read_runs_in_chunks_adds_as_it_goes_and_keeps_to_the_daily_pace(async_sm, monkeypatch) -> None:
    tid, shop = await _shop(async_sm)
    fake = EtsyShop(_shop_sales(inside=2400, outside=600))
    _patch(monkeypatch, tid, fake)
    monkeypatch.setattr(sales_worker, "CHUNK_PAGES", 5)
    monkeypatch.setattr(SalesSync, "DAILY_REQUESTS", 25)
    ctx = _ctx(async_sm)
    estimate = (await _estimated(async_sm, ctx, shop)).requests_used  # counts toward today
    await _start(async_sm, shop)
    fake.calls.clear()

    assert await sales_worker.sync_sales(ctx, str(shop)) == "reading:500"
    assert await _units(async_sm) == 500  # already useful before the read ends
    assert ctx["enqueue"][-1][0] == "sync_sales"  # the next chunk is queued

    # New sales arrive during the read: every page moves down by 7, and the run
    # steps back 10 in case of cancellations. The 17 sales seen again are
    # skipped, not counted twice; the new ones wait for the first update.
    fake.add([_sale(4, 0, hour=23) for _ in range(7)])
    assert await sales_worker.sync_sales(ctx, str(shop)) == "reading:983"
    assert await _units(async_sm) == 983

    # The day's pace runs out: it stops and carries on after the reset.
    result = await sales_worker.sync_sales(ctx, str(shop))
    while (await _sync_row(async_sm, shop)).state == "reading":
        result = await sales_worker.sync_sales(ctx, str(shop))
    sync = await _sync_row(async_sm, shop)
    assert sync.state == "waiting" and sync.resumes_at is not None and sync.requests_today == 25
    assert sync.requests_used == 25 and estimate + len(fake.calls) == 25
    assert "_defer_by" in ctx["enqueue"][-1][2]
    assert await _units(async_sm) == sync.read_count

    # The next day it resumes where it stopped and finishes.
    async with async_sm() as s:
        (await s.get(SalesSync, shop)).requests_day = TODAY - timedelta(days=1)
        await s.commit()
    result = await sales_worker.sync_sales(ctx, str(shop))
    while result.startswith("reading"):
        result = await sales_worker.sync_sales(ctx, str(shop))
    assert result == "complete:2400"
    assert await _units(async_sm) == 2400
    sync = await _sync_row(async_sm, shop)
    assert sync.state == "complete" and sync.read_count == 2400

    # After that, only what's new: one request.
    fake.add([_sale(5, 0, hour=23) for _ in range(3)])
    assert await sales_worker.sync_sales(ctx, str(shop)) == "updated:10"
    assert (await _sync_row(async_sm, shop)).last_update_requests == 1
    assert await _units(async_sm) == 2410


async def test_an_interrupted_read_resumes_without_counting_twice(async_sm, monkeypatch) -> None:
    tid, shop = await _shop(async_sm)
    fake = EtsyShop(_shop_sales(inside=750, outside=50))
    _patch(monkeypatch, tid, fake)
    ctx = _ctx(async_sm)
    await _estimated(async_sm, ctx, shop)
    await _start(async_sm, shop)
    fake.calls.clear()
    fake.fail_on = 4  # the connection drops on the fourth page
    with pytest.raises(RuntimeError):
        await sales_worker.sync_sales(ctx, str(shop))
    sync = await _sync_row(async_sm, shop)
    assert (sync.state, sync.read_count, sync.next_offset, sync.lock_until) == ("reading", 300, 300, None)

    fake.fail_on = None
    assert await sales_worker.sync_sales(ctx, str(shop)) == "complete:750"
    assert await _units(async_sm) == 750


async def test_oldest_first_order_is_handled_too(async_sm, monkeypatch) -> None:
    tid, shop = await _shop(async_sm)
    fake = EtsyShop(_shop_sales(inside=250, outside=120), newest_first=False)
    _patch(monkeypatch, tid, fake)
    ctx = _ctx(async_sm)
    sync = await _estimated(async_sm, ctx, shop)
    assert (sync.direction, sync.window_count, sync.start_offset) == ("asc", 250, 120)
    await _start(async_sm, shop)
    assert await sales_worker.sync_sales(ctx, str(shop)) == "complete:250"
    fake.add([_sale(6, 0, hour=23) for _ in range(2)])
    assert await sales_worker.sync_sales(ctx, str(shop)) == "updated:2"
    assert await _units(async_sm) == 252


async def test_nothing_is_read_before_the_seller_starts(async_sm, monkeypatch) -> None:
    tid, shop = await _shop(async_sm)
    fake = EtsyShop(_shop_sales(inside=10, outside=0))
    _patch(monkeypatch, tid, fake)
    assert await sales_worker.sync_sales(_ctx(async_sm), str(shop)) == "not-started"
    assert fake.calls == []


def test_days_at_the_daily_pace() -> None:
    assert sales_worker.days_needed(0) == 0
    assert sales_worker.days_needed(31) == 1
    assert sales_worker.days_needed(250) == 1 and sales_worker.days_needed(251) == 2
    assert sales_worker.days_needed(100, used_today=200) == 2  # 50 today, 50 tomorrow


async def test_a_shop_without_the_sales_permission_is_not_read(async_sm: async_sessionmaker, monkeypatch) -> None:
    tid, shop = await _shop(async_sm, scopes=("listings_r",))
    fake = EtsyShop([_sale(1, 0)])
    _patch(monkeypatch, tid, fake)
    assert await sales_worker.sync_sales(_ctx(async_sm), str(shop)) == "no-permission"
    assert fake.calls == []


async def test_totals_are_kept_13_months_and_deleted_with_the_shop(async_sm: async_sessionmaker) -> None:
    tid, shop = await _shop(async_sm)
    async with async_sm() as s:
        for days in (10, 390, 400):
            s.add(SalesDaily(connection_id=shop, listing_id=1, day=TODAY - timedelta(days=days), tenant_id=tid, units=1))
        await s.commit()
    async with async_sm() as s:
        counts = await purge_expired_rows(s)
    assert counts["sales_days"] == 1
    async with async_sm() as s:
        s.add(SalesSync(connection_id=shop, tenant_id=tid, state="complete"))
        await s.commit()
    async with async_sm() as s:
        await purge_shop_etsy_content(s, shop)
        await s.commit()
        assert (await s.execute(select(SalesDaily))).scalars().all() == []
        assert await s.get(SalesSync, shop) is None


async def test_a_refused_read_says_why_and_resumes_where_it_stopped(async_sm, monkeypatch) -> None:
    """Empty figures must never be the only sign of a failed read (the "$0" report)."""
    from app.etsy.errors import EtsyClientError

    tid, shop = await _shop(async_sm)
    fake = EtsyShop(_shop_sales(inside=450, outside=20))
    _patch(monkeypatch, tid, fake)
    ctx = _ctx(async_sm)
    await _estimated(async_sm, ctx, shop)
    await _start(async_sm, shop)
    fake.calls.clear()

    real = fake.get_shop_transactions

    async def refuse_third(shop_id, *, limit, offset, **kw):  # noqa: ANN001, ANN202
        if len(fake.calls) == 2:
            fake.calls.append((offset, limit))
            raise EtsyClientError(status_code=403, body="", path="/transactions", method="GET")
        return await real(shop_id, limit=limit, offset=offset, **kw)

    fake.get_shop_transactions = refuse_third
    assert await sales_worker.sync_sales(ctx, str(shop)) == "failed"
    sync = await _sync_row(async_sm, shop)
    assert sync.state == "failed" and "answered 403" in sync.note and sync.read_count == 200
    assert await _units(async_sm) == 200  # the pages read before stay

    fake.get_shop_transactions = real
    async with async_sm() as s:
        await sales_worker.resume(s, await s.get(SalesSync, shop))
        await s.commit()
    assert await sales_worker.sync_sales(ctx, str(shop)) == "complete:450"
    assert await _units(async_sm) == 450
