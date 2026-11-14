"""Preparing an uploaded photo in the worker (fix/perf-and-memory, 14 Nov 2026).

The upload request stores the file as it came and returns (``pipeline/ingest.py::add_file``); this
job makes the processed copy (at most 2000 px, JPEG) and removes the stored upload. The page shows
"Preparing…" for the photo until it is done and looks again (``AssetOut.status`` ``uploaded``).

Safe to run twice: a photo that is no longer waiting is left alone, and two runs of the same photo
never overlap (a Redis lock). ``requeue_waiting_uploads`` (recovery cron) queues again a photo that
waited too long (the queue emptied, the worker died).
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings

#: A photo still waiting after this long is queued again (a run takes seconds; the queue may have been lost).
WAITING_TOO_LONG = timedelta(minutes=10)
LOCK_SECONDS = 600


def _ingestor(ctx: dict[str, Any]):  # noqa: ANN202
    from app.pipeline.images import ImageProcessor
    from app.pipeline.ingest import BatchIngestor
    from app.pipeline.sku import SkuParser
    from app.pipeline.storage import LocalStorage

    return BatchIngestor(
        sessionmaker=ctx["sessionmaker"],
        storage=ctx.get("storage") or LocalStorage(get_settings().storage_dir),
        processor=ImageProcessor(),
        sku_parser=SkuParser(),
    )


async def process_upload(ctx: dict[str, Any], asset_id: str) -> str:
    redis = ctx.get("redis")
    lock = f"upload-prep:{asset_id}"
    if redis is not None and not await redis.set(lock, "1", nx=True, ex=LOCK_SECONDS):
        return "already running"
    try:
        return await _ingestor(ctx).process_stored(uuid.UUID(asset_id))
    finally:
        if redis is not None:
            await redis.delete(lock)


async def requeue_waiting_uploads(ctx: dict[str, Any]) -> int:
    """Photos stored but never prepared (``uploaded`` longer than ``WAITING_TOO_LONG``): queued again."""
    from app.db.models import Asset, AssetStatus

    cutoff = datetime.now(timezone.utc) - WAITING_TOO_LONG
    async with ctx["sessionmaker"]() as session:
        ids = list((await session.scalars(
            select(Asset.id).where(Asset.status == AssetStatus.uploaded, Asset.storage_key.is_not(None),
                                   Asset.uploaded_at < cutoff).limit(500)
        )).all())
    bucket = int(time.time() // WAITING_TOO_LONG.total_seconds())
    for asset_id in ids:
        # A new job id each period: arq keeps a finished job's id for a while and would refuse the same one.
        await ctx["redis"].enqueue_job("process_upload", str(asset_id), _job_id=f"process-upload:{asset_id}:{bucket}")
    return len(ids)
