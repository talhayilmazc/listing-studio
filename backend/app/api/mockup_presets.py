"""Placement presets per mockup template (feature/mockups-editor B4).

A preset is a named placement on one template ("Chest print", "Full front", "Left pocket"): where the design sits
relative to the template's automatic fit (pipeline/design_edits.py), without the look. The seller saves one from
the editor and applies it with one click: to the open mockup (the editor sets it), or to every design on the
template's photos in a run (``POST /mockup-placement-presets/{id}/apply``: each keeps its own look; the answer
holds Undo rows and the mockups to make again). "Add the usual three" makes three to start from, with our own
values (not a rule of any marketplace); the seller changes or deletes them. The seller's own; another account's
template, preset or run is 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import active_tenant, get_session
from app.api.mockup_edits import AdjustmentOut, _batch_designs, _run_photos, _store
from app.api.mockups import WRITTEN, _own_run, _own_template, _written
from app.db.models import MockupPlacementPreset, Tenant
from app.pipeline import design_edits
from app.workers import mockups as work

router = APIRouter(prefix="/api", tags=["mockups"])

#: "Add the usual three": starting points the seller can change (shares of the automatic fit; our own values).
USUAL = (
    ("Full front", {"scale": 1.0}),
    ("Chest print", {"scale": 0.72}),
    ("Left pocket", {"scale": 0.26, "dx": 0.26, "dy": 0.04}),  # the wearer's left: the viewer's right
)


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    data: dict[str, Any]


class PresetOut(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    name: str
    data: dict[str, Any]


class PresetApplyIn(BaseModel):
    run_id: uuid.UUID
    #: Only these designs; none = every design of the run.
    design_ids: list[uuid.UUID] | None = Field(None, max_length=2000)


class PresetApplyOut(BaseModel):
    changed: int
    undo: list[AdjustmentOut]
    #: (design, photo) pairs to make again.
    pairs: list[list[uuid.UUID]]


def _out(p: MockupPlacementPreset) -> PresetOut:
    return PresetOut(id=p.id, template_id=p.template_id, name=p.name, data=design_edits.placement_only(p.data))


def _placement(data: dict[str, Any]) -> dict[str, Any]:
    try:
        return design_edits.placement_only(data)
    except design_edits.EditError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/mockup-templates/{template_id}/placement-presets", response_model=list[PresetOut])
async def list_presets(
    template_id: uuid.UUID, session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
) -> list[PresetOut]:
    await _own_template(session, tenant, template_id)
    rows = (await session.execute(select(MockupPlacementPreset).where(MockupPlacementPreset.template_id == template_id)
                                  .order_by(MockupPlacementPreset.created_at, MockupPlacementPreset.name))).scalars()
    return [_out(p) for p in rows]


async def _add(session: AsyncSession, tenant: Tenant, template_id: uuid.UUID, name: str, data: dict[str, Any]) -> MockupPlacementPreset:
    count = await session.scalar(select(func.count()).select_from(MockupPlacementPreset)
                                 .where(MockupPlacementPreset.template_id == template_id)) or 0
    if count >= MockupPlacementPreset.MAX_PER_TEMPLATE:
        raise HTTPException(status_code=409, detail=f"A template keeps up to {MockupPlacementPreset.MAX_PER_TEMPLATE} presets.")
    existing = (await session.execute(select(MockupPlacementPreset).where(
        MockupPlacementPreset.template_id == template_id, func.lower(MockupPlacementPreset.name) == name.lower()))).scalar_one_or_none()
    if existing is not None:  # the same name: saved over (the seller's latest)
        existing.data = _placement(data)
        return existing
    preset = MockupPlacementPreset(tenant_id=tenant.id, template_id=template_id, name=name, data=_placement(data),
                                   created_at=datetime.now(timezone.utc))  # in the order they were added
    session.add(preset)
    return preset


@router.post("/mockup-templates/{template_id}/placement-presets", response_model=PresetOut, status_code=201)
async def save_preset(
    template_id: uuid.UUID, body: PresetIn, session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
) -> PresetOut:
    """Save a placement under a name (the same name again replaces it)."""
    await _own_template(session, tenant, template_id)
    preset = await _add(session, tenant, template_id, body.name.strip(), body.data)
    await session.commit()
    return _out(preset)


@router.post("/mockup-templates/{template_id}/placement-presets/usual", response_model=list[PresetOut], status_code=201)
async def add_usual(
    template_id: uuid.UUID, session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
) -> list[PresetOut]:
    """"Add the usual three": Full front, Chest print, Left pocket, to start from (names already there are kept)."""
    await _own_template(session, tenant, template_id)
    have = {n.lower() for n in (await session.execute(select(MockupPlacementPreset.name).where(
        MockupPlacementPreset.template_id == template_id))).scalars()}
    for name, data in USUAL:
        if name.lower() not in have:
            await _add(session, tenant, template_id, name, data)
    await session.commit()
    return await list_presets(template_id, session, tenant)


async def _own_preset(session: AsyncSession, tenant: Tenant, preset_id: uuid.UUID) -> MockupPlacementPreset:
    preset = await session.get(MockupPlacementPreset, preset_id)
    if preset is None or preset.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="preset not found")
    return preset


@router.delete("/mockup-placement-presets/{placement_id}", status_code=204)
async def delete_preset(
    placement_id: uuid.UUID, session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
) -> None:
    await session.delete(await _own_preset(session, tenant, placement_id))
    await session.commit()


@router.post("/mockup-placement-presets/{placement_id}/apply", response_model=PresetApplyOut)
async def apply_preset(
    placement_id: uuid.UUID, body: PresetApplyIn, session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
) -> PresetApplyOut:
    """The preset's placement on every design (or ``design_ids``) on the template's photos of the run, each keeping
    its own look. Undo rows hold each as it was (``/mockup-adjustments/restore``)."""
    preset = await _own_preset(session, tenant, placement_id)
    run = await _own_run(session, tenant, body.run_id)
    if await _written(session, run.batch_id):
        raise HTTPException(status_code=409, detail=WRITTEN)
    photos = [p for p in await _run_photos(session, run) if p.template_id == preset.template_id]
    designs = await _batch_designs(session, run.batch_id)
    if body.design_ids is not None:
        wanted = set(body.design_ids)
        if any(d not in {x.id for x in designs} for d in wanted):
            raise HTTPException(status_code=404, detail="design not found")
        designs = [d for d in designs if d.id in wanted]
    current, _ = await work.design_edits_for(session, [d.id for d in designs], [p.id for p in photos])
    placement = design_edits.placement_only(preset.data)
    undo: list[AdjustmentOut] = []
    pairs: list[list[uuid.UUID]] = []
    for design in designs:
        for photo in photos:
            for k in range(len(work.photo_print_areas(photo)) or 1):
                before = current.get((design.id, photo.id, k))
                undo.append(AdjustmentOut(design_id=design.id, photo_id=photo.id, area=k, data=before))
                await _store(session, tenant.id, design.id, photo.id, k, design_edits.with_placement(before, placement))
            pairs.append([design.id, photo.id])
    await session.commit()
    return PresetApplyOut(changed=len(undo), undo=undo, pairs=pairs)
