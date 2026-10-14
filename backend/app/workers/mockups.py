"""Mockups in the worker (pipeline/mockups.py): preparing templates, making a run's
mockups, turning them into listings, and building ZIPs to download.

* **Preparing a template**: finding the garment's outline and a default print area
  takes seconds on a large photo, so it runs here; the template shows "preparing"
  on the page until it is "ready" (or "failed", with the reason).
* **A run** prints every design of the batch on every template the seller chose,
  in the seller's template order. Each mockup is an output named
  "<SKU>_<template name>.jpg" (the design's file name when it has no SKU), unique
  in the run. Progress is saved after every mockup, per design.
* **Use for listings**: the chosen designs become listing groups (one per design,
  all its mockups as its photos, the cover the seller picked first). The photos
  read the outputs' files: nothing is copied.
* **Download**: a ZIP of the chosen designs (a folder per design, or flat), private
  to the account and deleted ``MockupZip.RETENTION_HOURS`` after it is ready.

No Etsy call and no model call is made here; nothing is counted against the
seller's "Listings generated" allowance.
"""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage_cap
from app.db.models import (
    Asset,
    AssetStatus,
    MockupDesign,
    MockupOutput,
    MockupRun,
    MockupTemplate,
    MockupZip,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.pipeline import mockups, upload_retention
from app.pipeline.images import ImageProcessingError
from app.pipeline.ingest import _rerank
from app.pipeline.storage import Storage

logger = logging.getLogger(__name__)

#: The most templates one run prints on.
MAX_TEMPLATES = 20
#: Said on the run when a design cannot be printed (its own words stay in the log).
DESIGN_FAILED = "Some designs could not be read as images."
#: Said on a template the worker could not prepare.
PREPARE_FAILED = "This photo could not be prepared. Try another file (JPEG or PNG)."


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- templates --------------------------------------------------------------------------------


async def prepare(session: AsyncSession, storage: Storage, template_id: uuid.UUID) -> MockupTemplate | None:
    """Find the template's outline and print area; "ready", or "failed" with the reason."""
    template = await session.get(MockupTemplate, template_id)
    if template is None or template.status != "preparing" or not template.source_key:
        return template
    try:
        source = storage.get(template.source_key)
        prepared = await asyncio.to_thread(mockups.prepare_blank, source)
    except (FileNotFoundError, ImageProcessingError, mockups.MockupError) as exc:
        logger.info("mockup template %s could not be prepared: %s", template.id, exc)
        template.status, template.error = "failed", PREPARE_FAILED
        await session.commit()
        return template
    base = f"{template.tenant_id}/templates/{template.id}"
    storage.put(f"{base}/blank.jpg", prepared.image, "image/jpeg")
    storage.put(f"{base}/mask.png", prepared.mask, "image/png")
    if prepared.occluder is not None:
        storage.put(f"{base}/occluder.png", prepared.occluder, "image/png")
    storage.delete_with_derivatives(template.source_key)
    storage_cap.forget(template.tenant_id)
    template.image_key, template.mask_key = f"{base}/blank.jpg", f"{base}/mask.png"
    template.occluder_key = f"{base}/occluder.png" if prepared.occluder is not None else None
    template.width, template.height, template.mask_source = prepared.width, prepared.height, prepared.mask_source
    template.print_area_confidence = prepared.confidence
    template.analysis = {
        "method": prepared.method, "note": prepared.note, "detected_area": prepared.print_area,
        "details": prepared.details,
    }
    if template.print_area is None:  # a set's area applied while it was preparing is kept
        template.print_area, template.print_area_source = prepared.print_area, "default"
    template.status, template.error, template.source_key = "ready", None, None
    template.version += 1
    await session.commit()
    return template


async def prepare_mockup_template(ctx: dict[str, Any], template_id: str) -> str:
    """arq entrypoint."""
    from app.api.deps import get_storage

    async with ctx["sessionmaker"]() as session:
        done = await prepare(session, get_storage(), uuid.UUID(template_id))
        return done.status if done is not None else "missing"


# --- runs -------------------------------------------------------------------------------------


async def _templates(session: AsyncSession, run: MockupRun) -> list[MockupTemplate]:
    found: list[MockupTemplate] = []
    for raw in run.template_ids:
        template = await session.get(MockupTemplate, uuid.UUID(str(raw)))
        if template is not None and template.tenant_id == run.tenant_id and template.status == "ready":
            found.append(template)
    return found


def design_base(design: MockupDesign) -> str:
    """What a design's mockups are named after: its SKU, else its file name."""
    return design.parsed_sku or PurePosixPath(design.original_filename.replace("\\", "/")).stem or "design"


async def replace_earlier(session: AsyncSession, storage: Storage, batch_id: uuid.UUID) -> int:
    """Remove what an earlier run made for this batch: its outputs and their files,
    the listing photos made from them, and mockups made as photos before outputs
    existed. Only while nothing is written for the batch (the API checks)."""
    assets = list((await session.execute(
        select(Asset).where(Asset.batch_id == batch_id, Asset.mockup_design_id.is_not(None))
    )).scalars())
    outputs = list((await session.execute(select(MockupOutput).where(MockupOutput.batch_id == batch_id))).scalars())
    keys = {a.processed_key for a in assets if a.processed_key} | {o.key for o in outputs if o.key}
    for key in keys:
        await asyncio.to_thread(storage.delete_with_derivatives, key)
    if assets:
        await session.execute(delete(Asset).where(Asset.id.in_([a.id for a in assets])))
    if outputs:
        await session.execute(delete(MockupOutput).where(MockupOutput.id.in_([o.id for o in outputs])))
    return len(keys)


async def run(session: AsyncSession, storage: Storage, run_id: uuid.UUID) -> MockupRun | None:
    """Make one run's mockups. Returns the run, or None when there is nothing to do."""
    job = await session.get(MockupRun, run_id)
    if job is None or job.status not in ("queued", "running"):
        return job
    job.status, job.started_at, job.done, job.failed, job.error = "running", _now(), 0, 0, None
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
    taken: set[str] = set()

    try:
        maps: dict[uuid.UUID, mockups.Maps] = {}
        for template in templates:
            blank, mask = storage.get(template.image_key or ""), storage.get(template.mask_key or "")
            occluder = storage.get(template.occluder_key) if template.occluder_key and template.occlusion else None
            maps[template.id] = await asyncio.to_thread(mockups.build_maps, blank, mask, occluder, template.print_area)
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
            base = design_base(design)
            for position, template in enumerate(templates):
                settings = mockups.Settings(template.print_area or [], template.size, template.fold_strength, template.occlusion)
                try:
                    jpeg = await asyncio.to_thread(mockups.render, maps[template.id], image, settings)
                except mockups.MockupError as exc:
                    job.failed += 1
                    job.error = f"{template.name}: {exc}"
                    await session.commit()
                    continue
                if cap is not None and in_use + len(jpeg) > cap:
                    raise storage_cap.StorageFull(
                        storage_cap.message(in_use, cap, len(jpeg), days.drafted_days, days.unpublished_days)
                    )
                output_id = uuid.uuid4()
                key = f"{job.tenant_id}/{job.batch_id}/mockups/{output_id}.jpg"
                storage.put(key, jpeg, "image/jpeg")
                storage_cap.added(job.tenant_id, len(jpeg))
                in_use += len(jpeg)
                session.add(MockupOutput(
                    id=output_id, tenant_id=job.tenant_id, run_id=job.id, batch_id=job.batch_id,
                    design_id=design.id, template_id=template.id, template_name=template.name, position=position,
                    filename=mockups.output_name(base, template.name, taken), key=key,
                    width=maps[template.id].width, height=maps[template.id].height, byte_size=len(jpeg),
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
    job.finished_at = _now()
    await session.commit()
    return job


async def generate_mockups(ctx: dict[str, Any], run_id: str) -> str:
    """arq entrypoint."""
    from app.api.deps import get_storage

    async with ctx["sessionmaker"]() as session:
        done = await run(session, get_storage(), uuid.UUID(run_id))
        return done.status if done is not None else "missing"


# --- use for listings -------------------------------------------------------------------------


async def outputs_by_design(session: AsyncSession, run_id: uuid.UUID) -> dict[uuid.UUID, list[MockupOutput]]:
    rows = (await session.execute(
        select(MockupOutput).where(MockupOutput.run_id == run_id).order_by(MockupOutput.position)
    )).scalars()
    found: dict[uuid.UUID, list[MockupOutput]] = {}
    for output in rows:
        found.setdefault(output.design_id, []).append(output)
    return found


async def make_listings(
    session: AsyncSession,
    job: MockupRun,
    design_ids: list[uuid.UUID] | None,
    covers: dict[uuid.UUID, uuid.UUID],
) -> tuple[int, int]:
    """The chosen designs (None: all) become listing groups: one per design, its
    mockups as its photos in the template order, the chosen cover first. Using a
    design again replaces its group's photos (another cover, say). Returns
    (groups, photos). The caller commits."""
    by_design = await outputs_by_design(session, job.id)
    wanted = set(design_ids) if design_ids is not None else set(by_design)
    designs = {d.id: d for d in (await session.execute(
        select(MockupDesign).where(MockupDesign.id.in_(wanted), MockupDesign.batch_id == job.batch_id)
    )).scalars()}
    groups = photos = 0
    for design_id, outputs in by_design.items():
        design = designs.get(design_id)
        usable = [o for o in outputs if o.key and o.files_removed_at is None]
        if design is None or not usable:
            continue
        cover = covers.get(design_id)
        ordered = sorted(usable, key=lambda o: (0 if o.template_id == cover else 1, o.position))
        await session.execute(delete(Asset).where(Asset.batch_id == job.batch_id, Asset.mockup_design_id == design_id))
        for rank, output in enumerate(ordered, start=1):
            session.add(Asset(
                batch_id=job.batch_id, tenant_id=job.tenant_id,
                original_filename=f"{design.group_key}/{output.filename}",
                parsed_sku=design.parsed_sku, group_key=design.group_key, upload_folder=design.group_key,
                processed_key=output.key, mime_type="image/jpeg", width=output.width, height=output.height,
                byte_size=output.byte_size, rank=rank, status=AssetStatus.processed,
                mockup_design_id=design.id, mockup_template_id=output.template_id,
            ))
            photos += 1
        groups += 1
    await session.flush()
    batch = await session.get(UploadBatch, job.batch_id)
    if batch is not None:
        assets = list((await session.execute(select(Asset).where(Asset.batch_id == batch.id))).scalars())
        _rerank(assets)
        batch.file_count = len(assets)
        batch.grouping_mode = batch.grouping_mode or "folder"
        if any(a.status is AssetStatus.processed for a in assets):
            batch.status = UploadBatchStatus.ready
    return groups, photos


# --- download ---------------------------------------------------------------------------------


async def build_zip(session: AsyncSession, storage: Storage, zip_id: uuid.UUID) -> MockupZip | None:
    """Write the ZIP (stored, not compressed: the JPEGs already are)."""
    archive = await session.get(MockupZip, zip_id)
    if archive is None or archive.status not in ("queued", "building"):
        return archive
    archive.status = "building"
    await session.commit()
    by_design = await outputs_by_design(session, archive.run_id)
    wanted = {uuid.UUID(str(i)) for i in archive.design_ids} if archive.design_ids is not None else set(by_design)
    designs = {d.id: d for d in (await session.execute(
        select(MockupDesign).where(MockupDesign.id.in_(wanted)))).scalars()}
    entries: list[tuple[str, str]] = []
    folders: set[str] = set()
    for design_id, outputs in by_design.items():
        design = designs.get(design_id)
        if design is None:
            continue
        folder = mockups.safe_name(design_base(design), 60)
        n, unique = 1, folder
        while unique.lower() in folders:
            n += 1
            unique = f"{folder} ({n})"
        folders.add(unique.lower())
        for output in outputs:
            if output.key and output.files_removed_at is None and storage.exists(output.key):
                entries.append((f"{unique}/{output.filename}" if archive.layout == "folders" else output.filename, output.key))
    tenant = await session.get(Tenant, archive.tenant_id)
    estimate = sum(len(storage.get(key)) for _, key in entries)  # a stored ZIP is its files plus headers
    if tenant is not None:
        cap = storage_cap.limit(tenant)
        in_use = await asyncio.to_thread(storage_cap.used, storage, archive.tenant_id)
        if in_use + estimate > cap:
            days = await upload_retention.policy(session)
            archive.status = "failed"
            archive.error = storage_cap.message(in_use, cap, estimate, days.drafted_days, days.unpublished_days)
            await session.commit()
            return archive
    if not entries:
        archive.status, archive.error = "failed", "There are no mockups to put in the ZIP."
        await session.commit()
        return archive

    def write() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
            for name, key in entries:
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))  # the same bytes every time
                zf.writestr(info, storage.get(key))
        return buf.getvalue()

    data = await asyncio.to_thread(write)
    key = f"{archive.tenant_id}/zips/{archive.id}.zip"
    storage.put(key, data, "application/zip")
    storage_cap.added(archive.tenant_id, len(data))
    archive.key, archive.byte_size, archive.files = key, len(data), len(entries)
    archive.status, archive.ready_at = "ready", _now()
    archive.expires_at = archive.ready_at + timedelta(hours=MockupZip.RETENTION_HOURS)
    await session.commit()
    return archive


async def build_mockup_zip(ctx: dict[str, Any], zip_id: str) -> str:
    """arq entrypoint."""
    from app.api.deps import get_storage

    async with ctx["sessionmaker"]() as session:
        done = await build_zip(session, get_storage(), uuid.UUID(zip_id))
        return done.status if done is not None else "missing"


async def expire_zips(session: AsyncSession, storage: Storage, now: datetime | None = None) -> int:
    """Delete every ZIP past its time (the file; the row says "expired")."""
    now = now or _now()
    rows = list((await session.execute(
        select(MockupZip).where(MockupZip.status == "ready", MockupZip.expires_at < now)
    )).scalars())
    for archive in rows:
        if archive.key:
            await asyncio.to_thread(storage.delete_with_derivatives, archive.key)
            storage_cap.forget(archive.tenant_id)
        archive.status, archive.key = "expired", None
    if rows:
        await session.commit()
    return len(rows)
