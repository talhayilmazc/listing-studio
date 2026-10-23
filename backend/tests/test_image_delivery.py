"""Images reach a screen as small, versioned copies that a browser keeps (page weight, 2026-10-23).

Grids get WebP copies made once and stored (pipeline/thumbs.py); a URL with the image's
version is kept for a year (``immutable``), any other is checked with its ETag and answered
304; nobody else's image is ever answered, not even with a 304.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps, images
from app.api.mockups import output_stamp
from app.core.sessions import SessionStore
from app.db import models as M
from app.db.base import Base
from app.main import create_app
from app.pipeline import thumbs
from app.pipeline.images import ImageProcessor
from app.pipeline.ingest import BatchIngestor
from app.pipeline.sku import SkuParser
from app.pipeline.storage import LocalStorage
from tests.auth_support import BROWSER_HEADERS, authenticate, make_tenant, open_session

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
IMMUTABLE = "private, max-age=31536000, immutable"
REVALIDATE = "private, no-cache"


def _jpeg(w: int = 1600, h: int = 2000, colour=(40, 90, 160)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _png(w: int = 1200, h: int = 1200) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (10, 10, 10, 255)).save(buf, "PNG")
    return buf.getvalue()


def _size(data: bytes) -> tuple[str, int, int]:
    img = Image.open(io.BytesIO(data))
    return img.format, img.width, img.height


async def _seed(sm, storage: LocalStorage, tenant_id: uuid.UUID) -> dict:
    async with sm() as s:
        batch = M.UploadBatch(tenant_id=tenant_id, status=M.UploadBatchStatus.ready, file_count=1)
        s.add(batch)
        await s.flush()
        asset_key = f"{tenant_id}/{batch.id}/processed/{uuid.uuid4()}.jpg"
        storage.put(asset_key, _jpeg(), "image/jpeg")
        asset = M.Asset(batch_id=batch.id, tenant_id=tenant_id, original_filename="SKU1/front.jpg", processed_key=asset_key,
                        mime_type="image/jpeg", width=1600, height=2000, rank=1, status=M.AssetStatus.processed, group_key="SKU1")
        template = M.MockupTemplate(tenant_id=tenant_id, name="Tees")
        s.add_all([asset, template])
        await s.flush()
        subgroup = M.MockupSubgroup(tenant_id=tenant_id, template_id=template.id, name="Tees")
        s.add(subgroup)
        await s.flush()
        photo = M.MockupPhoto(tenant_id=tenant_id, template_id=template.id, subgroup_id=subgroup.id, name="White",
                              rights_confirmed_at=NOW, status="ready", width=1600, height=2000)
        s.add(photo)
        await s.flush()
        photo.image_key = f"{tenant_id}/templates/{photo.id}/blank.jpg"
        storage.put(photo.image_key, _jpeg(colour=(240, 240, 240)), "image/jpeg")
        design = M.MockupDesign(tenant_id=tenant_id, batch_id=batch.id, group_key="SKU1", original_filename="SKU1.png",
                                width=1200, height=1200, design_key=f"{tenant_id}/{batch.id}/designs/{uuid.uuid4()}.png")
        storage.put(design.design_key, _png(), "image/png")
        s.add(design)
        await s.flush()
        run = M.MockupRun(tenant_id=tenant_id, batch_id=batch.id, template_ids=[str(template.id)], photo_ids=[str(photo.id)])
        s.add(run)
        await s.flush()
        out_key = f"{tenant_id}/{batch.id}/mockups/{uuid.uuid4().hex}.jpg"
        storage.put(out_key, _jpeg(2000, 2000), "image/jpeg")
        output = M.MockupOutput(tenant_id=tenant_id, run_id=run.id, batch_id=batch.id, design_id=design.id, photo_id=photo.id,
                                template_name="Tees", position=1, filename="o.jpg", width=2000, height=2000, byte_size=10, key=out_key)
        s.add(output)
        await s.commit()
        return {"batch": batch.id, "asset": asset.id, "asset_key": asset_key, "photo": photo.id, "photo_key": photo.image_key,
                "photo_version": photo.version, "design": design.id, "design_key": design.design_key, "run": run.id,
                "output": output.id, "output_key": out_key}


def _app(sm, storage: LocalStorage, redis: FakeAsyncRedis):
    async def _session():
        async with sm() as s:
            yield s

    app = create_app()
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_redis] = lambda: redis
    app.dependency_overrides[deps.get_session_store] = lambda: SessionStore(redis)
    app.dependency_overrides[deps.get_storage] = lambda: storage
    app.dependency_overrides[deps.get_ingestor] = lambda: BatchIngestor(
        sessionmaker=sm, storage=storage, processor=ImageProcessor(), sku_parser=SkuParser())
    return app


@pytest_asyncio.fixture()
async def world(tmp_path) -> AsyncIterator[dict]:
    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalStorage(tmp_path / "store")
    alice = await make_tenant(sm, "alice@example.com")
    bob = await make_tenant(sm, "bob@example.com")
    seeded = await _seed(sm, storage, alice)
    redis = FakeAsyncRedis()
    transport = ASGITransport(app=_app(sm, storage, redis))
    async with AsyncClient(transport=transport, base_url="http://test", headers=BROWSER_HEADERS) as a, \
            AsyncClient(transport=transport, base_url="http://test", headers=BROWSER_HEADERS) as b:
        authenticate(a, await open_session(redis, alice))
        authenticate(b, await open_session(redis, bob))
        yield {"alice": a, "bob": b, "sm": sm, "storage": storage, "tenant": alice, **seeded}
    await engine.dispose()


# --- copies of the right size, made once and stored --------------------------------------------


@pytest.mark.parametrize(("size", "width"), [("small", 240), ("thumb", 480), ("medium", 1200)])
async def test_each_size_is_a_webp_copy_that_wide(world, size: str, width: int) -> None:
    r = await world["alice"].get(f"/api/assets/{world['asset']}/image", params={"size": size, "v": thumbs.VERSION})
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    fmt, w, h = _size(r.content)
    assert (fmt, w) == ("WEBP", width) and h == round(2000 * width / 1600)
    assert world["storage"].exists(thumbs.key_for(world["asset_key"], size))


async def test_a_copy_is_made_once_then_read_from_storage(world, monkeypatch) -> None:
    made: list[str] = []
    real = thumbs.make
    monkeypatch.setattr(thumbs, "make", lambda data, size, aspect=None: made.append(size) or real(data, size, aspect))
    for _ in range(3):
        assert (await world["alice"].get(f"/api/assets/{world['asset']}/image?size=thumb")).status_code == 200
    assert made == ["thumb"]


async def test_a_cropped_tile_and_the_widths_from_before(world) -> None:
    tile = await world["alice"].get(f"/api/assets/{world['asset']}/image?size=thumb&ar=4:5")
    assert _size(tile.content)[1:] == (480, 600)
    # A page still open from before the update asks with ?w=: it gets the nearest copy.
    old = await world["alice"].get(f"/api/assets/{world['asset']}/image?w=224")
    assert old.headers["content-type"] == "image/webp" and _size(old.content)[1] == 240
    for bad in ("size=huge", "w=333", "ar=4:5", "size=thumb&ar=1:1"):
        assert (await world["alice"].get(f"/api/assets/{world['asset']}/image?{bad}")).status_code == 400, bad


async def test_the_full_file_is_still_there_for_downloads(world) -> None:
    r = await world["alice"].get(f"/api/assets/{world['asset']}/image")
    assert r.content == world["storage"].get(world["asset_key"]) and r.headers["content-type"] == "image/jpeg"


async def test_template_photos_and_mockups_have_the_same_copies(world) -> None:
    photo = await world["alice"].get(f"/api/mockup-photos/{world['photo']}/image?size=medium")
    assert _size(photo.content)[:2] == ("WEBP", 1200)
    tile = await world["alice"].get(f"/api/mockup-runs/{world['run']}/outputs/{world['output']}/image?size=small")
    assert _size(tile.content)[:2] == ("WEBP", 240)
    assert world["storage"].exists(thumbs.key_for(world["output_key"], "small"))


async def test_copies_are_deleted_with_their_file(world) -> None:
    await world["alice"].get(f"/api/assets/{world['asset']}/image?size=thumb")
    world["storage"].delete_with_derivatives(world["asset_key"])
    assert not world["storage"].exists(thumbs.key_for(world["asset_key"], "thumb"))


# --- cache headers, versioned URLs, ETag and 304 -------------------------------------------------


async def test_a_versioned_url_is_kept_for_a_year_any_other_is_checked(world) -> None:
    kept = await world["alice"].get(f"/api/assets/{world['asset']}/image?size=thumb&v={thumbs.VERSION}")
    assert kept.headers["cache-control"] == IMMUTABLE and kept.headers["etag"]
    for url in (f"/api/assets/{world['asset']}/image?size=thumb", f"/api/assets/{world['asset']}/image?size=thumb&v=0"):
        assert (await world["alice"].get(url)).headers["cache-control"] == REVALIDATE


async def test_the_etag_is_answered_304_without_the_image(world) -> None:
    url = f"/api/assets/{world['asset']}/image?size=thumb&v={thumbs.VERSION}"
    first = await world["alice"].get(url)
    again = await world["alice"].get(url, headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and again.content == b""
    assert again.headers["etag"] == first.headers["etag"] and again.headers["cache-control"] == IMMUTABLE
    assert (await world["alice"].get(url, headers={"If-None-Match": '"other"'})).status_code == 200
    # Each size, crop and file has its own tag.
    tags = {first.headers["etag"]}
    for q in ("size=small", "size=thumb&ar=4:5", ""):
        tags.add((await world["alice"].get(f"/api/assets/{world['asset']}/image?{q}")).headers["etag"])
    assert len(tags) == 4


async def test_a_mockup_is_versioned_by_its_stamp(world) -> None:
    url = f"/api/mockup-runs/{world['run']}/outputs/{world['output']}/image?size=small"
    stamp = output_stamp(world["output_key"])
    assert (await world["alice"].get(f"{url}&v={stamp}")).headers["cache-control"] == IMMUTABLE
    assert (await world["alice"].get(f"{url}&v=stale")).headers["cache-control"] == REVALIDATE
    tag = (await world["alice"].get(url)).headers["etag"]
    assert (await world["alice"].get(url, headers={"If-None-Match": tag})).status_code == 304


async def test_a_template_photo_and_its_front_layers(world) -> None:
    photo = await world["alice"].get(f"/api/mockup-photos/{world['photo']}/image?size=thumb&v={thumbs.VERSION}")
    assert photo.headers["cache-control"] == IMMUTABLE
    v = world["photo_version"]
    layer = await world["alice"].get(f"/api/mockup-photos/{world['photo']}/front?layer=brush&v={v}")
    assert layer.status_code == 200 and layer.headers["cache-control"] == IMMUTABLE
    assert (await world["alice"].get(f"/api/mockup-photos/{world['photo']}/front?layer=brush&v={v + 1}")).headers["cache-control"] == REVALIDATE
    assert (await world["alice"].get(f"/api/mockup-photos/{world['photo']}/front?layer=brush",
                                     headers={"If-None-Match": layer.headers["etag"]})).status_code == 304
    # The brush moves the photo's version, so the layer's tag moves with it.
    async with world["sm"]() as s:
        (await s.get(M.MockupPhoto, world["photo"])).version += 1
        await s.commit()
    assert (await world["alice"].get(f"/api/mockup-photos/{world['photo']}/front?layer=brush",
                                     headers={"If-None-Match": layer.headers["etag"]})).status_code == 200


async def test_a_design_file_carries_its_stamp(world) -> None:
    edits = (await world["alice"].get(f"/api/mockup-runs/{world['run']}/edits")).json()
    [file] = edits["designs"][0]["files"]
    assert file["stamp"] == images.stamp(world["design_key"])
    r = await world["alice"].get(f"/api/mockup-designs/{world['design']}/file?v={file['stamp']}")
    assert r.content == world["storage"].get(world["design_key"]) and r.headers["cache-control"] == IMMUTABLE


async def test_after_upload_retention_the_kept_cover_has_another_tag(world) -> None:
    url = f"/api/assets/{world['asset']}/image?size=thumb"
    before = (await world["alice"].get(url)).headers["etag"]
    kept = f"{world['tenant']}/{world['batch']}/kept/{world['asset']}.jpg"
    world["storage"].put(kept, _jpeg(448, 560), "image/jpeg")
    async with world["sm"]() as s:
        a = await s.get(M.Asset, world["asset"])
        a.files_removed_at, a.processed_key, a.thumbnail_key = NOW, None, kept
        await s.commit()
    after = await world["alice"].get(url, headers={"If-None-Match": before})
    assert after.status_code == 200 and _size(after.content)[1] == 448


# --- nobody else's images -------------------------------------------------------------------------


async def test_another_account_never_gets_the_image_or_a_304(world) -> None:
    urls = [
        f"/api/assets/{world['asset']}/image?size=thumb&v={thumbs.VERSION}",
        f"/api/mockup-photos/{world['photo']}/image?size=small",
        f"/api/mockup-runs/{world['run']}/outputs/{world['output']}/image?size=small",
        f"/api/mockup-photos/{world['photo']}/front?layer=brush&v={world['photo_version']}",
        f"/api/mockup-designs/{world['design']}/file",
    ]
    for url in urls:
        tag = (await world["alice"].get(url)).headers["etag"]
        for headers in ({}, {"If-None-Match": tag}, {"If-None-Match": "*"}):
            r = await world["bob"].get(url, headers=headers)
            assert r.status_code == 404 and "etag" not in r.headers, (url, headers)


# --- the upload path: three files at once ---------------------------------------------------------
# The upload page sends three photos of a batch at once. On SQLite (the rest of this file) one
# shared connection cannot hold three transactions, so this runs on a scratch Postgres database,
# created and dropped here beside the development one (synthetic rows only); skipped without one.
SCRATCH = "listyro_upload_concurrency_test"


@pytest_asyncio.fixture()
async def pg_world(tmp_path) -> AsyncIterator[dict]:
    import sqlalchemy as sa

    from app.core.config import get_settings

    url = sa.engine.make_url(get_settings().database_url.replace("+asyncpg", "+psycopg"))
    if url.get_backend_name() != "postgresql":
        pytest.skip("no Postgres configured")
    scratch = url.set(database=SCRATCH).render_as_string(hide_password=False)
    try:
        admin = sa.create_engine(url.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT",
                                 connect_args={"connect_timeout": 2})
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH}"'))
            c.execute(sa.text(f'CREATE DATABASE "{SCRATCH}"'))
    except Exception as exc:  # no server here
        pytest.skip(f"Postgres not reachable: {type(exc).__name__}")
    sync = sa.create_engine(scratch)
    Base.metadata.create_all(sync)
    sync.dispose()
    engine = create_async_engine(scratch.replace("+psycopg", "+asyncpg"))
    sm = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalStorage(tmp_path / "store")
    alice = await make_tenant(sm, "alice@example.com")
    redis = FakeAsyncRedis()
    app = _app(sm, storage, redis)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=BROWSER_HEADERS) as a:
            authenticate(a, await open_session(redis, alice))
            yield {"alice": a, "sm": sm}
    finally:
        await engine.dispose()
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH}"'))
        admin.dispose()


async def test_photos_sent_at_the_same_time_each_get_their_own_place(pg_world) -> None:
    import asyncio

    client = pg_world["alice"]
    batch = (await client.post("/api/batches", json={})).json()["id"]

    async def send(i: int):
        return await client.post(f"/api/batches/{batch}/assets",
                                 files={"file": (f"BR{i}00-front.jpg", _jpeg(800, 1000, (i * 20, 50, 90)), "image/jpeg")},
                                 data={"grouping": "sku"})

    answers = await asyncio.gather(*(send(i) for i in range(1, 7)))
    assert all(r.status_code == 201 for r in answers), [r.text for r in answers]
    async with pg_world["sm"]() as s:
        ranks = sorted((await s.execute(select(M.Asset.rank).where(M.Asset.batch_id == uuid.UUID(batch)))).scalars())
        assert ranks == [1, 2, 3, 4, 5, 6]
        assert (await s.get(M.UploadBatch, uuid.UUID(batch))).file_count == 6
    assert (await client.post(f"/api/batches/{batch}/finalize")).json()["file_count"] == 6


async def test_the_full_file_is_the_stored_one_and_grids_never_get_it(world) -> None:
    batch = (await world["alice"].post("/api/batches", json={})).json()["id"]
    r = await world["alice"].post(f"/api/batches/{batch}/assets", files={"file": ("SKU9-front.jpg", _jpeg(1500, 1800), "image/jpeg")},
                                  data={"grouping": "sku"})
    asset = r.json()["id"]
    async with world["sm"]() as s:
        key = (await s.get(M.Asset, uuid.UUID(asset))).processed_key
    full = await world["alice"].get(f"/api/assets/{asset}/image")
    assert full.content == world["storage"].get(key)
    tile = await world["alice"].get(f"/api/assets/{asset}/image?size=small")
    assert _size(tile.content)[1] == 240 and len(tile.content) < len(full.content) / 4
