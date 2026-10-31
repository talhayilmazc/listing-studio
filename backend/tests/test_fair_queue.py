"""Fair turns between accounts (workers/fair.py), on arq's own worker.

One seller's big batch must not hold back another seller: a go-live enqueued after seller A
asked for 300 drafts runs after a few of A's drafts, not after all 300; seller B's drafts asked
for later take turns with A's; a job that ended or waits long lets go of its turn; the minute's
top-up hands over what a lost hand-over left behind. And at most HEAVY_LIMIT heavy mockup jobs
run at once; the next is put back in the queue instead of holding a worker slot.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import arq.worker
import pytest
from arq.connections import ArqRedis
from arq.worker import Worker, func
from fakeredis import FakeAsyncRedis, FakeServer
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import EtsyConnection, Job, JobStatus, JobType, Tenant
from app.workers import fair

DRAFT_SECONDS = 0.02


async def _world(tmp_path):  # noqa: ANN001, ANN202
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fair.db'}", connect_args={"timeout": 60})

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    tenants = {}
    async with sm() as s:
        for name in ("a", "b"):
            t = Tenant(email=f"{name}-{uuid.uuid4()}@example.com", password_hash="!")
            s.add(t)
            await s.flush()
            shop = EtsyConnection(tenant_id=t.id, etsy_user_id=hash(name) % 10**6, shop_id=hash(name) % 10**6, shop_name=name)
            s.add(shop)
            await s.flush()
            tenants[name] = (t.id, shop.id)
        await s.commit()
    return engine, sm, tenants


async def _jobs(sm, tenant_id, shop_id, n: int, kind: JobType = JobType.create_draft) -> list[str]:  # noqa: ANN001
    async with sm() as s:
        rows = [Job(tenant_id=tenant_id, connection_id=shop_id, type=kind, status=JobStatus.queued, payload={})
                for _ in range(n)]
        s.add_all(rows)
        await s.commit()
        return [str(r.id) for r in rows]


def _worker(pool: ArqRedis, sm, finished: list[tuple[str, str]], monkeypatch) -> Worker:  # noqa: ANN001
    async def _no_info(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(arq.worker, "log_redis_info", _no_info)

    async def draft(ctx: dict[str, Any], job_id: str) -> str:
        """A stand-in for run_publish_job: the same turn hand-over in its finally."""
        try:
            await asyncio.sleep(DRAFT_SECONDS)
            async with sm() as s:
                job = await s.get(Job, uuid.UUID(job_id))
                job.status = JobStatus.succeeded
                await s.commit()
                finished.append(("draft:" + str(job.tenant_id), job_id))
            return "succeeded"
        finally:
            await fair.after_run(ctx, job_id)

    async def go_live(ctx: dict[str, Any], job_id: str) -> str:
        finished.append(("go_live", job_id))
        return "succeeded"

    async def startup(ctx: dict[str, Any]) -> None:
        ctx["sessionmaker"] = sm

    return Worker(functions=[func(draft, name="run_publish_job"), func(go_live, name="run_publish_live_job")],
                  redis_pool=pool, on_startup=startup, burst=True, handle_signals=False, poll_delay=0.01,
                  max_jobs=10, job_timeout=600, max_tries=3)


@pytest.mark.parametrize("fair_turns", [True, False])
async def test_a_big_batch_does_not_hold_back_another_sellers_go_live(tmp_path, monkeypatch, fair_turns: bool) -> None:
    engine, sm, tenants = await _world(tmp_path)
    redis = FakeAsyncRedis(server=FakeServer())
    pool = ArqRedis(connection_pool=redis.connection_pool)
    (a, a_shop), (b, b_shop) = tenants["a"], tenants["b"]
    for job_id in await _jobs(sm, a, a_shop, 300):  # seller A: "Create 300 drafts"
        if fair_turns:
            await fair.submit(redis, pool.enqueue_job, a, job_id)
        else:
            await pool.enqueue_job("run_publish_job", job_id)  # what happened before: all 300 at once
    [live] = await _jobs(sm, b, b_shop, 1, JobType.publish_live)
    await pool.enqueue_job("run_publish_live_job", live)  # seller B's go-live, due now
    b_drafts = await _jobs(sm, b, b_shop, 10)
    for job_id in b_drafts:
        if fair_turns:
            await fair.submit(redis, pool.enqueue_job, b, job_id)
        else:
            await pool.enqueue_job("run_publish_job", job_id)
    finished: list[tuple[str, str]] = []
    # The minute's top-up, as the cron runs it, while the worker drains the queue.
    worker = _worker(pool, sm, finished, monkeypatch)
    try:
        await asyncio.wait_for(worker.main(), timeout=300)
    finally:
        await worker.close()
    if fair_turns:
        async with sm() as s:
            while await fair.top_up_accounts(redis, s, pool.enqueue_job):  # nothing may be left held
                worker = _worker(pool, sm, finished, monkeypatch)
                try:
                    await asyncio.wait_for(worker.main(), timeout=300)
                finally:
                    await worker.close()
    order = [kind for kind, _ in finished]
    a_before_live = order[:order.index("go_live")].count(f"draft:{a}")
    b_done_at = max(i for i, (kind, _) in enumerate(finished) if kind == f"draft:{b}")
    async with sm() as s:
        left = [j for j in (await s.execute(select(Job).where(Job.type == JobType.create_draft))).scalars()
                if j.status is not JobStatus.succeeded]
    await engine.dispose()
    assert not left and order.count(f"draft:{a}") == 300 and order.count(f"draft:{b}") == 10
    if fair_turns:
        assert a_before_live <= fair.PER_ACCOUNT, a_before_live  # behind a few of A's drafts, not 300
        assert b_done_at < 60, b_done_at  # B's ten drafts take turns with A's, done long before A's 300
        assert not await redis.scard(fair.OUT.format(a)) and not await redis.llen(fair.HELD.format(a))
    else:
        assert a_before_live >= 290  # the failure this fixes: behind (nearly) every one of A's drafts


async def test_a_turn_is_let_go_by_a_job_that_ended_or_waits_long_and_the_top_up_hands_over(tmp_path) -> None:
    engine, sm, tenants = await _world(tmp_path)
    redis = FakeAsyncRedis(server=FakeServer())
    (a, a_shop) = tenants["a"]
    sent: list[str] = []

    async def enqueue(function: str, job_id: str, **_: Any) -> None:
        sent.append(job_id)

    ids = await _jobs(sm, a, a_shop, 7)
    for job_id in ids:
        await fair.submit(redis, enqueue, a, job_id)
    assert sent == ids[:fair.PER_ACCOUNT] and await redis.llen(fair.HELD.format(a)) == 3
    await fair.submit(redis, enqueue, a, ids[0])  # asked twice: not handed over twice
    assert sent.count(ids[0]) == 1
    async with sm() as s:  # one ended, one waits for the seller (personalization), one is still running
        for job_id, status, paused in ((ids[0], JobStatus.failed, None), (ids[1], JobStatus.queued, "personalization_held"),
                                       (ids[2], JobStatus.running, None)):
            job = await s.get(Job, uuid.UUID(job_id))
            job.status, job.paused_reason = status, paused
        await s.commit()
        assert await fair.top_up_accounts(redis, s, enqueue) == 2  # a lost hand-over: the cron does it
    assert sent == ids[:6] and await redis.llen(fair.HELD.format(a)) == 1
    await engine.dispose()


async def test_at_most_two_heavy_mockup_jobs_run_at_once_the_rest_wait_in_the_queue() -> None:
    put_back: list[tuple[str, dict]] = []

    async def enqueue(function: str, *args: str, **options: Any) -> None:
        put_back.append((function, options))

    running = peak = 0

    async def job(n: int) -> bool:
        nonlocal running, peak
        async with fair.heavy({"enqueue": enqueue}, "generate_mockups", str(n)) as turn:
            if not turn:
                return False
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.05)
            running -= 1
            return True

    ran = await asyncio.gather(*(job(n) for n in range(6)))
    assert peak == fair.HEAVY_LIMIT and sum(ran) == fair.HEAVY_LIMIT
    assert len(put_back) == 6 - fair.HEAVY_LIMIT
    assert all(o["_defer_by"] == fair.HEAVY_DEFER_SECONDS and o["_job_id"].startswith("heavy:generate_mockups:") for _, o in put_back)
