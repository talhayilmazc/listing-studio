"""The sellers (docker-compose.load.yml, docs/load-test.md): N sellers at once, over real HTTP.

    python -m scripts.load.driver --steps 10,25,50,100

Each step, N sellers each do a whole working session together, while reading pages between
actions, and the hourly reconcile and the due go-lives run in the worker meanwhile:

* upload DESIGNS designs × PHOTOS photos, 3 at a time, as the Upload page sends them;
* set the profile, write each group (one request per group, as "Generate all" does), approve all;
* send to two of their shops as drafts;
* every third seller also: a mockup template of PHOTOS photos, DESIGNS designs, a mockup run;
* LIVE drafts of theirs are due to go live during the step (planted when it starts).

Prints, for each step: request times per endpoint and per page, errors, how fast the queue
drained, how late go-lives were, Etsy requests per second and per account, and the peaks of memory,
CPU, database connections, Redis memory and queue depth. Writes it to /output.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import random
import statistics
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from arq.connections import ArqRedis, RedisSettings
from PIL import Image
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.core.sessions import SESSION_COOKIE, SessionStore

API = os.environ.get("LOAD_API", "http://load-app:8000")
FRONTEND = os.environ.get("LOAD_FRONTEND", "")
ORIGIN = "http://load-frontend"
DESIGNS, PHOTOS, LIVE = 5, 3, 12
SLOW_MS = 500

settings = get_settings()
if not settings.database_url.endswith("@load-postgres:5432/listyro_load"):
    raise SystemExit("load driver: not the load stack: refusing to run")
engine = create_async_engine(settings.database_url, pool_size=5)


def _photo(seed: int, side: int = 2000) -> bytes:
    """A photo-like JPEG of about a megabyte: smooth colour with noise, as a real photo compresses."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:side, 0:side]
    base = np.stack([(x / side * 180 + 40), (y / side * 160 + 50), ((x + y) / (2 * side) * 120 + 60)], axis=-1)
    pixels = np.clip(base + rng.normal(0, 7, (side, side, 3)), 0, 255).astype("uint8")
    out = io.BytesIO()
    Image.fromarray(pixels).save(out, format="JPEG", quality=85)
    return out.getvalue()


def _design(seed: int) -> bytes:
    """A design: a transparent PNG with a shape on it."""
    image = Image.new("RGBA", (1500, 1500), (0, 0, 0, 0))
    block = Image.new("RGBA", (900, 900), (20 + seed % 200, 60, 140, 255))
    image.paste(block, (300, 300))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class Meter:
    def __init__(self) -> None:
        self.times: dict[str, list[float]] = defaultdict(list)
        self.errors: Counter[str] = Counter()
        self.pages: dict[str, list[float]] = defaultdict(list)
        self.steps: dict[str, list[float]] = defaultdict(list)
        self.samples: list[tuple[float, float]] = []
        self.sent: list[tuple[float, int]] = []  # what the server sent, as on the wire (gzip)

    def add(self, key: str, ms: float, status: int, detail: str = "") -> None:
        self.times[key].append(ms)
        self.samples.append((time.time(), ms))
        if status >= 400:
            self.errors[f"{status} {key}" + (f": {detail[:160]}" if detail else "")] += 1


