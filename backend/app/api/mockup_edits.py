"""Each design adjusted on each mockup, its look, and recoloured parts (seller feedback, Part B;
pipeline/design_edits.py, workers/mockups.py, components/DesignEditor.tsx).

``GET  /api/mockup-runs/{run_id}/edits``          what the editor needs: the run's photos (their
                                                  print areas and size) and designs (their files,
                                                  trim boxes, subgroups), with every adjustment
                                                  and recolouring stored for them.
``PUT  /api/mockup-adjustments``                  one design on one photo's area: its adjustment
                                                  and look (null: back to the automatic fit).
``POST /api/mockup-adjustments/apply``            "Apply this change to…": the change from
                                                  ``before`` to ``after`` composed on top of each
                                                  target's own adjustment (this design on all
                                                  photos, all designs on this photo, all designs on
                                                  all photos of this template); returns ``undo``.
``POST /api/mockup-adjustments/restore``          undo: the rows as they were.
``PUT  /api/mockup-designs/{design_id}/recolour`` a design's recoloured regions for a subgroup ("":
                                                  every subgroup), optionally the same regions on
                                                  the batch's other designs; returns ``undo``.
``POST /api/mockup-recolours/restore``            undo a recolouring.
``POST /api/mockup-runs/{run_id}/rerender``       make the edited mockups again (the worker); a request
                                                  while others are being made joins their queue
                                                  (pipeline/mockup_refresh.py), never refused.
``GET  /api/mockup-designs/{design_id}/file``     a design file as uploaded and stored (the editor
                                                  previews the recolouring on it).

Every edit is a render-time layer: the uploaded design files are never changed. Only the
caller's own designs, photos and runs (another account's: 404). No Etsy call, no AI call.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from redis.asyncio import Redis

from app.api import images
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_redis, get_session, get_storage, submit_heavy
from app.api.mockups import WRITTEN, RunOut, _active, _own_design, _own_photo, _own_run, _run_out, _written, output_stamp
from app.db.models import (
    MockupAdjustment,
    MockupDesign,
    MockupDesignVersion,
    MockupOutput,
    MockupPhoto,
    MockupRecolour,
    MockupRun,
    MockupSubgroup,
    Tenant,
)
from app.pipeline import design_edits, mockup_refresh
from app.pipeline.storage import Storage
from app.workers import mockups as work

router = APIRouter(prefix="/api", tags=["mockups"])

Scope = Literal["design_photos", "photo_designs", "template_all"]


class Place(BaseModel):
    design_id: uuid.UUID
    photo_id: uuid.UUID
    area: int = Field(default=0, ge=0, le=100)


class AdjustmentIn(Place):
    #: The adjustment and look (pipeline/design_edits.py); null: the automatic fit.
    data: dict[str, Any] | None = None


class AdjustmentOut(Place):
    data: dict[str, Any] | None = None


class ApplyIn(Place):
    run_id: uuid.UUID
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    scope: Scope


class RecolourRow(BaseModel):
    design_id: uuid.UUID
    subgroup: str = ""
    regions: list[dict[str, Any]] | None = None



class ApplyOut(BaseModel):
    changed: int
    undo: list[AdjustmentOut]


class RestoreIn(BaseModel):
    rows: list[AdjustmentOut] = Field(max_length=5000)


class RecolourIn(BaseModel):
    subgroup: str = Field(default="", max_length=120)
    regions: list[dict[str, Any]] = Field(default_factory=list, max_length=design_edits.MAX_REGIONS)
    #: The same regions on the batch's other designs too ("All designs…"); null: this design only.
    scope: Scope | None = None


class RecolourOut(BaseModel):
    changed: int
    undo: list[RecolourRow]


class RecolourRestoreIn(BaseModel):
    rows: list[RecolourRow] = Field(max_length=2000)


class RerenderIn(BaseModel):
    #: (design id, photo id) pairs to make again.
    pairs: list[tuple[uuid.UUID, uuid.UUID]] = Field(min_length=1, max_length=3000)


class EditFile(BaseModel):
    #: The design itself or one of its versions (``version_id``), for a subgroup (null: every one).
    version_id: uuid.UUID | None = None
    subgroup: str | None = None
    filename: str
    width: int
    height: int
    #: The box of its visible pixels (left, top, right, bottom) on a file of ``trim_size``.
    trim: list[int] | None = None
    trim_size: list[int] | None = None
    #: Its file's version: the file URL carries it (``?v=``), so the browser keeps the file and
    #: asks again only when the design's file is another one.
    stamp: str = ""


class EditDesign(BaseModel):
    id: uuid.UUID
    original_filename: str
    sku: str | None = None
    files: list[EditFile]


class EditArea(BaseModel):
    print_area: list[list[float]]
    subgroup: str | None = None


class EditPhoto(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    name: str
    width: int
    height: int
    size: float
    areas: list[EditArea]


class EditsOut(BaseModel):
    run_id: uuid.UUID
    photos: list[EditPhoto]
    designs: list[EditDesign]
    adjustments: list[AdjustmentOut]
    recolours: list[RecolourRow]
    #: Whether mockups can still change (nothing of the batch is written yet).
    editable: bool


# --- reading ---------------------------------------------------------------------------------------


async def _run_photos(session: AsyncSession, run: MockupRun) -> list[MockupPhoto]:
    out = []
    for raw in run.photo_ids:
        photo = await session.get(MockupPhoto, uuid.UUID(str(raw)))
        if photo is not None and photo.tenant_id == run.tenant_id:
            out.append(photo)
    return out


async def _batch_designs(session: AsyncSession, batch_id: uuid.UUID) -> list[MockupDesign]:
    return list((await session.execute(
        select(MockupDesign).where(MockupDesign.batch_id == batch_id, MockupDesign.design_key.is_not(None))
        .order_by(MockupDesign.group_key, MockupDesign.original_filename)
    )).scalars())


def _trim(trims: dict | None) -> tuple[list[int] | None, list[int] | None]:
    front = (trims or {}).get("front") or {}
    return front.get("box"), front.get("size")


@router.get("/mockup-runs/{run_id}/edits", response_model=EditsOut)
async def get_edits(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
) -> EditsOut:
    run = await _own_run(session, tenant, run_id)
    photos = await _run_photos(session, run)
    designs = await _batch_designs(session, run.batch_id)
    groups = await work.template_subgroups(session, {p.template_id for p in photos})
    versions: dict[uuid.UUID, list[MockupDesignVersion]] = {}
    if designs:
        for v in (await session.execute(select(MockupDesignVersion).where(
                MockupDesignVersion.design_id.in_([d.id for d in designs]), MockupDesignVersion.design_key.is_not(None)))).scalars():
            versions.setdefault(v.design_id, []).append(v)
    adjust, recolour = await work.design_edits_for(session, [d.id for d in designs], [p.id for p in photos])
    rec_rows = (await session.execute(select(MockupRecolour).where(
        MockupRecolour.design_id.in_([d.id for d in designs] or [uuid.uuid4()])))).scalars().all()
    return EditsOut(
        run_id=run.id,
        photos=[EditPhoto(
            id=p.id, template_id=p.template_id, name=p.name, width=p.width or 1, height=p.height or 1, size=p.size,
            areas=[EditArea(print_area=a, subgroup=n) for a, n in zip(
                work.photo_print_areas(p), work.area_subgroup_names(p, groups.get(p.template_id, [])))],
        ) for p in photos],
        designs=[EditDesign(
            id=d.id, original_filename=d.original_filename, sku=d.parsed_sku,
            files=[EditFile(version_id=None, subgroup=d.subgroup, filename=d.original_filename, width=d.width, height=d.height,
                            trim=_trim(d.trims)[0], trim_size=_trim(d.trims)[1], stamp=images.stamp(d.design_key or ""))]
            + [EditFile(version_id=v.id, subgroup=v.subgroup, filename=v.original_filename, width=v.width, height=v.height,
                        trim=_trim(v.trims)[0], trim_size=_trim(v.trims)[1], stamp=images.stamp(v.design_key or ""))
               for v in versions.get(d.id, [])],
        ) for d in designs],
        adjustments=[AdjustmentOut(design_id=d, photo_id=p, area=k, data=v) for (d, p, k), v in adjust.items()],
        recolours=[RecolourRow(design_id=r.design_id, subgroup=r.subgroup, regions=r.regions) for r in rec_rows],
        editable=not await _written(session, run.batch_id),
    )


@router.get("/mockup-designs/{design_id}/file")
async def design_file(
    design_id: uuid.UUID, request: Request, version: uuid.UUID | None = Query(None), v: str | None = Query(None, max_length=40),
    session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant), storage: Storage = Depends(get_storage),
) -> Response:
    """A design file as stored (never changed): the editor previews recolouring on it. Full
    size, as the editor's arithmetic needs its pixels; kept by the browser with its ``stamp``."""
    design = await _own_design(session, tenant, design_id)
    key = design.design_key
    if version is not None:
        found = await session.get(MockupDesignVersion, version)
        if found is None or found.design_id != design.id:
            raise HTTPException(status_code=404, detail="file not found")
        key = found.design_key
    if not key:
        raise HTTPException(status_code=410, detail="this design's file has been removed")
    return await images.send_stored(request, storage, key=key, url_version=images.stamp(key), asked=v, size=None,
                                    media_type="image/png")


