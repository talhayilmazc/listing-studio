"""Mockup templates, sets, the designs printed on them, runs, listings and ZIPs
(pipeline/mockups.py, workers/mockups.py).

Everything is per account: another account's template, set, batch, run, mockup or
ZIP is 404. No Etsy call and no model call is made anywhere here. A template is
the seller's own blank photo, accepted only when they confirm they may use it; it
is kept until they delete it or their account. Mockups do not count towards
"Listings generated".

A blank photo or a design is up to ``max_mockup_upload_bytes`` (30 MB) per file,
one file per request; the request only checks and resizes it. Finding a
template's outline runs in the worker: the template is returned at once,
"preparing".
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session, get_storage
from app.core import audit, storage_cap
from app.core.config import get_settings
from app.db.models import (
    Asset,
    GeneratedContent,
    MockupDesign,
    MockupOutput,
    MockupRun,
    MockupTemplate,
    MockupTemplateSet,
    MockupZip,
    Tenant,
    UploadBatch,
)
from app.pipeline import grouping, mockup_sample, mockups, skus, upload_retention
from app.pipeline import mockup_vision as vision
from app.pipeline.images import PREVIEW_WIDTHS, ImageProcessingError, resize_preview
from app.pipeline.sku import SkuParser
from app.pipeline.storage import Storage
from app.pipeline.uploads import UploadRejected, admit_image
from app.workers import mockups as work

router = APIRouter(prefix="/api", tags=["mockups"])

#: What the seller confirms when uploading a blank (frontend lib/mockups.ts has the same words).
RIGHTS_STATEMENT = "This photo is my own, or I have the right to use it to show my products."
#: Templates per account: each keeps a photo and its mask on our disk.
MAX_TEMPLATES_PER_ACCOUNT = 60
#: A template's or a set's name, trimmed; any language.
MAX_NAME = 120
#: The width the Templates page previews at (fast; the real mockups use the full photo).
PREVIEW_WIDTH = 900
#: Said when a template's background could not be separated from the garment.
NO_MASK = (
    "The background could not be told apart from the garment, so the whole photo counts as fabric. "
    "A photo on a plain background, or a PNG with a transparent background, works best."
)
#: Said when mockups are asked for after listings are written from the batch.
WRITTEN = "Mockups can be changed only before any listing of this batch is written."
NOT_READY = "This template is still being prepared; try again in a moment."
_CACHE = {"Cache-Control": "private, max-age=3600"}


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    position: int
    set_id: uuid.UUID | None = None
    status: str
    error: str | None = None
    width: int | None = None
    height: int | None = None
    mask_source: str | None = None
    print_area: list[list[float]] | None = None
    print_area_source: str
    size: float
    fold_strength: float
    version: int
    notice: str | None = None
    #: How the print area was found ("pose", "outline", "default"), how sure (0..1),
    #: what the seller is told, and whether they are asked to check it.
    method: str | None = None
    confidence: float | None = None
    analysis_note: str | None = None
    needs_check: bool = False
    #: Print behind hair, skin and accessories; whether any were found.
    occlusion: bool = True
    has_occluder: bool = False


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    print_area: list[list[float]] | None = None
    #: Back to the area found from the garment's outline.
    reset_print_area: bool = False
    size: float | None = Field(default=None, ge=mockups.SIZE_RANGE[0], le=mockups.SIZE_RANGE[1])
    fold_strength: float | None = Field(default=None, ge=mockups.FOLD_RANGE[0], le=mockups.FOLD_RANGE[1])
    #: Sent: the set it moves to; null takes it out of its set.
    set_id: uuid.UUID | None = None
    occlusion: bool | None = None


class TemplateOrder(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=MAX_TEMPLATES_PER_ACCOUNT)


class PreviewRequest(BaseModel):
    """Unsaved settings to preview; a field left out is the template's own."""

    print_area: list[list[float]] | None = None
    size: float | None = Field(default=None, ge=mockups.SIZE_RANGE[0], le=mockups.SIZE_RANGE[1])
    fold_strength: float | None = Field(default=None, ge=mockups.FOLD_RANGE[0], le=mockups.FOLD_RANGE[1])
    occlusion: bool | None = None


class SetIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)


class SetOut(BaseModel):
    id: uuid.UUID
    name: str
    position: int
    template_ids: list[uuid.UUID] = Field(default_factory=list)


class ApplyToSet(BaseModel):
    #: The template whose print area, size and fold strength every other one takes.
    template_id: uuid.UUID


class DesignOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    group_key: str
    parsed_sku: str | None
    width: int
    height: int
    has_alpha: bool


class DesignUpdate(BaseModel):
    #: The SKU the design's mockups and listing are named after; null = none (its file name).
    sku: str | None = Field(default=None, max_length=200)


class RunRequest(BaseModel):
    template_ids: list[uuid.UUID] = Field(min_length=1, max_length=work.MAX_TEMPLATES)


class OutputOut(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID | None
    template_name: str
    filename: str
    available: bool


class RunDesignOut(BaseModel):
    design_id: uuid.UUID
    original_filename: str
    sku: str | None
    group_key: str
    done: int
    total: int
    in_listings: bool
    outputs: list[OutputOut] = Field(default_factory=list)


class ZipOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID
    status: str
    layout: str
    files: int | None = None
    byte_size: int | None = None
    error: str | None = None
    expires_at: datetime | None = None


class RunOut(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    status: str
    total: int
    done: int
    failed: int
    error: str | None = None
    templates: list[str] = Field(default_factory=list)
    designs: list[RunDesignOut] = Field(default_factory=list)
    zips: list[ZipOut] = Field(default_factory=list)


class ListingsRequest(BaseModel):
    #: The designs to use; null = every design of the run.
    design_ids: list[uuid.UUID] | None = None
    #: The cover per design: a template id; a design left out keeps the first template.
    covers: dict[uuid.UUID, uuid.UUID] = Field(default_factory=dict)


class ListingsOut(BaseModel):
    groups: int
    photos: int


class ZipRequest(BaseModel):
    design_ids: list[uuid.UUID] | None = None
    layout: Literal["folders", "flat"] = "folders"


# --- helpers --------------------------------------------------------------------------------


def _template_out(t: MockupTemplate) -> TemplateOut:
    analysis = t.analysis or {}
    confidence = t.print_area_confidence
    # Asked to check only when the area is still the found one and it was not sure.
    needs_check = (
        t.status == "ready" and confidence is not None and confidence < vision.CHECK_BELOW
        and t.print_area_source != "seller"
    )
    return TemplateOut(
        id=t.id, name=t.name, position=t.position, set_id=t.set_id, status=t.status, error=t.error,
        width=t.width, height=t.height, mask_source=t.mask_source, print_area=t.print_area,
        print_area_source=t.print_area_source, size=t.size, fold_strength=t.fold_strength, version=t.version,
        notice=NO_MASK if t.mask_source == "none" else None, method=analysis.get("method"), confidence=confidence,
        analysis_note=analysis.get("note"), needs_check=needs_check, occlusion=t.occlusion,
        has_occluder=t.occluder_key is not None,
    )


def clean_name(value: str | None, fallback: str) -> str:
    """A name, trimmed and at most ``MAX_NAME`` characters; ``fallback`` when empty."""
    text = " ".join((value or "").split())[:MAX_NAME].strip()
    return text or fallback[:MAX_NAME] or "Blank"


async def _own_template(session: AsyncSession, tenant: Tenant, template_id: uuid.UUID) -> MockupTemplate:
    template = await session.get(MockupTemplate, template_id)
    if template is None or template.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="template not found")
    return template


def _ready(template: MockupTemplate) -> None:
    if template.status == "failed":
        raise HTTPException(status_code=409, detail=template.error or work.PREPARE_FAILED)
    if template.status != "ready":
        raise HTTPException(status_code=409, detail=NOT_READY)


async def _own_set(session: AsyncSession, tenant: Tenant, set_id: uuid.UUID) -> MockupTemplateSet:
    found = await session.get(MockupTemplateSet, set_id)
    if found is None or found.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="set not found")
    return found