class Seller:
    def __init__(self, n: int, world: dict, meter: Meter, photos: list[bytes], designs: list[bytes], redis: Redis) -> None:
        self.n, self.world, self.meter, self.photos, self.designs, self.redis = n, world, meter, photos, designs, redis
        self.rnd = random.Random(n)
        self.client: httpx.AsyncClient | None = None
        self.batch: str | None = None
        self.working = True
        self.seen: set[str] = set()

    async def open(self) -> None:
        token = await SessionStore(self.redis).create(uuid.UUID(self.world["tenant"]))
        # One browser: up to 6 connections to one host.
        self.client = httpx.AsyncClient(base_url=API, timeout=120, headers={"origin": ORIGIN},
                                        cookies={SESSION_COOKIE: token}, limits=httpx.Limits(max_connections=6))

    async def call(self, method: str, url: str, key: str, **kw: Any) -> httpx.Response | None:
        assert self.client is not None
        t0 = time.perf_counter()
        try:
            res = await self.client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            self.meter.add(key, (time.perf_counter() - t0) * 1000, 599, type(exc).__name__)
            return None
        self.meter.add(key, (time.perf_counter() - t0) * 1000, res.status_code, res.text if res.status_code >= 400 else "")
        self.meter.sent.append((time.time(), res.num_bytes_downloaded))
        return res

    # -- reading pages ------------------------------------------------------------------------

    def _pages(self) -> dict[str, list[tuple[str, str]]]:
        w = self.world
        old, shop = w["old_batch"], w["shops"][1]
        batch = self.batch or old
        return {
            "Overview": [("/api/account/me", "account/me"), ("/api/shops", "shops"), ("/api/shop/summary?all=1", "shop/summary"),
                         ("/api/shop/overview", "shop/overview"), ("/api/attention", "attention"), ("/api/quota", "quota"),
                         ("/api/profiles", "profiles"), ("/api/batches", "batches"), ("/api/drafts/unfinished", "drafts/unfinished")],
            "Batches": [("/api/account/me", "account/me"), ("/api/shops", "shops"), ("/api/batches", "batches"),
                        ("/api/batches-deleted", "batches-deleted"), ("/api/shop/summary?all=1", "shop/summary")],
            "Batch page (150 listings)": [
                (f"/api/batches/{old}", "batches/{id}"), (f"/api/batches/{old}/groups", "batches/{id}/groups"),
                (f"/api/batches/{old}/content", "batches/{id}/content"), (f"/api/batches/{old}/shops", "batches/{id}/shops"),
                (f"/api/batches/{old}/shops/{shop}/rows", "workspace rows"), (f"/api/batches/{old}/sku-etsy", "batches/{id}/sku-etsy"),
                ("/api/profiles", "profiles")],
            "Review (the seller's batch)": [
                (f"/api/batches/{batch}", "batches/{id}"), (f"/api/batches/{batch}/content", "batches/{id}/content"),
                (f"/api/batches/{batch}/shops", "batches/{id}/shops"), (f"/api/batches/{batch}/send", "batches/{id}/send"),
                (f"/api/drafts/unfinished?batch_id={batch}", "drafts/unfinished"), (f"/api/batches/{batch}/sku-etsy", "batches/{id}/sku-etsy")],
            "Scheduled": [("/api/scheduled", "scheduled"), ("/api/shops", "shops"), ("/api/shop-groups", "shop-groups")],
        }

    async def read_pages(self) -> None:
        pages = self._pages
        await asyncio.sleep(self.rnd.uniform(0, 8))
        while self.working:
            page, urls = self.rnd.choice(list(pages().items()))
            t0 = time.perf_counter()
            await asyncio.gather(*(self.call("GET", u, key) for u, key in urls), self.document(page))
            self.meter.pages[page].append((time.perf_counter() - t0) * 1000)
            if page.startswith("Review") and self.batch:
                await self.thumbnails(self.batch)
            await asyncio.sleep(self.rnd.uniform(4, 12))  # reading it

    async def document(self, page: str) -> None:
        """The page itself, from the production frontend (its memory is part of the total)."""
        if not FRONTEND:
            return
        path = {"Overview": "/dashboard", "Batches": "/batches", "Scheduled": "/scheduled"}.get(page, "/batches")
        assert self.client is not None
        t0 = time.perf_counter()
        try:
            res = await self.client.get(FRONTEND + path, follow_redirects=False)
            self.meter.add("page document (frontend)", (time.perf_counter() - t0) * 1000, res.status_code)
        except httpx.HTTPError as exc:
            self.meter.add("page document (frontend)", (time.perf_counter() - t0) * 1000, 599, type(exc).__name__)

    async def thumbnails(self, batch: str) -> None:
        """The review page's pictures, at most four at a time, once each (the browser keeps them)."""
        res = await self.call("GET", f"/api/batches/{batch}", "batches/{id}")
        if res is None or res.status_code != 200:
            return
        limit = asyncio.Semaphore(4)
        new = [a["id"] for a in res.json().get("assets", []) if a["id"] not in self.seen]

        async def one(asset: str) -> None:
            async with limit:
                await self.call("GET", f"/api/assets/{asset}/image?size=thumb&v=1", "thumbnail")

        await asyncio.gather(*(one(a) for a in new))
        self.seen.update(new)

    # -- a working session -------------------------------------------------------------------

    async def _timed(self, name: str, coro: Any) -> Any:
        t0 = time.perf_counter()
        out = await coro
        self.meter.steps[name].append(time.perf_counter() - t0)
        return out

    async def _upload(self, batch: str) -> None:
        limit = asyncio.Semaphore(3)  # as the Upload page (lib/uploadQueue.ts)

        async def one(d: int, p: int) -> None:
            async with limit:
                name = f"LD{self.n:03d}{d:02d}-{p}.jpg"
                await self.call("POST", f"/api/batches/{batch}/assets", "upload photo", data={"group_key": f"LD{self.n:03d}{d:02d}", "grouping": "folder"},
                                files={"file": (name, self.photos[(self.n + d + p) % len(self.photos)], "image/jpeg")})

        await asyncio.gather(*(one(d, p) for d in range(DESIGNS) for p in range(PHOTOS)))
        await self.call("POST", f"/api/batches/{batch}/finalize", "batches/{id}/finalize")

    async def write_and_send(self) -> None:
        res = await self.call("POST", "/api/batches", "batches create", json={})
        if res is None or res.status_code >= 400:
            return
        batch = res.json()["id"]
        self.batch = batch
        await self._timed("upload 15 photos", self._upload(batch))
        await self.call("PUT", f"/api/batches/{batch}/groups", "batches/{id}/groups PUT", json={"profile_id": self.world["profiles"][0]})

        async def write() -> None:
            for d in range(DESIGNS):  # one by one, as "Generate all" sends them
                res = await self.call("POST", f"/api/batches/{batch}/generate", "generate one group", json={"group_key": f"LD{self.n:03d}{d:02d}"})
                if res is not None and res.status_code == 200:
                    body = res.json()
                    if body.get("failed") or body.get("paused"):
                        self.meter.errors[f"generate: {str((body.get('failures') or [{}])[0].get('error') or body.get('paused'))[:120]}"] += 1
                await asyncio.sleep(self.rnd.uniform(0.2, 1))

        await self._timed("write 5 listings", write())
        await self.call("POST", f"/api/batches/{batch}/approve-all", "approve-all", json={})
        await self.call("PUT", f"/api/batches/{batch}/send-to", "send-to", json={"send_to": {"group_ids": [], "connection_ids": self.world["shops"][:2]}})
        # Every other seller spreads them out (a group schedule), starting in a few minutes.
        when: dict[str, Any] = {"when": "drafts"}
        local = datetime.now(ZoneInfo("America/Chicago"))
        if self.n % 2 and local.hour < 23:
            start = local + timedelta(minutes=3)
            when = {"when": "spread", "schedule": {
                "start_date": local.date().isoformat(), "per_shop_per_day": 20, "window_start": start.strftime("%H:%M"),
                "window_end": "23:59", "spacing_minutes": 2, "stagger_minutes": 1}}
        await self.call("POST", f"/api/batches/{batch}/send/preview", "send/preview", json=when)
        await self.call("POST", f"/api/batches/{batch}/send", "send", json=when)

    async def mockups(self) -> None:
        res = await self.call("POST", "/api/mockup-templates", "mockup template", json={"name": f"Load {self.n}"})
        if res is None or res.status_code >= 400:
            return
        template = res.json()["id"]

        async def photo(p: int) -> None:
            await self.call("POST", f"/api/mockup-templates/{template}/photos", "mockup photo",
                            data={"name": f"Colour {p}", "rights_confirmed": "true"},
                            files={"file": (f"colour{p}.jpg", self.photos[p % len(self.photos)], "image/jpeg")})

        await asyncio.gather(*(photo(p) for p in range(PHOTOS)))
        t0 = time.perf_counter()
        ready: list[dict] = []
        while time.perf_counter() - t0 < 1800:
            res = await self.call("GET", "/api/mockup-templates", "mockup-templates")
            mine = next((t for t in (res.json() if res is not None and res.status_code == 200 else []) if t["id"] == template), None)
            photos = (mine or {}).get("photos") or []
            if photos and all(p["status"] != "preparing" for p in photos):
                ready = photos
                break
            await asyncio.sleep(5)
        self.meter.steps["prepare template (3 photos)"].append(time.perf_counter() - t0)
        confirmed = 0
        for p in ready:
            if p["status"] == "ready":
                res = await self.call("POST", f"/api/mockup-templates/{template}/confirm", "mockup confirm", json={"photo_ids": [p["id"]]})
                confirmed += res is not None and res.status_code < 400
        res = await self.call("POST", "/api/batches", "batches create", json={})
        if res is None or res.status_code >= 400 or not confirmed:
            return
        batch = res.json()["id"]
        for d in range(DESIGNS):
            await self.call("POST", f"/api/batches/{batch}/designs", "mockup design",
                            files={"file": (f"MK{self.n:03d}{d:02d}.png", self.designs[d % len(self.designs)], "image/png")})
        t0 = time.perf_counter()
        res = await self.call("POST", f"/api/batches/{batch}/mockups", "mockup run", json={"template_ids": [template]})
        if res is None or res.status_code >= 400:
            return
        run = res.json()["id"]
        while time.perf_counter() - t0 < 1800:
            res = await self.call("GET", f"/api/mockup-runs/{run}", "mockup-runs/{id}")
            if res is not None and res.status_code == 200 and res.json().get("status") in ("succeeded", "failed"):
                self.meter.steps[f"mockup run ({DESIGNS} designs × {PHOTOS} photos)"].append(time.perf_counter() - t0)
                if res.json().get("status") == "failed":
                    self.meter.errors["mockup run failed"] += 1
                break
            await asyncio.sleep(3)

    async def session(self) -> None:
        reading = asyncio.create_task(self.read_pages())
        try:
            work = [self.write_and_send()]
            if self.n % 3 == 0:
                work.append(self.mockups())
            await asyncio.gather(*work)
            await asyncio.sleep(30)  # still looking at it
        finally:
            self.working = False
            await reading


