"""Group schedules: the helpers "Where and when" uses, and the confirmed plans (v8 §B, B4).

Sending a batch's listings (to groups and shops, now or spread out) is api/send.py: every
approved listing goes to every chosen shop, each shop once; nothing is split between groups
(the owner, 2026-10-22). The old "distribution" endpoints (assign one group, "Split evenly",
plan, confirm) are gone with the panel that used them.

``GET /api/plans`` / ``DELETE /api/plans/{id}``  confirmed plans; cancelling
    withdraws what has not happened yet (drafts already made stay drafts).

Only approved listings are planned, and going live re-checks the approval and
compliance at its time (CLAUDE.md rule 3: the seller approved each listing and
chose its time). The caller's own plans only (404).
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime, time, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import active_tenant, get_session
from app.compliance.check import listing_problem
from app.core import audit, limits
from app.db.models import (
    Asset,
    GeneratedContent,
    GroupPlan,
    ListingPublication,
    PlannedSlot,
    Tenant,
    UploadBatch,
)
from app.etsy.rate_limiter import DailyQuota
from app.pipeline import group_plan as G
from app.pipeline.batch_names import names_by_id

router = APIRouter(prefix="/api", tags=["distribution"])


class ScheduleIn(BaseModel):
    start_date: date
    per_shop_per_day: int = Field(ge=1, le=50)
    window_start: time
    window_end: time
    spacing_minutes: int = Field(ge=1, le=24 * 60)
    stagger_minutes: int = Field(ge=0, le=24 * 60)


class PlanSummary(BaseModel):
    id: uuid.UUID
    batch_id: uuid.UUID | None
    batch_name: str | None
    created_at: datetime
    cancelled: bool
    counts: dict[str, int]
    first_publish: datetime | None
    last_publish: datetime | None


# --- helpers --------------------------------------------------------------------------------------


async def _batch(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID) -> UploadBatch:
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    return batch


async def _contents(session: AsyncSession, tenant: Tenant, batch: UploadBatch, ids: list[uuid.UUID] | None) -> list[GeneratedContent]:
    query = select(GeneratedContent).where(GeneratedContent.batch_id == batch.id, GeneratedContent.tenant_id == tenant.id)
    if ids:
        query = query.where(GeneratedContent.id.in_(ids))
    rows = list((await session.execute(query)).scalars())
    if ids and len(rows) != len(set(ids)):
        raise HTTPException(status_code=404, detail="listing not found")
    assets = {a.id: a for a in (await session.execute(
        select(Asset).where(Asset.id.in_([c.asset_id for c in rows])))).scalars()} if rows else {}
    order = {c.id: (assets[c.asset_id].group_key or "", assets[c.asset_id].original_filename) if c.asset_id in assets else ("", "")
             for c in rows}
    rows.sort(key=lambda c: order[c.id])
    return rows


async def _why_not(session: AsyncSession, content: GeneratedContent) -> str | None:
    if not content.approved:
        return "not approved yet"
    asset = await session.get(Asset, content.asset_id)
    if asset is not None and asset.files_removed_at is not None:
        return "its image files were deleted after the retention period"
    return await listing_problem(session, content)


async def _capacity(session: AsyncSession, tenant: Tenant, quota: DailyQuota, now: datetime, per_draft: int) -> Any:
    """Per UTC day: what this plan may spend. The account's ceiling, never more
    than the app's pause point, less what is spent today and what is reserved
    already (other plans' slots, go-lives scheduled by hand)."""
    ceiling = limits.ceiling_limit(tenant)
    cap = min(ceiling, quota.pause_at)
    used_today, _ = await quota.usage(tenant.id)
    reserved: Counter[date] = Counter()
    for slot in (await session.execute(select(PlannedSlot).where(
        PlannedSlot.tenant_id == tenant.id, PlannedSlot.state.in_(("waiting", "drafting"))
    ))).scalars():
        draft_at = slot.draft_at if slot.draft_at.tzinfo else slot.draft_at.replace(tzinfo=timezone.utc)
        publish_at = slot.publish_at if slot.publish_at.tzinfo else slot.publish_at.replace(tzinfo=timezone.utc)
        if slot.state == "waiting":
            reserved[draft_at.date()] += per_draft
        reserved[publish_at.date()] += G.PUBLISH_REQUESTS
    for when in (await session.execute(select(ListingPublication.scheduled_for).where(
        ListingPublication.tenant_id == tenant.id, ListingPublication.scheduled_for.is_not(None),
        ListingPublication.state != "active", ListingPublication.schedule_job_id.is_(None),
    ))).scalars():
        when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        reserved[when.date()] += G.PUBLISH_REQUESTS
    today = now.date()

    def capacity(day: date) -> int:
        spent = used_today if day == today else 0
        return cap - spent - reserved.get(day, 0)

    return capacity, ceiling


# --- endpoints -----------------------------------------------------------------------------------


async def _summary(session: AsyncSession, plan: GroupPlan, batch_name: str | None) -> PlanSummary:
    slots = list((await session.execute(select(PlannedSlot).where(PlannedSlot.plan_id == plan.id))).scalars())
    publishes = [x.publish_at for x in slots]
    return PlanSummary(id=plan.id, batch_id=plan.batch_id, batch_name=batch_name, created_at=plan.created_at,
                       cancelled=plan.cancelled_at is not None, counts=dict(Counter(x.state for x in slots)),
                       first_publish=min(publishes, default=None), last_publish=max(publishes, default=None))


@router.get("/plans", response_model=list[PlanSummary])
async def list_plans(session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)) -> list[PlanSummary]:
    plans = list((await session.execute(
        select(GroupPlan).where(GroupPlan.tenant_id == tenant.id).order_by(GroupPlan.created_at.desc()).limit(50)
    )).scalars())
    names = await names_by_id(session, [p.batch_id for p in plans if p.batch_id])
    return [await _summary(session, p, names.get(p.batch_id) if p.batch_id else None) for p in plans]