async def _own_batch(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID) -> UploadBatch:
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    return batch


async def _own_run(session: AsyncSession, tenant: Tenant, run_id: uuid.UUID) -> MockupRun:
    run = await session.get(MockupRun, run_id)
    if run is None or run.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="run not found")
    return run


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


def _admitted(data: bytes) -> None:
    """The file is an image we accept, at most 30 MB (one file per request)."""
    settings = get_settings()
    limit = settings.max_mockup_upload_bytes
    if len(data) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"This file is {len(data) / (1024 * 1024):.1f} MB: the limit is {limit // (1024 * 1024)} MB per file.",
        )
    try:
        admit_image(data, max_bytes=limit, max_pixels=settings.max_image_pixels)
    except UploadRejected as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


async def _resized(data: bytes, what: str) -> mockups.Source:
    try:
        return await asyncio.to_thread(mockups.resize_upload, data)
    except (ImageProcessingError, mockups.MockupError) as exc:
        raise HTTPException(status_code=422, detail=f"This file could not be read as {what}.") from exc


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
    name: str = Form("", max_length=1000),
    rights_confirmed: bool = Form(False),
    set_id: uuid.UUID | None = Form(None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> TemplateOut:
    """A blank garment photo becomes a template, at once, "preparing"; the worker
    finds its outline and print area. The seller confirms they may use the photo."""
    if not rights_confirmed:
        raise HTTPException(status_code=422, detail=f"Please confirm: “{RIGHTS_STATEMENT}”")
    if set_id is not None:
        await _own_set(session, tenant, set_id)
    count = await session.scalar(select(func.count()).select_from(MockupTemplate).where(MockupTemplate.tenant_id == tenant.id))
    if int(count or 0) >= MAX_TEMPLATES_PER_ACCOUNT:
        raise HTTPException(status_code=409, detail=f"An account keeps up to {MAX_TEMPLATES_PER_ACCOUNT} templates; delete one first.")
    data = await file.read()
    _admitted(data)
    source = await _resized(data, "a photo")
    await _room(session, storage, tenant, len(source.image))
    template_id = uuid.uuid4()
    key = f"{tenant.id}/templates/{template_id}/source{'.png' if source.has_alpha else '.jpg'}"
    storage.put(key, source.image, "image/png" if source.has_alpha else "image/jpeg")
    storage_cap.added(tenant.id, len(source.image))
    last = await session.scalar(select(func.max(MockupTemplate.position)).where(MockupTemplate.tenant_id == tenant.id))
    template = MockupTemplate(
        id=template_id, tenant_id=tenant.id, set_id=set_id,
        name=clean_name(name, PurePosixPath((file.filename or "").replace("\\", "/")).stem),
        position=int(last or 0) + 1, status="preparing", source_key=key, print_area_source="default",
        size=mockups.DEFAULT_SIZE, fold_strength=mockups.DEFAULT_FOLD, rights_confirmed_at=datetime.now(timezone.utc),
    )
    session.add(template)
    audit.record(session, "mockup_template.created", actor=tenant, target_tenant_id=tenant.id, by="seller",
                 object_id=str(template_id), rights_confirmed=True)
    await session.commit()
    await enqueuer.enqueue("prepare_mockup_template", str(template_id), _job_id=f"mockup-template:{template_id}")
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
        template.name = clean_name(body.name, template.name)
    if "set_id" in body.model_fields_set:
        if body.set_id is not None:
            await _own_set(session, tenant, body.set_id)
        template.set_id = body.set_id
    if body.occlusion is not None:
        template.occlusion = body.occlusion
    if body.reset_print_area:
        _ready(template)
        found = (template.analysis or {}).get("detected_area")
        if found:  # back to what the analysis found
            template.print_area = [list(c) for c in found]
        else:
            mask = await asyncio.to_thread(_mask_image, storage.get(template.mask_key or ""))
            template.print_area = mockups.default_print_area(mask)
        template.print_area_source = "default"
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
    """The template and its photo go. Mockups already made from it stay."""
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
    _ready(template)
    if w is None:
        return Response(storage.get(template.image_key or ""), media_type="image/jpeg", headers=_CACHE)
    if w not in PREVIEW_WIDTHS:
        raise HTTPException(status_code=400, detail=f"w must be one of {sorted(PREVIEW_WIDTHS)}")
    key = f"{template.image_key}.w{w}.jpg"
    if not storage.exists(key):
        storage.put(key, await asyncio.to_thread(resize_preview, storage.get(template.image_key or ""), w), "image/jpeg")
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
    _ready(template)
    try:
        corners = mockups.clean_print_area(body.print_area if body.print_area is not None else template.print_area)
    except mockups.MockupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    occlusion = body.occlusion if body.occlusion is not None else template.occlusion
    settings = mockups.Settings(
        corners,
        body.size if body.size is not None else template.size,
        body.fold_strength if body.fold_strength is not None else template.fold_strength,
        occlusion,
    )
    blank, mask = storage.get(template.image_key or ""), storage.get(template.mask_key or "")
    occluder = storage.get(template.occluder_key) if template.occluder_key and occlusion else None

    def draw() -> bytes:
        small = mockups.preview_scale(blank, mask, occluder, PREVIEW_WIDTH)
        maps = mockups.build_maps(*small, corners)
        design = mockups.load_design(mockup_sample.sample(light_ink=maps.underbase >= 0.5))
        return mockups.render(maps, design, settings, quality=80)

    try:
        data = await asyncio.to_thread(draw)
    except mockups.MockupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# --- sets -----------------------------------------------------------------------------------


async def _sets_out(session: AsyncSession, tenant: Tenant) -> list[SetOut]:
    sets = list((await session.execute(
        select(MockupTemplateSet).where(MockupTemplateSet.tenant_id == tenant.id)
        .order_by(MockupTemplateSet.position, MockupTemplateSet.created_at)
    )).scalars())
    members: dict[uuid.UUID, list[uuid.UUID]] = {s.id: [] for s in sets}
    for template in (await session.execute(
        select(MockupTemplate).where(MockupTemplate.tenant_id == tenant.id, MockupTemplate.set_id.is_not(None))
        .order_by(MockupTemplate.position, MockupTemplate.created_at)
    )).scalars():
        members.setdefault(template.set_id, []).append(template.id)
    return [SetOut(id=s.id, name=s.name, position=s.position, template_ids=members.get(s.id, [])) for s in sets]


@router.get("/mockup-sets", response_model=list[SetOut])
async def list_sets(session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)) -> list[SetOut]:
    return await _sets_out(session, tenant)


