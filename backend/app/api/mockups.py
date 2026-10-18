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
import json
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
    MockupDesignVersion,
    MockupOutput,
    MockupPhoto,
    MockupRun,
    MockupSubgroup,
    MockupTemplate,
    MockupZip,
    Tenant,
    UploadBatch,
)
from app.pipeline import grouping, mockup_sample, mockups, placement, skus, subgroups, upload_retention
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
#: Subgroups per template.
MAX_SUBGROUPS = 12
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
    #: "front" or "back": a back area prints the design's back file (or the template's
    #: "Back area without a back design").
    role: str = "front"
    #: The subgroup the seller linked this area to (None: by the garment's tone, else the
    #: photo's), and the garment's tone ("light"/"dark") that default comes from.
    subgroup_id: uuid.UUID | None = None
    tone: str | None = None
    #: "Design goes behind hair and arms" in this area, and why the analysis turned it off.
    occlusion: bool = True
    occlusion_note: str | None = None
    #: The analysis's estimate of the garment's own axis (degrees, clockwise; None: unknown):
    #: the editor snaps a rotation to it and offers "Match garment angle". The automatic
    #: suggestion stays upright unless two estimates agree; this one is the seller's choice.
    axis_angle: float | None = None


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
    #: "single", "front_back" or "several": how its areas are suggested; ``layout_source``
    #: "auto" (the analysis, when sure) or "seller".
    layout: str = "single"
    layout_source: str = "auto"
    #: The template's subgroup the photo belongs to.
    subgroup_id: uuid.UUID | None = None
    #: Every print area, in the seller's order.
    areas: list[AreaOut] = Field(default_factory=list)
    #: The suggestion was built from the seller's own placement, learnt from this many of
    #: their confirmed photos (None: the template standard).
    own_placement: int | None = None


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    position: int
    photos: list[PhotoOut] = Field(default_factory=list)
    #: Ready photos not confirmed yet (shown as "Needs your check").
    needs_check: int = 0
    confirmed: int = 0
    #: Its suggestions use the seller's own placement, from this many photos (None: the standard).
    own_placement: int | None = None
    #: "Back area without a back design": "front" (the front design) or "plain".
    back_without_design: str = "front"
    #: Its subgroups, in order (at least one).
    subgroups: list[SubgroupOut] = Field(default_factory=list)


class PlacementGroupOut(BaseModel):
    key: str
    label: str
    photos: int
    #: Used for new photos (enough photos: ``print_rule.PERSONAL_MIN``).
    in_use: bool


class PlacementOut(BaseModel):
    """The seller's own print placement, learnt from the photos they confirmed."""

    photos: int = 0
    groups: list[PlacementGroupOut] = Field(default_factory=list)


UseOn = Literal["any", "dark", "light"]


class SubgroupOut(BaseModel):
    id: uuid.UUID
    name: str
    #: "Use on": "any", "dark" garments or "light" garments.
    use_on: str = "any"
    #: The file-name suffix that pairs a design's files ("-white").
    suffix: str | None = None
    position: int = 0


class SubgroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    use_on: UseOn = "any"
    suffix: str | None = Field(default=None, max_length=40)


class SubgroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    use_on: UseOn | None = None
    suffix: str | None = Field(default=None, max_length=40)
    position: int | None = Field(default=None, ge=0, le=1000)


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    #: "Back area without a back design": the front design, or nothing.
    back_without_design: Literal["front", "plain"] | None = None


class Order(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=MAX_PHOTOS_PER_ACCOUNT)


class PhotoUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)
    size: float | None = Field(default=None, ge=mockups.SIZE_RANGE[0], le=mockups.SIZE_RANGE[1])
    fold_strength: float | None = Field(default=None, ge=mockups.FOLD_RANGE[0], le=mockups.FOLD_RANGE[1])
    occlusion: bool | None = None
    view: Literal["front", "back"] | None = None
    #: The photo's layout: its suggested areas are chosen again (confirmed areas never change).
    layout: Literal["single", "front_back", "several"] | None = None
    #: The subgroup of its template the photo belongs to.
    subgroup_id: uuid.UUID | None = None
    #: Move the photo to another of the seller's templates (at its end).
    template_id: uuid.UUID | None = None


class AreaIn(BaseModel):
    print_area: Quad
    detected_area: Quad | None = None
    source: Source = "seller"
    kind: str | None = Field(default=None, max_length=40)
    role: Literal["front", "back"] | None = None
    #: The subgroup this area prints for (several-garment photos); null: by the garment's tone.
    subgroup_id: uuid.UUID | None = None
    #: "Design goes behind hair and arms" in this area.
    occlusion: bool = True


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
    #: The last area: when no garment is found, a box of its size and angle goes where the seller clicked.
    like: Quad | None = None


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
    #: Each area's "design goes behind hair and arms" switch, in the areas' order.
    behind: list[bool] | None = Field(default=None, max_length=MAX_GARMENTS)


class DesignOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    group_key: str
    parsed_sku: str | None
    width: int
    height: int
    has_alpha: bool
    #: It has a back file (printed on back areas).
    has_back: bool = False
    #: The subgroup (by name) its file is for (None: every subgroup), the colour of its ink, and
    #: its files for other subgroups (one design, one listing).
    subgroup: str | None = None
    ink: str | None = None
    versions: list[DesignVersionOut] = Field(default_factory=list)


class DesignVersionOut(BaseModel):
    id: uuid.UUID
    subgroup: str
    original_filename: str
    has_alpha: bool = True


DesignArchiveOut.model_rebuild()
DesignOut.model_rebuild()


class DesignUpdate(BaseModel):
    #: The SKU the design's mockups and listing are named after; null = none (its file name).
    sku: str | None = Field(default=None, max_length=200)
    #: The subgroup (by name) its file is for (moved between upload slots); null = every subgroup.
    subgroup: str | None = Field(default=None, max_length=MAX_NAME)


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
        view=p.view or "front", own_placement=placement.built_from_own(p) if ready else None,
        layout=p.layout or "single", layout_source=p.layout_source or "auto", subgroup_id=p.subgroup_id,
        areas=[
            AreaOut(print_area=g["print_area"], detected_area=g.get("detected_area") or g["print_area"],
                    source=g.get("source") or "default", confidence=g.get("confidence"), kind=g.get("kind"),
                    axis_angle=(g.get("geometry") or {}).get("axis_estimate"),
                    role=g.get("role") if g.get("role") in ("front", "back") else ("back" if p.view == "back" else "front"),
                    subgroup_id=_uuid_or_none(g.get("subgroup_id")), tone=g.get("tone"),
                    occlusion=g.get("occlusion") is not False, occlusion_note=g.get("occlusion_note"))
            for g in photo_areas(p)
        ],
    )


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


async def _subgroups_of(session: AsyncSession, template_id: uuid.UUID) -> list[MockupSubgroup]:
    """A template's subgroups, in order; one is made (named after it) if it has none."""
    rows = list((await session.execute(
        select(MockupSubgroup).where(MockupSubgroup.template_id == template_id)
        .order_by(MockupSubgroup.position, MockupSubgroup.created_at)
    )).scalars())
    if not rows:
        template = await session.get(MockupTemplate, template_id)
        if template is None:
            return []
        first = MockupSubgroup(tenant_id=template.tenant_id, template_id=template_id, name=template.name, use_on="any")
        session.add(first)
        await session.flush()
        rows = [first]
    return rows


async def _template_full(session: AsyncSession, t: MockupTemplate) -> TemplateOut:
    groups = await _subgroups_of(session, t.id)
    return _template_out(t, await _photos_of(session, t.id), groups)


def _template_out(t: MockupTemplate, photos: list[MockupPhoto], groups: list[MockupSubgroup] | None = None) -> TemplateOut:
    out = [_photo_out(p) for p in photos]
    own = [p.own_placement for p in out if p.own_placement]
    return TemplateOut(id=t.id, name=t.name, position=t.position, photos=out, back_without_design=t.back_without_design,
                       subgroups=[SubgroupOut(id=g.id, name=g.name, use_on=g.use_on, suffix=g.suffix, position=g.position)
                                  for g in (groups or [])],
                       needs_check=sum(p.needs_check for p in out), confirmed=sum(p.confirmed for p in out),
                       own_placement=max(own) if own else None)


def _same_quad(a: Any, b: Any) -> bool:
    try:
        return bool(a and b) and all(abs(p[0] - q[0]) < 1e-3 and abs(p[1] - q[1]) < 1e-3 for p, q in zip(a, b, strict=True))
    except (TypeError, ValueError, IndexError):
        return False


def _kept(earlier: list[dict[str, Any]], suggested: Any) -> dict[str, Any]:
    """What the analysis kept with a suggestion (its geometry and ratios), found by the
    suggestion itself: the seller may have reordered the areas."""
    for row in earlier:
        if _same_quad(row.get("detected_area"), suggested):
            return {k: row[k] for k in ("geometry", "ratios", "personal_n", "guards", "layout", "view") if k in row}
    return {}


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
    groups: dict[uuid.UUID, list[MockupSubgroup]] = {}
    for g in (await session.execute(
        select(MockupSubgroup).where(MockupSubgroup.tenant_id == tenant.id)
        .order_by(MockupSubgroup.position, MockupSubgroup.created_at)
    )).scalars():
        groups.setdefault(g.template_id, []).append(g)
    for t in templates:
        if t.id not in groups:
            groups[t.id] = await _subgroups_of(session, t.id)
    return [_template_out(t, by_template.get(t.id, []), groups[t.id]) for t in templates]


