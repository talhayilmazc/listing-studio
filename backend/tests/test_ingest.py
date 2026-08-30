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


# --- folder groups (spec §D1/D2) -------------------------------------------
async def _new_batch(ingestor: BatchIngestor, sm: async_sessionmaker, tenant_id: uuid.UUID):
    async with sm() as s:
        batch = await ingestor.create_batch(s, tenant_id)
        await s.commit()
        return batch.id


async def test_folder_groups_take_folder_sku_and_alphabetical_rank(
    async_sm: async_sessionmaker, tmp_path, make_image: Callable[..., bytes]
) -> None:
    tenant_id = await _make_tenant(async_sm)
    ingestor = _ingestor(async_sm, tmp_path)
    batch_id = await _new_batch(ingestor, async_sm, tenant_id)

    # Two folders; files uploaded out of alphabetical order.
    uploads = [
        ("BR5475", "b_back.png"),
        ("BR5475", "a_front.png"),
        ("designs/AB1234", "z.png"),
        ("designs/AB1234", "m.png"),
    ]
    async with async_sm() as s:
        for group_key, name in uploads:
            await ingestor.add_file(
                s, batch_id, tenant_id, UploadFile(name, make_image(80, 80)), group_key=group_key
            )
        await ingestor.finalize_batch(s, batch_id)

    assets = await _assets(async_sm, batch_id)
    groups: dict[str, list[Asset]] = {}
    for a in assets:
        groups.setdefault(a.group_key, []).append(a)
    assert set(groups) == {"BR5475", "designs/AB1234"}
    # SKU parsed from the folder name (last segment), not the filename.
    assert {a.parsed_sku for a in groups["BR5475"]} == {"BR5475"}
    assert {a.parsed_sku for a in groups["designs/AB1234"]} == {"AB1234"}
    # Ranks are alphabetical within each group, regardless of upload order.
    br = sorted(groups["BR5475"], key=lambda a: a.rank)
    assert [a.original_filename for a in br] == ["a_front.png", "b_back.png"]
    assert [a.rank for a in br] == [1, 2]
    ab = sorted(groups["designs/AB1234"], key=lambda a: a.rank)
    assert [a.original_filename for a in ab] == ["m.png", "z.png"]
    assert [a.rank for a in ab] == [1, 2]


async def test_ten_folders_make_ten_groups(
    async_sm: async_sessionmaker, tmp_path, make_image: Callable[..., bytes]
) -> None:
    tenant_id = await _make_tenant(async_sm)
    ingestor = _ingestor(async_sm, tmp_path)
    batch_id = await _new_batch(ingestor, async_sm, tenant_id)
    async with async_sm() as s:
        for i in range(10):
            await ingestor.add_file(
                s,
                batch_id,
                tenant_id,
                UploadFile(f"img_{i}.png", make_image(50, 50)),
                group_key=f"BR{1000 + i}",
            )
        await ingestor.finalize_batch(s, batch_id)

    assets = await _assets(async_sm, batch_id)
    assert len({a.group_key for a in assets}) == 10  # 10 folders -> 10 groups
    assert all(a.rank == 1 for a in assets)  # one image per group


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
