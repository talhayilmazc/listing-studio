"""fix/perf-and-memory (production digest, 14 Nov 2026).

- An upload returns before its image is processed: the request stores the file and queues ``process_upload``;
  the worker makes the processed copy and removes the stored upload (pipeline/ingest.py, workers/uploads.py).
  A photo waiting too long is queued again; nothing is written from a group whose photos are not ready.
- ``/generate`` answers at once: the writing job writes, as "Write listings" does.
- The reads load what they show: the review page's listings ask a fixed number of queries whatever their
  number (the N+1 of each listing's profile and shop), and the shop summary and the partner check read three
  fields of the cached listings, never the whole listing.
"""

from __future__ import annotations

import io
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from PIL import Image
from sqlalchemy import event, select

from app.api import deps
from app.db.models import Asset, AssetStatus, ShopListingCache, UploadBatch
from app.pipeline import ingest as ingest_module
from app.pipeline.images import ImageProcessor
from app.pipeline.ingest import BatchIngestor
from app.pipeline.sku import SkuParser
from app.pipeline.storage import LocalStorage
from app.workers import uploads
from tests.test_publish_api import ctx  # noqa: F401  (fixture)
from tests.test_regenerate import VISION, _content_reply, llm  # noqa: F401
from tests.test_writing_job import _job_ctx, _two_groups, _written
from tests.support import fake_response


def _jpeg(side: int = 900) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (side, side), "orange").save(buf, "JPEG")
    return buf.getvalue()


def _ingestor(ctx, tmp_path, queued: list) -> BatchIngestor:  # noqa: F811
    async def dispatch(asset_id: uuid.UUID) -> None:
        queued.append(asset_id)

    ingestor = BatchIngestor(sessionmaker=ctx["sm"], storage=LocalStorage(tmp_path), processor=ImageProcessor(),
                             sku_parser=SkuParser(), dispatch=dispatch)
    ctx["app"].dependency_overrides[deps.get_storage] = lambda: LocalStorage(tmp_path)
    ctx["app"].dependency_overrides[deps.get_ingestor] = lambda: ingestor
    return ingestor


async def test_an_upload_returns_before_its_image_is_processed(ctx, tmp_path, monkeypatch) -> None:  # noqa: F811
    processed: list[int] = []
    real = ImageProcessor.process

    def watching(self, data: bytes, spec: Any) -> Any:  # noqa: ANN001
        processed.append(len(data))
        return real(self, data, spec)

    monkeypatch.setattr(ImageProcessor, "process", watching)
    queued: list[uuid.UUID] = []
    ingestor = _ingestor(ctx, tmp_path, queued)
    batch = (await ctx["client"].post("/api/batches", json={})).json()["id"]
    res = await ctx["client"].post(f"/api/batches/{batch}/assets", files={"file": ("BR1-front.jpg", _jpeg(), "image/jpeg")})
    assert res.status_code == 201, res.text
    out = res.json()
    # Stored and queued, not processed: the page shows "Preparing…" and looks again.
    assert processed == [] and out["status"] == "uploaded" and out["parsed_sku"] == "BR1"
    assert len(queued) == 1 and str(queued[0]) == out["id"]
    async with ctx["sm"]() as s:
        asset = await s.get(Asset, queued[0])
        incoming = asset.storage_key
        assert incoming.startswith(f"{ctx['tenant_id']}/{batch}/incoming/") and asset.processed_key is None
        assert asset.uploaded_at is not None
    assert (tmp_path / incoming).is_file()
    # The batch is ready while its photos are prepared (they become processed ones).
    assert (await ctx["client"].post(f"/api/batches/{batch}/finalize")).json()["status"] == "ready"

    # The worker's job: the processed copy, the stored upload gone.
    assert await uploads.process_upload({"sessionmaker": ctx["sm"], "storage": LocalStorage(tmp_path),
                                         "redis": ctx["redis"]}, str(queued[0])) == "processed"
    assert processed == [len(_jpeg())]
    async with ctx["sm"]() as s:
        asset = await s.get(Asset, queued[0])
        assert asset.status is AssetStatus.processed and asset.storage_key is None
        assert (tmp_path / asset.processed_key).is_file()
    assert not (tmp_path / incoming).exists()
    # Run again (the recovery queued it twice): nothing happens.
    assert await ingestor.process_stored(queued[0]) == "nothing to do"


