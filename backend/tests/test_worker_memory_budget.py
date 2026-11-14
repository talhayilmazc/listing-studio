"""The worker's memory budget for heavy work (fix/worker-memory-budget, workers/memory_budget.py).

A PSD conversion, a mockup run and a large upload being prepared reserve their estimated peak before they
start; what does not fit waits (queued again a few seconds later, never a failed try); a killed job's share
comes back when its lease runs out; and drafts and go-lives never take from it, so a full budget never holds
one up.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import Asset, AssetStatus, ListingPublication
from app.pipeline.ingest import BatchIngestor
from app.workers import memory_budget as budget
from app.workers import mockups as mockup_work
from app.workers import psd as psd_work
from app.workers import uploads
from app.workers.schedule import release_scheduled_publishes
from tests.test_end_to_end import _run_drafts, _send_drafts, _world
from tests.test_publish_api import ctx  # noqa: F401  (fixture)

APP = Path(__file__).resolve().parents[1] / "app"


async def test_what_fits_is_taken_and_what_does_not_waits() -> None:
    redis = FakeAsyncRedis()
    psd = await budget.take(redis, "convert_psd", budget.PSD_MB)
    assert psd and await budget.held(redis) == 1200
    assert await budget.take(redis, "generate_mockups", budget.MOCKUP_RUN_MB) is None  # 1,850 > 1,350
    small = await budget.take(redis, "process_upload", budget.upload_mb(4000, 3000))  # 12 MP: 115 MB
    assert small and await budget.held(redis) == 1315
    await budget.let_go(redis, psd)
    assert await budget.take(redis, "generate_mockups", budget.MOCKUP_RUN_MB)
    # Bigger than the whole budget: runs when nothing else holds any, so it is never stuck.
    empty = FakeAsyncRedis()
    assert await budget.take(empty, "huge", 5000) and await budget.take(empty, "next", 10) is None


def test_an_uploads_reservation_follows_its_pixels() -> None:
    assert budget.upload_mb(4000, 3000) == 115  # 12 MP phone photo
    assert budget.upload_mb(8000, 6000) == 160  # 48 MP
    assert budget.upload_mb(15000, 16600) == 411  # 249 MP design (measured 395)
    assert budget.upload_mb(None, None) == budget.UPLOAD_BASE_MB


async def test_a_crashed_heavy_job_gives_back_its_share_when_the_lease_runs_out() -> None:
    redis = FakeAsyncRedis()
    now = time.time()
    # A conversion whose worker was killed: nothing lets go, nothing renews.
    assert await budget.take(redis, "convert_psd", budget.PSD_MB, now=now)
    assert await budget.take(redis, "generate_mockups", budget.MOCKUP_RUN_MB, now=now + 30) is None
    assert await budget.held(redis, now=now + budget.LEASE_SECONDS - 1) == 1200
    # The lease runs out: the share is back, and the next job takes it.
    later = now + budget.LEASE_SECONDS + 1
    assert await budget.held(redis, now=later) == 0
    assert await budget.take(redis, "generate_mockups", budget.MOCKUP_RUN_MB, now=later)
    # A job that ends normally, fails or is cancelled lets go at once.
    for ending in ("fine", "raises", "cancelled"):
        clean = FakeAsyncRedis()

        async def work(ending: str = ending) -> None:
            async with budget.reserve(clean, "generate_mockups", budget.MOCKUP_RUN_MB) as fits:
                assert fits and await budget.held(clean) == budget.MOCKUP_RUN_MB
                if ending == "raises":
                    raise RuntimeError("the run broke")
                if ending == "cancelled":
                    await asyncio.sleep(60)

        task = asyncio.create_task(work())
        await asyncio.sleep(0.05)
        if ending == "cancelled":
            task.cancel()  # arq's job timeout
        await asyncio.gather(task, return_exceptions=True)
        assert await budget.held(clean) == 0, ending


async def test_a_running_job_keeps_its_lease_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = FakeAsyncRedis()
    monkeypatch.setattr(budget, "RENEW_SECONDS", 0.05)
    async with budget.reserve(redis, "convert_psd", 1200) as fits:
        assert fits
        before = (await redis.zrange(budget.BUDGET_KEY, 0, -1, withscores=True))[0][1]
        await asyncio.sleep(0.2)
        after = (await redis.zrange(budget.BUDGET_KEY, 0, -1, withscores=True))[0][1]
        assert after > before


async def test_three_heavy_jobs_queued_at_once_run_within_the_budget(ctx, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """A PSD conversion, a mockup run and a 249 MP upload, queued together: the real job entrypoints, a worker
    loop that runs what is queued (and queued again), the work itself stood in for by a wait that samples what
    the budget holds. Never more than the budget; each runs once; none fails for waiting."""
    redis = FakeAsyncRedis()
    peaks: list[int] = []
    ran: list[str] = []

    async def working(name: str) -> str:
        ran.append(name)
        for _ in range(4):
            peaks.append(await budget.held(redis))
            await asyncio.sleep(0.02)
        return "done"

    async def convert(_ctx, upload_id):  # noqa: ANN001, ANN202
        return await working("convert_psd")

    async def mockup_run(session, storage, run_id):  # noqa: ANN001, ANN202
        await working("generate_mockups")

    async def prepare(self, asset_id):  # noqa: ANN001, ANN202
        return await working("process_upload")

    monkeypatch.setattr(psd_work, "_convert", convert)
    monkeypatch.setattr(mockup_work, "run", mockup_run)
    monkeypatch.setattr(BatchIngestor, "process_stored", prepare)
    async with ctx["sm"]() as s:
        asset = (await s.execute(select(Asset))).scalars().first()
        if asset is None:
            from tests.test_publish_api import _add_content

            await _add_content(ctx["sm"], ctx["tenant_id"])
            asset = (await s.execute(select(Asset))).scalars().first()
        asset.status, asset.width, asset.height, asset.storage_key = AssetStatus.uploaded, 15000, 16600, "x/incoming/a.png"
        await s.commit()
        asset_id = str(asset.id)

    queue: list[tuple[str, tuple]] = [("convert_psd", (str(uuid.uuid4()),)), ("generate_mockups", (str(uuid.uuid4()),)),
                                      ("process_upload", (asset_id,))]
    waits: list[str] = []

    async def enqueue(function: str, *args: Any, **options: Any) -> None:
        waits.append(function)
        queue.append((function, args))

    @asynccontextmanager
    async def no_session():  # noqa: ANN202
        yield None

    jobs = {"convert_psd": psd_work.convert_psd, "generate_mockups": mockup_work.generate_mockups,
            "process_upload": uploads.process_upload}
    outcomes: list[str] = []
    for _ in range(50):  # the worker: everything queued at once, then what was queued again
        if not queue:
            break
        batch, queue[:] = list(queue), []
        results = await asyncio.gather(*(
            jobs[f]({"redis": redis, "enqueue": enqueue, "sessionmaker": ctx["sm"] if f == "process_upload" else no_session},
                    *args) for f, args in batch))
        outcomes += [str(r) for r in results]
    assert sorted(ran) == ["convert_psd", "generate_mockups", "process_upload"]
    assert max(peaks) <= budget.BUDGET_MB, max(peaks)
    assert waits, "something had to wait: the three do not fit together"
    # Each ran once ("missing": the stood-in run has no run row to report); the rest of the time they waited.
    assert sorted(o for o in outcomes if o not in ("waiting", "waiting_turn")) == ["done", "done", "missing"], outcomes
    assert await budget.held(redis) == 0  # everything let go


async def test_a_go_live_due_while_the_budget_is_full_runs_on_time(tmp_path, test_settings: Settings,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """The real path: drafts made on a fake Etsy, a go-live set for now, the budget full (a PSD conversion
    holds all of it and a mockup run waits). The release cron hands the go-live over and the job takes it
    live at once; neither takes from the budget, which is still full afterwards."""
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=2)
    await _run_drafts(w, await _send_drafts(w))
    keys = [i["key"] for i in (await w["client"].get("/api/scheduled")).json()["items"]]
    if not keys:  # drafts only: put one on the schedule, then make it due
        spread = {"start_date": (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(), "per_shop_per_day": 5,
                  "window_start": "09:00", "window_end": "17:00", "spacing_minutes": 30, "stagger_minutes": 5}
        assert (await w["client"].post(f"/api/batches/{w['batch']}/send", json={"when": "spread", "schedule": spread})).status_code == 200
        keys = [i["key"] for i in (await w["client"].get("/api/scheduled")).json()["items"]]
    assert (await w["client"].post("/api/scheduled/apply", json={"op": "publish_now", "keys": keys[:1]})).json()["changed"] == 1
    async with w["sm"]() as s:
        publication = (await s.execute(select(ListingPublication).where(ListingPublication.scheduled_for.is_not(None)))).scalars().first()
        publication.scheduled_for = datetime.now(timezone.utc) - timedelta(seconds=30)  # due now
        await s.commit()

    redis = w["ctx"]["redis"]
    psd = await budget.take(redis, "convert_psd", budget.BUDGET_MB)
    assert psd and await budget.take(redis, "generate_mockups", budget.MOCKUP_RUN_MB) is None  # full

    started = time.perf_counter()
    await release_scheduled_publishes(w["ctx"])
    live = [args[1] for args in w["enqueued"] if args[0] == "run_publish_live_job"]
    assert live, "the go-live was not handed over"
    from app.workers import publish as publish_worker

    assert await publish_worker.run_publish_live_job(w["ctx"], live[0]) == "succeeded"
    took = time.perf_counter() - started
    assert "active" in {d["state"] for d in w["etsy"].listings.values()}
    assert took < 5, f"the go-live took {took:.1f} s with the budget full"
    assert await budget.held(redis) == budget.BUDGET_MB  # it took nothing from the budget
    await w["engine"].dispose()


def test_only_heavy_work_reserves_memory() -> None:
    """Drafts, go-lives, publishing, listing writing and syncs never call the budget: a full budget cannot
    hold them up. The only callers are the heavy jobs."""
    callers = sorted(p.relative_to(APP).as_posix() for p in APP.rglob("*.py")
                     if "memory_budget" in p.read_text(encoding="utf-8") and p.name != "memory_budget.py")
    assert callers == ["workers/fair.py", "workers/psd.py", "workers/uploads.py"], callers
    fair = (APP / "workers" / "fair.py").read_text(encoding="utf-8")
    # In fair.py only the heavy mockup jobs' guard reserves; drafts' fair turns do not.
    assert fair.count("memory_budget.reserve(") == 1 and "async def heavy(" in fair
    for light in ("publish.py", "schedule.py", "writing.py", "plans.py", "profiles.py", "sales.py", "ledger.py"):
        assert "memory_budget" not in (APP / "workers" / light).read_text(encoding="utf-8"), light
