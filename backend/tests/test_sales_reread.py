"""The one-time sales re-read (shops read before order lines were kept).

All re-reads together keep to a share of the app's daily Etsy budget; shops are
read one at a time and what doesn't fit carries on the next night. A shop that
hasn't had its turn keeps its figures. Progress is shown per shop, to the
account that owns it only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update

from app.db.models import ConnectionStatus, EtsyConnection, SaleLine, SalesDaily, SalesSync, Tenant
from app.workers import sales as worker
from tests.test_admin import world  # noqa: F401  (fixture)
from tests.test_sales import TODAY, EtsyShop, _shop_sales
from tests.test_workers_profiles import FakeService

#: 1,200 sales in the 13 months and 300 older: 12 pages, and a 13th to see the edge.
PAGES = 13


def test_the_plan_spreads_the_queue_over_nights_inside_the_cap() -> None:
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    # 45 requests each, 100 a night: two shops a night, then the rest.
    plan = worker.reread_plan([(a, 45, 0), (b, 45, 0), (c, 45, 0), (d, 45, 0)], cap=100, used_tonight=0, per_shop=250)
    assert [plan[s] for s in (a, b, c, d)] == [0, 0, 1, 1]
    # Tonight is partly used; a big shop is held to its own daily share.
    plan = worker.reread_plan([(a, 600, 50), (b, 45, 0)], cap=1000, used_tonight=980, per_shop=250)
    assert plan == {a: 3, b: 1}  # a: 20 tonight, then 250, 250, 80; b: once a has taken tonight's rest
    assert worker.reread_plan([], cap=1000, used_tonight=0, per_shop=250) == {}


def test_the_nightly_share_is_a_fifth_of_the_apps_budget() -> None:
    assert worker.REREAD_BUDGET_PERCENT == 20 and worker.reread_cap() == 1000


class _Queue(list):
    async def __call__(self, function, *args, **kwargs):  # noqa: ANN001, ANN204
        self.append((function, args, kwargs))


async def _account(sm, shops: int, email: str | None = None) -> tuple[uuid.UUID, list[uuid.UUID], dict[uuid.UUID, EtsyShop]]:
    """An account whose shops were all read before order lines were kept."""
    fakes: dict[uuid.UUID, EtsyShop] = {}
    ids: list[uuid.UUID] = []
    async with sm() as s:
        tenant = Tenant(email=email or f"{uuid.uuid4()}@e.com", password_hash="x")
        s.add(tenant)
        await s.flush()
        for i in range(shops):
            c = EtsyConnection(
                tenant_id=tenant.id, status=ConnectionStatus.active, etsy_user_id=100 + i, shop_id=100 + i,
                shop_name=f"Shop {i + 1}", scopes=["listings_r", "transactions_r"],
                connected_at=datetime(2026, 1, 1 + i, tzinfo=timezone.utc),
                sales_synced_at=datetime.now(timezone.utc),
            )
            s.add(c)
            await s.flush()
            sales = _shop_sales(inside=1200, outside=300)
            for n, sale in enumerate(sales):
                sale["receipt_id"] = 5_000_000 + n  # the order number: what the re-read is for
            fake = EtsyShop(sales)
            newest = fake.sales[0]
            s.add(SalesSync(
                connection_id=c.id, tenant_id=tenant.id, state="complete", has_lines=False, direction="desc",
                total_count=1500, window_count=1200, pages_estimate=PAGES, read_count=1200, requests_used=20,
                newest_ts=newest["created_timestamp"], newest_id=newest["transaction_id"],
                started_at=datetime.now(timezone.utc) - timedelta(days=30), finished_at=datetime.now(timezone.utc) - timedelta(days=30),
                updated_at=datetime.now(timezone.utc),
            ))
            # The figures the earlier read left: they stay until the shop's turn.
            s.add(SalesDaily(connection_id=c.id, listing_id=1, day=TODAY - timedelta(days=2), tenant_id=tenant.id,
                             units=1200, orders=1200, revenue_minor=3_000_000, currency="USD"))
            fakes[c.id] = fake
            ids.append(c.id)
        await s.commit()
        return tenant.id, ids, fakes


def _patch(monkeypatch, tenant_id: uuid.UUID, fakes: dict[uuid.UUID, EtsyShop]) -> None:
    from app.workers import profiles as pw

    monkeypatch.setattr(pw, "_connection_service", lambda settings: FakeService(tenant_id))
    monkeypatch.setattr(worker, "_connection_service", lambda settings: FakeService(tenant_id))
    monkeypatch.setattr(worker, "_build_client", lambda ctx, http, settings, shop=None, **_: fakes[shop])


async def _drain(ctx: dict[str, Any]) -> list[tuple[str, str]]:
    """Run what was queued for now (not what was put off until the reset), as the worker would."""
    ran: list[tuple[str, str]] = []
    seen: set[str] = set()
    queue: _Queue = ctx["enqueue"]
    while queue:
        function, args, options = queue.pop(0)
        key = options.get("_job_id")
        if "_defer_by" in options or function != "sync_sales" or (key and key in seen):
            continue
        seen.add(key)
        ran.append((args[0], await worker.sync_sales(ctx, args[0])))
    return ran


async def _night(sm, ctx: dict[str, Any]) -> list[tuple[str, str]]:
    await worker.sync_all_sales(ctx)
    return await _drain(ctx)


async def _next_day(sm) -> None:
    async with sm() as s:
        await s.execute(update(SalesSync).values(requests_day=TODAY - timedelta(days=1)))
        await s.commit()


async def _state(sm) -> dict[uuid.UUID, SalesSync]:
    async with sm() as s:
        return {r.connection_id: r for r in (await s.execute(select(SalesSync))).scalars()}


async def _used(sm) -> int:
    async with sm() as s:
        return await worker.reread_used(s, datetime.now(timezone.utc).date())


def _reads(fake: EtsyShop) -> int:
    return len(fake.calls)


async def test_rereads_keep_to_the_nightly_share_one_shop_at_a_time_and_carry_on_the_next_night(async_sm, monkeypatch) -> None:
    tenant, shops, fakes = await _account(async_sm, 4)
    _patch(monkeypatch, tenant, fakes)
    monkeypatch.setattr(worker, "reread_cap", lambda: 30)
    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None, "enqueue": _Queue()}
    a, b, c, d = shops

    await _night(async_sm, ctx)
    rows = await _state(async_sm)
    # Two shops fit in tonight's 30 (13 each). The third would need 13 of the 2 left: it is not begun.
    assert [rows[s].reread for s in shops] == ["done", "done", None, None]
    # 13 for the read; the shop's ordinary nightly update (one request) ran after it and is counted too.
    assert (rows[a].requests_used, rows[b].requests_used) == (PAGES + 1, PAGES + 1)
    assert await _used(async_sm) == 2 * (PAGES + 1) <= 30
    # The shops still waiting were not touched: their figures stand and they took only tonight's new sales.
    assert (rows[c].state, rows[c].has_lines, rows[c].read_count) == ("complete", False, 1200)
    assert _reads(fakes[c]) == _reads(fakes[d]) == 1
    async with async_sm() as s:
        kept = (await s.execute(select(SalesDaily.units).where(SalesDaily.connection_id == c))).scalars().all()
        assert kept == [1200]
        lines = dict((await s.execute(select(SaleLine.connection_id, func.count()).group_by(SaleLine.connection_id))).all())
        assert lines == {a: 1200, b: 1200}  # every order line of the 13 months, for the shops that are done

    # Asking again the same night changes nothing: no request, nothing begun.
    before = sum(_reads(f) for f in fakes.values())
    async with async_sm() as s:
        assert await worker.advance_rereads(ctx, s) is None
    assert await _drain(ctx) == [] and sum(_reads(f) for f in fakes.values()) == before

    await _next_day(async_sm)
    await _night(async_sm, ctx)
    rows = await _state(async_sm)
    assert [rows[s].reread for s in shops] == ["done"] * 4
    assert all(rows[s].state == "complete" and rows[s].has_lines for s in shops)
    assert await _used(async_sm) <= 30
    async with async_sm() as s:
        assert await s.scalar(select(func.count()).select_from(SaleLine)) == 4 * 1200

    # Finished: the next night is the ordinary one request per shop, and nothing is read again.
    await _next_day(async_sm)
    for f in fakes.values():
        f.calls.clear()
    await _night(async_sm, ctx)
    assert [_reads(fakes[s]) for s in shops] == [1, 1, 1, 1]
    assert [r.reread for r in (await _state(async_sm)).values()] == ["done"] * 4


async def test_a_big_shop_takes_its_own_daily_share_and_the_night_is_never_exceeded(async_sm, monkeypatch) -> None:
    tenant, shops, fakes = await _account(async_sm, 4)
    _patch(monkeypatch, tenant, fakes)
    monkeypatch.setattr(worker, "reread_cap", lambda: 30)
    monkeypatch.setattr(worker, "CHUNK_PAGES", 4)
    monkeypatch.setattr(SalesSync, "DAILY_REQUESTS", 10)  # each shop needs 13: none finishes in a night
    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None, "enqueue": _Queue()}
    a, b, c, d = shops

    nights = 0
    while any(r.reread != "done" for r in (await _state(async_sm)).values()):
        for f in fakes.values():
            f.calls.clear()
        await _night(async_sm, ctx)
        nights += 1
        rows = await _state(async_sm)
        rereading = [s for s in shops if rows[s].reread is not None]
        # Tonight's re-read requests, counted on the fake Etsy itself: never past the share,
        # and no shop past its own daily share.
        assert sum(_reads(fakes[s]) for s in rereading) <= 30 + len(shops)  # + each shop's nightly update
        assert await _used(async_sm) <= 30
        assert all(worker.used_today(rows[s], datetime.now(timezone.utc).date()) <= 10 for s in shops)
        if nights == 1:
            # 10 + 10 + 10: the night is full exactly, and the fourth shop keeps its figures.
            assert await _used(async_sm) == 30
            assert [rows[s].reread for s in shops] == ["reading", "reading", "reading", None]
            assert all(rows[s].state == "waiting" and rows[s].resumes_at is not None for s in (a, b, c))
            # Not its turn while an earlier shop can still read: it waits without asking Etsy anything.
            await _next_day(async_sm)
            asked = _reads(fakes[b])
            assert await worker.sync_sales(ctx, str(b)) == "reread-waiting"
            assert _reads(fakes[b]) == asked
            assert any(job[1][0] == str(a) for job in ctx["enqueue"])  # the shop whose turn it is was queued
            ctx["enqueue"].clear()
        else:
            await _next_day(async_sm)
        assert nights < 6
    assert nights == 3  # 30, then 3+3+3+10, then the fourth shop's last 3
    async with async_sm() as s:
        assert await s.scalar(select(func.count()).select_from(SaleLine)) == 4 * 1200
        units = dict((await s.execute(select(SalesDaily.connection_id, func.sum(SalesDaily.units)).group_by(SalesDaily.connection_id))).all())
        assert units == {s_: 1200 for s_ in shops}  # rebuilt once each: nothing counted twice


async def test_progress_is_shown_per_shop_to_its_own_account_and_app_wide_to_the_admin(world, monkeypatch) -> None:  # noqa: F811
    sm = world["sm"]
    monkeypatch.setattr(worker, "reread_cap", lambda: 30)
    async with sm() as s:
        for party, at in ((world["bob"], 2), (world["admin"], 3)):
            c = await s.get(EtsyConnection, party.connection_id)
            c.scopes, c.shop_name, c.connected_at = ["transactions_r"], f"shop-{at}", datetime(2026, 1, at, tzinfo=timezone.utc)
            s.add(SalesSync(connection_id=c.id, tenant_id=party.tenant_id, state="complete", has_lines=False,
                            window_count=1200, pages_estimate=PAGES, read_count=1200, requests_used=20))
        # Bob's is under way: 500 of about 1,200 read, 5 requests today.
        bob = await s.get(SalesSync, world["bob"].connection_id)
        bob.reread, bob.state, bob.read_count, bob.requests_used = "reading", "waiting", 500, 5
        bob.requests_day, bob.requests_today, bob.note = datetime.now(timezone.utc).date(), 5, worker.REREAD_NIGHT_FULL
        await s.commit()

    mine = (await world["b"].get("/api/analytics/sales/reread")).json()
    assert mine["active"] is True and [s["shop_name"] for s in mine["shops"]] == ["shop-2"]
    shop = mine["shops"][0]
    assert (shop["status"], shop["read_count"], shop["window_count"], shop["requests_used"], shop["requests_left"]) == ("waiting", 500, 1200, 5, 8)
    today = datetime.now(timezone.utc).date()
    assert shop["finishes_on"] == today.isoformat() and mine["finishes_on"] == today.isoformat()
    assert mine["reads_back_to"] == (today - timedelta(days=SalesDaily.RETENTION_DAYS)).isoformat()
    # Nothing about the app's budget or anyone else's shops reaches a seller.
    assert not {"nightly_cap", "used_tonight", "budget_percent", "email"} & (set(mine) | set(shop))
    assert world["bob"].connection_id != world["admin"].connection_id and "shop-3" not in str(mine)

    # The other account's shop hasn't begun: 8 + 13 fits tonight's 25 left, so it is expected tonight too.
    theirs = (await world["a"].get("/api/analytics/sales/reread")).json()["shops"]
    assert [(s["shop_name"], s["status"], s["read_count"], s["requests_used"], s["requests_left"]) for s in theirs] == [("shop-3", "queued", 0, 0, PAGES)]

    assert (await world["b"].get("/api/admin/sales-reread")).status_code == 404
    assert (await world["anon"].get("/api/analytics/sales/reread")).status_code == 401
    everyone = (await world["a"].get("/api/admin/sales-reread")).json()
    assert (everyone["nightly_cap"], everyone["used_tonight"], everyone["budget_percent"]) == (30, 5, 20)
    assert (everyone["queued"], everyone["reading"], everyone["done"], everyone["requests_left"]) == (1, 1, 0, 8 + PAGES)
    assert {(s["email"], s["shop_name"], s["status"]) for s in everyone["shops"]} == {
        ("bob@example.com", "shop-2", "waiting"), ("admin@example.com", "shop-3", "queued"),
    }

    # The shop's own status says it is the second read.
    assert (await world["b"].get("/api/analytics/sales/status")).json()["reread"] == "reading"
