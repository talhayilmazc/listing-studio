"""Mockup templates and their photos, the designs printed on them, runs, listings and
ZIPs (pipeline/mockups.py, workers/mockups.py).

A **template** is a named group of the seller's blank photos (the same tee in ten
colours, or several shots of one product); every design of a run is placed on every
confirmed photo of each chosen template. A **photo** is accepted only when the seller
confirms they may use it; the worker suggests its print areas, and the photo is used
for mockups only after the seller has looked at them and pressed "Looks right"
(``confirmed_at``). Both are kept until the seller deletes them or their account.

Everything is per account: another account's template, photo, batch, run, mockup or
ZIP is 404. No Etsy call and no model call is made anywhere here ("Add area" grows a
garment from the seller's tap in the foreground map kept with the photo, without a
model). Mockups do not count towards "Listings generated".

A blank photo or a design is up to ``max_mockup_upload_bytes`` (30 MB) per file, one
file per request; the request only checks and resizes it. Finding a photo's outline
runs in the worker: the photo is returned at once, "preparing".
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, Literal

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
    MockupPhoto,
    MockupRun,
    MockupTemplate,
    MockupZip,
    Tenant,
    UploadBatch,
)
from app.pipeline import grouping, mockup_sample, mockups, skus, upload_retention
from app.pipeline.images import PREVIEW_WIDTHS, ImageProcessingError, resize_preview
from app.pipeline.sku import SkuParser
from app.pipeline.storage import Storage
from app.pipeline.uploads import UploadRejected, admit_image
from app.workers import mockups as work

router = APIRouter(prefix="/api", tags=["mockups"])

#: What the seller confirms when uploading a blank (frontend lib/mockups.ts has the same words).
RIGHTS_STATEMENT = "This photo is my own, or I have the right to use it to show my products."
MAX_TEMPLATES_PER_ACCOUNT = MockupTemplate.MAX_PER_ACCOUNT
MAX_PHOTOS_PER_ACCOUNT = MockupPhoto.MAX_PER_ACCOUNT
MAX_PHOTOS_PER_TEMPLATE = MockupPhoto.MAX_PER_TEMPLATE
#: The most print areas one photo can hold (one per garment).
MAX_GARMENTS = 24
#: A template's or a photo's name, trimmed; any language.
MAX_NAME = 120
#: The "Keep in front" brush as sent by the editor (a gray PNG at screen size).
MAX_BRUSH_BYTES = 900 * 1024
#: The width the editor previews at (fast; the real mockups use the full photo).
PREVIEW_WIDTH = 900
#: Said when a photo's background could not be separated from the garment.
NO_MASK = (
    "The background could not be told apart from the garment, so the whole photo counts as fabric. "
    "A photo on a plain background, or a PNG with a transparent background, works best."
)
#: Said when mockups are asked for after listings are written from the batch.
WRITTEN = "Mockups can be changed only before any listing of this batch is written."
NOT_READY = "This photo is still being prepared; try again in a moment."
#: Said when no chosen template has a photo the seller has confirmed.
NONE_CONFIRMED = (
    "None of the chosen templates has a confirmed photo yet. Open the template and press "
    "“Looks right” on each photo's print area first."
)
#: Said by "Add area" when nothing garment-shaped is under the tap: a box is placed there instead.
TAP_MISSED = "No garment was found where you tapped, so a box was placed there: drag it onto the print area."
_CACHE = {"Cache-Control": "private, max-age=3600"}
Quad = list[list[float]]
Source = Literal["default", "tap", "seller", "copied"]


class AreaOut(BaseModel):
    print_area: Quad
    #: The suggestion for this area (found, or from the seller's tap): "Reset to
    #: suggestion" and the editor's guides use it.
    detected_area: Quad
    #: "default" (suggested), "tap" (found from the seller's tap), "seller" (moved by
    #: hand) or "copied" (from another photo of the template).
    source: str = "default"
    confidence: float | None = None
    kind: str | None = None


class PhotoOut(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    name: str
    position: int
    status: str
    error: str | None = None
    width: int | None = None
    height: int | None = None
    mask_source: str | None = None
    size: float
    fold_strength: float
    version: int
    notice: str | None = None
    #: How the suggestion was found ("pose", "outline", "default") and what the seller is told.
    method: str | None = None
    confidence: float | None = None
    analysis_note: str | None = None
    #: Pressed "Looks right"; until then the photo is not used for mockups ("Needs your check").
    confirmed: bool = False
    needs_check: bool = False
    #: Print behind hair, skin, hands and held things; whether any were found.
    occlusion: bool = True
    has_occluder: bool = False
    #: The seller painted "Keep in front" on this photo.
    has_front_brush: bool = False
    #: "front" or "back" (found from the pose; the seller can change it).
    view: str = "front"
    #: Every print area, in the seller's order.
    areas: list[AreaOut] = Field(default_factory=list)


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    position: int
    photos: list[PhotoOut] = Field(default_factory=list)
    #: Ready photos not confirmed yet (shown as "Needs your check").
    needs_check: int = 0
    confirmed: int = 0


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)


class Order(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=MAX_PHOTOS_PER_ACCOUNT)


class PhotoUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    size: float | None = Field(default=None, ge=mockups.SIZE_RANGE[0], le=mockups.SIZE_RANGE[1])
    fold_strength: float | None = Field(default=None, ge=mockups.FOLD_RANGE[0], le=mockups.FOLD_RANGE[1])
    occlusion: bool | None = None
    view: Literal["front", "back"] | None = None
    #: Move the photo to another of the seller's templates (at its end).
    template_id: uuid.UUID | None = None


class AreaIn(BaseModel):
    print_area: Quad
    detected_area: Quad | None = None
    source: Source = "seller"
    kind: str | None = Field(default=None, max_length=40)


class AreasUpdate(BaseModel):
    """The photo's print areas from the editor, in the seller's order; ``confirm``: "Looks right"."""

    areas: list[AreaIn] = Field(min_length=1, max_length=MAX_GARMENTS)
    confirm: bool = False
    size: float | None = Field(default=None, ge=mockups.SIZE_RANGE[0], le=mockups.SIZE_RANGE[1])


class FindRequest(BaseModel):
    """"Add area": where the seller tapped (shares of the photo) and the areas already there."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    areas: list[Quad] = Field(default_factory=list, max_length=MAX_GARMENTS)


