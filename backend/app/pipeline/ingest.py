"""Upload-batch ingestion: parse SKUs, process images, persist assets.

For each uploaded file this stores the original, runs it through the image
processor, stores the derivative, and writes an :class:`Asset` row with a 1-based
display ``rank``. A file that fails to process is recorded as a ``failed`` asset
(original still kept) rather than aborting the whole batch.

No Etsy dependency: taxonomy mapping, content generation and draft creation come
in later parts of the pipeline.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Asset, AssetStatus, UploadBatch, UploadBatchStatus
from app.core.config import get_settings
from app.pipeline.images import ImageProcessingError, ImageProcessor, ProcessingSpec
from app.pipeline.uploads import AdmittedImage, UploadRejected, UploadTooLarge, admit_image
from app.pipeline.sku import SkuParser
from app.pipeline.storage import Storage



@dataclass
class UploadFile:
    filename: str
    data: bytes


@dataclass
class IngestConfig:
    processing: ProcessingSpec = field(default_factory=ProcessingSpec)
    sort_by_filename: bool = False


def _admit(upload: UploadFile) -> AdmittedImage:
    """Decide from the bytes whether ``upload`` is an image we accept (raises if not)."""
    settings = get_settings()
    return admit_image(
        upload.data,
        max_bytes=settings.max_upload_bytes,
        max_pixels=settings.max_image_pixels,
    )


class BatchIngestor:
    def __init__(
        self,
        sessionmaker: async_sessionmaker,
        storage: Storage,
        processor: ImageProcessor,
        sku_parser: SkuParser,
        config: IngestConfig | None = None,
    ) -> None:
        self._sm = sessionmaker
        self._storage = storage
        self._processor = processor
        self._sku = sku_parser
        self._config = config or IngestConfig()

    async def ingest(self, tenant_id: uuid.UUID, files: list[UploadFile]) -> uuid.UUID:
        """Ingest ``files`` for a tenant and return the created batch id."""
        cfg = self._config
        ordered = (
            sorted(files, key=lambda f: f.filename) if cfg.sort_by_filename else list(files)
        )

        async with self._sm() as session:
            batch = UploadBatch(
                tenant_id=tenant_id,
                status=UploadBatchStatus.processing,
                file_count=len(ordered),
            )
            session.add(batch)
            await session.flush()
            batch_id = batch.id

            processed_any = False
            rank = 0
            for upload in ordered:
                # Admission comes before storage: a refused file never lands on disk.
                try:
                    admitted = _admit(upload)
                except UploadRejected:
                    continue
                rank += 1
                asset = await self._ingest_one(tenant_id, batch_id, rank, upload, admitted)
                session.add(asset)
                processed_any = processed_any or asset.status is AssetStatus.processed

            batch.status = (
                UploadBatchStatus.ready if processed_any else UploadBatchStatus.failed
            )
            await session.commit()
            return batch_id

    # --- Incremental (per-file) API flow -----------------------------------
    # The batch-at-once ``ingest`` above suits workers; the UI uploads files one
    # at a time for per-file progress, so these drive the same steps with the
    # caller's session.
    async def create_batch(self, session: AsyncSession, tenant_id: uuid.UUID) -> UploadBatch:
        batch = UploadBatch(
            tenant_id=tenant_id, status=UploadBatchStatus.uploading, file_count=0
        )
        session.add(batch)
        await session.flush()
        return batch

    async def add_file(
        self,
        session: AsyncSession,
        batch_id: uuid.UUID,
        tenant_id: uuid.UUID,
        upload: UploadFile,
        group_key: str | None = None,
    ) -> Asset:
        """Process and persist one uploaded file.

        ``rank`` follows arrival order for now; ``finalize_batch`` re-ranks each
        folder group alphabetically (D1). ``group_key`` (the folder) groups a
        listing's images and drives the SKU (D2).
        """
        # Admission comes before storage: a refused file never lands on disk.
        admitted = _admit(upload)

        used = await session.scalar(
            select(func.coalesce(func.sum(Asset.byte_size), 0)).where(Asset.batch_id == batch_id)
        )
        ceiling = get_settings().max_batch_bytes
        if int(used or 0) + len(upload.data) > ceiling:
            raise UploadTooLarge(
                f"a batch is limited to {ceiling // (1024 * 1024)} MB in total"
            )

        count = await session.scalar(
            select(func.count()).select_from(Asset).where(Asset.batch_id == batch_id)
        )
        rank = int(count or 0) + 1
        asset = await self._ingest_one(tenant_id, batch_id, rank, upload, admitted, group_key)
        session.add(asset)
        batch = await session.get(UploadBatch, batch_id)
        if batch is not None:
            batch.file_count = rank
            if batch.status is UploadBatchStatus.uploading:
                batch.status = UploadBatchStatus.processing
        await session.commit()
        await session.refresh(asset)
        return asset

    async def finalize_batch(self, session: AsyncSession, batch_id: uuid.UUID) -> UploadBatch:
        """Mark the batch ready if any asset processed, else failed.

        Also re-ranks each folder group alphabetically by filename (D1), so a
        listing's images always appear in a stable, predictable order.
        """
        batch = await session.get(UploadBatch, batch_id)
        if batch is None:
            raise KeyError(batch_id)

        rows = await session.execute(select(Asset).where(Asset.batch_id == batch_id))
        assets = list(rows.scalars())
        groups: dict[str, list[Asset]] = {}
        for asset in assets:
            groups.setdefault(asset.group_key or "", []).append(asset)
        for members in groups.values():
            for rank, asset in enumerate(
                sorted(members, key=lambda a: a.original_filename.lower()), start=1
            ):
                asset.rank = rank

        processed = sum(1 for a in assets if a.status is AssetStatus.processed)
        batch.status = UploadBatchStatus.ready if processed > 0 else UploadBatchStatus.failed
        await session.commit()
        await session.refresh(batch)
        return batch

    async def _ingest_one(
        self,
        tenant_id: uuid.UUID,
        batch_id: uuid.UUID,
        rank: int,
        upload: UploadFile,
        admitted: AdmittedImage,
        group_key: str | None = None,
    ) -> Asset:
        asset_id = uuid.uuid4()
        # Folder-name SKU takes precedence over the filename rule (D2).
        sku = self._sku.parse_group(group_key) or self._sku.parse(upload.filename)
        # Extension and type come from the sniffed contents, never the filename:
        # "photo.png" holding HTML is stored and served as nothing but refused.
        original_key = f"{tenant_id}/{batch_id}/original/{asset_id}{admitted.ext}"
        self._storage.put(original_key, upload.data, admitted.mime)

        try:
            # Image work is CPU-bound; keep the event loop free.
            processed = await asyncio.to_thread(
                self._processor.process, upload.data, self._config.processing
            )
        except ImageProcessingError:
            return Asset(
                id=asset_id,
                batch_id=batch_id,
                tenant_id=tenant_id,
                original_filename=upload.filename,
                parsed_sku=sku,
                group_key=group_key,
                storage_key=original_key,
                mime_type=admitted.mime,
                byte_size=len(upload.data),
                rank=rank,
                status=AssetStatus.failed,
            )

        processed_key = f"{tenant_id}/{batch_id}/processed/{asset_id}{processed.extension}"
        self._storage.put(processed_key, processed.data, processed.mime_type)
        return Asset(
            id=asset_id,
            batch_id=batch_id,
            tenant_id=tenant_id,
            original_filename=upload.filename,
            parsed_sku=sku,
            group_key=group_key,
            storage_key=original_key,
            processed_key=processed_key,
            mime_type=processed.mime_type,
            width=processed.width,
            height=processed.height,
            byte_size=len(upload.data),
            rank=rank,
            status=AssetStatus.processed,
        )
