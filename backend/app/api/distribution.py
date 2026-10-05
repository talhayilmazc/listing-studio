"""Distribution and group scheduling on the review page (v8 §B).

``POST /api/batches/{id}/distribution/preview``  which listing goes to which group:
    assign the chosen listings to one group, or "Split evenly" the approved
    listings across the chosen groups in order. Every shop of a group gets the
    group's listings; a listing goes to no other group. A design sent to another
    group before is warned about, never blocked.
``POST /api/batches/{id}/distribution/plan``     the schedule for those listings,
    before anything is queued: a calendar per shop, Etsy requests per day
    against the account's daily ceiling, and the day it finishes.
``POST /api/batches/{id}/distribution/confirm``  the same, confirmed: the design ->
    group record, the plan and its slots. A cron makes each draft at its time
    and schedules its go-live for the confirmed time (workers/plans.py).
``GET /api/plans`` / ``DELETE /api/plans/{id}``  confirmed plans; cancelling
    withdraws what has not happened yet (drafts already made stay drafts).

Only approved listings are planned, and going live re-checks the approval and
compliance at its time (CLAUDE.md rule 3: the seller approved each listing and
chose its time). The caller's own batches, groups and plans only (404).
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_quota, get_session
from app.api.shops import shop_label
from app.compliance.check import listing_problem
from app.core import audit, limits
from app.core.timezones import valid_zone, zone_abbreviation
from app.db.models import (
    Asset,
    DesignDistribution,
    EtsyConnection,
    GeneratedContent,
    GroupPlan,
    ListingPublication,
    PlannedSlot,
    ShopGroup,
    Tenant,
    UploadBatch,
)
from app.etsy.rate_limiter import DailyQuota
from app.etsy.shops import active_shops
from app.pipeline import group_plan as G
from app.pipeline.batch_names import names_by_id
from app.pipeline.targets import resolve_target

router = APIRouter(prefix="/api", tags=["distribution"])


class DistributionIn(BaseModel):
    mode: Literal["assign", "split"]
    group_ids: list[uuid.UUID] = Field(min_length=1)
    #: "assign": the listings to send (all to the one group chosen). "split": none = every approved listing.
    content_ids: list[uuid.UUID] = Field(default_factory=list)


class Assignment(BaseModel):
    content_id: uuid.UUID
    group_id: uuid.UUID


class ScheduleIn(BaseModel):
    start_date: date
    per_shop_per_day: int = Field(ge=1, le=50)
    window_start: time
    window_end: time
    spacing_minutes: int = Field(ge=1, le=24 * 60)
    stagger_minutes: int = Field(ge=0, le=24 * 60)


class PlanIn(BaseModel):
    assignments: list[Assignment] = Field(min_length=1)
    schedule: ScheduleIn


class ShopCell(BaseModel):
    shop_id: uuid.UUID
    shop_name: str
    ok: bool
    reason: str | None = None


class DistributionRow(BaseModel):
    content_id: uuid.UUID
    title: str
    sku: str | None
    group_id: uuid.UUID
    group_name: str
    shops: list[ShopCell]
    #: Sent to another group before (a warning, never a block).
    warning: str | None = None


class DistributionOut(BaseModel):
    rows: list[DistributionRow]
    #: Listings not distributed and why (not approved, compliance...).
    left_out: list[dict[str, str]] = Field(default_factory=list)
    per_group: dict[str, int] = Field(default_factory=dict)


class SlotOut(BaseModel):
    time: str  # the seller's wall clock, "HH:MM"
    zone: str  # "CDT"
    content_id: uuid.UUID
    title: str


class DayOut(BaseModel):
    date: date
    slots: list[SlotOut]


class ShopCalendar(BaseModel):
    shop_id: uuid.UUID
    shop_name: str
    group_name: str
    listings: int
    days: list[DayOut]


class BudgetDay(BaseModel):
    date: date  # UTC day
    requests: int
    capacity: int  # what the account may spend that day, less what is reserved already


class PlanOut(BaseModel):
    shops: list[ShopCalendar]
    budget: list[BudgetDay]
    ceiling: int
    finishes_on: date | None
    drafts: int
    skipped: list[dict[str, str]]
    notes: list[str]
    time_zone: str
    requests_per_draft: int = G.DRAFT_REQUESTS
    requests_per_publish: int = G.PUBLISH_REQUESTS


class ConfirmOut(BaseModel):
    plan_id: uuid.UUID
    plan: PlanOut


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


async def _groups(session: AsyncSession, tenant: Tenant, ids: list[uuid.UUID]) -> dict[uuid.UUID, tuple[ShopGroup, list[EtsyConnection]]]:
    shops = await active_shops(session, tenant.id)
    out: dict[uuid.UUID, tuple[ShopGroup, list[EtsyConnection]]] = {}
    for gid in dict.fromkeys(ids):
        group = await session.get(ShopGroup, gid)
        if group is None or group.tenant_id != tenant.id:
            raise HTTPException(status_code=404, detail="group not found")
        members = [c for c in shops if c.group_id == gid]
        if not members:
            raise HTTPException(status_code=422, detail=f'group "{group.name}" has no connected shops')
        out[gid] = (group, members)
    return out


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


async def _sku(session: AsyncSession, content: GeneratedContent) -> str | None:
    asset = await session.get(Asset, content.asset_id)
    return asset.parsed_sku if asset is not None else None


async def _shop_cell(session: AsyncSession, content: GeneratedContent, shop: EtsyConnection) -> ShopCell:
    existing = await session.scalar(select(func.count()).select_from(ListingPublication).where(
        ListingPublication.content_id == content.id, ListingPublication.connection_id == shop.id))
    if existing:
        return ShopCell(shop_id=shop.id, shop_name=shop_label(shop), ok=False, reason="it already has a draft there")
    target = await resolve_target(session, content, shop)
    if not target.ok and not target.stale:
        return ShopCell(shop_id=shop.id, shop_name=shop_label(shop), ok=False, reason=target.reason)
    return ShopCell(shop_id=shop.id, shop_name=shop_label(shop), ok=True)


async def _warning(session: AsyncSession, tenant: Tenant, content: GeneratedContent, sku: str | None, group_id: uuid.UUID) -> str | None:
    """Sent to another group before: by this listing, or the same design (SKU) in another batch."""
    query = select(DesignDistribution).where(DesignDistribution.tenant_id == tenant.id)
    query = query.where((DesignDistribution.content_id == content.id) | (DesignDistribution.sku == sku)) if sku else \
        query.where(DesignDistribution.content_id == content.id)
    earlier = [d for d in (await session.execute(query.order_by(DesignDistribution.created_at))).scalars()
               if d.group_id != group_id]
    if not earlier:
        return None
    names = ", ".join(dict.fromkeys(d.group_name for d in earlier))
    return f"this design was sent to {names} before; it will now be in this group's shops too"


async def _rows(session: AsyncSession, tenant: Tenant, batch: UploadBatch, assignments: dict[uuid.UUID, uuid.UUID]) -> tuple[list[DistributionRow], dict[uuid.UUID, tuple[ShopGroup, list[EtsyConnection]]]]:
    groups = await _groups(session, tenant, list(assignments.values()))
    contents = {c.id: c for c in await _contents(session, tenant, batch, list(assignments))}
    rows: list[DistributionRow] = []
    for cid, gid in assignments.items():
        content = contents[cid]
        why = await _why_not(session, content)
        if why:
            raise HTTPException(status_code=422, detail=f'"{content.title or "a listing"}": {why}')
        group, members = groups[gid]
        sku = await _sku(session, content)
        rows.append(DistributionRow(
            content_id=cid, title=content.title or "", sku=sku, group_id=gid, group_name=group.name,
            shops=[await _shop_cell(session, content, shop) for shop in members],
            warning=await _warning(session, tenant, content, sku, gid),
        ))
    return rows, groups


async def _capacity(session: AsyncSession, tenant: Tenant, quota: DailyQuota, now: datetime) -> Any:
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
            reserved[draft_at.date()] += G.DRAFT_REQUESTS
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


async def _plan(session: AsyncSession, tenant: Tenant, batch: UploadBatch, body: PlanIn, quota: DailyQuota) -> tuple[G.Plan, list[DistributionRow], dict, PlanOut]:
    zone = tenant.time_zone if valid_zone(tenant.time_zone) else "UTC"
    assignments = {a.content_id: a.group_id for a in body.assignments}
    rows, groups = await _rows(session, tenant, batch, assignments)
    shops: list[G.Shop] = []
    for gid, (group, members) in groups.items():
        shops += [G.Shop(id=c.id, name=shop_label(c), group_id=gid) for c in members]
    listings = [
        G.Listing(content_id=r.content_id, title=r.title, group_id=r.group_id,
                  skip={c.shop_id: c.reason or "cannot go there" for c in r.shops if not c.ok})
        for r in rows
    ]
    now = datetime.now(timezone.utc)
    capacity, ceiling = await _capacity(session, tenant, quota, now)
    s = body.schedule
    settings = G.Settings(start=s.start_date, per_shop_per_day=s.per_shop_per_day, window_start=s.window_start,
                          window_end=s.window_end, spacing_minutes=s.spacing_minutes, stagger_minutes=s.stagger_minutes,
                          zone=zone)
    try:
        result = G.plan(settings, listings, shops, capacity=capacity, now=now)
    except G.PlanRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    names = {s.id: s.name for s in shops}
    group_name = {gid: g.name for gid, (g, _) in groups.items()}
    calendars: list[ShopCalendar] = []
    for shop in shops:
        mine = [x for x in result.slots if x.shop_id == shop.id]
        by_day: dict[date, list[SlotOut]] = defaultdict(list)
        for x in mine:
            by_day[x.local_day].append(SlotOut(time=x.local_time, zone=zone_abbreviation(x.publish_at, zone),
                                               content_id=x.content_id, title=x.title))
        calendars.append(ShopCalendar(shop_id=shop.id, shop_name=shop.name, group_name=group_name[shop.group_id],
                                      listings=len(mine), days=[DayOut(date=d, slots=v) for d, v in sorted(by_day.items())]))
    out = PlanOut(
        shops=calendars,
        budget=[BudgetDay(date=d, requests=n, capacity=result.capacity[d]) for d, n in result.requests.items()],
        ceiling=ceiling, finishes_on=result.finishes_on, drafts=len(result.slots),
        skipped=[{"content_id": str(c), "shop": names.get(sid, ""), "reason": why} for c, sid, why in result.skipped],
        notes=result.notes, time_zone=zone,
    )
    return result, rows, groups, out


# --- endpoints -----------------------------------------------------------------------------------


@router.post("/batches/{batch_id}/distribution/preview", response_model=DistributionOut)
async def preview_distribution(
    batch_id: uuid.UUID,
    body: DistributionIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> DistributionOut:
    batch = await _batch(session, tenant, batch_id)
    await _groups(session, tenant, body.group_ids)
    left_out: list[dict[str, str]] = []
    if body.mode == "assign":
        if len(body.group_ids) != 1:
            raise HTTPException(status_code=422, detail="choose one group to send the selected listings to")
        if not body.content_ids:
            raise HTTPException(status_code=422, detail="choose the listings to send")
        chosen = await _contents(session, tenant, batch, body.content_ids)
        assignments = {c.id: body.group_ids[0] for c in chosen}
    else:
        approved: list[uuid.UUID] = []
        for content in await _contents(session, tenant, batch, body.content_ids or None):
            why = await _why_not(session, content)
            if why:
                left_out.append({"content_id": str(content.id), "title": content.title or "", "reason": why})
            else:
                approved.append(content.id)
        try:
            assignments = G.split_evenly(approved, list(dict.fromkeys(body.group_ids)))
        except G.PlanRefused as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    if not assignments:
        raise HTTPException(status_code=422, detail="no approved listing to distribute")
    rows, _ = await _rows(session, tenant, batch, assignments)
    return DistributionOut(rows=rows, left_out=left_out, per_group=dict(Counter(r.group_name for r in rows)))


@router.post("/batches/{batch_id}/distribution/plan", response_model=PlanOut)
async def plan_distribution(
    batch_id: uuid.UUID,
    body: PlanIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    quota: DailyQuota = Depends(get_quota),
) -> PlanOut:
    """The schedule before anything is queued."""
    batch = await _batch(session, tenant, batch_id)
    _, _, _, out = await _plan(session, tenant, batch, body, quota)
    return out


@router.post("/batches/{batch_id}/distribution/confirm", response_model=ConfirmOut)
async def confirm_distribution(
    batch_id: uuid.UUID,
    body: PlanIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    quota: DailyQuota = Depends(get_quota),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> ConfirmOut:
    batch = await _batch(session, tenant, batch_id)
    result, rows, groups, out = await _plan(session, tenant, batch, body, quota)
    if not result.slots:
        raise HTTPException(status_code=422, detail="nothing to schedule: no listing can go to any shop of its group")
    for row in rows:
        session.add(DesignDistribution(tenant_id=tenant.id, content_id=row.content_id, sku=row.sku,
                                       group_id=row.group_id, group_name=row.group_name))
    s = body.schedule
    plan = GroupPlan(tenant_id=tenant.id, batch_id=batch.id, settings={
        "start_date": s.start_date.isoformat(), "per_shop_per_day": s.per_shop_per_day,
        "window_start": s.window_start.strftime("%H:%M"), "window_end": s.window_end.strftime("%H:%M"),
        "spacing_minutes": s.spacing_minutes, "stagger_minutes": s.stagger_minutes, "time_zone": out.time_zone,
        "groups": {str(gid): g.name for gid, (g, _) in groups.items()},
    })
    session.add(plan)
    await session.flush()
    for slot in result.slots:
        session.add(PlannedSlot(tenant_id=tenant.id, plan_id=plan.id, content_id=slot.content_id,
                                connection_id=slot.shop_id, draft_at=slot.draft_at, publish_at=slot.publish_at))
    await session.commit()
    # Drafts due now go at once; the rest when their time comes (the cron).
    from app.workers.plans import release_due

    await release_due(session, enqueuer.enqueue, tenant_id=tenant.id)
    return ConfirmOut(plan_id=plan.id, plan=out)


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
