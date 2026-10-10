"""Fifty drafts at once, through the real queue and the real limiter.

Small runs worked; ten to fifteen at once failed. So this runs fifty
``run_publish_job`` jobs on arq's own worker (ten at a time, its retries,
deferrals and time limit), through the app's own token bucket and daily quota,
against an Etsy that behaves badly the way the real one does:

* 429s, including one request refused more times than the client retries;
* slow answers, and answers that never arrive although Etsy did the work
  (a draft created, an image stored);
* 5xx, including one that outlasts the client's retries;
* a request that hangs past the worker's time limit.

Only Etsy is mocked, at the HTTP level. The bucket runs 20x faster than
production so some 700 requests take seconds, and the waits between tries are
shortened to match.

**Nothing here can fail because the machine is slow.** It used to, now and
then: the worker's time limit was four real seconds, so on a busy machine
healthy jobs were cut off until one ran out of tries, and the rate was checked
against the wall clock, so a stalled event loop looked like a burst. Now:

* the time limit is hit when the scenario says so (:class:`TimeLimit`), for the
  one request that hangs, and never by a clock; arq's own limit is set far out
  of reach, and so is the run lock that is derived from it;
* the fake Etsy never sleeps: a slow answer yields, a hung one waits to be cut off;
* the rate is checked by counting that every request took its turn from the
  bucket, not by timing them (the bucket's own pacing has its own tests).

What is left of real time is waiting only (the bucket's pacing, the shortened
pauses between tries): it can make the run longer, not make it fail.

What must hold: every one of the fifty ends as a finished draft, Etsy holds
exactly fifty drafts (a retry never makes a second one), each with its images
once and in order, and every request went through the bucket.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs

import arq.worker
import httpx
import pytest
from arq.connections import ArqRedis
from arq.worker import Worker, func
from cryptography.fernet import Fernet
from fakeredis import FakeAsyncRedis, FakeServer
from PIL import Image
from sqlalchemy import event, select
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings, set_settings_override
from app.core.crypto import TokenCipher
from app.db.base import Base
from app.db.models import (
    Asset,
    AssetStatus,
    DraftAttempt,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingPublication,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.etsy import api as etsy_api
from app.etsy import publisher
from app.etsy.connection import ConnectionService
from app.etsy.oauth import TokenResponse
from app.etsy.rate_limiter import DailyQuota, TokenBucket
from app.etsy.usage import UsageRecorder
from app.pipeline.storage import LocalStorage
from app.workers import publish as publish_worker
from app.workers import recovery
from tests.test_publisher import REFERENCE

LISTINGS = 50
RATE = 60.0  # the real bucket, 20x production's 3 a second
SHOP = 301
CHART = 9001  # the profile's size chart, copied onto every draft by its image id


def _png(color: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 400), (color % 255, 90, 160)).save(buf, format="PNG")
    return buf.getvalue()


class _Running:
    """One job as the worker runs it."""

    task: asyncio.Task | None = None
    timed_out = False


_running: ContextVar[_Running | None] = ContextVar("running_job", default=None)


class TimeLimit:
    """The worker's time limit, hit when the scenario says so and not by a clock.

    arq cuts a job off by cancelling it and reports a timeout. ``wrap`` does
    exactly that to the job whose request calls :meth:`hit`: the job is
    cancelled at that point, the app's own handling of an interrupted job runs,
    and arq is told the job timed out. No job is ever cut off for being slow.
    """

    def hit(self) -> None:
        job = _running.get()
        assert job is not None and job.task is not None, "a request outside a job"
        job.timed_out = True
        job.task.cancel()

    def wrap(self, function: Callable[[dict[str, Any], str], Awaitable[Any]]) -> Callable[[dict[str, Any], str], Awaitable[Any]]:
        async def run(ctx: dict[str, Any], job_id: str) -> Any:
            job = _Running()
            token = _running.set(job)
            # The task copies this context, so every request the job makes finds it.
            job.task = asyncio.ensure_future(function(ctx, job_id))
            _running.reset(token)
            try:
                return await job.task
            except asyncio.CancelledError:
                if job.timed_out:
                    raise asyncio.TimeoutError from None  # what arq raises for a job past its limit
                job.task.cancel()
                raise

        return run


class CountingBucket(TokenBucket):
    """The real bucket, counting each turn it gives out."""

    taken = 0

    async def acquire(self, tokens: float = 1.0) -> None:
        await super().acquire(tokens)
        self.taken += 1


class Etsy:
    """Etsy's side of the conversation, with the faults switched on by listing."""

    def __init__(self, time_limit: TimeLimit) -> None:
        self.time_limit = time_limit
        self.listings: dict[int, dict[str, Any]] = {}
        self.sent = 0
        self.faults: Counter[str] = Counter()
        self.once: set[str] = set()
        self.streak: Counter[str] = Counter()
        self.n = 0
        self.gets = 0
        self.inventories = 0
        self._next_id = 7000

    # -- fault helpers ---------------------------------------------------------------
    def first(self, key: str) -> bool:
        """True the first time ``key`` is asked about."""
        if key in self.once:
            return False
        self.once.add(key)
        return True

    def fault(self, kind: str) -> None:
        self.faults[kind] += 1

    def title_of(self, listing_id: int) -> str:
        return self.listings[listing_id]["title"]

    # -- the transport ---------------------------------------------------------------
    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.n += 1
        self.sent += 1
        method, path = request.method, request.url.path.removeprefix("/v3/application")
        if self.n % 13 == 0:
            # Other requests get in before this one is answered.
            self.fault("slow answer")
            await asyncio.sleep(0)
        if self.n % 37 == 0:
            self.fault("429")
            return httpx.Response(429, headers={"retry-after": "0.1"}, json={"error": "Exceeded per second rate limit"})

        if method == "GET" and path == f"/shops/{SHOP}/sections":
            return httpx.Response(200, json={"results": []})
        if method == "GET" and path.startswith("/seller-taxonomy/nodes/"):
            return httpx.Response(200, json={"results": []})
        if method == "GET" and path == f"/shops/{SHOP}/listings":
            drafts = sorted(self.listings.values(), key=lambda row: row["listing_id"], reverse=True)[:25]
            return httpx.Response(200, json={"count": len(drafts), "results": [
                {"listing_id": d["listing_id"], "title": d["title"], "state": "draft",
                 "original_creation_timestamp": d["created"]} for d in drafts]})
        if method == "POST" and path == f"/shops/{SHOP}/listings":
            return self.create(request)
        if m := re.fullmatch(r"/listings/(\d+)", path):
            return self.read(int(m.group(1)))
        if m := re.fullmatch(r"/listings/(\d+)/images", path):
            return httpx.Response(200, json={"results": [{"listing_image_id": i} for i in self.listings[int(m.group(1))]["images"]]})
        if m := re.fullmatch(r"/listings/(\d+)/inventory", path):
            return self.inventory(int(m.group(1)), request)
        if m := re.fullmatch(rf"/shops/{SHOP}/listings/(\d+)/images", path):
            return await self.image(int(m.group(1)), request)
        if m := re.fullmatch(rf"/shops/{SHOP}/listings/(\d+)", path):  # an edited title between tries
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": f"no route for {method} {path}"})

    def create(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        title = form["title"]
        # One draft is refused five times running: more than the client retries.
        if "Design 07" in title and self.streak["create 07"] < 5:
            self.streak["create 07"] += 1
            self.fault("429 past the client's retries")
            return httpx.Response(429, headers={"retry-after": "0.05"}, json={"error": "Exceeded per second rate limit"})
        # An error of Etsy's own, and nothing created.
        if "Design 11" in title and self.first("create 11"):
            self.fault("create: 500, nothing created")
            return httpx.Response(500, json={"error": "internal"})
        listing_id = self._next_id = self._next_id + 1
        self.listings[listing_id] = {
            "listing_id": listing_id, "title": title, "state": "draft", "created": int(time.time()),
            "taxonomy_id": int(form["taxonomy_id"]), "who_made": form["who_made"], "when_made": form["when_made"],
            "production_partners": [{"production_partner_id": int(p)} for p in form.get("production_partner_ids", "").split(",") if p],
            "images": [], "inventory": None, "tags": form.get("tags", ""),
        }
        # Etsy made the draft; the answer never arrives.
        if ("Design 03" in title or "Design 21" in title) and self.first(f"lost create {title}"):
            self.fault("create: draft made, answer lost")
            raise httpx.ReadTimeout("no answer", request=request)
        return httpx.Response(201, json={"listing_id": listing_id})

    def read(self, listing_id: int) -> httpx.Response:
        self.gets += 1
        if self.gets % 45 == 0:
            self.fault("read: no answer")
            raise httpx.ReadTimeout("no answer")
        row = self.listings.get(listing_id)
        if row is None:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json={k: row[k] for k in ("listing_id", "title", "state", "taxonomy_id", "who_made", "when_made", "production_partners")})

    def inventory(self, listing_id: int, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":  # the draft's read-back: what was stored
            stored = self.listings[listing_id]["inventory"]
            return httpx.Response(200, json=json.loads(stored) if stored else {"products": []})
        title = self.title_of(listing_id)
        # Down four times running: more than the client retries.
        if "Design 30" in title and self.streak["inventory 30"] < 4:
            self.streak["inventory 30"] += 1
            self.fault("5xx past the client's retries")
            return httpx.Response(503, json={"error": "unavailable"})
        self.inventories += 1
        if self.inventories % 9 == 0:
            self.fault("inventory: 502")
            return httpx.Response(502, json={"error": "bad gateway"})
        self.listings[listing_id]["inventory"] = request.content
        return httpx.Response(200, json={})

    async def image(self, listing_id: int, request: httpx.Request) -> httpx.Response:
        row = self.listings[listing_id]
        title, have = row["title"], len(row["images"])
        body = request.content
        copied = re.search(rb"listing_image_id=(\d+)", body)
        name = int(copied.group(1)) if copied else re.search(rb'filename="([^"]+)"', body).group(1).decode()
        # Hangs past the worker's time limit, nothing stored.
        if "Design 40" in title and have == 0 and self.first("hang 40"):
            self.fault("upload: hangs past the job's time limit")
            self.time_limit.hit()  # the job's time is up now
            await asyncio.Event().wait()  # never answered: the job is cut off here
        # Refused by Etsy's own error, nothing stored.
        if "Design 15" in title and have == 0 and self.first("503 upload 15"):
            self.fault("upload: 503, nothing stored")
            return httpx.Response(503, json={"error": "unavailable"})
        row["images"].append(name)
        # Stored; the answer never arrives.
        if ("Design 05" in title and have == 1 and self.first("lost upload 05")) or (
            "Design 25" in title and copied and self.first("lost copy 25")
        ):
            self.fault("upload: image stored, answer lost")
            raise httpx.ReadTimeout("no answer", request=request)
        return httpx.Response(201, json={"listing_image_id": 1})


class _Httpx:
    """``httpx`` for the worker, with every client talking to the fake."""

    Timeout = httpx.Timeout

    def __init__(self, etsy: Etsy) -> None:
        self._transport = httpx.MockTransport(etsy.handle)

    def AsyncClient(self, **kwargs: Any) -> httpx.AsyncClient:  # noqa: N802
        return httpx.AsyncClient(transport=self._transport, **kwargs)


async def _seed(sm: async_sessionmaker, storage: LocalStorage, cipher: TokenCipher) -> tuple[uuid.UUID, list[str]]:
    async with sm() as s:
        tenant = Tenant(email="volume@example.com", password_hash="!", etsy_ceiling_override=5000)
        s.add(tenant)
        await s.flush()
        service = ConnectionService(cipher, client_id="k", token_url="t")
        shop = await service.save_from_tokens(
            s, tenant.id, TokenResponse(access_token=f"{SHOP}.abc", refresh_token="r", expires_in=3600), ["listings_w"]
        )
        shop.shop_id, shop.shop_name = SHOP, "Volume Shop"
        profile = ListingProfile(
            tenant_id=tenant.id, connection_id=shop.id, name="Standard Tee", reference_listing_id=1,
            content_template="apparel", confirmed=True, fixed_image_ids=[CHART],
            cached_payload={**REFERENCE, "description": "Reference\n\nBody", "images": []},
            updated_at=datetime.now(timezone.utc),
        )
        batch = UploadBatch(tenant_id=tenant.id, status=UploadBatchStatus.ready, file_count=LISTINGS * 2)
        s.add_all([profile, batch])
        await s.flush()
        jobs = []
        for i in range(LISTINGS):
            keys = (f"p/{i:02d}-cover.png", f"p/{i:02d}-back.png")
            for key in keys:
                storage.put(key, _png(i * 5))
            cover = Asset(batch_id=batch.id, tenant_id=tenant.id, original_filename=f"D{i:02d}/front.png", parsed_sku=f"SKU{i:02d}",
                          storage_key=keys[0], processed_key=keys[0], status=AssetStatus.processed, rank=1, group_key=f"D{i:02d}")
            back = Asset(batch_id=batch.id, tenant_id=tenant.id, original_filename=f"D{i:02d}/back.png", parsed_sku=f"SKU{i:02d}",
                         storage_key=keys[1], processed_key=keys[1], status=AssetStatus.processed, rank=2, group_key=f"D{i:02d}",
                         mime_type="image/png")
            s.add_all([cover, back])
            await s.flush()
            content = GeneratedContent(
                tenant_id=tenant.id, batch_id=batch.id, asset_id=cover.id, approved=True, listing_profile_id=profile.id,
                title=f"Design {i:02d} Retro Graphic Tee", tags=[f"tag{t}" for t in range(13)], description="Body",
            )
            s.add(content)
            await s.flush()
            job = Job(tenant_id=tenant.id, connection_id=shop.id, type=JobType.create_draft, batch_id=batch.id,
                      status=JobStatus.queued, payload={"content_id": str(content.id), "profile_id": str(profile.id)})
            s.add(job)
            await s.flush()
            jobs.append(str(job.id))
        await s.commit()
        return tenant.id, jobs


async def test_fifty_drafts_at_once_all_finish_with_no_duplicate(tmp_path, test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    set_settings_override(test_settings.model_copy(update={
        # The run lock lasts worker_job_timeout + 60 s: far longer than this test on any machine.
        "storage_dir": str(tmp_path / "storage"), "thumbnail_size": 300, "worker_job_timeout": 900,
        "etsy_client_id": "k", "etsy_client_secret": "s",
    }))
    # A database of its own on disk: every job gets its own connection, as in production.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'volume.db'}", connect_args={"timeout": 60})

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    cipher = TokenCipher(Fernet.generate_key())
    tenant_id, jobs = await _seed(sm, LocalStorage(tmp_path / "storage"), cipher)

    time_limit = TimeLimit()
    etsy = Etsy(time_limit)
    monkeypatch.setattr(publish_worker, "httpx", _Httpx(etsy))
    monkeypatch.setattr(publish_worker, "get_cipher", lambda: cipher)
    # The waits between tries, shortened like the bucket; the logic is untouched.
    monkeypatch.setattr(etsy_api, "transient_wait", lambda attempt: 0.01)
    monkeypatch.setattr(publisher, "ADOPT_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(publisher, "NOT_YET_SECONDS", 0.3)
    monkeypatch.setattr(publisher, "RECREATE_AFTER_SECONDS", 0.2)
    monkeypatch.setattr(recovery, "MIN_RATE_WAIT", 0.2)
    monkeypatch.setattr(recovery, "INTERRUPTED_WAIT", 0.2)
    monkeypatch.setattr(recovery, "outage_wait", lambda attempt: 0.2)

    async def _no_info(*_: Any, **__: Any) -> None:  # the fake Redis has no INFO command
        return None

    monkeypatch.setattr(arq.worker, "log_redis_info", _no_info)
    server = FakeServer()
    redis = FakeAsyncRedis(server=server)
    pool = ArqRedis(connection_pool=redis.connection_pool)
    quota = DailyQuota(redis, global_daily_limit=5000, pause_percent=90)

    bucket: dict[str, CountingBucket] = {}

    async def startup(ctx: dict[str, Any]) -> None:
        ctx["sessionmaker"] = sm
        ctx["usage"] = UsageRecorder()
        ctx["bucket"] = bucket["it"] = CountingBucket(ctx["redis"], rate=RATE)
        ctx["quota"] = quota

    for job_id in jobs:  # all fifty at once, as "Create drafts for all" does
        await pool.enqueue_job("run_publish_job", job_id)
    worker = Worker(
        functions=[func(time_limit.wrap(publish_worker.run_publish_job), name="run_publish_job")],
        redis_pool=pool, on_startup=startup, burst=True, handle_signals=False, poll_delay=0.05,
        # arq's own limit is out of reach: only TimeLimit cuts a job off.
        max_jobs=10, job_timeout=3600, max_tries=5,
    )
    try:
        # Not a deadline the run is expected to come near: it only stops a hung test.
        await asyncio.wait_for(worker.main(), timeout=900)
    finally:
        await worker.close()

    async with sm() as s:
        rows = list((await s.execute(select(Job))).scalars())
        publications = list((await s.execute(select(ListingPublication))).scalars())
        attempts = await s.scalar(select(sa_func.count()).select_from(DraftAttempt))
    await engine.dispose()

    # The scenario really was a bad day: every kind of fault happened.
    for kind in ("429", "429 past the client's retries", "create: draft made, answer lost", "create: 500, nothing created",
                 "upload: image stored, answer lost", "upload: 503, nothing stored", "upload: hangs past the job's time limit",
                 "5xx past the client's retries", "read: no answer", "inventory: 502", "slow answer"):
        assert etsy.faults[kind] >= 1, f"the scenario must include: {kind}"

    # Every one finished, and none is still waiting or left "running".
    failed = [(r.status.value, r.last_error) for r in rows if r.status is not JobStatus.succeeded]
    assert not failed, failed
    assert len(publications) == LISTINGS and attempts == 0
    # A retry never made a second draft: Etsy holds exactly the fifty, each one known to the app.
    assert len(etsy.listings) == LISTINGS
    assert {p.etsy_listing_id for p in publications} == set(etsy.listings)
    assert len({d["title"] for d in etsy.listings.values()}) == LISTINGS
    # Each draft has its images once and in order: cover, the second image, the size chart.
    for draft in etsy.listings.values():
        names = draft["images"]
        assert len(names) == 3 and names[0].endswith("-thumb.jpg") and names[2] == CHART, (draft["title"], names)
        assert draft["inventory"] is not None
    # Every request, retries included, took its turn from the bucket and was counted against the day.
    assert bucket["it"].taken == etsy.sent
    assert (await quota.usage(tenant_id))[0] == etsy.sent
    # The jobs that could not finish in one go waited and ran again, rather than failing.
    assert sum(r.attempts for r in rows) >= 4