async def test_an_image_that_cannot_be_read_fails_with_a_reason_and_keeps_nothing(ctx, tmp_path, monkeypatch) -> None:  # noqa: F811
    from app.pipeline.images import ImageProcessingError

    def broken(self, data: bytes, spec: Any) -> Any:  # noqa: ANN001
        raise ImageProcessingError("bad")

    monkeypatch.setattr(ImageProcessor, "process", broken)
    queued: list[uuid.UUID] = []
    ingestor = _ingestor(ctx, tmp_path, queued)
    batch = (await ctx["client"].post("/api/batches", json={})).json()["id"]
    await ctx["client"].post(f"/api/batches/{batch}/assets", files={"file": ("BR2.jpg", _jpeg(), "image/jpeg")})
    assert await ingestor.process_stored(queued[0]) == "failed"
    async with ctx["sm"]() as s:
        asset = await s.get(Asset, queued[0])
        assert asset.status is AssetStatus.failed and asset.error.startswith("This image could not be read.")
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


async def test_a_photo_left_waiting_is_queued_again(ctx, tmp_path) -> None:  # noqa: F811
    queued: list[uuid.UUID] = []
    _ingestor(ctx, tmp_path, queued)
    batch = (await ctx["client"].post("/api/batches", json={})).json()["id"]
    await ctx["client"].post(f"/api/batches/{batch}/assets", files={"file": ("BR3.jpg", _jpeg(), "image/jpeg")})

    class Queue:
        def __init__(self) -> None:
            self.jobs: list[tuple] = []

        async def enqueue_job(self, function: str, *args: Any, **kw: Any) -> None:
            self.jobs.append((function, args))

    queue = Queue()
    assert await uploads.requeue_waiting_uploads({"sessionmaker": ctx["sm"], "redis": queue}) == 0  # just arrived
    async with ctx["sm"]() as s:
        (await s.get(Asset, queued[0])).uploaded_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        await s.commit()
    assert await uploads.requeue_waiting_uploads({"sessionmaker": ctx["sm"], "redis": queue}) == 1
    assert queue.jobs == [("process_upload", (str(queued[0]),))]


async def test_nothing_is_written_from_photos_still_being_prepared(ctx, tmp_path, llm) -> None:  # noqa: F811
    batch, profile = await _two_groups(ctx)
    async with ctx["sm"]() as s:
        asset = (await s.execute(select(Asset).where(Asset.batch_id == batch))).scalars().first()
        asset.status = AssetStatus.uploaded
        await s.commit()
    for path in ("generate", "write"):
        res = await ctx["client"].post(f"/api/batches/{batch}/{path}", json={"profile_id": str(profile)})
        assert res.status_code == 409 and "still being prepared" in res.json()["detail"], (path, res.text)
    assert not ctx["enqueuer"].calls and llm.calls == []


async def test_generate_answers_at_once_and_the_worker_writes(ctx, llm) -> None:  # noqa: F811
    batch, profile = await _two_groups(ctx)
    for _ in range(2):
        llm._responses.extend([fake_response(VISION), fake_response(_content_reply())])
    started = time.perf_counter()
    res = await ctx["client"].post(f"/api/batches/{batch}/generate", json={"profile_id": str(profile)})
    took = time.perf_counter() - started
    assert res.status_code == 200, res.text
    out = res.json()
    # Queued: no AI call in the request, nothing written yet; the page follows /writing.
    assert out["state"] == "queued" and out["total"] == 2 and took < 2
    assert llm.calls == [] and await _written(ctx, batch) == 0
    assert [c[0] for c in ctx["enqueuer"].calls] == ["write_batch_listings"]
    # A second click while it is queued: refused, never a second run.
    again = await ctx["client"].post(f"/api/batches/{batch}/generate", json={"profile_id": str(profile)})
    assert again.status_code == 409
    from app.workers import writing

    assert await writing.write_batch_listings(_job_ctx(ctx), str(batch)) == "done"
    assert await _written(ctx, batch) == 2
    state = (await ctx["client"].get(f"/api/batches/{batch}/writing")).json()
    assert (state["state"], state["written"]) == ("done", 2)


