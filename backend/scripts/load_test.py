"""Load test: does Listyro hold with 100 accounts? Rerunnable; synthetic data only; never the
development database, never Etsy.

    docker compose run --rm --entrypoint python tests scripts/load_test.py
        [--accounts 100] [--shops 3] [--listings 500] [--seconds 30] [--sellers 20] [--keep]

1. A scratch Postgres database (``listyro_load``, beside the development one; dropped at the
   end unless ``--keep``) gets ``accounts`` × ``shops`` shops × ``listings`` listings per shop:
   half written by the app and half by hand, 15% with a shop's own SKU and title, ~70% drafted or
   live in their shop and a third of those in a second shop too, go-lives scheduled, a group
   schedule's slots, mockup runs with their outputs, a draft job per draft, the shop's listing
   cache. Every account has its three shops in one group.
2. Page times: the API runs in this process (one event loop, as one uvicorn worker), against the
   scratch database, with an in-memory Redis. Each page is the requests its screen makes
   (Overview, Batches, a batch page with its shop workspace, review, Scheduled), timed one by
   one and all at once, with their SQL query counts; queries over 100 ms are listed.
3. ``sellers`` sellers at once, each opening pages one after another for ``seconds``: p50, p95
   and p99 per request, requests per second, errors.
4. The queue: arq's own worker (10 jobs at a time) against an Etsy stub (latency, some 429 and
   5xx answers) through the app's own token bucket at 10 requests a second: drafts and go-lives
   per minute, how long seller B's go-live waits while seller A has 3,000 drafts queued (with the
   fair turns of workers/fair.py), and how evenly the Etsy limiter is shared between accounts.
5. Memory: this process's peak and the database's size.

Prints a report; exits 1 if a page or the p95 is over its target.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import random
import resource
import statistics
import sys
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy import event, insert  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.core.config import get_settings, set_settings_override  # noqa: E402
from app.db import models as m  # noqa: E402
from app.db.base import Base  # noqa: E402

SCRATCH = "listyro_load"
PAGE_TARGET_MS = 500
P95_TARGET_MS = 500
SLOW_MS = 100
NOW = datetime.now(timezone.utc)
TAGS = [f"tag word {i}" for i in range(13)]
DESCRIPTION = "A soft cotton tee with a retro print. " * 8


_URLS: tuple[str, str] | None = None


def _urls() -> tuple[str, str]:
    """(the development database's server URL to create/drop on, the scratch URL); read once,
    before the settings point the app at the scratch database."""
    global _URLS
    if _URLS is not None:
        return _URLS
    url = sa.engine.make_url(get_settings().database_url.replace("+asyncpg", "+psycopg"))
    if url.get_backend_name() != "postgresql":
        raise SystemExit("needs Postgres (DATABASE_URL)")
    _URLS = url.render_as_string(hide_password=False), url.set(database=SCRATCH).render_as_string(hide_password=False)
    return _URLS


def _fresh_database() -> str:
    admin_url, scratch_url = _urls()
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH}"'))
        c.execute(sa.text(f'CREATE DATABASE "{SCRATCH}"'))
    admin.dispose()
    return scratch_url.replace("+psycopg", "+asyncpg")


def _drop_database() -> None:
    admin_url, _ = _urls()
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH}"'))
    admin.dispose()


# --- 1. Seeding ------------------------------------------------------------------------------------


class Rows:
    """Rows per table, inserted in chunks in foreign-key order."""

    ORDER = ["tenant", "etsy_connection", "shop_group", "shop_group_member", "listing_profile", "profile_shop_link",
             "upload_batch", "asset", "generated_content", "job", "listing_publication", "group_plan", "planned_slot",
             "shop_listing_cache", "mockup_template", "mockup_subgroup", "mockup_photo", "mockup_design", "mockup_run",
             "mockup_output"]

    def __init__(self) -> None:
        self.by_table: dict[str, list[dict]] = defaultdict(list)

    def add(self, table: str, **row: Any) -> dict:
        self.by_table[table].append(row)
        return row

    async def flush(self, engine) -> dict[str, int]:  # noqa: ANN001
        counts = {}
        tables = Base.metadata.tables
        async with engine.begin() as conn:
            for name in self.ORDER:
                rows = self.by_table.pop(name, [])
                for start in range(0, len(rows), 2000):
                    await conn.execute(insert(tables[name]), rows[start:start + 2000])
                counts[name] = len(rows)
        return counts


def _account(rows: Rows, n: int, shops: int, listings: int, terms_version: str) -> dict:
    rnd = random.Random(n)
    tid = uuid.uuid4()
    rows.add("tenant", id=tid, email=f"load{n:03d}@example.invalid", password_hash="!", terms_version=terms_version,
             terms_accepted_at=NOW, time_zone="America/Chicago")
    shop_ids = []
    for s in range(shops):
        sid = uuid.uuid4()
        shop_ids.append(sid)
        rows.add("etsy_connection", id=sid, tenant_id=tid, etsy_user_id=n * 10 + s + 1, shop_id=n * 10 + s + 1,
                 shop_name=f"LoadShop{n:03d}{s}", position=s, scopes=["listings_r", "listings_w", "shops_r", "shops_w"],
                 listing_counts={"active": listings // 2, "draft": listings // 4}, listing_counts_at=NOW)
    gid = uuid.uuid4()
    rows.add("shop_group", id=gid, tenant_id=tid, name="All shops")
    for sid in shop_ids:
        rows.add("shop_group_member", group_id=gid, connection_id=sid)
    profiles = []
    for s, sid in enumerate(shop_ids):
        pid = uuid.uuid4()
        profiles.append(pid)
        rows.add("listing_profile", id=pid, tenant_id=tid, connection_id=sid, name=f"Regular {s}", confirmed=True,
                 content_template="apparel", reference_listing_id=900000 + n * 10 + s, cached_payload={"title": "Ref"},
                 updated_at=NOW)
        for other in shop_ids:
            if other != sid:
                rows.add("profile_shop_link", tenant_id=tid, profile_id=pid, connection_id=other, status="ready")
    template = uuid.uuid4()
    rows.add("mockup_template", id=template, tenant_id=tid, name="Comfort Colors")
    subgroup = uuid.uuid4()
    rows.add("mockup_subgroup", id=subgroup, tenant_id=tid, template_id=template, name="Comfort Colors")
    photos = []
    for p in range(10):
        pid = uuid.uuid4()
        photos.append(pid)
        rows.add("mockup_photo", id=pid, tenant_id=tid, template_id=template, subgroup_id=subgroup, name=f"Colour {p}",
                 rights_confirmed_at=NOW, confirmed_at=NOW, position=p, status="ready")
    per_batch = 150
    total = shops * listings
    batches = []
    listing_ids = 0
    for b in range((total + per_batch - 1) // per_batch):
        bid = uuid.uuid4()
        batches.append(bid)
        rows.add("upload_batch", id=bid, tenant_id=tid, status="ready", file_count=per_batch,
                 created_at=NOW - timedelta(days=30 - b))
        plan = None
        for i in range(min(per_batch, total - b * per_batch)):
            k = b * per_batch + i
            shop = k % shops
            asset, content = uuid.uuid4(), uuid.uuid4()
            sku = f"L{n:03d}B{b:02d}{i:03d}"
            rows.add("asset", id=asset, batch_id=bid, tenant_id=tid, original_filename=f"{sku}/front.png", parsed_sku=sku,
                     group_key=sku, rank=1, status="processed", storage_key=None, processed_key=f"{tid}/{bid}/p/{sku}.jpg")
            manual = k % 2 == 1
            overrides = None
            if rnd.random() < 0.15:
                other = shop_ids[(shop + 1) % shops]
                overrides = {str(other): {"sku": f"X{sku}", "title": f"Shop title {sku}"}}
            approved = rnd.random() < 0.8
            rows.add("generated_content", id=content, tenant_id=tid, batch_id=bid, asset_id=asset,
                     title=f"Retro Graphic Tee {sku} Vintage Sunset Shirt", tags=TAGS, description=DESCRIPTION,
                     approved=approved, approved_at=NOW if approved else None, listing_profile_id=profiles[shop],
                     source="manual" if manual else "ai", shop_overrides=overrides)
            if not approved or rnd.random() > 0.7:
                continue
            for target in [shop] + ([(shop + 1) % shops] if rnd.random() < 0.33 else []):
                listing_ids += 1
                lid = (n * 100000 + listing_ids) * 10 + target
                live = rnd.random() < 0.6
                scheduled = not live and rnd.random() < 0.15
                rows.add("job", tenant_id=tid, connection_id=shop_ids[target], type="create_draft", status="succeeded",
                         batch_id=bid, payload={"content_id": str(content)}, attempts=1,
                         created_at=NOW - timedelta(days=20), finished_at=NOW - timedelta(days=20))
                rows.add("listing_publication", tenant_id=tid, content_id=content, connection_id=shop_ids[target],
                         etsy_listing_id=lid, state="active" if live else "draft", title=f"Retro Graphic Tee {sku}", sku=sku,
                         published_at=NOW - timedelta(days=rnd.randint(1, 20)) if live else None,
                         scheduled_for=NOW + timedelta(hours=rnd.randint(1, 240)) if scheduled else None)
                if target == 0 and rnd.random() < 0.3:
                    rows.add("shop_listing_cache", tenant_id=tid, listing_id=lid, connection_id=shop_ids[target],
                             payload={"listing_id": lid, "title": f"Retro Graphic Tee {sku}", "state": "active" if live else "draft",
                                      "skus": [sku]}, fetched_at=NOW)
        if b % 3 == 0:  # a group schedule for some of the batch's approved, not yet drafted listings
            plan = uuid.uuid4()
            rows.add("group_plan", id=plan, tenant_id=tid, batch_id=bid, settings={"per_shop_per_day": 5})
            contents = [r for r in rows.by_table["generated_content"][-per_batch:] if r["approved"]][:20]
            for j, c in enumerate(contents):
                at = NOW + timedelta(hours=2 + j)
                rows.add("planned_slot", tenant_id=tid, plan_id=plan, content_id=c["id"], connection_id=shop_ids[j % shops],
                         draft_at=at - timedelta(hours=2), publish_at=at, state="waiting")
    run = uuid.uuid4()
    rows.add("mockup_run", id=run, tenant_id=tid, batch_id=batches[-1], template_ids=[str(template)],
             photo_ids=[str(p) for p in photos], status="succeeded", total=500, done=500)
    for d in range(50):
        did = uuid.uuid4()
        rows.add("mockup_design", id=did, tenant_id=tid, batch_id=batches[-1], group_key=f"D{n:03d}{d:02d}",
                 original_filename=f"D{n:03d}{d:02d}.png", width=4000, height=4000, key=f"{tid}/d/{d}.png")
        for p, photo in enumerate(photos):
            rows.add("mockup_output", tenant_id=tid, run_id=run, batch_id=batches[-1], design_id=did, photo_id=photo,
                     template_name="Comfort Colors", photo_name=f"Colour {p}", position=p, filename=f"D{d}-{p}.jpg",
                     key=f"{tid}/o/{d}-{p}.jpg", width=2000, height=2000, byte_size=400_000)
    return {"tenant": tid, "shops": shop_ids, "batches": batches}


async def seed(engine, accounts: int, shops: int, listings: int) -> tuple[list[dict], dict[str, int], float]:  # noqa: ANN001
    from app.core.terms import TERMS_VERSION

    started = time.perf_counter()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    worlds, totals = [], Counter()
    for start in range(0, accounts, 10):
        rows = Rows()
        for n in range(start, min(accounts, start + 10)):
            worlds.append(_account(rows, n, shops, listings, TERMS_VERSION))
        totals.update(await rows.flush(engine))
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(sa.text("ANALYZE"))
    return worlds, dict(totals), time.perf_counter() - started


# --- 2–3. Pages and many sellers -------------------------------------------------------------------

current_request: contextvars.ContextVar[list | None] = contextvars.ContextVar("current_request", default=None)
SLOW: list[tuple[float, str]] = []


def _watch_sql(engine) -> None:  # noqa: ANN001
    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        context._t0 = time.perf_counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        ms = (time.perf_counter() - context._t0) * 1000
        bucket = current_request.get()
        if bucket is not None:
            bucket.append(ms)
        if ms > SLOW_MS:
            SLOW.append((ms, " ".join(statement.split())[:300]))


def pages_for(world: dict) -> dict[str, list[str]]:
    b, s = world["batches"][0], world["shops"][1]
    return {
        "Overview": ["/api/account/me", "/api/shops", "/api/shop/summary?all=1", "/api/shop/overview", "/api/attention",
                     "/api/quota", "/api/profiles", "/api/batches", "/api/drafts/unfinished"],
        "Batches": ["/api/account/me", "/api/shops", "/api/batches", "/api/batches-deleted", "/api/shop/summary?all=1"],
        "Batch page + workspace": [f"/api/batches/{b}", f"/api/batches/{b}/groups", f"/api/batches/{b}/content",
                                   f"/api/batches/{b}/shops", f"/api/batches/{b}/shops/{s}/rows", f"/api/batches/{b}/sku-etsy",
                                   "/api/profiles"],
        "Review": [f"/api/batches/{b}", f"/api/batches/{b}/content", f"/api/batches/{b}/shops", f"/api/batches/{b}/send",
                   f"/api/drafts/unfinished?batch_id={b}", f"/api/batches/{b}/sku-etsy"],
        "Scheduled": ["/api/scheduled", "/api/shops", "/api/shop-groups"],
    }


async def _app_clients(engine, worlds: list[dict]):  # noqa: ANN001, ANN202
    from fakeredis import FakeAsyncRedis
    from httpx import ASGITransport, AsyncClient

    from app.api import deps
    from app.core.sessions import SessionStore
    from app.etsy.rate_limiter import DailyQuota
    from app.main import create_app
    from tests.auth_support import BROWSER_HEADERS, authenticate, open_session

    sm = async_sessionmaker(engine, expire_on_commit=False)
    redis = FakeAsyncRedis()

    class Queue:
        async def enqueue(self, *_: Any, **__: Any) -> None:
            return None

    async def _session():  # noqa: ANN202
        from app.db.session import keep_loaded

        async with sm() as s:  # as app/api/deps.py::get_session
            keep_loaded(s)
            yield s

    app = create_app()
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_redis] = lambda: redis
    app.dependency_overrides[deps.get_session_store] = lambda: SessionStore(redis)
    app.dependency_overrides[deps.get_quota] = lambda: DailyQuota(redis, global_daily_limit=100000, pause_percent=90)
    app.dependency_overrides[deps.get_enqueuer] = lambda: Queue()
    clients = []
    for world in worlds:
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=BROWSER_HEADERS, timeout=120)
        authenticate(client, await open_session(redis, world["tenant"]))
        clients.append(client)
    return clients


async def _get(client, url: str) -> tuple[float, int, int]:  # noqa: ANN001
    queries: list = []
    token = current_request.set(queries)
    t0 = time.perf_counter()
    try:
        res = await client.get(url)
    finally:
        current_request.reset(token)
    return (time.perf_counter() - t0) * 1000, res.status_code, len(queries)


async def measure_pages(clients, worlds, sample: int = 5) -> tuple[list[str], bool]:  # noqa: ANN001
    lines, ok = [], True
    per_page: dict[str, list[tuple[float, float, int, int]]] = defaultdict(list)
    slowest_request: dict[str, tuple[float, str, int]] = {}
    for client, world in list(zip(clients, worlds))[:sample]:
        for page, urls in pages_for(world).items():
            await asyncio.gather(*(client.get(u) for u in urls))  # warm (first request of a page per process)
            serial, queries, errors = 0.0, 0, 0
            for u in urls:
                ms, status, q = await _get(client, u)
                serial += ms
                queries += q
                errors += status >= 400
                key = u.split("/api/")[1].split("?")[0]
                for part in (str(world["batches"][0]), str(world["shops"][1])):
                    key = key.replace(part, "{id}")
                if ms > slowest_request.get(key, (0, "", 0))[0]:
                    slowest_request[key] = (ms, page, q)
            t0 = time.perf_counter()
            await asyncio.gather(*(client.get(u) for u in urls))
            together = (time.perf_counter() - t0) * 1000
            per_page[page].append((serial, together, queries, errors))
    lines.append(f"{'page':<26} {'one by one':>11} {'all at once':>12} {'SQL queries':>12} {'errors':>7}   (worst of {sample} accounts)")
    for page, runs in per_page.items():
        serial = max(r[0] for r in runs)
        together = max(r[1] for r in runs)
        queries = max(r[2] for r in runs)
        errors = sum(r[3] for r in runs)
        flag = "" if together <= PAGE_TARGET_MS and not errors else "  OVER TARGET" if not errors else "  ERRORS"
        ok = ok and not flag
        lines.append(f"{page:<26} {serial:>9.0f}ms {together:>10.0f}ms {queries:>12} {errors:>7}{flag}")
    lines.append("")
    lines.append("slowest requests (ms, page, SQL queries):")
    for key, (ms, page, q) in sorted(slowest_request.items(), key=lambda kv: -kv[1][0])[:8]:
        lines.append(f"  {ms:7.0f}  {key:<40} {page:<24} {q} queries")
    return lines, ok


async def many_sellers(clients, worlds, seconds: int, sellers: int) -> tuple[list[str], bool]:  # noqa: ANN001
    latencies: list[float] = []
    errors = Counter()
    stop = time.perf_counter() + seconds

    async def seller(client, world) -> None:  # noqa: ANN001
        rnd = random.Random(str(world["tenant"]))
        pages = list(pages_for(world).items())
        while time.perf_counter() < stop:
            _, urls = rnd.choice(pages)
            results = await asyncio.gather(*(_get(client, u) for u in urls))
            for ms, status, _ in results:
                latencies.append(ms)
                if status >= 400:
                    errors[status] += 1
            await asyncio.sleep(rnd.uniform(0.2, 1.0))  # reading the page

    await asyncio.gather(*(seller(c, w) for c, w in list(zip(clients, worlds))[:sellers]))
    latencies.sort()
    q = statistics.quantiles(latencies, n=100)
    p95 = q[94]
    lines = [f"{sellers} sellers at once for {seconds}s: {len(latencies)} requests, {len(latencies) / seconds:.0f}/s, "
             f"p50 {q[49]:.0f} ms, p95 {p95:.0f} ms, p99 {q[98]:.0f} ms, max {latencies[-1]:.0f} ms, errors {dict(errors) or 0}"
             + ("" if p95 <= P95_TARGET_MS else "  OVER TARGET")]
    return lines, p95 <= P95_TARGET_MS and not errors


# --- 4. The queue --------------------------------------------------------------------------------


async def queue_run(seconds_after_live: float = 40.0) -> list[str]:
    """arq's worker draining real fair turns against an Etsy stub through the real token bucket."""
    import arq.worker
    from arq.connections import ArqRedis
    from arq.worker import Worker, func
    from fakeredis import FakeAsyncRedis, FakeServer

    from app.etsy.rate_limiter import TokenBucket
    from app.workers import fair

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    redis = FakeAsyncRedis(server=FakeServer())
    pool = ArqRedis(connection_pool=redis.connection_pool)
    bucket = TokenBucket(redis, rate=10.0)  # production: ETSY_REQUESTS_PER_SECOND 10, no burst
    rnd = random.Random(7)
    per_account: Counter[str] = Counter()
    events: list[tuple[float, str, str]] = []
    faults = Counter()

    async def etsy(account: str) -> None:
        """One Etsy request: a turn from the bucket, 80–250 ms, 3% 429 (waits and retries), 1% 5xx (retried)."""
        while True:
            await bucket.acquire()
            per_account[account] += 1
            await asyncio.sleep(rnd.uniform(0.08, 0.25))
            roll = rnd.random()
            if roll < 0.03:
                faults["429"] += 1
                await asyncio.sleep(1.0)
                continue
            if roll < 0.04:
                faults["5xx"] += 1
                continue
            return

    async with sm() as s:
        accounts = {}
        for name in ("A", "B"):
            t = m.Tenant(email=f"q{name}@example.invalid", password_hash="!")
            s.add(t)
            await s.flush()
            shop = m.EtsyConnection(tenant_id=t.id, etsy_user_id=ord(name), shop_id=ord(name), shop_name=name)
            s.add(shop)
            await s.flush()
            accounts[name] = (t.id, shop.id)
        await s.commit()

    async def draft(ctx: dict, job_id: str) -> str:
        try:
            async with sm() as s:
                job = await s.get(m.Job, uuid.UUID(job_id))
                account = "A" if job.tenant_id == accounts["A"][0] else "B"
            for _ in range(14):  # a draft with one photo: ~14 requests (tests/test_publish_volume.py)
                await etsy(account)
            async with sm() as s:
                job = await s.get(m.Job, uuid.UUID(job_id))
                job.status = m.JobStatus.succeeded
                await s.commit()
            events.append((time.perf_counter(), "draft", account))
            return "ok"
        finally:
            await fair.after_run(ctx, job_id)

    async def go_live(ctx: dict, job_id: str) -> str:
        for _ in range(4):  # state read, update, read back, listing read
            await etsy("B")
        events.append((time.perf_counter(), "go_live", "B"))
        return "ok"

    async def startup(ctx: dict) -> None:
        ctx["sessionmaker"] = sm

    async def _no_info(*_: Any, **__: Any) -> None:
        return None

    arq.worker.log_redis_info = _no_info
    a, a_shop = accounts["A"]
    async with sm() as s:
        jobs = [m.Job(tenant_id=a, connection_id=a_shop, type=m.JobType.create_draft, status=m.JobStatus.queued, payload={})
                for _ in range(3000)]
        s.add_all(jobs)
        await s.commit()
    for job in jobs:
        await fair.submit(redis, pool.enqueue_job, a, job.id)
    worker = Worker(functions=[func(draft, name="run_publish_job"), func(go_live, name="run_publish_live_job")],
                    redis_pool=pool, on_startup=startup, handle_signals=False, poll_delay=0.05, max_jobs=10,
                    job_timeout=900, max_tries=5)
    started = time.perf_counter()
    task = asyncio.create_task(worker.main())
    await asyncio.sleep(5)
    b, b_shop = accounts["B"]
    due = time.perf_counter()
    await pool.enqueue_job("run_publish_live_job", str(uuid.uuid4()))
    async with sm() as s:
        b_jobs = [m.Job(tenant_id=b, connection_id=b_shop, type=m.JobType.create_draft, status=m.JobStatus.queued, payload={})
                  for _ in range(20)]
        s.add_all(b_jobs)
        await s.commit()
    for job in b_jobs:
        await fair.submit(redis, pool.enqueue_job, b, job.id)
    while not any(k == "go_live" for _, k, _ in events) and time.perf_counter() - due < 600:
        await asyncio.sleep(0.2)
    live_wait = next(t for t, k, _ in events if k == "go_live") - due
    await asyncio.sleep(seconds_after_live)
    elapsed = time.perf_counter() - started
    task.cancel()
    try:
        await worker.close()
    except Exception:  # noqa: BLE001
        pass
    drafts = Counter(acc for _, k, acc in events if k == "draft")
    b_first = next((t for t, k, acc in events if k == "draft" and acc == "B"), None)
    requests = sum(per_account.values())
    await engine.dispose()
    return [
        f"seller A asked for 3,000 drafts; 5 s later seller B's go-live was due and B asked for 20 drafts:",
        f"  B's go-live ran {live_wait:.1f} s after it was due (with fair turns: behind ≤ {fair.PER_ACCOUNT} of A's drafts)",
        f"  B's first draft was done {b_first - due:.1f} s after B asked" if b_first else "  B's drafts: none finished in the window",
        f"  in {elapsed:.0f} s: drafts A {drafts['A']}, B {drafts['B']}; {requests} Etsy requests = {requests / elapsed:.1f}/s "
        f"(limit 10/s); 429s {faults['429']}, 5xx {faults['5xx']}",
        f"  Etsy requests by account: A {per_account['A']}, B {per_account['B']} "
        f"(B's share {per_account['B'] / max(requests, 1):.0%} with 4 of B's drafts and 4 of A's in flight)",
        f"  throughput: {60 * sum(drafts.values()) / elapsed:.0f} drafts a minute at 10 requests a second "
        f"(~{36000 // 14} an hour at 14 requests a draft; go-lives at 4 requests: ~9,000 an hour)",
    ]


# --- main -----------------------------------------------------------------------------------------


async def main(args: argparse.Namespace) -> int:
    url = _fresh_database()
    settings = get_settings()
    set_settings_override(settings.model_copy(update={"database_url": url, "rate_limit_requests": 10**9}))
    engine = create_async_engine(url, pool_size=20, max_overflow=10)
    report = [f"Listyro load test, {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC: {args.accounts} accounts × {args.shops} "
              f"shops × {args.listings} listings per shop (synthetic, scratch database {SCRATCH})", ""]
    ok = True
    try:
        worlds, counts, took = await seed(engine, args.accounts, args.shops, args.listings)
        report.append(f"seeded in {took:.0f} s: " + ", ".join(f"{k} {v:,}" for k, v in counts.items() if v))
        async with engine.connect() as conn:
            size = (await conn.execute(sa.text(f"SELECT pg_size_pretty(pg_database_size('{SCRATCH}'))"))).scalar_one()
        report += [f"database size: {size}", ""]
        _watch_sql(engine)
        clients = await _app_clients(engine, worlds)
        lines, pages_ok = await measure_pages(clients, worlds)
        report += ["== pages (target: under 500 ms each)", *lines, ""]
        lines, p95_ok = await many_sellers(clients, worlds, args.seconds, args.sellers)
        report += ["== many sellers (target: p95 under 500 ms)", *lines, ""]
        ok = pages_ok and p95_ok
        slow = sorted(SLOW, key=lambda x: -x[0])
        report.append(f"== SQL queries over {SLOW_MS} ms: {len(slow)}")
        seen = set()
        for ms, sql in slow:
            if sql[:120] in seen:
                continue
            seen.add(sql[:120])
            report.append(f"  {ms:6.0f} ms  {sql}")
            if len(seen) >= 10:
                break
        report.append("")
        if not args.skip_queue:
            report += ["== the queue (arq worker, Etsy stub with latency, 429 and 5xx, token bucket at 10/s)",
                       *await queue_run(args.queue_seconds), ""]
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        report += ["== memory", f"  this process (API, one event loop, + seeding) peaked at {peak:.0f} MB", ""]
        for c in clients:
            await c.aclose()
    finally:
        await engine.dispose()
        if not args.keep:
            _drop_database()
    print("\n".join(report))
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--accounts", type=int, default=100)
    parser.add_argument("--shops", type=int, default=3)
    parser.add_argument("--listings", type=int, default=500)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--sellers", type=int, default=20)
    parser.add_argument("--queue-seconds", type=float, default=40.0)
    parser.add_argument("--skip-queue", action="store_true")
    parser.add_argument("--keep", action="store_true", help="keep the scratch database")
    raise SystemExit(asyncio.run(main(parser.parse_args())))