async def worlds(count: int) -> list[dict]:
    async with engine.connect() as c:
        rows = (await c.execute(text("SELECT id, email FROM tenant ORDER BY email LIMIT :n"), {"n": count})).all()
        out = []
        for tenant, _ in rows:
            shops = [str(r[0]) for r in await c.execute(text("SELECT id FROM etsy_connection WHERE tenant_id = :t ORDER BY position"), {"t": tenant})]
            profiles = [str(r[0]) for r in await c.execute(text("SELECT id FROM listing_profile WHERE tenant_id = :t ORDER BY name"), {"t": tenant})]
            old = (await c.execute(text("SELECT id FROM upload_batch WHERE tenant_id = :t AND file_count > 0 ORDER BY created_at LIMIT 1"), {"t": tenant})).scalar_one()
            out.append({"tenant": str(tenant), "shops": shops, "profiles": profiles, "old_batch": str(old)})
    return out


async def plant_go_lives(tenants: list[str], until: datetime) -> int:
    """LIVE of each seller's drafts become due, evenly over the time to ``until``."""
    planted = 0
    now = datetime.now(timezone.utc)
    async with engine.begin() as c:
        for t in tenants:
            ids = [r[0] for r in await c.execute(text(
                "SELECT lp.id FROM listing_publication lp JOIN generated_content g ON g.id = lp.content_id "
                "WHERE lp.tenant_id = :t AND lp.state = 'draft' AND lp.scheduled_for IS NULL AND g.approved "
                "AND lp.etsy_listing_id % 37 <> 0 LIMIT :n"), {"t": t, "n": LIVE})]
            for i, pid in enumerate(ids):
                at = now + (until - now) * (i + 1) / (len(ids) + 1)
                await c.execute(text("UPDATE listing_publication SET scheduled_for = :at, etsy_checked_at = now() WHERE id = :id"), {"at": at, "id": pid})
                planted += 1
    return planted


