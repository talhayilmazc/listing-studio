"""The second round of what the 100-seller load test found (docs/load-test.md).

* no database connection across slow work (core/db_release.py), and only when nothing can be lost;
* Postgres ends a transaction left idle, the log says so, and a job that hits it goes on;
* a short queue: drafts handed over in turn, a global cap, go-lives first, reconcile spread out;
* heavy mockup jobs in turn, with a real position; one seller's 100 do not block the others;
* one draft job per slot, and a second job for a finished draft is not a failure.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import db_release, image_work
from app.core.config import get_settings, set_settings_override
from app.db.base import Base
from app.db.models import EtsyConnection, Job, JobStatus, JobType, ListingPublication, MockupPhoto, PlannedSlot, Tenant
from app.db.session import get_engine
from app.etsy.api import EtsyApiClient
from app.workers import fair, plans, reconcile as reconcile_worker, recovery, schedule
from app.workers import publish as publish_worker
from tests.test_end_to_end import _run_drafts, _send_drafts, _world
from tests.test_migration_0068 import scratch  # noqa: F401  (fixture)
from tests.test_reconcile_etsy import _upkeep


async def _sessions(tmp_path) -> tuple[Any, async_sessionmaker]:  # noqa: ANN001
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.db'}", connect_args={"timeout": 60})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _tenant(sm: async_sessionmaker) -> uuid.UUID:
    async with sm() as s:
        t = Tenant(email=f"{uuid.uuid4()}@example.com", password_hash="!")
        s.add(t)
        await s.commit()
        return t.id


async def _shop(sm: async_sessionmaker, tenant_id: uuid.UUID) -> uuid.UUID:
    number = uuid.uuid4().int % 10**9
    async with sm() as s:
        shop = EtsyConnection(tenant_id=tenant_id, etsy_user_id=number, shop_id=number, shop_name="S")
        s.add(shop)
        await s.commit()
        return shop.id


# -- core/db_release.py --------------------------------------------------------------------------


async def test_a_read_only_transaction_is_ended_and_its_objects_stay_usable(tmp_path) -> None:  # noqa: ANN001
    engine, sm = await _sessions(tmp_path)
    tid = await _tenant(sm)
    async with sm() as s:
        tenant = await s.get(Tenant, tid)
        assert s.in_transaction()
        assert await db_release.before_slow() == 1
        assert not s.in_transaction()
        assert tenant.email.endswith("@example.com")  # not expired: no lazy load on an async session
    await engine.dispose()


async def test_a_transaction_with_anything_to_lose_is_kept(tmp_path) -> None:  # noqa: ANN001
    engine, sm = await _sessions(tmp_path)
    tid = await _tenant(sm)
    async with sm() as s:  # a change not flushed yet
        tenant = await s.get(Tenant, tid)
        tenant.email = "changed@example.com"
        assert await db_release.before_slow() == 0 and s.in_transaction()
        await s.rollback()
    async with sm() as s:  # a change flushed, not committed
        tenant = await s.get(Tenant, tid)
        tenant.email = "changed@example.com"
        await s.flush()
        assert not s.dirty
        assert await db_release.before_slow() == 0 and s.in_transaction()
        await s.rollback()
    async with sm() as s:  # a bulk statement is a write too, once flushed... and a row lock
        await s.execute(select(Tenant).where(Tenant.id == tid).with_for_update())
        assert await db_release.before_slow() == 0 and s.in_transaction()
        await s.commit()
        await s.get(Tenant, tid)  # after the commit the lock is gone: it may go again
        s.expire_all()
        assert await db_release.before_slow() == 1
    async with sm() as s:  # a savepoint
        async with s.begin_nested():
            await s.get(Tenant, tid)
            assert await db_release.before_slow() == 0
        await s.rollback()
    async with async_sessionmaker(engine, expire_on_commit=True)() as s:  # its objects would expire
        await s.get(Tenant, tid)
        assert await db_release.before_slow() == 0 and s.in_transaction()
    async with sm() as s:  # nothing was lost by any of it
        assert (await s.get(Tenant, tid)).email != "changed@example.com"
    await engine.dispose()


async def test_only_this_tasks_own_sessions(tmp_path) -> None:  # noqa: ANN001
    engine, sm = await _sessions(tmp_path)
    tid = await _tenant(sm)
    async with sm() as mine:
        await mine.get(Tenant, tid)

        async def other_task() -> int:
            return await db_release.before_slow()

        assert await asyncio.create_task(other_task()) == 0
        assert mine.in_transaction(), "another task's session, which may be in the middle of a query"
    await engine.dispose()


async def test_an_etsy_request_holds_no_transaction(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from app.etsy import guard

    monkeypatch.setattr(guard, "check", lambda what="": None)
    engine, sm = await _sessions(tmp_path)
    tid = await _tenant(sm)
    seen: list[bool] = []

    async with sm() as s:
        await s.get(Tenant, tid)

        async def answer(request: httpx.Request) -> httpx.Response:
            seen.append(s.in_transaction())
            return httpx.Response(200, json={"listing_id": 1, "state": "draft"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
            client = EtsyApiClient(client_id="k", shared_secret="s", http_client=http)
            await client.get_listing(1, access_token="1.t")
    assert seen == [False]
    await engine.dispose()


async def test_image_work_runs_on_its_own_lower_priority_threads_with_no_transaction(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    import threading

    engine, sm = await _sessions(tmp_path)
    tid = await _tenant(sm)
    names: list[str] = []
    async with sm() as s:
        await s.get(Tenant, tid)

        def work(n: int) -> int:
            names.append(threading.current_thread().name)
            return n + 1

        assert await image_work.run(work, 1) == 2
        assert not s.in_transaction()
    assert names[0].startswith("image-work")
    asked: list[tuple] = []
    monkeypatch.setattr(image_work.os, "setpriority", lambda *a: asked.append(a), raising=False)
    image_work._lower_priority()
    assert asked and asked[0][-1] == get_settings().image_work_nice > 0

    def refused(*_: Any) -> None:
        raise OSError("not allowed")

    monkeypatch.setattr(image_work.os, "setpriority", refused, raising=False)
    image_work._lower_priority()  # only a priority: the work still runs
    await engine.dispose()


async def test_preparing_a_template_photo_holds_no_transaction_during_the_analysis(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """The longest found: 37 s idle in a transaction per photo, across the analysis."""
    from app.db.models import MockupSubgroup, MockupTemplate
    from app.pipeline import mockups
    from app.pipeline.storage import LocalStorage
    from app.workers import mockups as worker

    engine, sm = await _sessions(tmp_path)
    tid = await _tenant(sm)
    storage = LocalStorage(tmp_path / "files")
    storage.put("t/source.jpg", b"not looked at", "image/jpeg")
    async with sm() as s:
        template = MockupTemplate(tenant_id=tid, name="T")
        s.add(template)
        await s.flush()
        group = MockupSubgroup(tenant_id=tid, template_id=template.id, name="T")
        s.add(group)
        await s.flush()
        photo = MockupPhoto(tenant_id=tid, template_id=template.id, subgroup_id=group.id, name="P", position=1, status="preparing",
                            source_key="t/source.jpg", print_area_source="default", size=1.0, fold_strength=1.0,
                            rights_confirmed_at=datetime.now(timezone.utc))
        s.add(photo)
        await s.commit()
        photo_id = photo.id
    during: list[bool] = []

    async with sm() as s:
        def analysis(*_: Any, **__: Any) -> Any:
            during.append(s.in_transaction())
            raise mockups.MockupError("stop here")

        monkeypatch.setattr(mockups, "prepare_blank", analysis)
        done = await worker.prepare(s, storage, photo_id)
    assert during == [False]
    assert done.status == "failed"  # the failure still says so on the photo
    await engine.dispose()


# -- the pool and the idle-transaction timeout ----------------------------------------------------


def test_connections_are_kept_and_an_idle_transaction_has_a_limit(test_settings) -> None:  # noqa: ANN001
    set_settings_override(test_settings.model_copy(update={
        "database_url": "postgresql+asyncpg://u:p@localhost:1/none", "db_pool_size": 7, "db_pool_overflow": 3,
        "db_idle_transaction_seconds": 45}))
    engine = get_engine.__wrapped__()
    assert engine.pool.size() == 7 and engine.pool._max_overflow == 3
    args = engine.dialect.create_connect_args(engine.url)
    assert args is not None
    assert get_settings().db_idle_transaction_seconds == 45
    # (The setting itself reaches Postgres: the next test, on a real one.)


async def test_postgres_ends_an_idle_transaction_and_the_log_says_so(test_settings, caplog: pytest.LogCaptureFixture) -> None:  # noqa: ANN001
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("needs Postgres")
    set_settings_override(test_settings.model_copy(update={"database_url": url, "db_idle_transaction_seconds": 1}))
    engine = get_engine.__wrapped__()
    try:
        async with AsyncSession(engine) as s:
            try:
                assert (await s.execute(text("SHOW idle_in_transaction_session_timeout"))).scalar_one() == "1s"
            except OSError:
                pytest.skip("no Postgres to connect to")
            await asyncio.sleep(1.6)
            with caplog.at_level(logging.ERROR, logger="app.db.session"):
                with pytest.raises(Exception):  # noqa: B017, PT011 - which one is asyncpg's: the log is the point
                    await s.execute(text("SELECT 1"))
    finally:
        await engine.dispose()
    assert "idle_in_transaction_session_timeout fired" in caplog.text


async def test_a_job_whose_connection_was_lost_goes_on(tmp_path) -> None:  # noqa: ANN001
    from sqlalchemy.exc import InterfaceError

    engine, sm = await _sessions(tmp_path)
    tid = await _tenant(sm)
    shop = await _shop(sm, tid)
    async with sm() as s:
        job = Job(tenant_id=tid, connection_id=shop, type=JobType.create_draft, status=JobStatus.running, payload={}, max_attempts=5)
        s.add(job)
        await s.commit()
        job_id = job.id
    queued: list[tuple] = []

    async def enqueue(*args: Any, **kwargs: Any) -> None:
        queued.append(args)

    lost = InterfaceError("SELECT 1", {}, Exception("connection was closed in the middle of operation"))
    async with sm() as s:
        assert await recovery.after_failure({"enqueue": enqueue}, s, job_id, lost, "run_publish_job") == "deferred"
    async with sm() as s:
        job = await s.get(Job, job_id)
        assert job.status is JobStatus.queued and job.paused_reason == recovery.WAIT_INTERRUPTED and queued
    await engine.dispose()


# -- a short queue ---------------------------------------------------------------------------------


def _capture() -> tuple[list[tuple], Any]:
    sent: list[tuple] = []

    async def enqueue(function: str, *args: Any, **kwargs: Any) -> None:
        sent.append((function, *args))

    return sent, enqueue


async def _ending(redis: Any, tenant: str, job: str) -> None:
    """What after_run does for a job that ended, without a database."""
    await redis.srem(fair.OUT.format(tenant), job)
    await redis.srem(fair.ALL_OUT, job)


async def test_all_accounts_together_have_a_short_queue_and_take_turns() -> None:
    redis = FakeAsyncRedis(server=FakeServer())
    sent, enqueue = _capture()
    # Twenty sellers, each asking for 100 drafts.
    for job in range(100):
        for tenant in range(20):
            await fair.submit(redis, enqueue, f"t{tenant}", f"t{tenant}-{job}")
    assert len(sent) <= fair.DRAFTS_IN_QUEUE, "the queue holds a few, not 2,000"
    assert await redis.scard(fair.ALL_OUT) == len(sent)
    handed = 0
    # As each finishes, the next goes to the next account in turn: nobody waits for one seller.
    seen: list[str] = []
    for _ in range(100):
        function, job = sent[handed]
        handed += 1
        tenant = job.split("-")[0]
        await _ending(redis, tenant, job)
        await fair._hand_over(redis, enqueue)
        seen.append(tenant)
    counts = {t: seen.count(f"t{t}") for t in range(20)}
    assert min(counts.values()) >= 3 and max(counts.values()) <= 8, counts


async def test_one_sellers_3000_drafts_let_anothers_two_through_at_once() -> None:
    redis = FakeAsyncRedis(server=FakeServer())
    sent, enqueue = _capture()
    for job in range(3000):
        await fair.submit(redis, enqueue, "big", f"big-{job}")
    assert len(sent) == fair.PER_ACCOUNT
    await fair.submit(redis, enqueue, "small", "small-1")
    assert ("run_publish_job", "small-1") in sent
    await fair.submit(redis, enqueue, "small", "small-2")
    assert ("run_publish_job", "small-2") in sent


async def test_a_lost_handover_is_repaired_and_the_count_follows(tmp_path) -> None:  # noqa: ANN001
    engine, sm = await _sessions(tmp_path)
    redis = FakeAsyncRedis(server=FakeServer())
    sent, enqueue = _capture()
    tid = await _tenant(sm)
    shop = await _shop(sm, tid)
    async with sm() as s:
        jobs = [Job(tenant_id=tid, connection_id=shop, type=JobType.create_draft, status=JobStatus.queued, payload={}) for _ in range(6)]
        s.add_all(jobs)
        await s.commit()
        ids = [str(j.id) for j in jobs]
    for job in ids:
        await fair.submit(redis, enqueue, str(tid), job)
    assert len(sent) == fair.PER_ACCOUNT
    await redis.sadd(fair.ALL_OUT, "a-job-that-no-longer-exists")  # drift
    async with sm() as s:
        for job in jobs[:2]:
            (await s.get(Job, job.id)).status = JobStatus.succeeded
        await s.commit()
        assert await fair.top_up_accounts(redis, s, enqueue) == 2
    assert await redis.scard(fair.ALL_OUT) == fair.PER_ACCOUNT
    await engine.dispose()


async def test_a_go_live_goes_to_the_front_of_the_queue() -> None:
    calls: list[dict] = []

    class Pool:
        async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> None:
            calls.append({"function": function, **kwargs})

    await schedule._enqueue({"redis": Pool()}, "run_publish_live_job", "job")
    assert calls[0]["_defer_until"] < datetime.now(timezone.utc) - timedelta(minutes=30)


async def test_the_hourly_reconcile_is_spread_over_the_hour_and_cheap_on_the_database(tmp_path) -> None:  # noqa: ANN001
    assert reconcile_worker.spread_seconds(0, 300) == 0
    assert reconcile_worker.spread_seconds(299, 300) < reconcile_worker.SPREAD_OVER_SECONDS
    assert reconcile_worker.spread_seconds(150, 300) == pytest.approx(reconcile_worker.SPREAD_OVER_SECONDS / 2)
    assert reconcile_worker.spread_seconds(0, 0) == 0


# -- heavy mockup jobs ---------------------------------------------------------------------------


async def _finish_heavy(redis: Any, function: str, ref: str, enqueue: Any, seconds: float = 30.0) -> None:
    await fair.after_heavy({"redis": redis, "enqueue": enqueue}, function, ref, seconds)


async def test_one_sellers_100_mockups_do_not_block_everyone_else() -> None:
    redis = FakeAsyncRedis(server=FakeServer())
    sent, enqueue = _capture()
    for n in range(100):
        await fair.submit_heavy(redis, enqueue, "big", "generate_mockups", f"big-{n}", job_id=f"j-big-{n}")
    assert [s[1] for s in sent] == ["big-0"], "one at a time per account"
    await fair.submit_heavy(redis, enqueue, "small", "generate_mockups", "small-0", job_id="j-small-0")
    assert sent[-1][1] == "small-0", "the other seller's first goes at once: the turns have room"
    await fair.submit_heavy(redis, enqueue, "later", "generate_mockups", "later-0", job_id="j-later-0")
    assert len(sent) == 2, "all turns taken"
    await _finish_heavy(redis, "generate_mockups", "big-0", enqueue)
    # The next turn is not big's 2nd of 100: the seller who has had none goes first.
    assert sent[-1][1] == "later-0"
    await _finish_heavy(redis, "generate_mockups", "small-0", enqueue)
    assert sent[-1][1] == "big-1"


async def test_the_position_is_real_and_counts_every_accounts_jobs() -> None:
    redis = FakeAsyncRedis(server=FakeServer())
    sent, enqueue = _capture()
    await redis.hset(fair.HEAVY_SECONDS, "generate_mockups", "120")
    for n in range(4):
        await fair.submit_heavy(redis, enqueue, "a", "generate_mockups", f"a-{n}", job_id=f"ja{n}")
    for n in range(4):
        await fair.submit_heavy(redis, enqueue, "b", "generate_mockups", f"b-{n}", job_id=f"jb{n}")
    # a-0 and b-0 run. a-1 is next among a's: one of b's goes around it in turn.
    a = await fair.positions(redis, "a")
    assert a["a-0"].ahead == 0 and a["a-0"].minutes == 0
    assert a["a-1"].ahead == 2 + 1
    assert a["a-2"].ahead == 2 + 1 + 2
    assert a["a-1"].ahead < a["a-2"].ahead < a["a-3"].ahead and a["a-1"].minutes <= a["a-3"].minutes
    assert a["a-1"].minutes >= 1
    # Another account's list says nothing of a's refs.
    assert not {k for k in await fair.positions(redis, "b") if k.startswith("a-")}
    assert await fair.positions(redis, "nobody") == {}
    assert await fair.positions(None, "a") == {}


async def test_the_estimate_follows_how_long_jobs_take() -> None:
    redis = FakeAsyncRedis(server=FakeServer())
    sent, enqueue = _capture()
    await fair.submit_heavy(redis, enqueue, "a", "prepare_mockup_template", "p0", job_id="p0")
    await _finish_heavy(redis, "prepare_mockup_template", "p0", enqueue, seconds=100.0)
    assert float(await redis.hget(fair.HEAVY_SECONDS, "prepare_mockup_template")) == 100.0
    await fair.submit_heavy(redis, enqueue, "a", "prepare_mockup_template", "p1", job_id="p1")
    await _finish_heavy(redis, "prepare_mockup_template", "p1", enqueue, seconds=200.0)
    assert float(await redis.hget(fair.HEAVY_SECONDS, "prepare_mockup_template")) == pytest.approx(120.0)


async def test_a_lost_heavy_turn_is_let_go_and_a_stub_enqueuer_still_works() -> None:
    redis = FakeAsyncRedis(server=FakeServer())
    sent, enqueue = _capture()
    await fair.submit_heavy(redis, enqueue, "a", "generate_mockups", "r0", job_id="j0")
    await fair.submit_heavy(redis, enqueue, "a", "generate_mockups", "r1", job_id="j1")
    assert len(sent) == 1
    await redis.hset(fair.HEAVY_OUT, fair._heavy_key("generate_mockups", "r0"), '{"tenant": "a", "at": 1}')
    assert await fair._hand_over_heavy(redis, enqueue, forget_after=60.0) == 1
    assert sent[-1][1] == "r1"
    direct, enqueue2 = _capture()
    assert await fair.submit_heavy(None, enqueue2, "a", "generate_mockups", "r9", job_id="j9") is True
    assert direct == [("generate_mockups", "r9")]


async def test_the_api_says_where_a_waiting_photo_stands(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from app.api import mockups as api

    redis = FakeAsyncRedis(server=FakeServer())
    sent, enqueue = _capture()
    tenant = type("T", (), {"id": uuid.uuid4()})()
    other = uuid.uuid4()
    await fair.submit_heavy(redis, enqueue, other, "prepare_mockup_template", "busy-1", job_id="x1")
    await fair.submit_heavy(redis, enqueue, str(uuid.uuid4()), "prepare_mockup_template", "busy-2", job_id="x2")
    await fair.submit_heavy(redis, enqueue, tenant.id, "prepare_mockup_template", "mine", job_id="m")
    standing = await api._queue_positions(redis, tenant)
    assert standing["mine"].ahead == 2 and standing["mine"].minutes >= 1

    class Broken:
        async def lrange(self, *_: Any) -> None:
            raise RuntimeError("redis is down")

    assert await api._queue_positions(Broken(), tenant) == {}, "a line of information never fails a page"


# -- one draft per slot; a second job is not a failure ------------------------------------------


async def test_two_cron_runs_at_once_release_a_slot_once(scratch) -> None:  # noqa: ANN001, F811
    """On Postgres: its row locks are the point, and this is what overlapped in the load test."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.db.models import Asset, AssetStatus, GeneratedContent, GroupPlan, UploadBatch, UploadBatchStatus

    engine: AsyncEngine = create_async_engine(scratch.url.render_as_string(hide_password=False).replace("+psycopg", "+asyncpg"))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sm = async_sessionmaker(engine, expire_on_commit=False)
        tid = await _tenant(sm)
        shop = await _shop(sm, tid)
        now = datetime.now(timezone.utc)
        async with sm() as s:
            batch = UploadBatch(tenant_id=tid, status=UploadBatchStatus.ready, file_count=20)
            s.add(batch)
            await s.flush()
            plan = GroupPlan(tenant_id=tid, batch_id=batch.id, settings={})
            s.add(plan)
            await s.flush()
            for n in range(20):
                asset = Asset(tenant_id=tid, batch_id=batch.id, original_filename=f"{n}.png", status=AssetStatus.processed, rank=1, group_key=f"G{n}")
                s.add(asset)
                await s.flush()
                content = GeneratedContent(tenant_id=tid, batch_id=batch.id, asset_id=asset.id, approved=True, title="t", tags=[], description="d")
                s.add(content)
                await s.flush()
                s.add(PlannedSlot(tenant_id=tid, plan_id=plan.id, content_id=content.id, connection_id=shop,
                                  draft_at=now - timedelta(minutes=1), publish_at=now + timedelta(hours=2), state="waiting"))
            await s.commit()
        enqueued: list[str] = []

        async def enqueue(function: str, job_id: str) -> None:
            enqueued.append(job_id)

        async def one_run() -> int:
            async with sm() as s:
                return await plans.release_due(s, enqueue)

        released = await asyncio.gather(*(one_run() for _ in range(4)))
        async with sm() as s:
            jobs = list((await s.execute(select(Job).where(Job.type == JobType.create_draft))).scalars())
            states = [slot.state for slot in (await s.execute(select(PlannedSlot))).scalars()]
        keys = [j.payload["content_id"] for j in jobs]
        assert len(keys) == len(set(keys)) == 20, "one draft job per slot"
        assert sum(released) == 20 and sorted(enqueued) == sorted(str(j.id) for j in jobs)
        assert set(states) == {"drafting"}
    finally:
        await engine.dispose()


