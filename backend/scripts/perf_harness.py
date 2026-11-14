"""Production-shaped timing of the slow endpoints (fix/perf-and-memory). LOCAL ONLY, synthetic data only.

    docker compose run --rm --entrypoint python tests scripts/perf_harness.py [--rows 2000] [--runs 12]

Creates a scratch database (``listyro_perf``) in the local Postgres, migrates it to head, fills it with
synthetic data shaped like production (6 accounts, 5 shops each, ``--rows`` cached listings per shop with
Etsy-sized payloads, batches of 50 designs with 3 photos, profiles with their category data, drafts), then times
each endpoint in process through the real app (ASGI): p50, p95 and the SQL queries of one answer. The scratch
database is dropped at the end. Refuses to run with APP_ENV=production. Nothing reaches Etsy or the AI provider.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import random
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa  # noqa: E402

from app.core.config import get_settings, set_settings_override  # noqa: E402

SCRATCH = "listyro_perf"
NOW = datetime.now(timezone.utc)
RNG = random.Random(20261114)
WORDS = ("retro sunset mountain camping vintage outdoor funny nurse teacher dog mom cat lover floral boho "
         "graphic tee shirt sweatshirt hoodie gift christmas halloween summer beach coffee book club").split()


def _sentence(n: int) -> str:
    return " ".join(RNG.choice(WORDS) for _ in range(n)).capitalize()


def _payload(listing_id: int, state: str) -> dict:
    """An Etsy listing as getListingsByShop returns it (the fields we keep, Etsy's sizes)."""
    return {
        "listing_id": listing_id, "state": state, "title": _sentence(14)[:140],
        "description": " ".join(_sentence(20) + "." for _ in range(8)), "tags": [_sentence(2)[:20] for _ in range(13)],
        "skus": [f"BR{listing_id % 100000}"], "price": {"amount": 2499, "divisor": 100, "currency_code": "USD"},
        "quantity": 999, "taxonomy_id": 482, "shop_section_id": 100 + listing_id % 7,
        "url": f"https://www.etsy.com/listing/{listing_id}", "views": RNG.randint(0, 900), "num_favorers": RNG.randint(0, 80),
        "original_creation_timestamp": int((NOW - timedelta(days=RNG.randint(1, 900))).timestamp()),
        "state_timestamp": int((NOW - timedelta(days=RNG.randint(1, 300))).timestamp()),
        "images": [{"listing_image_id": listing_id * 10 + k, "rank": k + 1,
                    "url_570xN": f"https://i.etsystatic.com/{listing_id}/{k}/il_570xN.jpg",
                    "url_fullxfull": f"https://i.etsystatic.com/{listing_id}/{k}/il_fullxfull.jpg"} for k in range(5)],
        "personalization": None,
    }


def _profile_payload() -> dict:
    props = [{"property_id": 1000 + p, "name": f"Property {p}", "display_name": f"Property {p}",
              "is_multivalued": p % 3 == 0, "max_values_allowed": 5,
              "possible_values": [{"value_id": 50000 + p * 100 + v, "name": f"Value {v}"} for v in range(60)]}
             for p in range(40)]
    return {"description": " ".join(_sentence(20) + "." for _ in range(12)), "taxonomy_id": 482, "payload_version": 9,
            "price": 24.99, "currency": "USD", "images": [{"listing_image_id": i, "url_fullxfull": "u"} for i in range(8)],
            "category_properties": props,
            "category_attributes": {**{f"Property {p}": [f"Value {v}" for v in range(60)] for p in range(20)},
                                    "Occasion": [f"Occasion {v}" for v in range(40)],
                                    "Holiday": [f"Holiday {v}" for v in range(30)]}, "who_made": "i_did", "when_made": "made_to_order"}


_ADMIN: list[str] = []


def _sync_url() -> tuple[str, str]:
    if not _ADMIN:  # read once: measuring points the settings at the scratch database
        _ADMIN.append(get_settings().database_url.replace("+asyncpg", "+psycopg"))
    url = sa.engine.make_url(_ADMIN[0])
    return url.render_as_string(hide_password=False), url.set(database=SCRATCH).render_as_string(hide_password=False)


def create_and_migrate() -> str:
    admin_url, scratch_url = _sync_url()
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH}"'))
        c.execute(sa.text(f'CREATE DATABASE "{SCRATCH}"'))
    admin.dispose()
    env = {**os.environ, "DATABASE_URL": scratch_url.replace("+psycopg", "+asyncpg")}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env,
                   cwd=str(Path(__file__).resolve().parents[1]), capture_output=True)
    return scratch_url


def drop() -> None:
    admin_url, _ = _sync_url()
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH}"'))
    admin.dispose()