@router.delete("/plans/{plan_id}", response_model=PlanSummary)
async def cancel_plan(plan_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)) -> PlanSummary:
    """Withdraw what has not happened yet: drafts not started are not made, and
    go-lives not yet released are withdrawn. Drafts already made stay drafts."""
    from app.etsy.publisher import publication_for
    from app.etsy.scheduling import ScheduleBusy, withdraw
    from app.db.models import Job, JobStatus

    plan = await session.get(GroupPlan, plan_id)
    if plan is None or plan.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="plan not found")
    now = datetime.now(timezone.utc)
    withdrawn = 0
    for slot in (await session.execute(select(PlannedSlot).where(
        PlannedSlot.plan_id == plan.id, PlannedSlot.state.in_(("waiting", "drafting", "scheduled"))
    ))).scalars():
        if slot.state == "drafting" and slot.job_id:
            job = await session.get(Job, slot.job_id)
            if job is not None and job.status is JobStatus.queued:
                job.status, job.finished_at, job.last_error = JobStatus.cancelled, now, "cancelled: the group schedule was cancelled"
        if slot.state == "scheduled" and slot.content_id:
            publication = await publication_for(session, slot.content_id, slot.connection_id)
            if publication is not None and publication.scheduled_for is not None and publication.state != "active":
                try:
                    await withdraw(session, publication, actor=tenant, reason="group schedule cancelled")
                except ScheduleBusy:
                    continue  # going live right now
        slot.state, slot.note = "cancelled", "the group schedule was cancelled"
        withdrawn += 1
    plan.cancelled_at = now
    audit.destructive(session, "plan.cancelled", actor=tenant, tenant_id=tenant.id, shop_id=None, object_id=plan.id,
                      slots=withdrawn)
    await session.commit()
    names = await names_by_id(session, [plan.batch_id] if plan.batch_id else [])
    return await _summary(session, plan, names.get(plan.batch_id) if plan.batch_id else None)