# --- adjustments ----------------------------------------------------------------------------------


async def _check_place(session: AsyncSession, tenant: Tenant, place: Place) -> tuple[MockupDesign, MockupPhoto]:
    design = await _own_design(session, tenant, place.design_id)
    photo = await _own_photo(session, tenant, place.photo_id)
    if await _written(session, design.batch_id):
        raise HTTPException(status_code=409, detail=WRITTEN)
    return design, photo


async def _store(session: AsyncSession, tenant_id: uuid.UUID, design_id: uuid.UUID, photo_id: uuid.UUID, area: int,
                 data: dict[str, Any] | None) -> None:
    row = (await session.execute(select(MockupAdjustment).where(
        MockupAdjustment.design_id == design_id, MockupAdjustment.photo_id == photo_id, MockupAdjustment.area == area,
    ))).scalar_one_or_none()
    if data is None:
        if row is not None:
            await session.delete(row)
        return
    if row is None:
        session.add(MockupAdjustment(tenant_id=tenant_id, design_id=design_id, photo_id=photo_id, area=area, data=data))
    else:
        row.data = data


def _clean(data: dict[str, Any] | None) -> dict[str, Any] | None:
    try:
        return design_edits.clean(data)
    except design_edits.EditError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/mockup-adjustments", response_model=AdjustmentOut)