def pct(values: list[float], p: int) -> float:
    if len(values) < 2:
        return values[0] if values else 0.0
    return statistics.quantiles(values, n=100)[p - 1]


class Watch:
    """Peaks while a step runs: database connections, queue depth, Redis memory."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.conns = self.waiting = 0
        self.queue = self.redis_used = self.redis_peak = 0
        self.conns_by_state: Counter[str] = Counter()
        self.lines: list[str] = []
        self.stop = False
        self.long: dict[str, list[float]] = {}

    async def run(self) -> None:
        started = time.time()
        while not self.stop:
            try:
                async with engine.connect() as c:
                    rows = (await c.execute(text("SELECT state, count(*), count(*) FILTER (WHERE wait_event_type = 'Lock') "
                                                 "FROM pg_stat_activity WHERE datname = 'listyro_load' GROUP BY state"))).all()
                total = sum(r[1] for r in rows)
                self.conns = max(self.conns, total)
                self.waiting = max(self.waiting, sum(r[2] for r in rows))
                for r in rows:
                    self.conns_by_state[str(r[0])] = max(self.conns_by_state[str(r[0])], r[1])
                async with engine.connect() as c:
                    for age, query in (await c.execute(text(
                            "SELECT extract(epoch FROM now() - xact_start)::float, left(regexp_replace(query, '\\s+', ' ', 'g'), 130) "
                            "FROM pg_stat_activity WHERE datname = 'listyro_load' AND state LIKE 'idle in%' "
                            "AND now() - xact_start > interval '2 seconds'"))).all():
                        seen = self.long.setdefault(query, [0, 0.0])
                        seen[0] += 1
                        seen[1] = max(seen[1], age)
                queue = await self.redis.zcard("arq:queue")
                self.queue = max(self.queue, queue)
                info = await self.redis.info("memory")
                self.redis_used = max(self.redis_used, info["used_memory"])
                self.redis_peak = max(self.redis_peak, info["used_memory_peak"])
                if int(time.time() - started) % 60 < 5:
                    self.lines.append(f"    t+{int(time.time() - started):>4}s  queue {queue:>5}  db connections {total:>3} ({dict((str(r[0]), r[1]) for r in rows)})")
            except Exception as exc:  # noqa: BLE001 - a missed sample is only a missed sample
                self.lines.append(f"    sample failed: {type(exc).__name__}")
            await asyncio.sleep(5)


async def stub_totals(redis: Redis) -> dict:
    total: Counter[str] = Counter()
    accounts: Counter[str] = Counter()
    seconds: Counter[str] = Counter()
    minutes: dict[str, dict[int, dict[str, float]]] = {"api": {}, "worker": {}}
    for raw in (await redis.hgetall("load:stub")).values():
        data = json.loads(raw)
        total.update(data["counts"])
        accounts.update(data["per_account"])
        seconds.update(data["seconds"])
        for key, value in data.get("minutes", {}).items():
            row = minutes[data.get("role", "api")].setdefault(int(key), {"lag": 0.0, "out": 0, "limit": value["limit"]})
            row["lag"] = max(row["lag"], value["lag"])
            row["out"] = max(row["out"], value["out"])
    return {"counts": total, "accounts": accounts, "seconds": seconds, "minutes": minutes}


async def proc_samples(redis: Redis, since: float) -> list[dict]:
    return [s for s in (json.loads(x) for x in await redis.lrange("load:proc", 0, -1)) if s["at"] >= since]


async def job_figures(since: datetime) -> dict[str, Any]:
    async with engine.connect() as c:
        by_type = (await c.execute(text("SELECT type::text, status::text, count(*) FROM job WHERE created_at >= :s GROUP BY 1, 2 ORDER BY 1, 2"), {"s": since})).all()
        drafts = (await c.execute(text(
            "SELECT count(*), extract(epoch FROM percentile_cont(0.5) WITHIN GROUP (ORDER BY finished_at - created_at)), "
            "extract(epoch FROM percentile_cont(0.95) WITHIN GROUP (ORDER BY finished_at - created_at)), "
            "extract(epoch FROM max(finished_at) - min(created_at)) "
            "FROM job WHERE created_at >= :s AND type = 'create_draft' AND status = 'succeeded'"), {"s": since})).one()
        live = (await c.execute(text(
            "SELECT count(*), count(published_at), "
            "extract(epoch FROM percentile_cont(0.5) WITHIN GROUP (ORDER BY published_at - scheduled_for)), "
            "extract(epoch FROM percentile_cont(0.95) WITHIN GROUP (ORDER BY published_at - scheduled_for)), "
            "extract(epoch FROM max(published_at - scheduled_for)) "
            "FROM listing_publication WHERE scheduled_for >= :s"), {"s": since})).one()
        failures = (await c.execute(text(
            "SELECT left(coalesce(last_error, ''), 110), count(*) FROM job WHERE created_at >= :s AND status = 'failed' "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 8"), {"s": since})).all()
    return {"by_type": by_type, "drafts": drafts, "live": live, "failures": failures}


async def mockup_figures(since: datetime) -> tuple[int, int, int]:
    async with engine.connect() as c:
        ready = (await c.execute(text("SELECT count(*) FILTER (WHERE status = 'ready'), count(*) FILTER (WHERE status = 'failed') FROM mockup_photo WHERE created_at >= :s"), {"s": since})).one()
        outputs = (await c.execute(text("SELECT count(*) FROM mockup_output WHERE created_at >= :s"), {"s": since})).scalar_one()
    return int(ready[0]), int(ready[1]), int(outputs)


async def drain(redis: Redis, since: datetime, seconds: float) -> float:
    """Wait for the drafts and go-lives of this step to finish; returns how long it took."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        async with engine.connect() as c:
            open_jobs = (await c.execute(text(
                "SELECT count(*) FROM job WHERE created_at >= :s AND status IN ('queued', 'running') AND paused_reason IS NULL"), {"s": since})).scalar_one()
            due = (await c.execute(text(
                "SELECT count(*) FROM listing_publication WHERE scheduled_for >= :s AND state = 'draft' AND scheduled_for <= now()"), {"s": since})).scalar_one()
        if not open_jobs and not due:
            break
        await asyncio.sleep(10)
    return time.perf_counter() - t0


