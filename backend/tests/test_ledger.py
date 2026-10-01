"""Reading the shop's payment ledger into daily totals per type (workers/ledger.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from app.db.models import LedgerDaily, LedgerSync, SalesSync
from app.etsy.errors import EtsyClientError
from app.workers import ledger as ledger_worker
from app.workers.retention import purge_shop_etsy_content
from tests.test_sales import _ctx, _patch, _shop

NOW = int(datetime.now(timezone.utc).timestamp())


class LedgerShop:
    """A shop's ledger as Etsy serves it: entries between two times, by offset."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries
        self.calls: list[tuple[int, int, int, int]] = []
        self.refuse = False

    async def get_ledger_entries(self, shop_id: int, *, min_created: int, max_created: int, limit: int, offset: int, **_: Any):
        self.calls.append((min_created, max_created, limit, offset))
        if self.refuse:
            raise EtsyClientError(status_code=403, body="", path="/ledger-entries", method="GET")
        inside = [e for e in self.entries if min_created <= e["created_timestamp"] <= max_created]
        return {"count": len(inside), "results": inside[offset: offset + limit]}


def _entries(n: int, kind: str = "prolist", amount: int = -100, start: int = NOW - 80 * 86400) -> list[dict[str, Any]]:
    return [{"entry_id": i, "ledger_type": kind, "amount": amount, "currency": "USD",
             "created_timestamp": start + i * 600, "description": "Payment", "reference_id": "1"} for i in range(n)]


async def _estimated(sm, shop, ctx) -> None:  # noqa: ANN001
    async with sm() as s:
        from app.db.models import EtsyConnection

        c = await s.get(EtsyConnection, shop)
        s.add(LedgerSync(connection_id=shop, tenant_id=c.tenant_id, state="estimating"))
        await s.commit()
    assert (await ledger_worker.estimate_ledger(ctx, str(shop))).startswith("estimate:")


async def _row(sm, shop) -> LedgerSync:  # noqa: ANN001
    async with sm() as s:
        return await s.get(LedgerSync, shop)


async def _total(sm, kind: str) -> int:  # noqa: ANN001
    async with sm() as s:
        return int(await s.scalar(select(func.sum(LedgerDaily.amount_minor)).where(LedgerDaily.ledger_type == kind)) or 0)


async def test_the_estimate_costs_one_request_and_the_read_totals_each_type(async_sm, monkeypatch) -> None:
    tid, shop = await _shop(async_sm)
    fake = LedgerShop(_entries(250) + _entries(30, "transaction", -65))
    _patch(monkeypatch, tid, fake)
    from app.workers import sales as sales_worker

    monkeypatch.setattr(sales_worker, "_build_client", lambda ctx, http, settings, **_: fake)
    ctx = _ctx(async_sm)
    await _estimated(async_sm, shop, ctx)
    sync = await _row(async_sm, shop)
    assert (sync.state, sync.total_count, sync.requests_used) == ("estimated", 280, 1)
    assert fake.calls[0][2] == 1  # one entry asked for: the count comes with it

    async with async_sm() as s:
        await ledger_worker.begin(s, await s.get(LedgerSync, shop))
        await s.commit()
    assert await ledger_worker.sync_ledger(ctx, str(shop)) == "complete:280"
    assert await _total(async_sm, "prolist") == -25000 and await _total(async_sm, "transaction") == -1950
    sync = await _row(async_sm, shop)
    assert sync.state == "complete" and sync.synced_until == sync.window_end

    # After that, only what's new since the last read.
    fake.entries += _entries(3, "offsite_ads_fee", -40, start=sync.window_end + 5)
    fake.calls.clear()
    import app.workers.ledger as lw

    monkeypatch.setattr(lw, "_now", lambda: datetime.fromtimestamp(sync.window_end + 2000, tz=timezone.utc))
    assert await ledger_worker.sync_ledger(ctx, str(shop)) == "updated:3"
    assert len(fake.calls) == 1 and fake.calls[0][0] == sync.window_end + 1
    assert await _total(async_sm, "offsite_ads_fee") == -120