def seed(url: str, rows: int, storage_dir: str) -> dict:
    """The synthetic accounts. Returns what the timing needs for account 1."""
    from PIL import Image
    from sqlalchemy.orm import Session

    from app.core import terms
    from app.db import models as M

    engine = sa.create_engine(url)
    first: dict = {}
    started = time.time()
    with Session(engine) as s:
        for a in range(6):
            tenant = M.Tenant(email=f"perf-{a}@example.test", password_hash="!", time_zone="America/Chicago",
                              terms_version=terms.TERMS_VERSION, terms_accepted_at=NOW)
            s.add(tenant)
            s.flush()
            shops = []
            for k in range(5):
                shop = M.EtsyConnection(tenant_id=tenant.id, status=M.ConnectionStatus.active, etsy_user_id=a * 10 + k + 1,
                                        shop_id=a * 10 + k + 1, shop_name=f"PerfShop{a}{k}", display_name=f"Perf Shop {a}-{k}",
                                        position=k, listing_counts={"active": int(rows * 0.9), "draft": rows - int(rows * 0.9),
                                                                    "inactive": 3, "sold_out": 1, "expired": 2},
                                        listing_counts_at=NOW)
                s.add(shop)
                shops.append(shop)
            s.flush()
            for k, shop in enumerate(shops):
                base = (a * 5 + k) * 1_000_000
                s.execute(sa.insert(M.ShopListingCache), [
                    {"id": uuid.uuid4(), "tenant_id": tenant.id, "connection_id": shop.id, "listing_id": base + n,
                     "payload": _payload(base + n, "active" if n % 10 else "draft"), "fetched_at": NOW}
                    for n in range(rows)])
            profiles = []
            for p in range(8):
                prof = M.ListingProfile(tenant_id=tenant.id, connection_id=shops[p % 5].id, name=f"Profile {p}",
                                        reference_listing_id=(a * 5 + p % 5) * 1_000_000 + p, content_template="apparel",
                                        cached_payload=_profile_payload(), confirmed=True, updated_at=NOW)
                s.add(prof)
                profiles.append(prof)
            s.flush()
            batches = []
            for b in range(10):
                batch = M.UploadBatch(tenant_id=tenant.id, status=M.UploadBatchStatus.ready, file_count=150, name=f"Drop {b}")
                s.add(batch)
                s.flush()
                batches.append(batch)
                assets, contents = [], []
                for g in range(50):
                    key = f"BR{a}{b:02d}{g:02d}"
                    for r in range(3):
                        assets.append({"id": uuid.uuid4(), "tenant_id": tenant.id, "batch_id": batch.id,
                                       "original_filename": f"{key}/{r}.jpg", "parsed_sku": key, "group_key": key,
                                       "upload_folder": key, "processed_key": f"{tenant.id}/{batch.id}/processed/{key}-{r}.jpg",
                                       "mime_type": "image/jpeg", "width": 2000, "height": 2000, "byte_size": 900_000,
                                       "rank": r + 1, "status": M.AssetStatus.processed})
                    contents.append({"id": uuid.uuid4(), "tenant_id": tenant.id, "batch_id": batch.id,
                                     "asset_id": assets[-3]["id"], "listing_profile_id": profiles[g % 8].id,
                                     "title": _sentence(12)[:140], "tags": [_sentence(2)[:20] for _ in range(13)],
                                     "description": " ".join(_sentence(20) + "." for _ in range(8)),
                                     "approved": g % 3 != 0, "source": "ai",
                                     "attributes": {"listing": {"Primary color": "Blue"}, "search": {"opening": _sentence(15)}}})
                s.execute(sa.insert(M.Asset), assets)
                s.execute(sa.insert(M.GeneratedContent), contents)
                s.add_all([M.ListingGroupSetting(tenant_id=tenant.id, batch_id=batch.id, group_key=f"BR{a}{b:02d}{g:02d}",
                                                 profile_id=profiles[g % 8].id) for g in range(50)])
                if b < 5:  # half the batches are drafted in two shops
                    s.execute(sa.insert(M.ListingPublication), [
                        {"id": uuid.uuid4(), "tenant_id": tenant.id, "content_id": c["id"], "connection_id": shops[j].id,
                         "etsy_listing_id": (a * 5 + j) * 1_000_000 + 500_000 + b * 100 + i, "state": "draft",
                         "title": c["title"], "created_at": NOW - timedelta(days=3)}
                        for i, c in enumerate(contents) for j in (0, 1)])
            s.flush()
            if a == 0:
                first = {"tenant": tenant.id, "shop": shops[0].id, "batch": batches[0].id, "batches": [b.id for b in batches],
                         "asset": None}
        s.commit()
        # Real files for the image endpoint: account 1's first batch.
        rows_ = s.execute(sa.select(M.Asset.id, M.Asset.processed_key).where(M.Asset.batch_id == first["batch"])).all()
        img = Image.new("RGB", (2000, 2000))
        img.putdata([(RNG.randint(0, 255), 80, 120)] * (2000 * 2000))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        for asset_id, key in rows_[:20]:
            path = Path(storage_dir) / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(buf.getvalue())
        first["asset"] = rows_[0][0]
    engine.dispose()
    print(f"seeded in {time.time() - started:.0f}s", flush=True)
    return first