async def test_a_second_job_for_a_finished_draft_is_not_a_failure(tmp_path, test_settings, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    jobs = await _send_drafts(w)
    await _run_drafts(w, jobs)
    made = len(w["etsy"].listings)
    async with w["sm"]() as s:
        first = await s.get(Job, uuid.UUID(jobs[0]))
        second = Job(tenant_id=first.tenant_id, connection_id=first.connection_id, type=JobType.create_draft, batch_id=first.batch_id,
                     status=JobStatus.queued, payload=dict(first.payload))
        s.add(second)
        await s.commit()
        second_id = second.id
    assert await publish_worker.run_publish_job(w["ctx"], str(second_id)) == "succeeded"
    assert len(w["etsy"].listings) == made, "never a second draft"
    async with w["sm"]() as s:
        job = await s.get(Job, second_id)
        assert job.status is JobStatus.succeeded and job.last_error is None and job.payload["already_there"] is True
        assert (await s.execute(select(ListingPublication))).scalars().first() is not None
    await w["engine"].dispose()




async def test_reconcile_writes_what_did_not_change_in_one_statement(tmp_path, test_settings, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=3)
    _upkeep(w, monkeypatch)
    await _run_drafts(w, await _send_drafts(w))
    statements: list[str] = []
    engine = w["engine"]

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _seen(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        if statement.lstrip().upper().startswith("UPDATE LISTING_PUBLICATION"):
            statements.append(statement)

    assert (await reconcile_worker.reconcile_listings(w["ctx"], str(w["shops"][0]))).startswith("reconcile:3")
    assert len(statements) == 1, statements
    async with w["sm"]() as s:
        rows = list((await s.execute(select(ListingPublication))).scalars())
    assert all(r.etsy_checked_at is not None and r.state == "draft" for r in rows)
    await w["engine"].dispose()


# -- connections are for queries, not for answers ---------------------------------------------------


def test_every_route_ends_its_session_before_the_answer_is_sent() -> None:
    """A slow client must not keep a pooled connection (the load test): with the default scope
    the session lasted until the response had been sent."""
    from pathlib import Path

    import app.api

    sources = {f.name: f.read_text(encoding="utf-8") for f in Path(app.api.__file__).parent.glob("*.py")}
    assert not [name for name, text in sources.items() if "Depends(get_session)" in text]
    assert sum(text.count('Depends(get_session, scope="function")') for text in sources.values()) > 100


async def test_the_session_is_closed_when_the_answer_is_sent(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from fastapi import Depends, FastAPI
    from fastapi.responses import JSONResponse
    from httpx import ASGITransport, AsyncClient

    from app.api.deps import get_session

    engine, sm = await _sessions(tmp_path)
    open_when_sending: list[bool] = []
    used: list[AsyncSession] = []

    async def session_dep():  # noqa: ANN202
        async with sm() as s:
            used.append(s)
            yield s

    class Watching(JSONResponse):
        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            open_when_sending.append(used[0].in_transaction())
            await super().__call__(scope, receive, send)

    app = FastAPI()
    app.dependency_overrides[get_session] = session_dep

    @app.get("/x", response_class=Watching)
    async def route(session: AsyncSession = Depends(get_session, scope="function")) -> dict:
        await session.execute(select(Tenant))
        assert session.in_transaction()
        return {"name": "n"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        assert (await client.get("/x")).json() == {"name": "n"}
    assert open_when_sending == [False]
    await engine.dispose()


async def test_a_written_listing_waits_through_a_busy_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    class Session:
        tries = 0

        async def connection(self) -> None:
            Session.tries += 1
            if Session.tries < 3:
                raise PoolTimeout("QueuePool limit reached")

    await db_release.ready_to_save(Session())
    assert Session.tries == 3

    class Never:
        async def connection(self) -> None:
            raise PoolTimeout("QueuePool limit reached")

    with pytest.raises(PoolTimeout):
        await db_release.ready_to_save(Never(), tries=2)


def test_the_listing_is_saved_only_after_a_connection_is_held() -> None:
    import inspect

    from app.api import another_shop
    from app.pipeline import generation

    source = inspect.getsource(generation.generate_listing_content)
    assert source.index("ready_to_save") < source.index("session.add(content)")
    assert "ready_to_save" in inspect.getsource(another_shop._write)


async def test_quota_uses_no_transaction_across_its_redis_reads(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from app.api import meta
    from app.core import limits

    engine, sm = await _sessions(tmp_path)
    tid = await _tenant(sm)
    during: list[bool] = []
    real = limits.etsy_ceiling
    async with sm() as s:
        tenant = await s.get(Tenant, tid)

        async def watching(quota: Any, t: Any, now: Any = None) -> Any:
            during.append(s.in_transaction())
            return await real(quota, t, now)

        monkeypatch.setattr(limits, "etsy_ceiling", watching)
        from fakeredis import FakeAsyncRedis as Fake

        from app.etsy.rate_limiter import DailyQuota

        redis = Fake()
        out = await meta.get_quota_status(None, tenant, DailyQuota(redis, global_daily_limit=100, pause_percent=90), s, redis)
    assert during == [False]
    assert out.history[-1].count == out.ceiling.used
    await engine.dispose()