async def test_an_update_longer_than_one_run_carries_on_without_counting_twice(async_sm, monkeypatch) -> None:
    tid, shop = await _shop(async_sm)
    fake = LedgerShop(_entries(5))
    _patch(monkeypatch, tid, fake)
    from app.workers import sales as sales_worker

    monkeypatch.setattr(sales_worker, "_build_client", lambda ctx, http, settings, **_: fake)
    ctx = _ctx(async_sm)
    await _estimated(async_sm, shop, ctx)
    async with async_sm() as s:
        await ledger_worker.begin(s, await s.get(LedgerSync, shop))
        await s.commit()
    assert await ledger_worker.sync_ledger(ctx, str(shop)) == "complete:5"
    sync = await _row(async_sm, shop)

    # 250 new entries, and a run that may read two pages.
    fake.entries += _entries(250, "transaction", -10, start=sync.window_end + 5)
    fake.entries = [dict(e, created_timestamp=min(e["created_timestamp"], sync.window_end + 900)) if e["ledger_type"] == "transaction" else e
                    for e in fake.entries]
    monkeypatch.setattr(ledger_worker, "UPDATE_PAGES", 2)
    monkeypatch.setattr(ledger_worker, "_now", lambda: datetime.fromtimestamp(sync.window_end + 2000, tz=timezone.utc))
    assert await ledger_worker.sync_ledger(ctx, str(shop)) == "updated:200"
    mid = await _row(async_sm, shop)
    assert mid.synced_until == sync.window_end and mid.next_offset == 200 and mid.update_end == sync.window_end + 2000
    # Later, the same window from where it stopped, even though time has moved on.
    monkeypatch.setattr(ledger_worker, "_now", lambda: datetime.fromtimestamp(sync.window_end + 9000, tz=timezone.utc))
    assert await ledger_worker.sync_ledger(ctx, str(shop)) == "updated:50"
    assert await _total(async_sm, "transaction") == -2500
    done = await _row(async_sm, shop)
    assert done.synced_until == sync.window_end + 2000 and done.update_end is None


async def test_a_refused_ledger_read_says_why_and_is_deleted_with_the_shop(async_sm, monkeypatch) -> None:
    tid, shop = await _shop(async_sm)
    fake = LedgerShop(_entries(10))
    _patch(monkeypatch, tid, fake)
    from app.workers import sales as sales_worker

    monkeypatch.setattr(sales_worker, "_build_client", lambda ctx, http, settings, **_: fake)
    ctx = _ctx(async_sm)
    await _estimated(async_sm, shop, ctx)
    async with async_sm() as s:
        await ledger_worker.begin(s, await s.get(LedgerSync, shop))
        await s.commit()
    fake.refuse = True
    assert await ledger_worker.sync_ledger(ctx, str(shop)) == "failed"
    sync = await _row(async_sm, shop)
    assert sync.state == "failed" and "answered 403" in sync.note and sync.lock_until is None

    async with async_sm() as s:
        s.add(LedgerDaily(connection_id=shop, day=datetime.now(timezone.utc).date(), ledger_type="prolist",
                          tenant_id=tid, amount_minor=-5, entries=1))
        await s.commit()
    async with async_sm() as s:
        await purge_shop_etsy_content(s, shop)
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(LedgerDaily)) == 0
        assert await s.get(LedgerSync, shop) is None


def test_pages_needed() -> None:
    assert ledger_worker.pages_for(0) == 1 and ledger_worker.pages_for(100) == 1 and ledger_worker.pages_for(101) == 2


# --- the 13-month history ------------------------------------------------------------------


async def _read_first(async_sm, monkeypatch, entries, *, sales_done=True):  # noqa: ANN001, ANN202
    tid, shop = await _shop(async_sm)
    fake = LedgerShop(entries)
    _patch(monkeypatch, tid, fake)
    from app.workers import sales as sales_worker

    monkeypatch.setattr(sales_worker, "_build_client", lambda ctx, http, settings, **_: fake)
    ctx = _ctx(async_sm)
    await _estimated(async_sm, shop, ctx)
    async with async_sm() as s:
        await ledger_worker.begin(s, await s.get(LedgerSync, shop))
        if sales_done:
            s.add(SalesSync(connection_id=shop, tenant_id=tid, state="complete"))
        await s.commit()
    assert (await ledger_worker.sync_ledger(ctx, str(shop))).startswith("complete:")
    assert ctx["enqueue"][-1][0] == "backfill_ledger"  # queued by the first read finishing
    fake.calls.clear()
    return tid, shop, fake, ctx


def _old(n: int, day_of) -> list[dict[str, Any]]:  # noqa: ANN001
    return [{"entry_id": 10_000 + i, "ledger_type": "transaction", "amount": -10, "currency": "USD",
             "created_timestamp": day_of(i)} for i in range(n)]