async def test_generate_carries_regenerate_and_its_confirmation_to_the_worker(ctx, llm) -> None:  # noqa: F811
    from app.workers import writing

    batch, profile = await _two_groups(ctx)
    res = await ctx["client"].post(f"/api/batches/{batch}/generate",
                                   json={"profile_id": str(profile), "group_key": "G1", "replace": True,
                                         "replace_approved": True, "only_empty": False})
    assert res.status_code == 200 and res.json()["total"] == 1
    state = await writing.read(ctx["redis"], batch)
    assert state["groups"] == ["G1"]
    assert state["options"] == {"replace": True, "replace_approved": True, "only_empty": False}


# -- the reads -----------------------------------------------------------------------------------


def _counting(ctx) -> list[str]:  # noqa: F811
    seen: list[str] = []
    engine = ctx["sm"].kw["bind"]

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, *_a) -> None:  # noqa: ANN001
        seen.append(statement)

    return seen


async def _review_page(ctx, listings: int) -> uuid.UUID:  # noqa: F811
    """A batch of ``listings`` written listings across 4 profiles in 2 shops."""
    from app.db.models import EtsyConnection, GeneratedContent, ListingProfile

    async with ctx["sm"]() as s:
        tenant = ctx["tenant_id"]
        shops = list((await s.execute(select(EtsyConnection).where(EtsyConnection.tenant_id == tenant))).scalars())
        while len(shops) < 2:
            shop = EtsyConnection(tenant_id=tenant, status=shops[0].status, etsy_user_id=900 + len(shops),
                                  shop_id=900 + len(shops), shop_name=f"Extra{len(shops)}", position=len(shops))
            s.add(shop)
            shops.append(shop)
        await s.flush()
        profiles = []
        for n in range(4):
            p = ListingProfile(tenant_id=tenant, connection_id=shops[n % 2].id, name=f"P{n}", reference_listing_id=n + 1,
                               content_template="apparel", cached_payload={"price": 20, "currency": "USD"})
            s.add(p)
            profiles.append(p)
        batch = UploadBatch(tenant_id=tenant, status="ready", file_count=listings)
        s.add(batch)
        await s.flush()
        for n in range(listings):
            asset = Asset(tenant_id=tenant, batch_id=batch.id, original_filename=f"D{n}.png", group_key=f"D{n}",
                          parsed_sku=f"D{n}", processed_key=f"x/{n}.jpg", status=AssetStatus.processed, rank=1)
            s.add(asset)
            await s.flush()
            s.add(GeneratedContent(tenant_id=tenant, batch_id=batch.id, asset_id=asset.id, title=f"Shirt {n}",
                                   tags=["tee"], description="A shirt.", listing_profile_id=profiles[n % 4].id))
        await s.commit()
        return batch.id


async def test_the_review_page_asks_the_same_queries_for_10_or_40_listings(ctx) -> None:  # noqa: F811
    small, large = await _review_page(ctx, 10), await _review_page(ctx, 40)
    seen = _counting(ctx)
    assert (await ctx["client"].get(f"/api/batches/{small}/content")).status_code == 200
    ten = len(seen)
    seen.clear()
    res = await ctx["client"].get(f"/api/batches/{large}/content")
    assert res.status_code == 200 and len(res.json()) == 40
    # Was 2 more per listing (each listing's profile and shop read again): 125 queries for 50 listings.
    assert len(seen) == ten, (ten, len(seen))


async def test_the_shop_summary_reads_three_fields_never_whole_listings(ctx) -> None:  # noqa: F811
    from app.db.models import EtsyConnection

    async with ctx["sm"]() as s:
        shop = (await s.execute(select(EtsyConnection).where(EtsyConnection.tenant_id == ctx["tenant_id"]))).scalars().first()
        now = datetime.now(timezone.utc)
        for n in range(30):
            s.add(ShopListingCache(tenant_id=ctx["tenant_id"], connection_id=shop.id, listing_id=5000 + n, fetched_at=now,
                                   payload={"state": "active" if n % 3 else "draft", "title": "x" * 140,
                                            "description": "y" * 4000, "state_timestamp": int(now.timestamp()) - 60}))
        await s.commit()
    seen = _counting(ctx)
    out = (await ctx["client"].get(f"/api/shop/summary?shop={shop.id}")).json()
    assert (out["total"], out["active"], out["draft"]) == (30, 20, 10)
    assert out["published_this_month"] == 20
    reads = [q for q in seen if "shop_listing_cache" in q]
    # The payload only inside a JSON extraction (SQLite here, ->> on Postgres), never the column itself.
    assert reads and all(re.search(r"(?<!JSON_EXTRACT\()shop_listing_cache\.payload", q) is None for q in reads), reads
