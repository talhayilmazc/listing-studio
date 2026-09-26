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

from app.db.models import ConnectionStatus, EtsyConnection, SalesDaily, Tenant
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


class SalesEtsy:
    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages, self.offsets = pages, []

    async def get_shop_transactions(self, shop_id: int, *, limit: int, offset: int, **_: Any) -> dict[str, Any]:
        self.offsets.append(offset)
        i = offset // limit
        return {"results": self.pages[i] if i < len(self.pages) else []}


async def _shop(sm, *, scopes=("listings_r", "transactions_r")) -> tuple[uuid.UUID, uuid.UUID]:
    async with sm() as s:
        t = Tenant(email=f"{uuid.uuid4()}@e.com", password_hash="x", daily_quota=2000)
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


async def test_the_first_read_goes_back_13_months_then_only_recent_days(async_sm: async_sessionmaker, monkeypatch) -> None:
    tid, shop = await _shop(async_sm)
    full_page = [_tx(1, TODAY - timedelta(days=1)) for _ in range(100)]
    old = [_tx(2, TODAY - timedelta(days=380)), _tx(3, TODAY - timedelta(days=500))]
    fake = SalesEtsy([full_page, old])
    _patch(monkeypatch, tid, fake)
    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None}

    assert await sales_worker.sync_sales(ctx, str(shop)) == "sales:2"
    assert fake.offsets == [0, 100]
    async with async_sm() as s:
        rows = {(r.listing_id, r.units) for r in (await s.execute(select(SalesDaily))).scalars()}
    assert rows == {(1, 100), (2, 1)}  # 500 days ago is outside 13 months

    # Next run: only the last few days are re-read and replaced.
    fake2 = SalesEtsy([[_tx(1, TODAY - timedelta(days=1), 3)]])
    _patch(monkeypatch, tid, fake2)
    await sales_worker.sync_sales(ctx, str(shop))
    async with async_sm() as s:
        rows = {(r.listing_id, r.units) for r in (await s.execute(select(SalesDaily))).scalars()}
    assert rows == {(1, 3), (2, 1)}


async def test_a_shop_without_the_sales_permission_is_not_read(async_sm: async_sessionmaker, monkeypatch) -> None:
    tid, shop = await _shop(async_sm, scopes=("listings_r",))
    fake = SalesEtsy([[_tx(1, TODAY)]])
    _patch(monkeypatch, tid, fake)
    assert await sales_worker.sync_sales({"sessionmaker": async_sm, "bucket": None, "quota": None}, str(shop)) == "no-permission"
    assert fake.offsets == []


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
        await purge_shop_etsy_content(s, shop)
        await s.commit()
        assert (await s.execute(select(SalesDaily))).scalars().all() == []
