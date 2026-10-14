"""Mockup templates, the designs printed on them, and each run (pipeline/mockups.py).

Everything is per account: another account's template, batch or run is 404. No
Etsy call and no model call is made anywhere here. A template is the seller's own
blank photo, accepted only when they confirm they may use it; it is kept until
they delete it or their account. Mockups do not count towards "Listings generated".
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session, get_storage
from app.core import audit, storage_cap
from app.core.config import get_settings
from app.db.models import GeneratedContent, MockupDesign, MockupRun, MockupTemplate, Tenant, UploadBatch
from app.pipeline import grouping, mockup_sample, mockups, upload_retention
from app.pipeline.images import PREVIEW_WIDTHS, ImageProcessingError, resize_preview
from app.pipeline.ingest import UploadFile as IngestFile
from app.pipeline.ingest import _admit
from app.pipeline.sku import SkuParser
from app.pipeline.storage import Storage
from app.pipeline.uploads import UploadRejected
from app.workers.mockups import MAX_TEMPLATES

router = APIRouter(prefix="/api", tags=["mockups"])

#: What the seller confirms when uploading a blank (frontend lib/mockups.ts has the same words).
RIGHTS_STATEMENT = "This photo is my own, or I have the right to use it to show my products."
#: Templates per account: each keeps a photo and its mask on our disk.
MAX_TEMPLATES_PER_ACCOUNT = 20
#: The width the Templates page previews at (fast; the real mockups use the full photo).
PREVIEW_WIDTH = 900
#: Said when a template's background could not be separated from the garment.
NO_MASK = (
    "The background could not be told apart from the garment, so the whole photo counts as fabric. "
    "A photo on a plain background, or a PNG with a transparent background, works best."
)
#: Said when mockups are asked for after listings are written from the batch.
WRITTEN = "Mockups can be made only before any listing of this batch is written."
_CACHE = {"Cache-Control": "private, max-age=3600"}


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    position: int
    width: int
    height: int
    mask_source: str
    print_area: list[list[float]]
    print_area_source: str
    size: float
    fold_strength: float
    version: int
    notice: str | None = None


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    print_area: list[list[float]] | None = None
    #: Back to the area found from the garment's outline.
    reset_print_area: bool = False
    size: float | None = Field(default=None, ge=mockups.SIZE_RANGE[0], le=mockups.SIZE_RANGE[1])
    fold_strength: float | None = Field(default=None, ge=mockups.FOLD_RANGE[0], le=mockups.FOLD_RANGE[1])


class TemplateOrder(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=MAX_TEMPLATES_PER_ACCOUNT)


class PreviewRequest(BaseModel):
    """Unsaved settings to preview; a field left out is the template's own."""

    print_area: list[list[float]] | None = None
    size: float | None = Field(default=None, ge=mockups.SIZE_RANGE[0], le=mockups.SIZE_RANGE[1])
    fold_strength: float | None = Field(default=None, ge=mockups.FOLD_RANGE[0], le=mockups.FOLD_RANGE[1])


class DesignOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    group_key: str
    parsed_sku: str | None
    width: int
    height: int
    has_alpha: bool


class RunRequest(BaseModel):
    template_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_TEMPLATES)


class RunOut(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    status: str
    total: int
    done: int
    failed: int
    error: str | None = None
    templates: list[str] = Field(default_factory=list)


def _template_out(t: MockupTemplate) -> TemplateOut:
    return TemplateOut(
        id=t.id, name=t.name, position=t.position, width=t.width, height=t.height, mask_source=t.mask_source,
        print_area=t.print_area, print_area_source=t.print_area_source, size=t.size, fold_strength=t.fold_strength,
        version=t.version, notice=NO_MASK if t.mask_source == "none" else None,
    )


async def _own_template(session: AsyncSession, tenant: Tenant, template_id: uuid.UUID) -> MockupTemplate:
    template = await session.get(MockupTemplate, template_id)
    if template is None or template.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="template not found")
    return template


async def _own_batch(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID) -> UploadBatch:
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    return batch


async def _room(session: AsyncSession, storage: Storage, tenant: Tenant, incoming: int) -> None:
    """413 when storing ``incoming`` more bytes would pass the account's storage cap."""
    cap = storage_cap.limit(tenant)
    in_use = await asyncio.to_thread(storage_cap.used, storage, tenant.id)
    if in_use + incoming > cap:
        days = await upload_retention.policy(session)
        raise HTTPException(
            status_code=413,
            detail=storage_cap.message(in_use, cap, incoming, days.drafted_days, days.unpublished_days),
        )