@router.get("/mockup-templates", response_model=list[TemplateOut])
async def list_templates(
    session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> list[TemplateOut]:
    return await _templates_out(session, tenant)


@router.post("/mockup-templates/{template_id}/subgroups", response_model=TemplateOut, status_code=201)
async def add_subgroup(
    template_id: uuid.UUID, body: SubgroupIn, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> TemplateOut:
    """A subgroup of the template ("Comfort Colors White"), with "Use on" and the file-name
    suffix that pairs a design's files."""
    template = await _own_template(session, tenant, template_id)
    groups = await _subgroups_of(session, template.id)
    if len(groups) >= MAX_SUBGROUPS:
        raise HTTPException(status_code=409, detail=f"A template has up to {MAX_SUBGROUPS} subgroups.")
    if any(subgroups.same(g.name, body.name) for g in groups):
        raise HTTPException(status_code=409, detail="This template already has a subgroup of that name.")
    session.add(MockupSubgroup(tenant_id=tenant.id, template_id=template.id, name=clean_name(body.name, "Subgroup"),
                               use_on=body.use_on, suffix=(body.suffix or "").strip() or None,
                               position=max((g.position for g in groups), default=0) + 1))
    await session.commit()
    return await _template_full(session, template)


async def _own_subgroup(session: AsyncSession, tenant: Tenant, subgroup_id: uuid.UUID) -> MockupSubgroup:
    group = await session.get(MockupSubgroup, subgroup_id)
    if group is None or group.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="subgroup not found")
    return group


@router.patch("/mockup-subgroups/{subgroup_id}", response_model=TemplateOut)
async def update_subgroup(
    subgroup_id: uuid.UUID, body: SubgroupUpdate, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> TemplateOut:
    group = await _own_subgroup(session, tenant, subgroup_id)
    if body.name is not None:
        others = [g for g in await _subgroups_of(session, group.template_id) if g.id != group.id]
        if any(subgroups.same(g.name, body.name) for g in others):
            raise HTTPException(status_code=409, detail="This template already has a subgroup of that name.")
        group.name = clean_name(body.name, group.name)
    if body.use_on is not None:
        group.use_on = body.use_on
    if body.suffix is not None:
        group.suffix = body.suffix.strip() or None
    if body.position is not None:
        group.position = body.position
    await session.commit()
    return await _template_full(session, await session.get(MockupTemplate, group.template_id))


@router.delete("/mockup-subgroups/{subgroup_id}", response_model=TemplateOut)
async def delete_subgroup(
    subgroup_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant),
) -> TemplateOut:
    """Remove a subgroup: its photos go to the template's first remaining subgroup; area
    links to it fall back to the default. A template keeps at least one."""
    group = await _own_subgroup(session, tenant, subgroup_id)
    groups = await _subgroups_of(session, group.template_id)
    if len(groups) <= 1:
        raise HTTPException(status_code=409, detail="A template keeps at least one subgroup.")
    keep = next(g for g in groups if g.id != group.id)
    for photo in await _photos_of(session, group.template_id):
        if photo.subgroup_id == group.id:
            photo.subgroup_id = keep.id
        if any(r.get("subgroup_id") == str(group.id) for r in photo.garments or []):
            photo.garments = [{**r, "subgroup_id": None} if r.get("subgroup_id") == str(group.id) else r
                              for r in photo.garments or []]
            photo.version += 1
    audit.destructive(session, "mockup_subgroup.deleted", actor=tenant, tenant_id=tenant.id, shop_id=None, object_id=group.id)
    template = await session.get(MockupTemplate, group.template_id)
    await session.delete(group)
    await session.commit()
    return await _template_full(session, template)


@router.get("/mockup-placement", response_model=PlacementOut)
async def get_placement(
    session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> PlacementOut:
    """The seller's own print placement, learnt from the photos they confirmed (theirs only)."""
    return PlacementOut(**await placement.summary(session, tenant.id))


@router.delete("/mockup-placement", response_model=PlacementOut)
async def reset_placement(
    session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> PlacementOut:
    """"Reset": forget the seller's placement; new photos are suggested from the standard
    again. Areas already on photos stay as they are."""
    removed = await placement.reset(session, tenant.id)
    audit.destructive(session, "mockup_placement.reset", actor=tenant, tenant_id=tenant.id, shop_id=None,
                      object_id=str(tenant.id), rows=removed)
    await session.commit()
    return PlacementOut()


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
    await session.flush()
    first = MockupSubgroup(tenant_id=tenant.id, template_id=template.id, name=template.name, use_on="any")
    session.add(first)
    await session.commit()
    return _template_out(template, [], [first])


@router.get("/mockup-templates/{template_id}", response_model=TemplateOut)
async def get_template(
    template_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> TemplateOut:
    template = await _own_template(session, tenant, template_id)
    return await _template_full(session, template)


@router.patch("/mockup-templates/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: uuid.UUID, body: TemplateUpdate, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> TemplateOut:
    """Rename a template, or set what its back areas print without a back design. (Each
    photo has its own layout: ``PATCH /mockup-photos/{id}`` ``layout``.)"""
    template = await _own_template(session, tenant, template_id)
    if body.name is not None:
        template.name = clean_name(body.name, template.name)
    if body.back_without_design is not None:
        template.back_without_design = body.back_without_design
    await session.commit()
    return await _template_full(session, template)


def resuggest(photo: MockupPhoto, layout: str) -> bool:
    """A waiting photo's suggested areas chosen again for its layout from its analysis's
    candidates (``mockups.chosen_rows``); False when the photo is confirmed, edited by the
    seller or has no candidates (analysed before they were kept): confirmed areas never change
    without the seller."""
    candidates = (photo.analysis or {}).get("candidates")
    rows = photo.garments or []
    if (photo.status != "ready" or photo.confirmed_at is not None or not candidates
            or any((r.get("source") or "default") != "default" for r in rows)):
        return False
    chosen = mockups.chosen_rows(candidates, layout)
    if [(r.get("print_area"), r.get("role")) for r in chosen] == [(r.get("print_area"), r.get("role")) for r in rows]:
        return False
    photo.garments = chosen
    photo.print_area, photo.print_area_source = chosen[0]["print_area"], "default"
    photo.version += 1
    return True


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
        id=photo_id, tenant_id=tenant.id, template_id=template.id, subgroup_id=(await _subgroups_of(session, template.id))[0].id,
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
    return await _template_full(session, template)


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
    if body.layout is not None:
        photo.layout, photo.layout_source = body.layout, "seller"
        resuggest(photo, body.layout)
    if body.subgroup_id is not None:
        if body.subgroup_id not in {g.id for g in await _subgroups_of(session, photo.template_id)}:
            raise HTTPException(status_code=422, detail="That subgroup is not in this photo's template.")
        photo.subgroup_id = body.subgroup_id
    if body.size is not None:
        photo.size = round(body.size, 3)
    if body.fold_strength is not None:
        photo.fold_strength = round(body.fold_strength, 3)
    if body.template_id is not None and body.template_id != photo.template_id:
        target = await _own_template(session, tenant, body.template_id)
        last = await session.scalar(select(func.max(MockupPhoto.position)).where(MockupPhoto.template_id == target.id))
        photo.template_id, photo.position = target.id, int(last or 0) + 1
        photo.subgroup_id = (await _subgroups_of(session, target.id))[0].id
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
    valid = {str(g.id) for g in await _subgroups_of(session, photo.template_id)}
    rows = []
    for i, area in enumerate(body.areas):
        corners = _clean(area.print_area)
        before = earlier[i] if i < len(earlier) else {}
        suggested = _clean(area.detected_area) if area.detected_area else (before.get("detected_area") or corners)
        rows.append({
            **_kept(earlier, suggested),
            "print_area": corners, "detected_area": suggested, "source": area.source,
            "confidence": before.get("confidence") if area.source in ("default", "seller") else None,
            "kind": area.kind or before.get("kind"),
            "role": area.role or before.get("role") or ("back" if photo.view == "back" else "front"),
            "subgroup_id": str(area.subgroup_id) if area.subgroup_id and str(area.subgroup_id) in valid else None,
            "tone": before.get("tone"),
            "occlusion": area.occlusion,
            # The analysis's reason stays only while the seller leaves the switch as it set it.
            "occlusion_note": before.get("occlusion_note") if area.occlusion is (before.get("occlusion") is not False) else None,
        })
    photo.garments = rows
    photo.print_area = rows[0]["print_area"]
    photo.print_area_source = "default" if all(r["source"] == "default" for r in rows) else "seller"
    if body.size is not None:
        photo.size = round(body.size, 3)
    photo.confirmed_at = datetime.now(timezone.utc) if body.confirm else None
    photo.version += 1
    await placement.record(session, photo)  # "Looks right" teaches the seller's placement
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
    # 1. A garment the analysis already found under the click (not yet given an area).
    picked = work.candidate_at(photo, body.x, body.y, others)
    if picked is not None:
        return FindOut(found=True, print_area=picked["print_area"], confidence=picked.get("confidence"),
                       kind=picked.get("kind"))
    blank = storage.get(photo.image_key or "")
    ground = storage.get(photo.foreground_key) if photo.foreground_key else storage.get(photo.mask_key or "")
    occluder = storage.get(photo.occluder_key) if photo.occluder_key else None
    personal = await placement.personal(session, tenant.id)
    found = await asyncio.to_thread(work.find_at, blank, ground, occluder, (body.x, body.y), others, photo.name,
                                    personal)
    if found is not None:
        corners, confidence, kind = found
        return FindOut(found=True, print_area=corners, confidence=confidence, kind=kind)
    # 3. Nothing garment-shaped there: the last area's size and angle, centred on the click.
    like = _clean(body.like) if body.like else None
    box = (work.like_at(body.x, body.y, like, photo.width or 1, photo.height or 1) if like
           else work.box_at(body.x, body.y, others, photo.width or 1, photo.height or 1))
    return FindOut(found=False, print_area=box, note=TAP_MISSED)


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
                    # The seller's angle travels as set (the mapping above can skew it).
                    placed = mockups.with_angle_of(placed, g["print_area"], (source.width, source.height or 1),
                                                   (target.width, target.height or 1))
                except mockups.MockupError:
                    placed = [list(c) for c in g["print_area"]]  # a degenerate suggestion: the same shares
            rows.append({**(_kept(own, suggested) if suggested else {}),
                         "print_area": placed, "detected_area": suggested or placed,
                         "source": "copied", "confidence": None, "kind": g.get("kind"),
                         "occlusion": g.get("occlusion") is not False})
        target.garments = rows
        target.print_area, target.print_area_source = rows[0]["print_area"], "seller"
        target.size, target.fold_strength = source.size, source.fold_strength
        target.confirmed_at = None
        target.version += 1
        await placement.record(session, target)  # not confirmed any more: nothing learnt from it
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
            await placement.record(session, photo)
    await session.commit()
    return await _template_full(session, template)


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
        switches = body.behind if body.behind is not None else work.photo_area_occlusion(photo)
        return mockups.render_many(
            maps, [(design, a, switches[i] if i < len(switches) else True) for i, a in enumerate(areas)], settings,
            quality=80)

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


def _design_out(d: MockupDesign, versions: list[MockupDesignVersion] | None = None) -> DesignOut:
    return DesignOut(id=d.id, original_filename=d.original_filename, group_key=d.group_key, parsed_sku=d.parsed_sku,
                     has_back=bool(d.back_key), subgroup=d.subgroup, ink=d.ink,
                     versions=[DesignVersionOut(id=v.id, subgroup=v.subgroup, original_filename=v.original_filename,
                                                has_alpha=v.has_alpha) for v in versions or []],
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
    rows = list((await session.execute(
        select(MockupDesign).where(MockupDesign.batch_id == batch_id).order_by(MockupDesign.group_key)
    )).scalars())
    versions = await _versions_of(session, [d.id for d in rows])
    return [_design_out(d, versions.get(d.id, [])) for d in rows]


async def _versions_of(session: AsyncSession, design_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[MockupDesignVersion]]:
    out: dict[uuid.UUID, list[MockupDesignVersion]] = {}
    if design_ids:
        for v in (await session.execute(
            select(MockupDesignVersion).where(MockupDesignVersion.design_id.in_(design_ids))
            .order_by(MockupDesignVersion.created_at)
        )).scalars():
            out.setdefault(v.design_id, []).append(v)
    return out


def _slots(raw: str | None) -> list[dict[str, Any]]:
    """The upload's subgroup slots, as the page sends them: [{"name", "suffix", "use_on"}]."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="subgroups must be a JSON list") from exc
    if not isinstance(data, list):
        raise HTTPException(status_code=422, detail="subgroups must be a JSON list")
    return [{"name": str(g.get("name") or "")[:MAX_NAME], "suffix": str(g.get("suffix") or "")[:40] or None,
             "use_on": g.get("use_on") if g.get("use_on") in ("any", "dark", "light") else "any"}
            for g in data[:MAX_SUBGROUPS] if isinstance(g, dict) and g.get("name")]


@router.post("/batches/{batch_id}/designs", response_model=DesignOut, status_code=201)
async def add_design(
    batch_id: uuid.UUID,
    file: UploadFile,
    subgroup: str | None = Form(None),
    subgroups_json: str | None = Form(None, alias="subgroups"),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> DesignOut:
    """A design to print on templates; it becomes its own listing group. Its SKU is
    read from its file name (pipeline/grouping.py) and can be changed before the
    mockups are made. With the chosen templates' subgroups (``subgroups``: their names,
    suffixes and "Use on") the file goes to the subgroup it was dropped on (``subgroup``),
    else the one its name's suffix says, else the one its ink suggests; and a file whose name
    without the suffix matches a design of the batch becomes that design's version for its
    subgroup (one design, one listing)."""
    batch = await _own_batch(session, tenant, batch_id)
    if await _written(session, batch_id):
        raise HTTPException(status_code=409, detail=WRITTEN)
    data = await file.read()
    design = await _store_design(session, storage, tenant, batch, file.filename or "design.png", data,
                                 subgroup=subgroup, slots=_slots(subgroups_json))
    await session.commit()
    return _design_out(design, (await _versions_of(session, [design.id])).get(design.id, []))


@router.post("/batches/{batch_id}/designs/archive", response_model=DesignArchiveOut, status_code=201)
async def add_design_archive(
    batch_id: uuid.UUID,
    file: UploadFile,
    subgroups_json: str | None = Form(None, alias="subgroups"),
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
    slots = _slots(subgroups_json)
    for name in contents.too_large:
        out.failed.append(DesignFailed(filename=name, reason=f"over the {settings.max_mockup_upload_bytes // (1024 * 1024)} MB limit per file"))
    if contents.unsupported:
        out.failed.append(DesignFailed(filename=f"{contents.unsupported} file(s)", reason="not an image (PNG, JPEG or WebP)"))
    for item in contents.files:
        try:
            design = await _store_design(session, storage, tenant, batch, item.filename, item.data, slots=slots)
        except HTTPException as exc:
            if exc.status_code == 413 and "storage" in str(exc.detail).lower():
                raise  # the account's storage is full: nothing more fits
            out.failed.append(DesignFailed(filename=item.filename, reason=str(exc.detail)))
            continue
        await session.commit()
        # A file that paired with an earlier one is that design's version: listed once.
        shown = _design_out(design, (await _versions_of(session, [design.id])).get(design.id, []))
        out.added = [d for d in out.added if d.id != design.id] + [shown]
    return out


async def _store_design(session: AsyncSession, storage: Storage, tenant: Tenant, batch: UploadBatch,
                        filename: str, data: bytes, subgroup: str | None = None,
                        slots: list[dict[str, Any]] | None = None) -> MockupDesign:
    """Check, resize and store one design of the batch (the caller commits). With subgroup
    ``slots``: its subgroup (dropped on, else its name's suffix, else its ink) and, when a
    design of the batch has the same name without the suffix, a version of that design."""
    batch_id = batch.id
    _admitted(data)
    try:
        prepared = await asyncio.to_thread(mockups.prepare_design, data)
    except mockups.DesignTooSmall as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ImageProcessingError, mockups.MockupError) as exc:
        raise HTTPException(status_code=422, detail="This file could not be read as a design.") from exc
    await _room(session, storage, tenant, len(prepared.image))
    # Its trim box (transparent padding left out), kept with it; the file stays as uploaded.
    trim = await asyncio.to_thread(mockups.design_trim, prepared.image)
    slots = slots or []
    base, by_suffix = subgroups.split_name(filename, slots)
    ink = await asyncio.to_thread(subgroups.ink_of, prepared.image)
    chosen = None
    if slots:
        named = next((g["name"] for g in slots if subgroups.same(g["name"], subgroup)), None) if subgroup else None
        chosen = named or by_suffix or subgroups.by_ink(ink, slots)
    if chosen:
        for other in (await session.execute(
            select(MockupDesign).where(MockupDesign.batch_id == batch_id, MockupDesign.design_key.is_not(None))
        )).scalars():
            if not subgroups.same(other.pair_base, base) or subgroups.same(other.subgroup, chosen):
                continue
            have = (await _versions_of(session, [other.id])).get(other.id, [])
            if any(subgroups.same(v.subgroup, chosen) for v in have):
                continue
            version_id = uuid.uuid4()
            vkey = f"{tenant.id}/{batch_id}/designs/{other.id}-v-{version_id.hex[:8]}.png"
            storage.put(vkey, prepared.image, "image/png")
            storage_cap.added(tenant.id, len(prepared.image))
            session.add(MockupDesignVersion(id=version_id, tenant_id=tenant.id, design_id=other.id, subgroup=chosen,
                                            original_filename=filename, design_key=vkey, width=prepared.width,
                                            height=prepared.height, has_alpha=prepared.has_alpha, ink=ink,
                                            trims={"front": trim}))
            await session.flush()
            return other
    taken = set((await session.execute(select(MockupDesign.group_key).where(MockupDesign.batch_id == batch_id))).scalars())
    sku = grouping.sku_of(base if slots else filename, SkuParser())
    design_id = uuid.uuid4()
    key = f"{tenant.id}/{batch_id}/designs/{design_id}.png"
    storage.put(key, prepared.image, "image/png")
    storage_cap.added(tenant.id, len(prepared.image))
    design = MockupDesign(
        id=design_id, tenant_id=tenant.id, batch_id=batch_id, group_key=design_group(filename, sku, taken),
        original_filename=filename, parsed_sku=sku, design_key=key, width=prepared.width, height=prepared.height,
        has_alpha=prepared.has_alpha, subgroup=chosen, pair_base=base, ink=ink, trims={"front": trim},
    )
    session.add(design)
    batch.grouping_mode = batch.grouping_mode or "folder"
    await session.flush()
    return design


async def _own_design(session: AsyncSession, tenant: Tenant, design_id: uuid.UUID) -> MockupDesign:
    design = await session.get(MockupDesign, design_id)
    if design is None or design.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="design not found")
    return design


@router.put("/mockup-designs/{design_id}/back", response_model=DesignOut)
async def set_design_back(
    design_id: uuid.UUID,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> DesignOut:
    """The design's back file: printed on back areas (front & back photos, garments seen from
    behind). Checked and stored like the design itself."""
    design = await _own_design(session, tenant, design_id)
    if await _written(session, design.batch_id):
        raise HTTPException(status_code=409, detail=WRITTEN)
    data = await file.read()
    _admitted(data)
    try:
        prepared = await asyncio.to_thread(mockups.prepare_design, data)
    except mockups.DesignTooSmall as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ImageProcessingError, mockups.MockupError) as exc:
        raise HTTPException(status_code=422, detail="This file could not be read as a design.") from exc
    await _room(session, storage, tenant, len(prepared.image))
    if design.back_key:
        storage.delete_with_derivatives(design.back_key)
    key = f"{tenant.id}/{design.batch_id}/designs/{design.id}-back-{uuid.uuid4().hex[:8]}.png"
    storage.put(key, prepared.image, "image/png")
    storage_cap.added(tenant.id, len(prepared.image))
    design.back_key = key
    design.trims = {**(design.trims or {}), "back": await asyncio.to_thread(mockups.design_trim, prepared.image)}
    await session.commit()
    return _design_out(design)


@router.delete("/mockup-designs/{design_id}/back", response_model=DesignOut)
async def delete_design_back(
    design_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> DesignOut:
    design = await _own_design(session, tenant, design_id)
    if design.back_key:
        storage.delete_with_derivatives(design.back_key)
        storage_cap.forget(tenant.id)
        design.back_key = None
        design.trims = {k: v for k, v in (design.trims or {}).items() if k != "back"} or None
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
    versions = (await _versions_of(session, [design.id])).get(design.id, [])
    if "subgroup" in body.model_fields_set:
        wanted = (body.subgroup or "").strip() or None
        if wanted and any(subgroups.same(v.subgroup, wanted) for v in versions):
            raise HTTPException(status_code=409, detail="This design already has a file for that subgroup.")
        design.subgroup = wanted
    if "sku" in body.model_fields_set or "subgroup" not in body.model_fields_set:
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
    return _design_out(design, versions)


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
    # Per design: the run's photos it has a file for (a design made for one subgroup only
    # prints on that subgroup's photos).
    run_photos = [p for raw in run.photo_ids if (p := await session.get(MockupPhoto, uuid.UUID(str(raw)))) is not None]
    groups = await work.template_subgroups(session, {p.template_id for p in run_photos})
    photo_names = [work.area_subgroup_names(p, groups.get(p.template_id, [])) for p in run_photos]
    versions = await _versions_of(session, [d.id for d in designs])
    per_design = {d.id: sum(work.prints_on(d, versions.get(d.id, []), n) for n in photo_names) if run_photos else len(run.photo_ids)
                  for d in designs}
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
                done=len(outputs.get(d.id, [])), total=per_design[d.id], in_listings=d.id in used,
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


class VersionUpdate(BaseModel):
    #: The subgroup (by name) this file is for (moved between upload slots).
    subgroup: str = Field(min_length=1, max_length=MAX_NAME)


class DesignDeleted(BaseModel):
    #: The whole design went (with its files); False: only the chosen file, another took its place.
    deleted: bool
    design: DesignOut | None = None


#: Said when a design's mockups are already a listing's photos (B2): the design stays.
DESIGN_IN_LISTING = (
    "This design is used by a listing in this batch, so it cannot be deleted. Delete that listing first if you no "
    "longer need it."
)
DESIGN_IN_GROUP = (
    "This design's mockups are the photos of a listing group in this batch, so it cannot be deleted. Remove that "
    "group's photos first."
)


async def _design_in_use(session: AsyncSession, design: MockupDesign) -> str | None:
    """Why ``design`` cannot be deleted: its mockups became a listing group's photos ("Use
    for listings"), and that listing may already be written or drafted. None: unused."""
    group = (await session.execute(
        select(Asset.group_key).where(Asset.batch_id == design.batch_id, Asset.mockup_design_id == design.id).limit(1)
    )).scalar()
    if group is None:
        return None
    written = (await session.execute(
        select(GeneratedContent.id).join(Asset, GeneratedContent.asset_id == Asset.id)
        .where(Asset.batch_id == design.batch_id, Asset.group_key == group).limit(1)
    )).scalar()
    return DESIGN_IN_LISTING if written is not None else DESIGN_IN_GROUP


def _drop_files(storage: Storage, keys: list[str | None]) -> None:
    for key in keys:
        if key:
            storage.delete_with_derivatives(key)


@router.delete("/mockup-designs/{design_id}", response_model=DesignDeleted)
async def delete_design(
    design_id: uuid.UUID,
    only_file: bool = Query(False),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> DesignDeleted:
    """Delete a design, its files (front, back, every subgroup's file) and the mockups made
    from it; or with ``only_file`` just its own file: its first other file takes its place
    (with none, the whole design goes). Refused, saying why, while its mockups are a listing's
    photos. Files go the way every upload goes (with their previews)."""
    design = await _own_design(session, tenant, design_id)
    reason = await _design_in_use(session, design)
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    versions = (await _versions_of(session, [design.id])).get(design.id, [])
    if only_file and versions:
        first, rest = versions[0], versions[1:]
        _drop_files(storage, [design.design_key, design.back_key])
        design.design_key, design.back_key = first.design_key, first.back_key
        design.subgroup, design.original_filename = first.subgroup, first.original_filename
        design.width, design.height, design.has_alpha = first.width, first.height, first.has_alpha
        design.ink, design.trims = first.ink, first.trims
        await session.delete(first)
        audit.destructive(session, "mockup_design.deleted", actor=tenant, tenant_id=tenant.id, shop_id=None,
                          object_id=design.id)
        storage_cap.forget(tenant.id)
        await session.commit()
        return DesignDeleted(deleted=False, design=_design_out(design, rest))
    outputs = list((await session.execute(select(MockupOutput).where(MockupOutput.design_id == design.id))).scalars())
    _drop_files(storage, [design.design_key, design.back_key, *[k for v in versions for k in (v.design_key, v.back_key)],
                          *[o.key for o in outputs]])
    for o in outputs:
        await session.delete(o)
    for v in versions:
        await session.delete(v)
    await session.delete(design)
    audit.destructive(session, "mockup_design.deleted", actor=tenant, tenant_id=tenant.id, shop_id=None,
                      object_id=design_id)
    storage_cap.forget(tenant.id)
    await session.commit()
    return DesignDeleted(deleted=True)


@router.delete("/mockup-design-versions/{version_id}", response_model=DesignOut)
async def delete_design_version(
    version_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> DesignOut:
    """Delete one of a design's files for another subgroup (the design keeps its others).
    Mockups already made stay until mockups are made again."""
    version = await session.get(MockupDesignVersion, version_id)
    if version is None or version.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="design not found")
    design = await _own_design(session, tenant, version.design_id)
    reason = await _design_in_use(session, design)
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    _drop_files(storage, [version.design_key, version.back_key])
    await session.delete(version)
    audit.destructive(session, "mockup_design.deleted", actor=tenant, tenant_id=tenant.id, shop_id=None,
                      object_id=version_id)
    storage_cap.forget(tenant.id)
    await session.commit()
    rest = (await _versions_of(session, [design.id])).get(design.id, [])
    return _design_out(design, rest)


@router.patch("/mockup-design-versions/{version_id}", response_model=DesignOut)
async def update_design_version(
    version_id: uuid.UUID, body: VersionUpdate, session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> DesignOut:
    """Move one of a design's files to another subgroup's slot (the design keeps one listing)."""
    version = await session.get(MockupDesignVersion, version_id)
    if version is None or version.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="design not found")
    design = await _own_design(session, tenant, version.design_id)
    if await _written(session, design.batch_id):
        raise HTTPException(status_code=409, detail=WRITTEN)
    versions = (await _versions_of(session, [design.id])).get(design.id, [])
    wanted = body.subgroup.strip()
    if subgroups.same(design.subgroup, wanted) or any(
            v.id != version.id and subgroups.same(v.subgroup, wanted) for v in versions):
        raise HTTPException(status_code=409, detail="This design already has a file for that subgroup.")
    version.subgroup = wanted
    await session.commit()
    return _design_out(design, versions)


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