def big_photo(megabytes: int = 48) -> bytes:
    """A camera-like JPEG of about 100 megapixels, under ``megabytes`` (the per-file limit is 50 MiB)."""
    from PIL import Image

    small = Image.frombytes("RGB", (3000, 2250), random.Random(3).randbytes(3000 * 2250 * 3))
    img = small.resize((12000, 9000), Image.BILINEAR)
    for quality in (92, 85, 75, 60):
        out = io.BytesIO()
        img.save(out, "JPEG", quality=quality)
        if out.tell() <= megabytes * 1024 * 1024:
            print(f"photo 12000x9000 (108 MP), {out.tell() / 1048576:.1f} MB", flush=True)
            return out.getvalue()
    raise RuntimeError("photo too large")


def huge_design() -> bytes:
    """A 250-megapixel design PNG (the most the upload accepts since the hotfix): flat colour, a few MB."""
    from PIL import Image, ImageDraw

    Image.MAX_IMAGE_PIXELS = None
    img = Image.new("RGBA", (15000, 16600), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i in range(60):
        draw.ellipse((i * 200, i * 230, i * 200 + 4000, i * 230 + 3000), fill=(i * 4, 120, 200 - i * 3, 255))
    out = io.BytesIO()
    img.save(out, "PNG", compress_level=6)
    print(f"design 15000x16600 (249 MP), {out.tell() / 1048576:.1f} MB", flush=True)
    return out.getvalue()


async def measure(url: str, first: dict, runs: int, storage_dir: str, endpoints: list[str] | None) -> list[dict]:
    from fakeredis import FakeAsyncRedis
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api import deps
    from app.core.sessions import SessionStore
    from app.main import create_app
    from app.pipeline.storage import LocalStorage
    from tests.auth_support import BROWSER_HEADERS, authenticate, open_session
    from tests.test_publish_api import StubEnqueuer

    set_settings_override(get_settings().model_copy(update={
        "database_url": url.replace("+psycopg", "+asyncpg"), "storage_dir": storage_dir,
        "llm_api_key": "perf-stub-never-called", "max_batch_bytes": 10 * 1024**3}))
    deps.get_storage.cache_clear()
    engine = create_async_engine(url.replace("+psycopg", "+asyncpg"), pool_size=10)
    queries = {"n": 0}
    slow: list[tuple[float, str]] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        queries["n"] += 1
        conn.info["t0"] = time.perf_counter()

    @event.listens_for(engine.sync_engine, "after_cursor_execute")
    def _took(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        slow.append(((time.perf_counter() - conn.info.pop("t0", time.perf_counter())) * 1000, " ".join(statement.split())[:220]))

    sm = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with sm() as s:
            yield s

    app = create_app()
    redis = FakeAsyncRedis()
    enqueuer = StubEnqueuer()
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_redis] = lambda: redis
    app.dependency_overrides[deps.get_session_store] = lambda: SessionStore(redis)
    app.dependency_overrides[deps.get_enqueuer] = lambda: enqueuer
    app.dependency_overrides[deps.get_storage] = lambda: LocalStorage(storage_dir)
    from app.pipeline.images import ImageProcessor
    from app.pipeline.ingest import BatchIngestor
    from app.pipeline.sku import SkuParser

    queued_uploads: list = []

    async def _dispatch(asset_id) -> None:  # noqa: ANN001  (recorded: nothing reaches a real queue)
        queued_uploads.append(asset_id)

    try:
        ingestor = BatchIngestor(sessionmaker=sm, storage=LocalStorage(storage_dir), processor=ImageProcessor(),
                                 sku_parser=SkuParser(), dispatch=_dispatch)
    except TypeError:  # the code before fix/perf-and-memory: processed in the request
        ingestor = BatchIngestor(sessionmaker=sm, storage=LocalStorage(storage_dir), processor=ImageProcessor(),
                                 sku_parser=SkuParser())
    app.dependency_overrides[deps.get_ingestor] = lambda: ingestor
    b, shop, asset = first["batch"], first["shop"], first["asset"]
    plan = {
        "GET /api/shop/summary?all=1": ("GET", "/api/shop/summary?all=1", None),
        "GET /api/shop/summary?shop=": ("GET", f"/api/shop/summary?shop={shop}", None),
        "GET /api/shop/overview": ("GET", "/api/shop/overview", None),
        "GET /api/batches/{id}/content": ("GET", f"/api/batches/{b}/content", None),
        "GET /api/batches/{id}/groups": ("GET", f"/api/batches/{b}/groups", None),
        "GET /api/profiles": ("GET", "/api/profiles", None),
        "GET /api/assets/{id}/image?size=thumb": ("GET", f"/api/assets/{asset}/image?size=thumb&v=1", None),
        "POST /api/batches/{id}/assets (50 MB)": ("UPLOAD", f"/api/batches/{b}/assets", None),
        "POST /api/batches/{id}/assets x9 at once (250 MP PNG)": ("BURST", f"/api/batches/{b}/assets", None),
        "POST /api/batches/{id}/generate (regenerate all)": ("POST", f"/api/batches/{first['batches'][9]}/generate",
                                                             {"replace": True, "replace_approved": True}),
    }
    out = []
    photo = design = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://perf", headers=BROWSER_HEADERS, timeout=600) as client:
        authenticate(client, await open_session(redis, first["tenant"]))
        for name, (method, path, body) in plan.items():
            if endpoints and not any(e in name for e in endpoints):
                continue
            times, counts, status = [], [], None
            n = runs if method not in ("UPLOAD", "BURST") else 3
            if method == "BURST":
                design = design or huge_design()
                for i in range(2):
                    started = time.perf_counter()

                    async def one(k: int) -> float:
                        t0 = time.perf_counter()
                        r = await client.post(path, files={"file": (f"BIG{i}-{k}.png", design, "image/png")})
                        assert r.status_code == 201, r.text
                        return (time.perf_counter() - t0) * 1000

                    took9 = sorted(await asyncio.gather(*(one(k) for k in range(9))))
                    print(f"   burst {i}: slowest {took9[-1]:.0f} ms, all done in {(time.perf_counter() - started) * 1000:.0f} ms", flush=True)
                    times += took9
                times.sort()
                out.append({"endpoint": name, "status": 201, "p50_ms": round(statistics.median(times)),
                            "p95_ms": round(times[int(round(0.95 * (len(times) - 1)))]), "queries": None})
                print(json.dumps(out[-1]), flush=True)
                continue
            for i in range(n + 1):
                queries["n"] = 0
                slow.clear()
                t = time.perf_counter()
                if method == "UPLOAD":
                    photo = photo or big_photo(48)  # the per-file limit is 50 MiB
                    r = await client.post(path, files={"file": (f"PERF{i}.png", photo, "image/png")})
                else:
                    if "generate" in path:
                        await redis.flushall()
                        authenticate(client, await open_session(redis, first["tenant"]))
                    r = await client.request(method, path, json=body)
                took = (time.perf_counter() - t) * 1000
                status = r.status_code
                if i:  # the first is the warm-up
                    times.append(took)
                    counts.append(queries["n"])
            times.sort()
            p95 = times[min(len(times) - 1, int(round(0.95 * (len(times) - 1))))]
            out.append({"endpoint": name, "status": status, "p50_ms": round(statistics.median(times)),
                        "p95_ms": round(p95), "queries": max(counts)})
            print(json.dumps(out[-1]), flush=True)
            if method == "UPLOAD" and queued_uploads:
                worker = []
                for asset_id in list(queued_uploads):
                    t0 = time.perf_counter()
                    assert await ingestor.process_stored(asset_id) == "processed"
                    worker.append((time.perf_counter() - t0) * 1000)
                queued_uploads.clear()
                print(f"   the worker's step (process_upload): {statistics.median(worker):.0f} ms each", flush=True)
            if os.environ.get("PERF_SQL"):
                total = sum(ms for ms, _ in slow)
                print(f"   sql total {total:.0f} ms in {len(slow)} statements; slowest:", flush=True)
                for ms, sql in sorted(slow, reverse=True)[:4]:
                    print(f"   {ms:7.1f} ms  {sql}", flush=True)
                if os.environ.get("PERF_SQL") == "2":
                    from collections import Counter
                    for sql, n in Counter(sql[:150] for _, sql in slow).most_common(8):
                        if n > 2:
                            print(f"   x{n:<4} {sql}", flush=True)
    await engine.dispose()
    return out


def main() -> int:
    if get_settings().app_env == "production":
        print("refused: APP_ENV=production")
        return 2
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=2000)
    parser.add_argument("--runs", type=int, default=12)
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--keep", action="store_true", help="keep the scratch database (to measure again)")
    args = parser.parse_args()
    storage_dir = tempfile.mkdtemp(prefix="perf-")
    url = create_and_migrate()
    try:
        first = seed(url, args.rows, storage_dir)
        asyncio.run(measure(url, first, args.runs, storage_dir, args.only))
    finally:
        if not args.keep:
            drop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