async def put_adjustment(
    body: AdjustmentIn, session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
) -> AdjustmentOut:
    await _check_place(session, tenant, body)
    data = _clean(body.data)
    await _store(session, tenant.id, body.design_id, body.photo_id, body.area, data)
    await session.commit()
    return AdjustmentOut(design_id=body.design_id, photo_id=body.photo_id, area=body.area, data=data)


@router.post("/mockup-adjustments/apply", response_model=ApplyOut)
async def apply_adjustment(
    body: ApplyIn, session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
) -> ApplyOut:
    """The change from ``before`` to ``after`` on the source placement, given to every other
    placement in the scope as a change on top of its own adjustment (its own fit and earlier
    changes kept). ``undo`` holds each target as it was."""
    design, photo = await _check_place(session, tenant, body)
    run = await _own_run(session, tenant, body.run_id)
    if run.batch_id != design.batch_id or str(photo.id) not in {str(p) for p in run.photo_ids}:
        raise HTTPException(status_code=422, detail="This design and photo are not part of that run.")
    change = design_edits.delta(_clean(body.before), _clean(body.after))
    photos = await _run_photos(session, run)
    designs = await _batch_designs(session, run.batch_id)
    if body.scope == "design_photos":
        targets = [(design.id, p) for p in photos]
    elif body.scope == "photo_designs":
        targets = [(d.id, photo) for d in designs]
    else:
        targets = [(d.id, p) for d in designs for p in photos if p.template_id == photo.template_id]
    current, _ = await work.design_edits_for(session, list({d for d, _ in targets}), list({p.id for _, p in targets}))
    undo: list[AdjustmentOut] = []
    for design_id, p in targets:
        for k in range(len(work.photo_print_areas(p)) or 1):
            if (design_id, p.id, k) == (body.design_id, body.photo_id, body.area):
                continue
            before = current.get((design_id, p.id, k))
            undo.append(AdjustmentOut(design_id=design_id, photo_id=p.id, area=k, data=before))
            await _store(session, tenant.id, design_id, p.id, k, design_edits.compose(before, change))
    await session.commit()
    return ApplyOut(changed=len(undo), undo=undo)


@router.post("/mockup-adjustments/restore", response_model=ApplyOut)
async def restore_adjustments(
    body: RestoreIn, session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
) -> ApplyOut:
    """Undo "Apply this change to…": each row back to what it was."""
    for row in body.rows:
        await _check_place(session, tenant, row)
        await _store(session, tenant.id, row.design_id, row.photo_id, row.area, _clean(row.data))
    await session.commit()
    return ApplyOut(changed=len(body.rows), undo=[])