class FindOut(BaseModel):
    found: bool
    print_area: Quad
    confidence: float | None = None
    kind: str | None = None
    note: str | None = None


class CopyRequest(BaseModel):
    #: Other photos of the same template that take this photo's print areas, size and fold strength.
    photo_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_PHOTOS_PER_TEMPLATE)
    #: Place each area by the target photo's own garment ("Apply this area to selected"):
    #: where it sits on the source's garment, it is put on the target's. False: the same
    #: shares of the photo.
    fit: bool = True


class ConfirmRequest(BaseModel):
    #: Photos of this template to confirm as they are ("Confirm selected").
    photo_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_PHOTOS_PER_TEMPLATE)


class DesignFailed(BaseModel):
    filename: str
    reason: str


class DesignArchiveOut(BaseModel):
    added: list["DesignOut"] = Field(default_factory=list)
    failed: list[DesignFailed] = Field(default_factory=list)


class PreviewRequest(BaseModel):
    """Unsaved settings to preview; a field left out is the photo's own."""

    areas: list[Quad] | None = Field(default=None, max_length=MAX_GARMENTS)
    size: float | None = Field(default=None, ge=mockups.SIZE_RANGE[0], le=mockups.SIZE_RANGE[1])
    fold_strength: float | None = Field(default=None, ge=mockups.FOLD_RANGE[0], le=mockups.FOLD_RANGE[1])
    occlusion: bool | None = None


class DesignOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    group_key: str
    parsed_sku: str | None
    width: int
    height: int
    has_alpha: bool


DesignArchiveOut.model_rebuild()


class DesignUpdate(BaseModel):
    #: The SKU the design's mockups and listing are named after; null = none (its file name).
    sku: str | None = Field(default=None, max_length=200)


class RunRequest(BaseModel):
    template_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_TEMPLATES_PER_ACCOUNT)
    #: Print on the chosen templates' front photos, back photos or both.
    sides: Literal["front", "back", "both"] = "both"
    #: A photo with several garments: the same design on each ("same"), or one design
    #: per garment in order ("order").
    garment_mode: Literal["same", "order"] = "same"


class OutputOut(BaseModel):
    id: uuid.UUID
    photo_id: uuid.UUID | None
    template_name: str
    photo_name: str | None = None
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
    photos: int = 0
    #: Photos of the chosen templates left out because they are not confirmed yet, and their names.
    skipped: int = 0
    skipped_photos: list[str] = Field(default_factory=list)
    designs: list[RunDesignOut] = Field(default_factory=list)
    zips: list[ZipOut] = Field(default_factory=list)


class ListingsRequest(BaseModel):
    #: The designs to use; null = every design of the run.
    design_ids: list[uuid.UUID] | None = None
    #: The cover per design: a photo id of the run; a design left out keeps the first photo.
    covers: dict[uuid.UUID, uuid.UUID] = Field(default_factory=dict)


class ListingsOut(BaseModel):
    groups: int
    photos: int


class ZipRequest(BaseModel):
    design_ids: list[uuid.UUID] | None = None
    layout: Literal["folders", "flat"] = "folders"


# --- helpers --------------------------------------------------------------------------------