def _admitted(filename: str, data: bytes) -> None:
    try:
        _admit(IngestFile(filename=filename, data=data))
    except UploadRejected as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


# --- templates ------------------------------------------------------------------------------


@router.get("/mockup-templates", response_model=list[TemplateOut])
async def list_templates(
    session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> list[TemplateOut]:
    rows = await session.execute(
        select(MockupTemplate).where(MockupTemplate.tenant_id == tenant.id)
        .order_by(MockupTemplate.position, MockupTemplate.created_at)
    )
    return [_template_out(t) for t in rows.scalars()]


@router.post("/mockup-templates", response_model=TemplateOut, status_code=201)
async def create_template(
    file: UploadFile,
    name: str = Form("", max_length=60),
    rights_confirmed: bool = Form(False),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> TemplateOut:
    """A blank garment photo becomes a template. The seller confirms they may use it."""
    if not rights_confirmed:
        raise HTTPException(status_code=422, detail=f"Please confirm: “{RIGHTS_STATEMENT}”")
    count = await session.scalar(select(func.count()).select_from(MockupTemplate).where(MockupTemplate.tenant_id == tenant.id))
    if int(count or 0) >= MAX_TEMPLATES_PER_ACCOUNT:
        raise HTTPException(status_code=409, detail=f"An account keeps up to {MAX_TEMPLATES_PER_ACCOUNT} templates; delete one first.")
    data = await file.read()
    _admitted(file.filename or "blank", data)
    try:
        prepared = await asyncio.to_thread(mockups.prepare_blank, data)
    except (ImageProcessingError, mockups.MockupError) as exc:
        raise HTTPException(status_code=422, detail="This file could not be read as a photo.") from exc
    await _room(session, storage, tenant, len(prepared.image) + len(prepared.mask))
    template_id = uuid.uuid4()
    base = f"{tenant.id}/templates/{template_id}"
    storage.put(f"{base}/blank.jpg", prepared.image, "image/jpeg")
    storage.put(f"{base}/mask.png", prepared.mask, "image/png")
    storage_cap.added(tenant.id, len(prepared.image) + len(prepared.mask))
    last = await session.scalar(select(func.max(MockupTemplate.position)).where(MockupTemplate.tenant_id == tenant.id))
    fallback = PurePosixPath(file.filename or "").stem.strip()[:60]
    template = MockupTemplate(
        id=template_id, tenant_id=tenant.id, name=name.strip() or fallback or "Blank", position=int(last or 0) + 1,
        image_key=f"{base}/blank.jpg", mask_key=f"{base}/mask.png", width=prepared.width, height=prepared.height,
        mask_source=prepared.mask_source, print_area=prepared.print_area, print_area_source="default",
        size=mockups.DEFAULT_SIZE, fold_strength=mockups.DEFAULT_FOLD, rights_confirmed_at=datetime.now(timezone.utc),
    )
    session.add(template)
    audit.record(session, "mockup_template.created", actor=tenant, target_tenant_id=tenant.id, by="seller",
                 object_id=str(template_id), rights_confirmed=True)
    await session.commit()
    return _template_out(template)


@router.patch("/mockup-templates/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: uuid.UUID,
    body: TemplateUpdate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> TemplateOut:
    template = await _own_template(session, tenant, template_id)
    if body.name is not None:
        template.name = body.name.strip() or template.name
    if body.reset_print_area:
        mask = await asyncio.to_thread(_mask_image, storage.get(template.mask_key))
        template.print_area, template.print_area_source = mockups.default_print_area(mask), "default"
    elif body.print_area is not None:
        try:
            template.print_area = mockups.clean_print_area(body.print_area)
        except mockups.MockupError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        template.print_area_source = "seller"
    if body.size is not None:
        template.size = round(body.size, 3)
    if body.fold_strength is not None:
        template.fold_strength = round(body.fold_strength, 3)
    template.version += 1
    await session.commit()
    return _template_out(template)


def _mask_image(mask_png: bytes):  # noqa: ANN202
    import pyvips

    return pyvips.Image.new_from_buffer(mask_png, "", access="random")[0]


@router.put("/mockup-templates/order", response_model=list[TemplateOut])
async def order_templates(
    body: TemplateOrder, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> list[TemplateOut]:
    """The seller's template order: a design's mockups follow it; the first is the cover."""
    rows = {t.id: t for t in (await session.execute(
        select(MockupTemplate).where(MockupTemplate.tenant_id == tenant.id))).scalars()}
    if set(body.ids) - set(rows):
        raise HTTPException(status_code=404, detail="template not found")
    rest = [t for t in sorted(rows.values(), key=lambda t: t.position) if t.id not in set(body.ids)]
    for position, template in enumerate([*(rows[i] for i in body.ids), *rest], start=1):
        template.position = position
    await session.commit()
    return await list_templates(session, tenant)


@router.delete("/mockup-templates/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    """The template and its photo go. Mockups already made from it stay in their batches."""
    template = await _own_template(session, tenant, template_id)
    storage.delete_prefix(f"{tenant.id}/templates/{template.id}")
    storage_cap.forget(tenant.id)
    audit.destructive(session, "mockup_template.deleted", actor=tenant, tenant_id=tenant.id, shop_id=None,
                      object_id=template.id)
    await session.delete(template)
    await session.commit()
    return Response(status_code=204)


@router.get("/mockup-templates/{template_id}/image")
async def template_image(
    template_id: uuid.UUID,
    w: int | None = Query(None, description=f"Optional preview width; one of {sorted(PREVIEW_WIDTHS)}."),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    template = await _own_template(session, tenant, template_id)
    if w is None:
        return Response(storage.get(template.image_key), media_type="image/jpeg", headers=_CACHE)
    if w not in PREVIEW_WIDTHS:
        raise HTTPException(status_code=400, detail=f"w must be one of {sorted(PREVIEW_WIDTHS)}")
    key = f"{template.image_key}.w{w}.jpg"
    if not storage.exists(key):
        storage.put(key, await asyncio.to_thread(resize_preview, storage.get(template.image_key), w), "image/jpeg")
    return Response(storage.get(key), media_type="image/jpeg", headers=_CACHE)


@router.post("/mockup-templates/{template_id}/preview")
async def preview_template(
    template_id: uuid.UUID,
    body: PreviewRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    """The template with a sample design, at preview size, with unsaved settings if sent."""
    template = await _own_template(session, tenant, template_id)
    try:
        corners = mockups.clean_print_area(body.print_area if body.print_area is not None else template.print_area)
    except mockups.MockupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    settings = mockups.Settings(
        corners,
        body.size if body.size is not None else template.size,
        body.fold_strength if body.fold_strength is not None else template.fold_strength,
    )
    blank, mask = storage.get(template.image_key), storage.get(template.mask_key)

    def draw() -> bytes:
        small = mockups.preview_scale((blank, mask), PREVIEW_WIDTH)
        maps = mockups.build_maps(*small)
        design = mockups.load_design(mockup_sample.sample(light_ink=maps.underbase >= 0.5))
        return mockups.render(maps, design, settings, quality=80)

    try:
        data = await asyncio.to_thread(draw)
    except mockups.MockupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# --- designs and runs -----------------------------------------------------------------------


def design_group(filename: str, parser: SkuParser, taken: set[str]) -> tuple[str, str | None]:
    """A design is its own listing: grouped by the SKU in its name, else by its name."""
    sku = grouping.sku_of(filename, parser)
    stem = PurePosixPath(filename.replace("\\", "/")).stem.strip()[:60] or "Design"
    base = sku or stem
    if base == grouping.UNSORTED:
        base = "Design"
    key, n = base, 1
    while key in taken:
        n += 1
        key = f"{base} {n}"
    return key, sku


@router.get("/batches/{batch_id}/designs", response_model=list[DesignOut])
async def list_designs(
    batch_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> list[DesignOut]:
    await _own_batch(session, tenant, batch_id)
    rows = await session.execute(
        select(MockupDesign).where(MockupDesign.batch_id == batch_id).order_by(MockupDesign.group_key)
    )
    return [_design_out(d) for d in rows.scalars()]


def _design_out(d: MockupDesign) -> DesignOut:
    return DesignOut(id=d.id, original_filename=d.original_filename, group_key=d.group_key, parsed_sku=d.parsed_sku,
                     width=d.width, height=d.height, has_alpha=d.has_alpha)


@router.post("/batches/{batch_id}/designs", response_model=DesignOut, status_code=201)
async def add_design(
    batch_id: uuid.UUID,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> DesignOut:
    """A design to print on templates; it becomes its own listing group."""
    batch = await _own_batch(session, tenant, batch_id)
    if await _written(session, batch_id):
        raise HTTPException(status_code=409, detail=WRITTEN)
    data = await file.read()
    filename = file.filename or "design.png"
    _admitted(filename, data)
    try:
        prepared = await asyncio.to_thread(mockups.prepare_design, data)
    except (ImageProcessingError, mockups.MockupError) as exc:
        raise HTTPException(status_code=422, detail="This file could not be read as a design.") from exc
    await _room(session, storage, tenant, len(prepared.image))
    taken = set((await session.execute(select(MockupDesign.group_key).where(MockupDesign.batch_id == batch_id))).scalars())
    group_key, sku = design_group(filename, SkuParser(), taken)
    design_id = uuid.uuid4()
    key = f"{tenant.id}/{batch_id}/designs/{design_id}.png"
    storage.put(key, prepared.image, "image/png")
    storage_cap.added(tenant.id, len(prepared.image))
    design = MockupDesign(
        id=design_id, tenant_id=tenant.id, batch_id=batch_id, group_key=group_key, original_filename=filename,
        parsed_sku=sku, design_key=key, width=prepared.width, height=prepared.height, has_alpha=prepared.has_alpha,
    )
    session.add(design)
    batch.grouping_mode = batch.grouping_mode or "folder"
    await session.commit()
    return _design_out(design)


async def _written(session: AsyncSession, batch_id: uuid.UUID) -> bool:
    return bool(await session.scalar(
        select(func.count()).select_from(GeneratedContent).where(GeneratedContent.batch_id == batch_id)
    ))


def _run_out(run: MockupRun, names: dict[str, str] | None = None) -> RunOut:
    return RunOut(id=run.id, batch_id=run.batch_id, status=run.status, total=run.total, done=run.done,
                  failed=run.failed, error=run.error,
                  templates=[(names or {}).get(str(t), "") for t in run.template_ids if (names or {}).get(str(t))])


def _active(run: MockupRun) -> bool:
    """Queued or running, and not left behind by a worker that stopped."""
    if run.status not in ("queued", "running"):
        return False
    since = run.started_at or run.created_at
    since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - since < timedelta(seconds=get_settings().worker_job_timeout + 120)


@router.post("/batches/{batch_id}/mockups", response_model=RunOut, status_code=202)
async def start_run(
    batch_id: uuid.UUID,
    body: RunRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> RunOut:
    """Print every design of the batch on the chosen templates, in the seller's template order."""
    await _own_batch(session, tenant, batch_id)
    templates = [await _own_template(session, tenant, t) for t in dict.fromkeys(body.template_ids)]
    templates.sort(key=lambda t: (t.position, t.created_at or datetime.min.replace(tzinfo=timezone.utc)))
    if await _written(session, batch_id):
        raise HTTPException(status_code=409, detail=WRITTEN)
    designs = await session.scalar(select(func.count()).select_from(MockupDesign).where(
        MockupDesign.batch_id == batch_id, MockupDesign.design_key.is_not(None)))
    if not designs:
        raise HTTPException(status_code=409, detail="Upload at least one design first.")
    runs = (await session.execute(select(MockupRun).where(MockupRun.batch_id == batch_id))).scalars()
    if any(_active(r) for r in runs):
        raise HTTPException(status_code=409, detail="Mockups for this batch are already being made.")
    run = MockupRun(tenant_id=tenant.id, batch_id=batch_id, template_ids=[str(t.id) for t in templates],
                    total=int(designs) * len(templates))
    session.add(run)
    await session.commit()
    await enqueuer.enqueue("generate_mockups", str(run.id), _job_id=f"mockups:{run.id}")
    return _run_out(run, {str(t.id): t.name for t in templates})


@router.get("/mockup-runs/{run_id}", response_model=RunOut)
async def get_run(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> RunOut:
    run = await session.get(MockupRun, run_id)
    if run is None or run.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="run not found")
    names: dict[str, Any] = {}
    for raw in run.template_ids:
        template = await session.get(MockupTemplate, uuid.UUID(str(raw)))
        if template is not None and template.tenant_id == tenant.id:
            names[str(template.id)] = template.name
    return _run_out(run, names)