async def step(count: int, all_worlds: list[dict], redis: Redis, pool: ArqRedis, photos: list[bytes], designs: list[bytes], args: argparse.Namespace) -> tuple[list[str], bool]:
    meter = Meter()
    chosen = all_worlds[:count]
    started_at = datetime.now(timezone.utc)
    started = time.time()
    before = await stub_totals(redis)
    planted = await plant_go_lives([w["tenant"] for w in chosen], started_at + timedelta(minutes=args.live_minutes)) if args.scenario == "all" else 0
    # The hourly reconcile fires as the step starts (spread as workers/reconcile.py spreads it).
    shops = [s for w in chosen for s in w["shops"]] if args.scenario == "all" else []
    for i, shop in enumerate(shops):
        await pool.enqueue_job("reconcile_listings", shop, _defer_by=i * 3000 / len(shops))
    watch = Watch(redis)
    watching = asyncio.create_task(watch.run())
    sellers = [Seller(i, w, meter, photos, designs, redis) for i, w in enumerate(chosen)]
    await asyncio.gather(*(s.open() for s in sellers))

    async def staggered(s: Seller) -> None:
        await asyncio.sleep(s.n * args.ramp / max(count, 1))  # all within the first ramp seconds
        await s.session()

    if args.scenario == "mockups":
        # Only mockups: the first sellers make them while everyone reads pages until they are done.
        reading = [asyncio.create_task(s.read_pages()) for s in sellers]
        await asyncio.gather(*(s.mockups() for s in sellers[:args.mockup_sellers]))
        for s in sellers:
            s.working = False
        await asyncio.gather(*reading)
    else:
        await asyncio.gather(*(staggered(s) for s in sellers))
    worked = time.time() - started
    drained = await drain(redis, started_at, args.drain_seconds)
    watch.stop = True
    await watching
    for s in sellers:
        await s.client.aclose()  # type: ignore[union-attr]

    after = await stub_totals(redis)
    samples = await proc_samples(redis, started)
    jobs = await job_figures(started_at)
    elapsed = time.time() - started
    counts = after["counts"] - before["counts"]
    accounts = after["accounts"] - before["accounts"]
    second_keys = [k for k in after["seconds"] if int(k) >= started]
    per_sec = [after["seconds"][k] for k in second_keys]
    lines = [f"\n===== {count} sellers at once ({'every' if count == len(all_worlds) else 'of ' + str(len(all_worlds))} seeded accounts); "
             f"work took {worked:.0f} s, drain {drained:.0f} s more; {planted} go-lives planted, {len(shops)} shops reconciled"]
    everything = [v for vals in meter.times.values() for v in vals]
    p95_all = pct(everything, 95)
    during = len(everything) / max(worked, 1)
    lines.append(f"all requests: {len(everything)}, {during:.1f}/s, p50 {pct(everything, 50):.0f} ms, p95 {p95_all:.0f} ms, p99 {pct(everything, 99):.0f} ms, errors {sum(meter.errors.values())}")
    lines.append(f"\n{'endpoint':<30}{'n':>7}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}")
    reads = {k: v for k, v in meter.times.items()}
    for key, values in sorted(reads.items(), key=lambda kv: -pct(kv[1], 95)):
        lines.append(f"{key:<30}{len(values):>7}{pct(values, 50):>8.0f}m{pct(values, 95):>8.0f}m{pct(values, 99):>8.0f}m{max(values):>8.0f}m")
    lines.append(f"\n{'page (all its requests at once)':<34}{'n':>6}{'p50':>9}{'p95':>9}")
    for page, values in meter.pages.items():
        lines.append(f"{page:<34}{len(values):>6}{pct(values, 50):>8.0f}m{pct(values, 95):>8.0f}m")
    lines.append(f"\n{'seller action':<38}{'n':>5}{'p50 s':>9}{'p95 s':>9}{'max s':>9}")
    for name, values in meter.steps.items():
        lines.append(f"{name:<38}{len(values):>5}{pct(values, 50):>9.1f}{pct(values, 95):>9.1f}{max(values):>9.1f}")
    if meter.errors:
        lines.append("\nerrors:")
        for key, n in meter.errors.most_common(14):
            lines.append(f"  {n:>6} × {key}")
    if meter.samples:
        lines.append("\nminute by minute (when it got slow, how much the server sent [its uplink is 14 Mbps = 105 MB a minute], CPU cores used, "
                     "worst event-loop lag and most pooled connections of an API process and the worker):")
        minutes: dict[int, list[float]] = defaultdict(list)
        bytes_out: Counter[int] = Counter()
        for at, ms in meter.samples:
            minutes[int(at - started) // 60].append(ms)
        for at, n in meter.sent:
            bytes_out[int(at - started) // 60] += n
        for minute in sorted(minutes):
            values = minutes[minute]
            base = int(started) // 60 + minute
            api, wrk = after["minutes"]["api"].get(base, {}), after["minutes"]["worker"].get(base, {})
            rows = [s for s in samples if int(s["at"] - started) // 60 == minute]
            used = ""
            if len(rows) > 1:
                span = max(rows[-1]["at"] - rows[0]["at"], 1)
                used = f"  cpu api {(rows[-1]['api_cpu'] - rows[0]['api_cpu']) / span:.2f} worker {(rows[-1]['worker_cpu'] - rows[0]['worker_cpu']) / span:.2f}"
            lines.append(f"  min {minute:>3}: {len(values):>5} requests  p50 {pct(values, 50):>6.0f} ms  p95 {pct(values, 95):>6.0f} ms  sent {bytes_out[minute] / 1e6:>6.1f} MB"
                         f"{used}  lag api {api.get('lag', 0):>5.2f}s wkr {wrk.get('lag', 0):>5.2f}s  pool api {int(api.get('out', 0)):>2}/{int(api.get('limit', 0))} wkr {int(wrk.get('out', 0)):>2}/{int(wrk.get('limit', 0))}")
    if args.scenario == "mockups":
        made = await mockup_figures(started_at)
        lines.append(f"\nmockups: {made[0]} photos prepared, {made[1]} failed, {made[2]} mockups made in {worked:.0f} s = "
                     f"{60 * made[0] / max(worked, 1):.1f} photos a minute, {60 * made[2] / max(worked, 1):.1f} mockups a minute")
    lines.append("\nworker:")
    for jtype, status, n in jobs["by_type"]:
        lines.append(f"  {jtype:<28}{status:<12}{n:>6}")
    d = jobs["drafts"]
    if d[0]:
        lines.append(f"  drafts finished: {d[0]}; waited+ran p50 {float(d[1] or 0):.0f} s, p95 {float(d[2] or 0):.0f} s; "
                     f"first asked to last done {float(d[3] or 0):.0f} s = {60 * d[0] / max(float(d[3] or 1), 1):.1f} drafts a minute")
    live = jobs["live"]
    if live[0]:
        lines.append(f"  go-lives: {live[1]} of {live[0]} due went live; late by p50 {float(live[2] or 0):.0f} s, p95 {float(live[3] or 0):.0f} s, worst {float(live[4] or 0):.0f} s")
    for reason, n in jobs["failures"]:
        lines.append(f"  failed × {n}: {reason}")
    lines.append("\nEtsy (the stub):")
    busy = [v for v in per_sec if v > 0]
    lines.append(f"  {counts['etsy']} requests; {counts['etsy_drafts']} drafts, {counts['etsy_images']} images, {counts['etsy_published']} published; "
                 f"429s {counts['etsy_429']}, 5xx {counts['etsy_5xx']}")
    if busy:
        lines.append(f"  requests per second: busiest second {max(busy)}, mean while busy {statistics.mean(busy):.1f} (limit 10)")
    image_mb = counts["etsy_image_bytes"] / 1e6
    if counts["etsy_images"]:
        lines.append(f"  image bytes sent to Etsy {image_mb:.0f} MB, {image_mb / counts['etsy_images']:.2f} MB each: at 14 Mbps (1.75 MB/s) that alone takes {image_mb / 1.75:.0f} s")
    if len(accounts) > 1:
        shares = sorted(accounts.values())
        lines.append(f"  requests per account: min {shares[0]}, median {statistics.median(shares):.0f}, max {shares[-1]}")
    no_route = {k: v for k, v in counts.items() if k.startswith("etsy_no_route")}
    if no_route:
        lines.append(f"  stub had no route for: {no_route}")
    lines.append(f"  model calls: {counts['model_vision']} vision, {counts['model_content']} content")
    lines.append("\npeaks:")
    if samples:
        api = [s["api_rss"] for s in samples]
        work = [s["worker_rss"] for s in samples]
        span = max(samples[-1]["at"] - samples[0]["at"], 1)
        lines.append(f"  API (2 processes + parent): {max(api) / 1e6:.0f} MB; worker: {max(work) / 1e6:.0f} MB")
        lines.append(f"  CPU over the step: API {(samples[-1]['api_cpu'] - samples[0]['api_cpu']) / span:.2f} cores, worker {(samples[-1]['worker_cpu'] - samples[0]['worker_cpu']) / span:.2f} cores")
    lines.append(f"  database connections {watch.conns} ({dict(watch.conns_by_state)}), waiting on a lock {watch.waiting}")
    if watch.long:
        lines.append("  transactions left idle over 2 s (pg_stat_activity, every 5 s: samples, longest, the last query):")
        for query, (n, longest) in sorted(watch.long.items(), key=lambda kv: -kv[1][1])[:12]:
            lines.append(f"    {n:>4} × {longest:>6.1f} s  {query}")
    else:
        lines.append("  transactions left idle over 2 s: none seen")
    lines.append(f"  Redis memory {watch.redis_used / 1e6:.0f} MB (limit 256 MB); arq queue depth {watch.queue}")
    lines.extend(watch.lines[:25])
    lines.append(f"\nstep total {elapsed:.0f} s")
    ok = not meter.errors and p95_all <= SLOW_MS
    return lines, ok


async def slowest_queries() -> list[str]:
    lines = ["\n===== slowest queries (pg_stat_statements, all steps)"]
    async with engine.connect() as c:
        for title, order in (("by total time", "total_exec_time"), ("by time per call", "mean_exec_time")):
            lines.append(f"-- {title}")
            rows = (await c.execute(text(
                f"SELECT calls, round(total_exec_time::numeric / 1000, 1), round(mean_exec_time::numeric, 1), rows, left(regexp_replace(query, '\\s+', ' ', 'g'), 210) "
                f"FROM pg_stat_statements WHERE query NOT LIKE '%pg_stat%' AND calls >= 3 ORDER BY {order} DESC LIMIT 12"))).all()
            for calls, total, mean, n, query in rows:
                lines.append(f"  {calls:>7} calls {total:>8} s total {mean:>9} ms each {n:>9} rows  {query}")
    return lines


async def main(args: argparse.Namespace) -> int:
    redis = Redis.from_url(settings.redis_url)
    pool = ArqRedis(connection_pool=redis.connection_pool)
    photos = [_photo(i) for i in range(4)]
    designs = [_design(i) for i in range(5)]
    report = [f"Listyro load test {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. CPU set of the app, Postgres and Redis: {os.environ.get('LOAD_CPUSET_SHOWN', '?')}. "
              f"Etsy answers in {os.environ.get('LOAD_ETSY_MS')} ms, the model in {os.environ.get('LOAD_MODEL_MS')} ms. "
              f"Photos {len(photos[0]) / 1e6:.2f} MB, designs {len(designs[0]) / 1e6:.2f} MB."]
    async with engine.connect() as c:
        size = (await c.execute(text("SELECT pg_size_pretty(pg_database_size('listyro_load'))"))).scalar_one()
        listings = (await c.execute(text("SELECT count(*) FROM generated_content"))).scalar_one()
    report.append(f"database: {size}, {listings:,} listings")
    all_worlds = await worlds(max(args.steps))
    print("\n".join(report), flush=True)
    ok = True
    for count in args.steps:
        lines, step_ok = await step(count, all_worlds, redis, pool, photos, designs, args)
        report += lines
        ok = ok and step_ok
        print("\n".join(lines), flush=True)
        with open(f"/output/report-{args.label}.txt", "w", encoding="utf-8") as out:
            out.write("\n".join(report) + "\n")
    tail = await slowest_queries()
    report += tail
    print("\n".join(tail), flush=True)
    with open(f"/output/report-{args.label}.txt", "w", encoding="utf-8") as out:
        out.write("\n".join(report) + "\n")
    await engine.dispose()
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--steps", type=lambda s: [int(x) for x in s.split(",")], default=[10, 25, 50, 100])
    parser.add_argument("--ramp", type=float, default=60.0, help="seconds over which a step's sellers start")
    parser.add_argument("--live-minutes", type=float, default=10.0)
    parser.add_argument("--drain-seconds", type=float, default=1200.0)
    parser.add_argument("--label", default="run")
    parser.add_argument("--scenario", choices=("all", "mockups"), default="all")
    parser.add_argument("--mockup-sellers", type=int, default=12)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
