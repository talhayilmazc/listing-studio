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
import os
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import Asset, AssetStatus, UploadBatch, UploadBatchStatus
from app.pipeline.images import ImageProcessingError, ImageProcessor, ProcessingSpec
from app.pipeline.sku import SkuParser
from app.pipeline.storage import Storage

_EXT_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


@dataclass
class UploadFile:
    filename: str
    data: bytes


@dataclass
class IngestConfig:
    processing: ProcessingSpec = field(default_factory=ProcessingSpec)
    sort_by_filename: bool = False


def _original_mime(filename: str) -> str:
    return _EXT_MIME.get(os.path.splitext(filename)[1].lower(), "application/octet-stream")


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
            for rank, upload in enumerate(ordered, start=1):
                asset = await self._ingest_one(tenant_id, batch_id, rank, upload)
                session.add(asset)
                processed_any = processed_any or asset.status is AssetStatus.processed

            batch.status = (
                UploadBatchStatus.ready if processed_any else UploadBatchStatus.failed
            )
            await session.commit()
            return batch_id

    async def _ingest_one(
        self, tenant_id: uuid.UUID, batch_id: uuid.UUID, rank: int, upload: UploadFile
    ) -> Asset:
        asset_id = uuid.uuid4()
        sku = self._sku.parse(upload.filename)
        ext = os.path.splitext(upload.filename)[1].lower() or ".bin"
        original_key = f"{tenant_id}/{batch_id}/original/{asset_id}{ext}"
        self._storage.put(original_key, upload.data, _original_mime(upload.filename))

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
                storage_key=original_key,
                mime_type=_original_mime(upload.filename),
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
            storage_key=original_key,
            processed_key=processed_key,
            mime_type=processed.mime_type,
            width=processed.width,
            height=processed.height,
            rank=rank,
            status=AssetStatus.processed,
        )
