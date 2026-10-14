"""Making a batch's mockups (pipeline/mockups.py), in the worker.

One run prints every design of the batch on every template the seller chose,
in the seller's template order. Each mockup is an ordinary processed photo of
the design's listing group (``asset``, with the design and template it came
from), named so that the group's alphabetical order is the template order: the
first template's mockup is the cover. Everything after that (the board, review,
drafts, upload retention, the storage cap) treats them like uploaded photos.

No Etsy call and no model call is made here; nothing is counted against the
seller's "Listings generated" allowance. Progress is saved after every mockup.
A run started again replaces the mockups of an earlier run of the same batch
(only while nothing has been written for the batch: the API checks).
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage_cap
from app.db.models import (
    Asset,
    AssetStatus,
    MockupDesign,
    MockupRun,
    MockupTemplate,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.pipeline import mockups, upload_retention
from app.pipeline.images import ImageProcessingError
from app.pipeline.ingest import _rerank
from app.pipeline.storage import Storage

logger = logging.getLogger(__name__)

#: The most templates one run prints on (Etsy keeps 20 photos per listing).
MAX_TEMPLATES = 10
#: Said on the run when a design cannot be printed (its own words stay in the log).
DESIGN_FAILED = "Some designs could not be read as images."


def mockup_filename(group_key: str, index: int, template_name: str) -> str:
    """"BR5229/01-white tee.jpg": the template order is the group's alphabetical order."""
    name = re.sub(r"[^A-Za-z0-9 ._-]+", "", template_name).strip()[:40] or "mockup"
    return f"{group_key}/{index:02d}-{name}.jpg"


async def _templates(session: AsyncSession, run: MockupRun) -> list[MockupTemplate]:
    found: list[MockupTemplate] = []
    for raw in run.template_ids:
        template = await session.get(MockupTemplate, uuid.UUID(str(raw)))
        if template is not None and template.tenant_id == run.tenant_id:
            found.append(template)
    return found


async def replace_earlier(session: AsyncSession, storage: Storage, batch_id: uuid.UUID) -> int:
    """Remove the mockups an earlier run made for this batch (files, then rows)."""
    rows = list((await session.execute(
        select(Asset).where(Asset.batch_id == batch_id, Asset.mockup_design_id.is_not(None))
    )).scalars())
    for asset in rows:
        if asset.processed_key:
            await asyncio.to_thread(storage.delete_with_derivatives, asset.processed_key)
    if rows:
        await session.execute(delete(Asset).where(Asset.id.in_([a.id for a in rows])))
    return len(rows)


async def run(session: AsyncSession, storage: Storage, run_id: uuid.UUID) -> MockupRun | None:
    """Make one run's mockups. Returns the run, or None when there is nothing to do."""
    job = await session.get(MockupRun, run_id)
    if job is None or job.status not in ("queued", "running"):
        return job
    job.status, job.started_at, job.done, job.failed, job.error = "running", datetime.now(timezone.utc), 0, 0, None
    templates = await _templates(session, job)
    designs = list((await session.execute(
        select(MockupDesign)
        .where(MockupDesign.batch_id == job.batch_id, MockupDesign.design_key.is_not(None),
               MockupDesign.files_removed_at.is_(None))
        .order_by(MockupDesign.group_key, MockupDesign.original_filename)
    )).scalars())
    job.total = len(designs) * len(templates)
    await replace_earlier(session, storage, job.batch_id)
    await session.commit()

    tenant = await session.get(Tenant, job.tenant_id)
    cap = storage_cap.limit(tenant) if tenant is not None else None
    days = await upload_retention.policy(session)
    in_use = await asyncio.to_thread(storage_cap.used, storage, job.tenant_id)

    try:
        maps: dict[uuid.UUID, mockups.Maps] = {}
        for template in templates:
            blank, mask = storage.get(template.image_key), storage.get(template.mask_key)
            maps[template.id] = await asyncio.to_thread(mockups.build_maps, blank, mask)
        made = 0
        for design in designs:
            try:
                image = await asyncio.to_thread(mockups.load_design, storage.get(design.design_key or ""))
            except (mockups.MockupError, ImageProcessingError, FileNotFoundError) as exc:
                logger.info("mockup design %s could not be used: %s", design.id, exc)
                job.failed += len(templates)
                job.error = DESIGN_FAILED
                await session.commit()
                continue
            for index, template in enumerate(templates, start=1):
                settings = mockups.Settings(template.print_area, template.size, template.fold_strength)
                try:
                    jpeg = await asyncio.to_thread(mockups.render, maps[template.id], image, settings)
                except mockups.MockupError as exc:
                    job.failed += 1
                    job.error = f"{template.name}: {exc}"
                    await session.commit()
                    continue
                if cap is not None and in_use + len(jpeg) > cap:
                    raise storage_cap.StorageFull(storage_cap.message(in_use, cap, len(jpeg), days.drafted_days, days.unpublished_days))
                asset_id = uuid.uuid4()
                key = f"{job.tenant_id}/{job.batch_id}/processed/{asset_id}.jpg"
                storage.put(key, jpeg, "image/jpeg")
                storage_cap.added(job.tenant_id, len(jpeg))
                in_use += len(jpeg)
                session.add(Asset(
                    id=asset_id, batch_id=job.batch_id, tenant_id=job.tenant_id,
                    original_filename=mockup_filename(design.group_key, index, template.name),
                    parsed_sku=design.parsed_sku, group_key=design.group_key, upload_folder=design.group_key,
                    processed_key=key, mime_type="image/jpeg", width=maps[template.id].width,
                    height=maps[template.id].height, byte_size=len(jpeg), rank=index,
                    status=AssetStatus.processed, mockup_design_id=design.id, mockup_template_id=template.id,
                ))
                job.done += 1
                made += 1
                await session.commit()
        job.status = "succeeded" if made or not job.total else "failed"
    except storage_cap.StorageFull as exc:
        job.status, job.error = "failed", str(exc)
    except Exception:  # an unexpected fault: the run says so, the log has the rest
        logger.exception("mockup run %s failed", job.id)
        await session.rollback()
        job = await session.get(MockupRun, run_id)
        if job is None:
            return None
        job.status, job.error = "failed", "The mockups could not be made. Please try again."
    batch = await session.get(UploadBatch, job.batch_id)
    if batch is not None:
        assets = list((await session.execute(select(Asset).where(Asset.batch_id == batch.id))).scalars())
        _rerank(assets)
        batch.file_count = len(assets)
        batch.grouping_mode = batch.grouping_mode or "folder"
        if any(a.status is AssetStatus.processed for a in assets):
            batch.status = UploadBatchStatus.ready
        elif batch.status in (UploadBatchStatus.uploading, UploadBatchStatus.processing):
            batch.status = UploadBatchStatus.failed
    job.finished_at = datetime.now(timezone.utc)
    await session.commit()
    return job


async def generate_mockups(ctx: dict[str, Any], run_id: str) -> str:
    """arq entrypoint."""
    from app.api.deps import get_storage

    async with ctx["sessionmaker"]() as session:
        done = await run(session, get_storage(), uuid.UUID(run_id))
        return done.status if done is not None else "missing"