def photo_areas(photo: MockupPhoto) -> list[dict[str, Any]]:
    """The photo's print areas (rows of ``garments``), the first mirroring ``print_area``."""
    if photo.status != "ready" or not photo.print_area:
        return []
    rows = [dict(g) for g in (photo.garments or []) if g.get("print_area")]
    if not rows:
        found = (photo.analysis or {}).get("detected_area") or photo.print_area
        rows = [{"print_area": photo.print_area, "detected_area": found, "source": photo.print_area_source,
                 "confidence": photo.print_area_confidence}]
    return rows


def _photo_out(p: MockupPhoto) -> PhotoOut:
    analysis = p.analysis or {}
    ready = p.status == "ready"
    return PhotoOut(
        id=p.id, template_id=p.template_id, name=p.name, position=p.position, status=p.status, error=p.error,
        width=p.width, height=p.height, mask_source=p.mask_source, size=p.size, fold_strength=p.fold_strength,
        version=p.version, notice=NO_MASK if p.mask_source == "none" else None, method=analysis.get("method"),
        confidence=p.print_area_confidence, analysis_note=analysis.get("note"),
        confirmed=p.confirmed_at is not None, needs_check=ready and p.confirmed_at is None,
        occlusion=p.occlusion, has_occluder=p.occluder_key is not None, has_front_brush=p.occluder_manual_key is not None,
        view=p.view or "front",
        areas=[
            AreaOut(print_area=g["print_area"], detected_area=g.get("detected_area") or g["print_area"],
                    source=g.get("source") or "default", confidence=g.get("confidence"), kind=g.get("kind"))
            for g in photo_areas(p)
        ],
    )


def _template_out(t: MockupTemplate, photos: list[MockupPhoto]) -> TemplateOut:
    out = [_photo_out(p) for p in photos]
    return TemplateOut(id=t.id, name=t.name, position=t.position, photos=out,
                       needs_check=sum(p.needs_check for p in out), confirmed=sum(p.confirmed for p in out))


def clean_name(value: str | None, fallback: str) -> str:
    """A name, trimmed and at most ``MAX_NAME`` characters; ``fallback`` when empty."""
    text = " ".join((value or "").split())[:MAX_NAME].strip()
    return text or fallback[:MAX_NAME] or "Blank"


def _clean(area: Any) -> Quad:
    try:
        return mockups.clean_print_area(area)
    except mockups.MockupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _photos_of(session: AsyncSession, template_id: uuid.UUID) -> list[MockupPhoto]:
    return list((await session.execute(
        select(MockupPhoto).where(MockupPhoto.template_id == template_id)
        .order_by(MockupPhoto.position, MockupPhoto.created_at)
    )).scalars())


async def _own_template(session: AsyncSession, tenant: Tenant, template_id: uuid.UUID) -> MockupTemplate:
    template = await session.get(MockupTemplate, template_id)
    if template is None or template.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="template not found")
    return template


async def _own_photo(session: AsyncSession, tenant: Tenant, photo_id: uuid.UUID) -> MockupPhoto:
    photo = await session.get(MockupPhoto, photo_id)
    if photo is None or photo.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="photo not found")
    return photo


def _ready(photo: MockupPhoto) -> None:
    if photo.status == "failed":
        raise HTTPException(status_code=409, detail=photo.error or work.PREPARE_FAILED)
    if photo.status != "ready":
        raise HTTPException(status_code=409, detail=NOT_READY)


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


async def _templates_out(session: AsyncSession, tenant: Tenant) -> list[TemplateOut]:
    templates = list((await session.execute(
        select(MockupTemplate).where(MockupTemplate.tenant_id == tenant.id)
        .order_by(MockupTemplate.position, MockupTemplate.created_at)
    )).scalars())
    by_template: dict[uuid.UUID, list[MockupPhoto]] = {t.id: [] for t in templates}
    for photo in (await session.execute(
        select(MockupPhoto).where(MockupPhoto.tenant_id == tenant.id)
        .order_by(MockupPhoto.position, MockupPhoto.created_at)
    )).scalars():
        by_template.setdefault(photo.template_id, []).append(photo)
    return [_template_out(t, by_template.get(t.id, [])) for t in templates]