@router.post("/mockup-sets", response_model=SetOut, status_code=201)
async def create_set(
    body: SetIn, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> SetOut:
    """A set (one garment in several colours, say): its templates are uploaded into it."""
    last = await session.scalar(select(func.max(MockupTemplateSet.position)).where(MockupTemplateSet.tenant_id == tenant.id))
    found = MockupTemplateSet(tenant_id=tenant.id, name=clean_name(body.name, "Set"), position=int(last or 0) + 1)
    session.add(found)
    await session.commit()
    return SetOut(id=found.id, name=found.name, position=found.position)


@router.patch("/mockup-sets/{set_id}", response_model=SetOut)
async def rename_set(
    set_id: uuid.UUID, body: SetIn, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> SetOut:
    found = await _own_set(session, tenant, set_id)
    found.name = clean_name(body.name, found.name)
    await session.commit()
    return next(s for s in await _sets_out(session, tenant) if s.id == set_id)


@router.delete("/mockup-sets/{set_id}", status_code=204)
async def delete_set(
    set_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> Response:
    """The set goes; its templates stay, in no set."""
    found = await _own_set(session, tenant, set_id)
    audit.destructive(session, "mockup_set.deleted", actor=tenant, tenant_id=tenant.id, shop_id=None, object_id=found.id)
    for template in (await session.execute(select(MockupTemplate).where(MockupTemplate.set_id == set_id))).scalars():
        template.set_id = None
    await session.delete(found)
    await session.commit()
    return Response(status_code=204)


@router.post("/mockup-sets/{set_id}/apply", response_model=list[TemplateOut])
async def apply_to_set(
    set_id: uuid.UUID, body: ApplyToSet, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[TemplateOut]:
    """One template's print area (as shares of the photo), size and fold strength,
    given to every template of its set: the same garment in other colours, shot
    the same way. A template still preparing keeps it when it is ready."""
    await _own_set(session, tenant, set_id)
    source = await _own_template(session, tenant, body.template_id)
    if source.set_id != set_id:
        raise HTTPException(status_code=422, detail="That template is not in this set.")
    _ready(source)
    members = list((await session.execute(
        select(MockupTemplate).where(MockupTemplate.set_id == set_id, MockupTemplate.tenant_id == tenant.id)
        .order_by(MockupTemplate.position)
    )).scalars())
    for template in members:
        if template.id == source.id:
            continue
        template.print_area = [list(c) for c in (source.print_area or [])]
        template.print_area_source = "seller"
        template.size, template.fold_strength = source.size, source.fold_strength
        template.version += 1
    await session.commit()
    return [_template_out(t) for t in members]


# --- designs --------------------------------------------------------------------------------


def design_group(filename: str, sku: str | None, taken: set[str]) -> str:
    """A design is its own listing: its group is its SKU, else its file name."""
    stem = PurePosixPath(filename.replace("\\", "/")).stem.strip()[:60] or "Design"
    base = sku or stem
    if base == grouping.UNSORTED:
        base = "Design"
    key, n = base, 1
    while key in taken:
        n += 1
        key = f"{base} {n}"
    return key


def _design_out(d: MockupDesign) -> DesignOut:
    return DesignOut(id=d.id, original_filename=d.original_filename, group_key=d.group_key, parsed_sku=d.parsed_sku,
                     width=d.width, height=d.height, has_alpha=d.has_alpha)


async def _written(session: AsyncSession, batch_id: uuid.UUID) -> bool:
    return bool(await session.scalar(
        select(func.count()).select_from(GeneratedContent).where(GeneratedContent.batch_id == batch_id)
    ))


@router.get("/batches/{batch_id}/designs", response_model=list[DesignOut])
async def list_designs(
    batch_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> list[DesignOut]:
    await _own_batch(session, tenant, batch_id)
    rows = await session.execute(
        select(MockupDesign).where(MockupDesign.batch_id == batch_id).order_by(MockupDesign.group_key)
    )
    return [_design_out(d) for d in rows.scalars()]


@router.post("/batches/{batch_id}/designs", response_model=DesignOut, status_code=201)
async def add_design(
    batch_id: uuid.UUID,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> DesignOut:
    """A design to print on templates; it becomes its own listing group. Its SKU is
    read from its file name (pipeline/grouping.py) and can be changed before the
    mockups are made."""
    batch = await _own_batch(session, tenant, batch_id)
    if await _written(session, batch_id):
        raise HTTPException(status_code=409, detail=WRITTEN)
    data = await file.read()
    filename = file.filename or "design.png"
    _admitted(data)
    try:
        prepared = await asyncio.to_thread(mockups.prepare_design, data)
    except (ImageProcessingError, mockups.MockupError) as exc:
        raise HTTPException(status_code=422, detail="This file could not be read as a design.") from exc
    await _room(session, storage, tenant, len(prepared.image))
    taken = set((await session.execute(select(MockupDesign.group_key).where(MockupDesign.batch_id == batch_id))).scalars())
    sku = grouping.sku_of(filename, SkuParser())
    design_id = uuid.uuid4()
    key = f"{tenant.id}/{batch_id}/designs/{design_id}.png"
    storage.put(key, prepared.image, "image/png")
    storage_cap.added(tenant.id, len(prepared.image))
    design = MockupDesign(
        id=design_id, tenant_id=tenant.id, batch_id=batch_id, group_key=design_group(filename, sku, taken),
        original_filename=filename, parsed_sku=sku, design_key=key, width=prepared.width, height=prepared.height,
        has_alpha=prepared.has_alpha,
    )
    session.add(design)
    batch.grouping_mode = batch.grouping_mode or "folder"
    await session.commit()
    return _design_out(design)


@router.patch("/mockup-designs/{design_id}", response_model=DesignOut)
async def update_design(
    design_id: uuid.UUID, body: DesignUpdate, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> DesignOut:
    """The design's SKU (Etsy's SKU rules, pipeline/skus.py); its group follows it.
    Only before anything is written from the batch."""
    design = await session.get(MockupDesign, design_id)
    if design is None or design.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="design not found")
    if await _written(session, design.batch_id):
        raise HTTPException(status_code=409, detail=WRITTEN)
    sku = None
    if body.sku is not None and body.sku.strip():
        try:
            sku = skus.validate(body.sku)
        except skus.SkuInvalid as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    taken = set((await session.execute(
        select(MockupDesign.group_key).where(MockupDesign.batch_id == design.batch_id, MockupDesign.id != design.id)
    )).scalars())
    design.parsed_sku = sku
    design.group_key = design_group(design.original_filename, sku, taken)
    await session.commit()
    return _design_out(design)


# --- runs -----------------------------------------------------------------------------------


def _active(run: MockupRun) -> bool:
    """Queued or running, and not left behind by a worker that stopped."""
    if run.status not in ("queued", "running"):
        return False
    since = run.started_at or run.created_at
    since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - since < timedelta(seconds=get_settings().worker_job_timeout + 120)


def _zip_out(z: MockupZip) -> ZipOut:
    return ZipOut(id=z.id, run_id=z.run_id, status=z.status, layout=z.layout, files=z.files, byte_size=z.byte_size,
                  error=z.error, expires_at=z.expires_at)


async def _run_out(session: AsyncSession, run: MockupRun) -> RunOut:
    names: list[str] = []
    for raw in run.template_ids:
        template = await session.get(MockupTemplate, uuid.UUID(str(raw)))
        if template is not None and template.tenant_id == run.tenant_id:
            names.append(template.name)
    designs = list((await session.execute(
        select(MockupDesign).where(MockupDesign.batch_id == run.batch_id).order_by(MockupDesign.group_key)
    )).scalars())
    outputs = await work.outputs_by_design(session, run.id)
    # Designs already used for listings: their group has the mockups as photos.
    used = set((await session.execute(
        select(Asset.mockup_design_id).where(Asset.batch_id == run.batch_id, Asset.mockup_design_id.is_not(None))
    )).scalars())
    per_design = len(run.template_ids)
    zips = (await session.execute(select(MockupZip).where(MockupZip.run_id == run.id).order_by(MockupZip.created_at))).scalars()
    return RunOut(
        id=run.id, batch_id=run.batch_id, status=run.status, total=run.total, done=run.done, failed=run.failed,
        error=run.error, templates=names,
        designs=[
            RunDesignOut(
                design_id=d.id, original_filename=d.original_filename, sku=d.parsed_sku, group_key=d.group_key,
                done=len(outputs.get(d.id, [])), total=per_design, in_listings=d.id in used,
                outputs=[
                    OutputOut(id=o.id, template_id=o.template_id, template_name=o.template_name, filename=o.filename,
                              available=bool(o.key) and o.files_removed_at is None)
                    for o in outputs.get(d.id, [])
                ],
            )
            for d in designs
        ],
        zips=[_zip_out(z) for z in zips],
    )


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
    for template in templates:
        if template.status != "ready":
            raise HTTPException(status_code=409, detail=f"“{template.name}” is not ready yet; wait until every chosen template is ready.")
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
    return await _run_out(session, run)


@router.get("/mockup-runs/{run_id}", response_model=RunOut)
async def get_run(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> RunOut:
    return await _run_out(session, await _own_run(session, tenant, run_id))


@router.get("/mockup-runs/{run_id}/outputs/{output_id}/image")
async def output_image(
    run_id: uuid.UUID,
    output_id: uuid.UUID,
    w: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    """A mockup, or a small preview of it (to pick a cover)."""
    run = await _own_run(session, tenant, run_id)
    output = await session.get(MockupOutput, output_id)
    if output is None or output.run_id != run.id or output.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="mockup not found")
    if not output.key:
        raise HTTPException(status_code=410, detail="this mockup's file has been removed")
    if w is None:
        return Response(storage.get(output.key), media_type="image/jpeg", headers=_CACHE)
    if w not in PREVIEW_WIDTHS:
        raise HTTPException(status_code=400, detail=f"w must be one of {sorted(PREVIEW_WIDTHS)}")
    key = f"{output.key}.w{w}.jpg"
    if not storage.exists(key):
        storage.put(key, await asyncio.to_thread(resize_preview, storage.get(output.key), w), "image/jpeg")
    return Response(storage.get(key), media_type="image/jpeg", headers=_CACHE)


@router.post("/mockup-runs/{run_id}/listings", response_model=ListingsOut)
async def use_for_listings(
    run_id: uuid.UUID, body: ListingsRequest, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> ListingsOut:
    """"Use for listings": each chosen design becomes a listing group, all its
    mockups as its photos, the chosen cover first. Using it again changes them."""
    run = await _own_run(session, tenant, run_id)
    if run.status in ("queued", "running"):
        raise HTTPException(status_code=409, detail="The mockups are still being made.")
    if await _written(session, run.batch_id):
        raise HTTPException(status_code=409, detail=WRITTEN)
    templates = {uuid.UUID(str(t)) for t in run.template_ids}
    if any(t not in templates for t in body.covers.values()):
        raise HTTPException(status_code=422, detail="A cover must be one of this run's templates.")
    groups, photos = await work.make_listings(session, run, body.design_ids, body.covers)
    await session.commit()
    return ListingsOut(groups=groups, photos=photos)


@router.post("/mockup-runs/{run_id}/zip", response_model=ZipOut, status_code=202)
async def request_zip(
    run_id: uuid.UUID, body: ZipRequest, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant), enqueuer: Enqueuer = Depends(get_enqueuer),
) -> ZipOut:
    """"Download": a ZIP built in the worker, private to the account, deleted 24 hours after it is ready."""
    run = await _own_run(session, tenant, run_id)
    if run.status in ("queued", "running"):
        raise HTTPException(status_code=409, detail="The mockups are still being made.")
    archive = MockupZip(tenant_id=tenant.id, run_id=run.id, layout=body.layout,
                        design_ids=[str(d) for d in body.design_ids] if body.design_ids is not None else None)
    session.add(archive)
    await session.commit()
    await enqueuer.enqueue("build_mockup_zip", str(archive.id), _job_id=f"mockup-zip:{archive.id}")
    return _zip_out(archive)


async def _own_zip(session: AsyncSession, tenant: Tenant, zip_id: uuid.UUID) -> MockupZip:
    archive = await session.get(MockupZip, zip_id)
    if archive is None or archive.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="download not found")
    return archive


@router.get("/mockup-zips/{zip_id}", response_model=ZipOut)
async def get_zip(
    zip_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> ZipOut:
    return _zip_out(await _own_zip(session, tenant, zip_id))


@router.get("/mockup-zips/{zip_id}/download")
async def download_zip(
    zip_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    archive = await _own_zip(session, tenant, zip_id)
    expires = archive.expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if archive.status == "expired" or (expires is not None and expires < datetime.now(timezone.utc)):
        raise HTTPException(status_code=410, detail="This download has expired; make a new one.")
    if archive.status != "ready" or not archive.key:
        raise HTTPException(status_code=409, detail="This download is not ready yet.")
    try:
        data = storage.get(archive.key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="This download has expired; make a new one.") from exc
    stamp = (archive.ready_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="mockups-{stamp}.zip"', "Cache-Control": "private, no-store",
    })