# --- recolouring ----------------------------------------------------------------------------------


async def _store_recolour(session: AsyncSession, tenant_id: uuid.UUID, design_id: uuid.UUID, subgroup: str,
                          regions: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    row = (await session.execute(select(MockupRecolour).where(
        MockupRecolour.design_id == design_id, MockupRecolour.subgroup == subgroup))).scalar_one_or_none()
    before = row.regions if row is not None else None
    if not regions:
        if row is not None:
            await session.delete(row)
    elif row is None:
        session.add(MockupRecolour(tenant_id=tenant_id, design_id=design_id, subgroup=subgroup, regions=regions))
    else:
        row.regions = regions
    return before


@router.put("/mockup-designs/{design_id}/recolour", response_model=RecolourOut)
async def put_recolour(
    design_id: uuid.UUID, body: RecolourIn, session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
) -> RecolourOut:
    """A design's recoloured regions for one subgroup ("" = every subgroup without its own); with
    ``scope``, the same regions (shares of each file) on the batch's other designs too."""
    design = await _own_design(session, tenant, design_id)
    if await _written(session, design.batch_id):
        raise HTTPException(status_code=409, detail=WRITTEN)
    try:
        regions = design_edits.clean_regions(body.regions)
    except design_edits.EditError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    subgroup = body.subgroup.strip()
    targets = [design.id]
    if body.scope in ("photo_designs", "template_all"):
        targets += [d.id for d in await _batch_designs(session, design.batch_id) if d.id != design.id]
    undo = []
    for target in targets:
        before = await _store_recolour(session, tenant.id, target, subgroup, regions)
        undo.append(RecolourRow(design_id=target, subgroup=subgroup, regions=before))
    await session.commit()
    return RecolourOut(changed=len(targets), undo=undo)


@router.post("/mockup-recolours/restore", response_model=RecolourOut)
async def restore_recolours(
    body: RecolourRestoreIn, session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
) -> RecolourOut:
    for row in body.rows:
        design = await _own_design(session, tenant, row.design_id)
        if await _written(session, design.batch_id):
            raise HTTPException(status_code=409, detail=WRITTEN)
        try:
            regions = design_edits.clean_regions(row.regions)
        except design_edits.EditError as exc:  # 422 in plain words, not a 500 (review R19)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await _store_recolour(session, tenant.id, design.id, row.subgroup, regions)
    await session.commit()
    return RecolourOut(changed=len(body.rows), undo=[])


# --- making them again -----------------------------------------------------------------------------


@router.post("/mockup-runs/{run_id}/rerender", status_code=202, response_model=RunOut)
async def rerender(
    run_id: uuid.UUID, body: RerenderIn, session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant), enqueuer: Enqueuer = Depends(get_enqueuer),
    redis: Redis = Depends(get_redis),
) -> RunOut:
    """Make the chosen mockups again with the seller's edits (the worker). Returns the run with
    the pairs now being made again (``refreshing``): the tiles show a preview until each new file
    is in. Pairs asked for while others are being made join the same queue; only the first
    making of the run's mockups is waited for (409)."""
    run = await _own_run(session, tenant, run_id)
    if _active(run) and not await mockup_refresh.claimed(redis, run.id):
        raise HTTPException(status_code=409, detail="The mockups are still being made.")
    if await _written(session, run.batch_id):
        raise HTTPException(status_code=409, detail=WRITTEN)
    photo_ids = {str(p) for p in run.photo_ids}
    design_ids = {d.id for d in await _batch_designs(session, run.batch_id)}
    pairs = [(d, p) for d, p in dict.fromkeys(body.pairs) if d in design_ids and str(p) in photo_ids]
    if not pairs:
        raise HTTPException(status_code=422, detail="Nothing of this run to make again.")
    stamps = {(o.design_id, o.photo_id): output_stamp(o.key) for o in (await session.execute(
        select(MockupOutput).where(MockupOutput.run_id == run.id))).scalars()}
    if await mockup_refresh.add(redis, run.id, {pair: stamps.get(pair, "") for pair in pairs}):  # no worker has the run
        run.status, run.error = "queued", None
        await session.commit()
        # The job takes the pairs from the run's queue (these and any asked for meanwhile).
        await submit_heavy(enqueuer, run.tenant_id, "rerender_mockups", run.id, job_id=f"rerender:{run.id}:{uuid.uuid4().hex[:8]}")
    return await _run_out(session, run, redis)