@router.get("/mockup-templates", response_model=list[TemplateOut])
async def list_templates(
    session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> list[TemplateOut]:
    return await _templates_out(session, tenant)


@router.post("/mockup-templates", response_model=TemplateOut, status_code=201)
async def create_template(
    body: TemplateIn, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> TemplateOut:
    """A new, empty template; photos are uploaded into it."""
    count = await session.scalar(select(func.count()).select_from(MockupTemplate).where(MockupTemplate.tenant_id == tenant.id))
    if int(count or 0) >= MAX_TEMPLATES_PER_ACCOUNT:
        raise HTTPException(status_code=409, detail=f"An account keeps up to {MAX_TEMPLATES_PER_ACCOUNT} templates; delete one first.")
    last = await session.scalar(select(func.max(MockupTemplate.position)).where(MockupTemplate.tenant_id == tenant.id))
    template = MockupTemplate(tenant_id=tenant.id, name=clean_name(body.name, "Template"), position=int(last or 0) + 1)
    session.add(template)
    await session.commit()
    return _template_out(template, [])


@router.get("/mockup-templates/{template_id}", response_model=TemplateOut)
async def get_template(
    template_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> TemplateOut:
    template = await _own_template(session, tenant, template_id)
    return _template_out(template, await _photos_of(session, template.id))


@router.patch("/mockup-templates/{template_id}", response_model=TemplateOut)
async def rename_template(
    template_id: uuid.UUID, body: TemplateIn, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> TemplateOut:
    template = await _own_template(session, tenant, template_id)
    template.name = clean_name(body.name, template.name)
    await session.commit()
    return _template_out(template, await _photos_of(session, template.id))


@router.put("/mockup-templates/order", response_model=list[TemplateOut])
async def order_templates(
    body: Order, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> list[TemplateOut]:
    """The seller's order of templates."""
    rows = {t.id: t for t in (await session.execute(
        select(MockupTemplate).where(MockupTemplate.tenant_id == tenant.id))).scalars()}
    if set(body.ids) - set(rows):
        raise HTTPException(status_code=404, detail="template not found")
    rest = [t for t in sorted(rows.values(), key=lambda t: t.position) if t.id not in set(body.ids)]
    for position, template in enumerate([*(rows[i] for i in body.ids), *rest], start=1):
        template.position = position
    await session.commit()
    return await _templates_out(session, tenant)


@router.delete("/mockup-templates/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    """The template and all its photos go. Mockups already made from them stay."""
    template = await _own_template(session, tenant, template_id)
    photos = await _photos_of(session, template.id)
    for photo in photos:
        storage.delete_prefix(f"{tenant.id}/templates/{photo.id}")
    storage_cap.forget(tenant.id)
    audit.destructive(session, "mockup_template.deleted", actor=tenant, tenant_id=tenant.id, shop_id=None,
                      object_id=template.id, photos=len(photos))
    for photo in photos:
        await session.delete(photo)
    await session.delete(template)
    await session.commit()
    return Response(status_code=204)


# --- photos ---------------------------------------------------------------------------------


@router.post("/mockup-templates/{template_id}/photos", response_model=PhotoOut, status_code=201)
async def add_photo(
    template_id: uuid.UUID,
    file: UploadFile,
    name: str = Form("", max_length=1000),
    rights_confirmed: bool = Form(False),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> PhotoOut:
    """A blank photo joins the template, at once, "preparing"; the worker suggests its
    print areas, which the seller then confirms. The seller confirms they may use it."""
    if not rights_confirmed:
        raise HTTPException(status_code=422, detail=f"Please confirm: “{RIGHTS_STATEMENT}”")
    template = await _own_template(session, tenant, template_id)
    count = await session.scalar(select(func.count()).select_from(MockupPhoto).where(MockupPhoto.tenant_id == tenant.id))
    if int(count or 0) >= MAX_PHOTOS_PER_ACCOUNT:
        raise HTTPException(status_code=409, detail=f"An account keeps up to {MAX_PHOTOS_PER_ACCOUNT} template photos; delete one first.")
    in_template = await session.scalar(select(func.count()).select_from(MockupPhoto).where(MockupPhoto.template_id == template.id))
    if int(in_template or 0) >= MAX_PHOTOS_PER_TEMPLATE:
        raise HTTPException(status_code=409, detail=f"A template holds up to {MAX_PHOTOS_PER_TEMPLATE} photos; start another template.")
    data = await file.read()
    _admitted(data)
    source = await _resized(data, "a photo")
    await _room(session, storage, tenant, len(source.image))
    photo_id = uuid.uuid4()
    key = f"{tenant.id}/templates/{photo_id}/source{'.png' if source.has_alpha else '.jpg'}"
    storage.put(key, source.image, "image/png" if source.has_alpha else "image/jpeg")
    storage_cap.added(tenant.id, len(source.image))
    last = await session.scalar(select(func.max(MockupPhoto.position)).where(MockupPhoto.template_id == template.id))
    photo = MockupPhoto(
        id=photo_id, tenant_id=tenant.id, template_id=template.id,
        name=clean_name(name, PurePosixPath((file.filename or "").replace("\\", "/")).stem),
        position=int(last or 0) + 1, status="preparing", source_key=key, print_area_source="default",
        size=mockups.DEFAULT_SIZE, fold_strength=mockups.DEFAULT_FOLD, rights_confirmed_at=datetime.now(timezone.utc),
    )
    session.add(photo)
    audit.record(session, "mockup_photo.created", actor=tenant, target_tenant_id=tenant.id, by="seller",
                 object_id=str(photo_id), rights_confirmed=True)
    await session.commit()
    await enqueuer.enqueue("prepare_mockup_template", str(photo_id), _job_id=f"mockup-photo:{photo_id}")
    return _photo_out(photo)


@router.put("/mockup-templates/{template_id}/photos/order", response_model=TemplateOut)
async def order_photos(
    template_id: uuid.UUID, body: Order, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> TemplateOut:
    """The order of a template's photos: a design's mockups follow it, the first is the cover."""
    template = await _own_template(session, tenant, template_id)
    photos = await _photos_of(session, template.id)
    rows = {p.id: p for p in photos}
    if set(body.ids) - set(rows):
        raise HTTPException(status_code=404, detail="photo not found")
    rest = [p for p in photos if p.id not in set(body.ids)]
    for position, photo in enumerate([*(rows[i] for i in body.ids), *rest], start=1):
        photo.position = position
    await session.commit()
    return _template_out(template, await _photos_of(session, template.id))


@router.get("/mockup-photos/{photo_id}", response_model=PhotoOut)
async def get_photo(
    photo_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> PhotoOut:
    return _photo_out(await _own_photo(session, tenant, photo_id))


@router.patch("/mockup-photos/{photo_id}", response_model=PhotoOut)
async def update_photo(
    photo_id: uuid.UUID, body: PhotoUpdate, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> PhotoOut:
    photo = await _own_photo(session, tenant, photo_id)
    if body.name is not None:
        photo.name = clean_name(body.name, photo.name)
    if body.occlusion is not None:
        photo.occlusion = body.occlusion
    if body.view is not None:
        photo.view = body.view
    if body.size is not None:
        photo.size = round(body.size, 3)
    if body.fold_strength is not None:
        photo.fold_strength = round(body.fold_strength, 3)
    if body.template_id is not None and body.template_id != photo.template_id:
        target = await _own_template(session, tenant, body.template_id)
        last = await session.scalar(select(func.max(MockupPhoto.position)).where(MockupPhoto.template_id == target.id))
        photo.template_id, photo.position = target.id, int(last or 0) + 1
    photo.version += 1
    await session.commit()
    return _photo_out(photo)


@router.put("/mockup-photos/{photo_id}/areas", response_model=PhotoOut)
async def save_areas(
    photo_id: uuid.UUID, body: AreasUpdate, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> PhotoOut:
    """The editor's print areas, in the seller's order. ``confirm`` ("Looks right") marks
    the photo confirmed; saving without it leaves the photo waiting for the seller's check."""
    photo = await _own_photo(session, tenant, photo_id)
    _ready(photo)
    earlier = photo_areas(photo)
    rows = []
    for i, area in enumerate(body.areas):
        corners = _clean(area.print_area)
        before = earlier[i] if i < len(earlier) else {}
        suggested = _clean(area.detected_area) if area.detected_area else (before.get("detected_area") or corners)
        rows.append({
            "print_area": corners, "detected_area": suggested, "source": area.source,
            "confidence": before.get("confidence") if area.source in ("default", "seller") else None,
            "kind": area.kind or before.get("kind"),
        })
    photo.garments = rows
    photo.print_area = rows[0]["print_area"]
    photo.print_area_source = "default" if all(r["source"] == "default" for r in rows) else "seller"
    if body.size is not None:
        photo.size = round(body.size, 3)
    photo.confirmed_at = datetime.now(timezone.utc) if body.confirm else None
    photo.version += 1
    await session.commit()
    return _photo_out(photo)


@router.post("/mockup-photos/{photo_id}/find", response_model=FindOut)
async def find_garment(
    photo_id: uuid.UUID, body: FindRequest, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant), storage: Storage = Depends(get_storage),
) -> FindOut:
    """"Add area": the garment under the seller's tap, alone (the other areas keep their
    garments), and its print area. Nothing is saved; nothing garment-shaped there gives a
    box where they tapped. No model runs (pipeline/garments.py ``find_at``)."""
    photo = await _own_photo(session, tenant, photo_id)
    _ready(photo)
    others = [_clean(a) for a in body.areas]
    blank = storage.get(photo.image_key or "")
    ground = storage.get(photo.foreground_key) if photo.foreground_key else storage.get(photo.mask_key or "")
    occluder = storage.get(photo.occluder_key) if photo.occluder_key else None
    found = await asyncio.to_thread(work.find_at, blank, ground, occluder, (body.x, body.y), others, photo.name)
    if found is not None:
        corners, confidence, kind = found
        return FindOut(found=True, print_area=corners, confidence=confidence, kind=kind)
    return FindOut(found=False, print_area=work.box_at(body.x, body.y, others, photo.width or 1, photo.height or 1),
                   note=TAP_MISSED)


@router.post("/mockup-photos/{photo_id}/copy", response_model=list[PhotoOut])
async def copy_areas(
    photo_id: uuid.UUID, body: CopyRequest, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[PhotoOut]:
    """This photo's print areas, size and fold strength, given to other photos of its
    template ("Apply this area to selected"). With ``fit`` each area is placed by the
    target's own garment: where it sits relative to the garment found on this photo, it is
    put relative to the garment found on the target (pipeline/mockups.py ``transfer_area``);
    without a found garment on either, the same shares of the photo. Each one then waits
    for the seller's "Looks right" on its own photo."""
    source = await _own_photo(session, tenant, photo_id)
    _ready(source)
    areas = photo_areas(source)
    out = []
    for target_id in dict.fromkeys(body.photo_ids):
        if target_id == source.id:
            continue
        target = await _own_photo(session, tenant, target_id)
        if target.template_id != source.template_id:
            raise HTTPException(status_code=422, detail="Areas can be copied only to photos of the same template.")
        if target.status != "ready":
            continue
        own = photo_areas(target)
        rows = []
        for i, g in enumerate(areas):
            suggested = own[i].get("detected_area") if i < len(own) else None
            placed = [list(c) for c in g["print_area"]]
            if body.fit and suggested and g.get("detected_area") and source.width and target.width:
                try:
                    placed = mockups.transfer_area(g["print_area"], g["detected_area"], suggested,
                                                   (source.width, source.height or 1), (target.width, target.height or 1))
                except mockups.MockupError:
                    placed = [list(c) for c in g["print_area"]]  # a degenerate suggestion: the same shares
            rows.append({"print_area": placed, "detected_area": suggested or placed,
                         "source": "copied", "confidence": None, "kind": g.get("kind")})
        target.garments = rows
        target.print_area, target.print_area_source = rows[0]["print_area"], "seller"
        target.size, target.fold_strength = source.size, source.fold_strength
        target.confirmed_at = None
        target.version += 1
        out.append(target)
    await session.commit()
    return [_photo_out(p) for p in out]


@router.post("/mockup-templates/{template_id}/confirm", response_model=TemplateOut)
async def confirm_photos(
    template_id: uuid.UUID, body: ConfirmRequest, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> TemplateOut:
    """"Confirm selected": these photos' print areas, as they are, are confirmed in one step
    (the same as "Looks right" on each). A photo still preparing is left as it is."""
    template = await _own_template(session, tenant, template_id)
    now = datetime.now(timezone.utc)
    for photo_id in dict.fromkeys(body.photo_ids):
        photo = await _own_photo(session, tenant, photo_id)
        if photo.template_id != template.id:
            raise HTTPException(status_code=422, detail="Only photos of this template can be confirmed here.")
        if photo.status == "ready" and photo_areas(photo):
            photo.confirmed_at = now
            photo.version += 1
    await session.commit()
    return _template_out(template, await _photos_of(session, template.id))


@router.put("/mockup-photos/{photo_id}/front", response_model=PhotoOut)
async def save_front_brush(
    photo_id: uuid.UUID,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> PhotoOut:
    """The seller's "Keep in front" brush for this photo: a gray PNG (white = in front,
    black = not in front, mid gray = as the analysis found), at any size; stored at the
    photo's size and combined with the analysis's map into the occluder every preview and
    mockup uses. On our server only; no model involved."""
    photo = await _own_photo(session, tenant, photo_id)
    _ready(photo)
    data = await file.read()
    if len(data) > MAX_BRUSH_BYTES:
        raise HTTPException(status_code=413, detail="The brush is too large to save; paint at a smaller size.")
    try:
        brush = await asyncio.to_thread(mockups.brush_png, data, photo.width or 1, photo.height or 1)
    except Exception as exc:  # noqa: BLE001 - one message for every unreadable mask
        raise HTTPException(status_code=422, detail="The brush could not be read.") from exc
    await asyncio.to_thread(work.save_front, storage, photo, brush=brush)
    storage_cap.forget(tenant.id)
    await session.commit()
    return _photo_out(photo)


@router.delete("/mockup-photos/{photo_id}/front", response_model=PhotoOut)
async def clear_front_brush(
    photo_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> PhotoOut:
    """The brush goes; what is in front is the analysis's map again."""
    photo = await _own_photo(session, tenant, photo_id)
    _ready(photo)
    await asyncio.to_thread(work.save_front, storage, photo, brush=None)
    await session.commit()
    return _photo_out(photo)


@router.get("/mockup-photos/{photo_id}/front")
async def front_mask(
    photo_id: uuid.UUID,
    layer: Literal["combined", "brush", "cutout"] = Query("combined"),
    w: int | None = Query(None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    """What is in front of the garment: ``combined`` (gray, as mockups use it), ``brush``
    (the seller's own strokes; mid gray where none) or ``cutout`` (the photo's own pixels
    that are in front, transparent elsewhere: the editor's live preview draws it over the
    design, so the preview hides the design exactly where the mockup does)."""
    photo = await _own_photo(session, tenant, photo_id)
    _ready(photo)
    width, height = photo.width or 1, photo.height or 1
    scale_to = w if w and w < width else None

    def build() -> bytes:
        if layer == "brush":
            if photo.occluder_manual_key:
                return mockups.brush_png(storage.get(photo.occluder_manual_key), width, height)
            import pyvips

            return (pyvips.Image.black(width, height) + mockups.BRUSH_UNTOUCHED).cast("uchar").copy(
                interpretation="b-w").write_to_buffer(".png", compression=6)
        occluder = storage.get(photo.occluder_key) if photo.occluder_key else None
        if occluder is None:
            raise HTTPException(status_code=404, detail="Nothing is in front of the garment on this photo.")
        if layer == "cutout":
            return mockups.front_layer(storage.get(photo.image_key or ""), occluder, scale_to)
        return occluder

    data = await asyncio.to_thread(build)
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.delete("/mockup-photos/{photo_id}", status_code=204)
async def delete_photo(
    photo_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    """The photo goes from its template. Mockups already made from it stay."""
    photo = await _own_photo(session, tenant, photo_id)
    storage.delete_prefix(f"{tenant.id}/templates/{photo.id}")
    storage_cap.forget(tenant.id)
    audit.destructive(session, "mockup_photo.deleted", actor=tenant, tenant_id=tenant.id, shop_id=None,
                      object_id=photo.id)
    await session.delete(photo)
    await session.commit()
    return Response(status_code=204)


@router.get("/mockup-photos/{photo_id}/image")
async def photo_image(
    photo_id: uuid.UUID,
    w: int | None = Query(None, description=f"Optional preview width; one of {sorted(PREVIEW_WIDTHS)}."),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    photo = await _own_photo(session, tenant, photo_id)
    _ready(photo)
    if w is None:
        return Response(storage.get(photo.image_key or ""), media_type="image/jpeg", headers=_CACHE)
    if w not in PREVIEW_WIDTHS:
        raise HTTPException(status_code=400, detail=f"w must be one of {sorted(PREVIEW_WIDTHS)}")
    key = f"{photo.image_key}.w{w}.jpg"
    if not storage.exists(key):
        storage.put(key, await asyncio.to_thread(resize_preview, storage.get(photo.image_key or ""), w), "image/jpeg")
    return Response(storage.get(key), media_type="image/jpeg", headers=_CACHE)


@router.post("/mockup-photos/{photo_id}/preview")
async def preview_photo(
    photo_id: uuid.UUID,
    body: PreviewRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    """The photo with a sample design printed (shaded, warped by its folds), at preview
    size, with unsaved areas and settings if sent."""
    photo = await _own_photo(session, tenant, photo_id)
    _ready(photo)
    areas = [_clean(a) for a in (body.areas if body.areas is not None else work.photo_print_areas(photo))]
    if not areas:
        raise HTTPException(status_code=422, detail="Add a print area first.")
    occlusion = body.occlusion if body.occlusion is not None else photo.occlusion
    settings = mockups.Settings(
        areas[0],
        body.size if body.size is not None else photo.size,
        body.fold_strength if body.fold_strength is not None else photo.fold_strength,
        occlusion,
    )
    blank, mask = storage.get(photo.image_key or ""), storage.get(photo.mask_key or "")
    occluder = storage.get(photo.occluder_key) if photo.occluder_key and occlusion else None

    def draw() -> bytes:
        small = mockups.preview_scale(blank, mask, occluder, PREVIEW_WIDTH)
        maps = mockups.build_maps(*small, areas)
        design = mockups.load_design(mockup_sample.sample(light_ink=maps.underbase >= 0.5))
        return mockups.render_many(maps, [(design, a) for a in areas], settings, quality=80)

    try:
        data = await asyncio.to_thread(draw)
    except mockups.MockupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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
    design = await _store_design(session, storage, tenant, batch, file.filename or "design.png", data)
    await session.commit()
    return _design_out(design)


@router.post("/batches/{batch_id}/designs/archive", response_model=DesignArchiveOut, status_code=201)
async def add_design_archive(
    batch_id: uuid.UUID,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> DesignArchiveOut:
    """A ZIP of designs: each image in it becomes a design (named by its file name, its SKU
    read from it), read in memory (pipeline/archive.py: zip slip, nesting and size caps).
    A file that fails (too small, not an image, over 30 MB) is said with its reason; the
    others are added."""
    from app.pipeline.archive import read_archive

    batch = await _own_batch(session, tenant, batch_id)
    if await _written(session, batch_id):
        raise HTTPException(status_code=409, detail=WRITTEN)
    settings = get_settings()
    data = await file.read()
    if len(data) > settings.max_archive_bytes:
        raise HTTPException(status_code=413, detail=f"a ZIP is limited to {settings.max_archive_bytes // (1024 * 1024)} MB")
    try:
        contents = read_archive(data, max_files=settings.max_archive_files,
                                max_total_bytes=settings.max_archive_unpacked_bytes,
                                max_file_bytes=settings.max_mockup_upload_bytes)
    except UploadRejected as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    del data
    out = DesignArchiveOut()
    for name in contents.too_large:
        out.failed.append(DesignFailed(filename=name, reason=f"over the {settings.max_mockup_upload_bytes // (1024 * 1024)} MB limit per file"))
    if contents.unsupported:
        out.failed.append(DesignFailed(filename=f"{contents.unsupported} file(s)", reason="not an image (PNG, JPEG or WebP)"))
    for item in contents.files:
        try:
            design = await _store_design(session, storage, tenant, batch, item.filename, item.data)
        except HTTPException as exc:
            if exc.status_code == 413 and "storage" in str(exc.detail).lower():
                raise  # the account's storage is full: nothing more fits
            out.failed.append(DesignFailed(filename=item.filename, reason=str(exc.detail)))
            continue
        await session.commit()
        out.added.append(_design_out(design))
    return out


async def _store_design(session: AsyncSession, storage: Storage, tenant: Tenant, batch: UploadBatch,
                        filename: str, data: bytes) -> MockupDesign:
    """Check, resize and store one design of the batch (the caller commits)."""
    batch_id = batch.id
    _admitted(data)
    try:
        prepared = await asyncio.to_thread(mockups.prepare_design, data)
    except mockups.DesignTooSmall as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    await session.flush()
    return design


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
    per_design = len(run.photo_ids)
    used_photos = {str(p) for p in run.photo_ids}
    skipped_photos: list[str] = []
    for raw in run.template_ids:
        for photo in await _photos_of(session, uuid.UUID(str(raw))):
            if str(photo.id) not in used_photos and photo.tenant_id == run.tenant_id and (
                    run.sides == "both" or (photo.view or "front") == run.sides):
                skipped_photos.append(photo.name)
    zips = (await session.execute(select(MockupZip).where(MockupZip.run_id == run.id).order_by(MockupZip.created_at))).scalars()
    return RunOut(
        id=run.id, batch_id=run.batch_id, status=run.status, total=run.total, done=run.done, failed=run.failed,
        error=run.error, templates=names, photos=len(run.photo_ids), skipped=run.skipped,
        skipped_photos=skipped_photos[: max(run.skipped, 0)] if run.skipped else [],
        designs=[
            RunDesignOut(
                design_id=d.id, original_filename=d.original_filename, sku=d.parsed_sku, group_key=d.group_key,
                done=len(outputs.get(d.id, [])), total=per_design, in_listings=d.id in used,
                outputs=[
                    OutputOut(id=o.id, photo_id=o.photo_id, template_name=o.template_name, photo_name=o.photo_name,
                              filename=o.filename, available=bool(o.key) and o.files_removed_at is None)
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
    """Print every design of the batch on every confirmed photo of the chosen templates
    (template order, then each template's photo order). Photos the seller has not
    confirmed are left out, and the run says how many."""
    await _own_batch(session, tenant, batch_id)
    templates = [await _own_template(session, tenant, t) for t in dict.fromkeys(body.template_ids)]
    templates.sort(key=lambda t: (t.position, t.created_at or datetime.min.replace(tzinfo=timezone.utc)))
    photos: list[MockupPhoto] = []
    skipped = 0
    for template in templates:
        for photo in await _photos_of(session, template.id):
            if body.sides != "both" and (photo.view or "front") != body.sides:
                continue
            if photo.status == "ready" and photo.confirmed_at is not None:
                photos.append(photo)
            else:
                skipped += 1
    if not photos:
        if skipped:
            raise HTTPException(status_code=409, detail=NONE_CONFIRMED)
        if body.sides != "both":
            raise HTTPException(status_code=409, detail=f"None of the chosen templates has a photo of the {body.sides}.")
        raise HTTPException(status_code=409, detail="The chosen templates have no photos yet.")
    if len(photos) > work.MAX_PHOTOS:
        raise HTTPException(status_code=422, detail=f"One run prints on up to {work.MAX_PHOTOS} photos; choose fewer templates.")
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
                    photo_ids=[str(p.id) for p in photos], skipped=skipped,
                    total=int(designs) * len(photos), sides=body.sides, garment_mode=body.garment_mode)
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
    photos = {uuid.UUID(str(p)) for p in run.photo_ids}
    if any(p not in photos for p in body.covers.values()):
        raise HTTPException(status_code=422, detail="A cover must be one of this run's photos.")
    groups, count = await work.make_listings(session, run, body.design_ids, body.covers)
    await session.commit()
    return ListingsOut(groups=groups, photos=count)


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