async def test_the_history_waits_for_the_sales_read_then_fills_back_to_13_months(async_sm, monkeypatch) -> None:
    # 20 recent entries, and 450 spread over the nine months before the first window.
    old = _old(450, lambda i: NOW - (100 + i * 270 // 450) * 86400)
    tid, shop, fake, ctx = await _read_first(async_sm, monkeypatch, _entries(20) + old, sales_done=False)
    assert await ledger_worker.backfill_ledger(ctx, str(shop)) == "sales-first" and not fake.calls
    async with async_sm() as s:
        s.add(SalesSync(connection_id=shop, tenant_id=tid, state="complete"))
        await s.commit()

    first = await _row(async_sm, shop)
    monkeypatch.setattr(ledger_worker, "CHUNK_PAGES", 4)
    assert (await ledger_worker.backfill_ledger(ctx, str(shop))).startswith("reading:")
    mid = await _row(async_sm, shop)
    assert mid.backfill_state == "reading" and mid.backfill_requests == 4
    assert mid.backfill_target < mid.covered_from < first.window_start and mid.covered_from % 86400 == 0
    assert ctx["enqueue"][-1][0] == "backfill_ledger"  # the next chunk

    for _ in range(40):
        if (await ledger_worker.backfill_ledger(ctx, str(shop))).startswith("complete"):
            break
    done = await _row(async_sm, shop)
    assert done.backfill_state == "complete" and done.covered_from == done.backfill_target
    assert await _total(async_sm, "transaction") == -4500  # every old entry once, none twice
    # Slices never overlap, and none reaches into what the first read covered.
    spans = sorted({(c[0], c[1]) for c in fake.calls})
    assert all(a[1] < b[0] for a, b in zip(spans, spans[1:])) and spans[-1][1] == first.window_start - 1
    assert await ledger_worker.backfill_ledger(ctx, str(shop)) == "not-due"


async def test_the_history_keeps_to_the_daily_cap_and_carries_on_where_it_stopped(async_sm, monkeypatch) -> None:
    old = _old(350, lambda i: NOW - 100 * 86400 - i)  # one slice, four pages
    tid, shop, fake, ctx = await _read_first(async_sm, monkeypatch, _entries(5) + old)
    async with async_sm() as s:  # most of today's cap is used: two requests left above the reserve
        sync = await s.get(LedgerSync, shop)
        sync.requests_day = datetime.now(timezone.utc).date()
        sync.requests_today = LedgerSync.DAILY_REQUESTS - LedgerSync.BACKFILL_RESERVE - 2
        await s.commit()
    assert await ledger_worker.backfill_ledger(ctx, str(shop)) == "reading:200"
    assert await ledger_worker.backfill_ledger(ctx, str(shop)) == "paced"
    mid = await _row(async_sm, shop)
    assert (mid.backfill_state, mid.slice_offset) == ("waiting", 200) and "today's share" in mid.backfill_note
    assert len(fake.calls) == 2

    async with async_sm() as s:  # the next day
        (await s.get(LedgerSync, shop)).requests_today = 0
        await s.commit()
    for _ in range(40):
        if (await ledger_worker.backfill_ledger(ctx, str(shop))).startswith("complete"):
            break
    assert fake.calls[2][3] == 200  # from the kept offset, not from the top
    assert await _total(async_sm, "transaction") == -3500


async def test_a_refused_history_read_leaves_the_recent_figures_alone(async_sm, monkeypatch) -> None:
    tid, shop, fake, ctx = await _read_first(async_sm, monkeypatch, _entries(5))
    fake.refuse = True
    assert await ledger_worker.backfill_ledger(ctx, str(shop)) == "failed"
    sync = await _row(async_sm, shop)
    assert sync.state == "complete" and sync.backfill_state == "failed" and "answered 403" in sync.backfill_note
    assert sync.lock_until is None and await _total(async_sm, "prolist") == -500


async def test_starting_the_read_again_does_not_count_the_first_pages_twice(async_sm, monkeypatch) -> None:
    tid, shop, fake, ctx = await _read_first(async_sm, monkeypatch, _entries(30))
    async with async_sm() as s:
        sync = await s.get(LedgerSync, shop)
        sync.state = "failed"
        await ledger_worker.begin(s, sync)
        await s.commit()
    assert await ledger_worker.sync_ledger(ctx, str(shop)) == "complete:30"
    assert await _total(async_sm, "prolist") == -3000
