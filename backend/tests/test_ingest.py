"""Batch ingestion tests: persistence, rank, SKU, storage side effects."""

import uuid
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import Asset, AssetStatus, Tenant, UploadBatch, UploadBatchStatus
from app.pipeline.images import ImageProcessor, PillowBackend, ProcessingSpec
from app.pipeline.ingest import BatchIngestor, IngestConfig, UploadFile
from app.pipeline.sku import SkuParser
from app.pipeline.storage import LocalStorage


async def _make_tenant(sm: async_sessionmaker) -> uuid.UUID:
    async with sm() as session:
        tenant = Tenant(email=f"{uuid.uuid4()}@example.com", password_hash="x")
        session.add(tenant)
        await session.commit()
        return tenant.id


def _ingestor(sm: async_sessionmaker, tmp_path, *, sort: bool = False) -> BatchIngestor:
    return BatchIngestor(
        sessionmaker=sm,
        storage=LocalStorage(tmp_path),
        processor=ImageProcessor(backend=PillowBackend()),
        sku_parser=SkuParser(),
        config=IngestConfig(processing=ProcessingSpec(target_format="JPEG"), sort_by_filename=sort),
    )


async def _assets(sm: async_sessionmaker, batch_id: uuid.UUID) -> list[Asset]:
    async with sm() as session:
        rows = await session.execute(
            select(Asset).where(Asset.batch_id == batch_id).order_by(Asset.rank)
        )
        return list(rows.scalars())


async def test_ingest_persists_assets_with_rank_and_sku(
    async_sm: async_sessionmaker, tmp_path, make_image: Callable[..., bytes]
) -> None:
    tenant_id = await _make_tenant(async_sm)
    storage = LocalStorage(tmp_path)
    ingestor = BatchIngestor(
        sessionmaker=async_sm,
        storage=storage,
        processor=ImageProcessor(backend=PillowBackend()),
        sku_parser=SkuParser(),
        config=IngestConfig(processing=ProcessingSpec(target_format="JPEG")),
    )
    files = [
        UploadFile("SKU1_front.png", make_image(400, 300)),
        UploadFile("SKU1_back.png", make_image(300, 400)),
        UploadFile("SKU2-main.png", make_image(200, 200)),
    ]

    batch_id = await ingestor.ingest(tenant_id, files)

    async with async_sm() as session:
        batch = await session.get(UploadBatch, batch_id)
        assert batch.status is UploadBatchStatus.ready
        assert batch.file_count == 3

    assets = await _assets(async_sm, batch_id)
    assert [a.rank for a in assets] == [1, 2, 3]
    assert [a.parsed_sku for a in assets] == ["SKU1", "SKU1", "SKU2"]
    for asset in assets:
        assert asset.status is AssetStatus.processed
        assert asset.mime_type == "image/jpeg"
        assert asset.width and asset.height
        # Both original and processed derivatives were written to storage.
        assert storage.exists(asset.storage_key)
        assert storage.exists(asset.processed_key)


async def test_corrupt_file_marked_failed_without_aborting_batch(
    async_sm: async_sessionmaker, tmp_path, make_image: Callable[..., bytes]
) -> None:
    tenant_id = await _make_tenant(async_sm)
    ingestor = _ingestor(async_sm, tmp_path)
    files = [
        UploadFile("good_1.png", make_image(200, 200)),
        UploadFile("broken_2.png", b"not an image"),
    ]

    batch_id = await ingestor.ingest(tenant_id, files)

    async with async_sm() as session:
        batch = await session.get(UploadBatch, batch_id)
        assert batch.status is UploadBatchStatus.ready  # partial success still ready

    assets = await _assets(async_sm, batch_id)
    by_name = {a.original_filename: a for a in assets}
    assert by_name["good_1.png"].status is AssetStatus.processed
    assert by_name["good_1.png"].processed_key is not None
    broken = by_name["broken_2.png"]
    assert broken.status is AssetStatus.failed
    assert broken.processed_key is None  # no derivative
    assert broken.storage_key is not None  # original still kept


async def test_all_failed_marks_batch_failed(
    async_sm: async_sessionmaker, tmp_path
) -> None:
    tenant_id = await _make_tenant(async_sm)
    ingestor = _ingestor(async_sm, tmp_path)
    files = [UploadFile("a.png", b"nope"), UploadFile("b.png", b"still not")]

    batch_id = await ingestor.ingest(tenant_id, files)

    async with async_sm() as session:
        batch = await session.get(UploadBatch, batch_id)
        assert batch.status is UploadBatchStatus.failed


async def test_sort_by_filename_orders_rank(
    async_sm: async_sessionmaker, tmp_path, make_image: Callable[..., bytes]
) -> None:
    tenant_id = await _make_tenant(async_sm)
    ingestor = _ingestor(async_sm, tmp_path, sort=True)
    files = [
        UploadFile("c.png", make_image(100, 100)),
        UploadFile("a.png", make_image(100, 100)),
        UploadFile("b.png", make_image(100, 100)),
    ]

    batch_id = await ingestor.ingest(tenant_id, files)

    assets = await _assets(async_sm, batch_id)
    assert [a.original_filename for a in assets] == ["a.png", "b.png", "c.png"]
    assert [a.rank for a in assets] == [1, 2, 3]
