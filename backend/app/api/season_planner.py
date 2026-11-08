"""The seasonal launch planner: pick a holiday, batches and shops; see the spread; then save it.

``GET  /api/season-planner``          the holidays from today forward, with the window we suggest (our advice,
                                      pipeline/season_calendar.py), and the batches, shops and groups to choose from.
``POST /api/season-planner/preview``  what would happen, shop by shop and day by day: limits, slots already taken and
                                      what does not fit. Nothing is saved.
``POST /api/season-planner/save``     the same, saved, only if it is still what the preview showed.

Saving writes the ordinary plan and slots (one ``group_plan`` per batch, ``settings.mode`` "season"), so the Scheduled
page shows and edits them, ``workers/plans.py`` releases them, and going live re-checks the approval and the
compliance findings (workers/schedule.py). Only approved listings are planned (CLAUDE.md rule 3: ``send._work``).
Nothing here calls Etsy. Etsy request counts are never shown (A3). The caller's own batches, groups and shops only (404).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_quota, get_session, submit_draft
from app.api.distribution import _capacity
from app.api.scheduled import _items, _world
from app.api.send import DEFAULT_SCHEDULE, _plural, _Work, _work
from app.api.shops import shop_label
from app.core import audit, request_cost
from app.core.timezones import valid_zone
from app.db.models import GeneratedContent, GroupPlan, PlannedSlot, ShopGroup, Tenant, UploadBatch
from app.etsy.rate_limiter import DailyQuota
from app.etsy.shops import active_shops, group_shops
from app.pipeline import group_plan as G
from app.pipeline import examples, profile_shops
from app.pipeline import season_calendar as C
from app.pipeline import season_plan as P
from app.pipeline.batch_names import names_by_id

router = APIRouter(prefix="/api/season-planner", tags=["season-planner"])

MAX_WINDOW_DAYS = 366
CHANGED = "The schedule has changed since this preview, so nothing was saved. Look at the new one."
WHY_NOT = {
    P.NO_ROOM: "no room left in this shop's days in the window",
    P.NO_BUDGET: "your daily Etsy limit is used up on the days left in the window",
}


class SeasonOut(BaseModel):
    key: str
    name: str
    on: date
    opens: date
    closes: date
    first_left: date | None
    why: str


class BatchChoice(BaseModel):
    id: uuid.UUID
    name: str
    approved: int
    not_approved: int


class Named(BaseModel):
    id: uuid.UUID
    name: str


class GroupChoice(Named):
    shop_ids: list[uuid.UUID]


class Defaults(BaseModel):
    per_shop_per_day: int
    window_start: time
    window_end: time
    spacing_minutes: int
    stagger_minutes: int


class PlannerOut(BaseModel):
    today: date
    reviewed: date
    about: str
    seasons: list[SeasonOut]
    batches: list[BatchChoice]
    shops: list[Named]
    groups: list[GroupChoice]
    defaults: Defaults
    time_zone: str


class PlanIn(BaseModel):
    holiday: str
    batch_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    connection_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    group_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    #: The seller's own dates. Omitted: what is left of the window we suggest.
    first_day: date | None = None
    last_day: date | None = None
    per_shop_per_day: int = Field(default=int(DEFAULT_SCHEDULE["per_shop_per_day"]), ge=1, le=50)
    window_start: time = time.fromisoformat(str(DEFAULT_SCHEDULE["window_start"]))
    window_end: time = time.fromisoformat(str(DEFAULT_SCHEDULE["window_end"]))
    spacing_minutes: int = Field(default=int(DEFAULT_SCHEDULE["spacing_minutes"]), ge=1, le=24 * 60)
    stagger_minutes: int = Field(default=int(DEFAULT_SCHEDULE["stagger_minutes"]), ge=0, le=24 * 60)


class SaveIn(PlanIn):
    #: What the preview showed: a different plan is not saved.
    fingerprint: str


class DayOut(BaseModel):
    day: date
    new: int
    taken: int


class ShopRow(BaseModel):
    shop_id: uuid.UUID
    name: str
    total: int
    #: The most this shop takes in a day: its own limit or this plan's "a day".
    cap: int
    #: Its own daily limit, across every schedule; None: only this plan's.
    limit: int | None
    moved: int
    taken: int
    days: list[DayOut]


class NotFitting(BaseModel):
    title: str
    shop_name: str
    reason: str


class LeftOut(BaseModel):
    title: str
    batch: str
    reason: str


class PreviewOut(BaseModel):
    holiday: str
    first_day: date | None
    last_day: date | None
    days: list[date]
    time_zone: str
    listings: int
    shops: int
    total: int
    fingerprint: str | None
    sentence: str
    button: str
    #: Why it cannot be saved; None: it can.
    disabled: str | None
    rows: list[ShopRow]
    conflicts: list[str]
    did_not_fit: list[NotFitting]
    left_out: list[LeftOut]


class SavedOut(BaseModel):
    message: str
    plan_ids: list[uuid.UUID]
    scheduled: int


def _zone(tenant: Tenant) -> str:
    return tenant.time_zone if valid_zone(tenant.time_zone) else "UTC"


def _today(zone: str) -> date:
    return datetime.now(timezone.utc).astimezone(ZoneInfo(zone)).date()


def _short(d: date) -> str:
    return f"{d:%b} {d.day}"


@router.get("", response_model=PlannerOut)
async def planner(session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant)) -> PlannerOut:
    zone = _zone(tenant)
    today = _today(zone)
    counts = await session.execute(
        select(GeneratedContent.batch_id, GeneratedContent.approved, func.count())
        .join(UploadBatch, UploadBatch.id == GeneratedContent.batch_id)
        .where(GeneratedContent.tenant_id == tenant.id, UploadBatch.deleted_at.is_(None))
        .group_by(GeneratedContent.batch_id, GeneratedContent.approved))
    tally: dict[uuid.UUID, list[int]] = {}
    for batch_id, approved, count in counts.all():
        tally.setdefault(batch_id, [0, 0])[0 if approved else 1] += count
    names = await names_by_id(session, list(tally))
    shops = await active_shops(session, tenant.id)
    by_group = await group_shops(session, tenant.id, shops)
    groups = (await session.execute(select(ShopGroup).where(ShopGroup.tenant_id == tenant.id).order_by(ShopGroup.position, ShopGroup.name))).scalars()
    d = DEFAULT_SCHEDULE
    return PlannerOut(
        today=today, reviewed=C.REVIEWED, about=C.ABOUT,
        seasons=[SeasonOut(key=s.holiday.key, name=s.holiday.name, on=s.on, opens=s.opens, closes=s.closes, first_left=s.first_left, why=s.holiday.why)
                 for s in C.calendar(today)],
        batches=sorted((BatchChoice(id=b, name=names.get(b) or "Batch", approved=n[0], not_approved=n[1]) for b, n in tally.items()),
                       key=lambda b: (-b.approved, b.name)),
        shops=[Named(id=s.id, name=shop_label(s)) for s in shops],
        groups=[GroupChoice(id=g.id, name=g.name, shop_ids=[s.id for s in by_group.get(g.id, [])]) for g in groups],
        defaults=Defaults(per_shop_per_day=int(d["per_shop_per_day"]), window_start=time.fromisoformat(str(d["window_start"])),
                          window_end=time.fromisoformat(str(d["window_end"])), spacing_minutes=int(d["spacing_minutes"]),
                          stagger_minutes=int(d["stagger_minutes"])),
        time_zone=zone,
    )


async def _plan(session: AsyncSession, tenant: Tenant, quota: DailyQuota, body: PlanIn) -> tuple[PreviewOut, P.SeasonPlan | None, list[_Work], dict[uuid.UUID, UploadBatch]]:
    zone = _zone(tenant)
    today = _today(zone)
    batches = {b.id: b for b in (await session.execute(select(UploadBatch).where(
        UploadBatch.tenant_id == tenant.id, UploadBatch.id.in_(body.batch_ids), UploadBatch.deleted_at.is_(None)))).scalars()}
    if len(batches) != len(set(body.batch_ids)):
        raise HTTPException(status_code=404, detail="batch not found")
    connected = await active_shops(session, tenant.id)
    ours = {s.id: s for s in connected}
    if any(c not in ours for c in body.connection_ids):
        raise HTTPException(status_code=404, detail="shop not found")
    by_group = await group_shops(session, tenant.id, connected)
    group_rows = (await session.execute(select(ShopGroup.id).where(ShopGroup.tenant_id == tenant.id, ShopGroup.id.in_(body.group_ids)))).scalars().all() if body.group_ids else []
    if len(set(group_rows)) != len(set(body.group_ids)):
        raise HTTPException(status_code=404, detail="group not found")
    holiday = C.BY_KEY.get(body.holiday)
    if holiday is None:
        raise HTTPException(status_code=422, detail="Choose a holiday from the calendar.")
    wanted: dict[uuid.UUID, None] = dict.fromkeys(body.connection_ids)
    for gid in body.group_ids:
        wanted.update(dict.fromkeys(s.id for s in by_group.get(gid, [])))
    shop_ids = [s.id for s in connected if s.id in wanted]

    season = C.season_of(holiday, today)
    first = max(body.first_day, today) if body.first_day else season.first_left
    last = body.last_day or season.closes
    disabled: str | None = None
    conflicts: list[str] = []
    if first is None:
        disabled = f"The window we suggest for {holiday.name} closed on {_short(season.closes)}. Choose your own dates."
    elif last < first:
        disabled = "The last day must be on or after the first day, and today at the earliest."
    elif (last - first).days >= MAX_WINDOW_DAYS:
        disabled = f"Choose a window of at most {MAX_WINDOW_DAYS} days."
    if last > season.on and body.last_day:
        conflicts.append(f"Your last day, {_short(last)}, is after {holiday.name} ({_short(season.on)}).")

    works: list[_Work] = []
    left_out: list[LeftOut] = []
    listings: list[G.Listing] = []
    seen: set[uuid.UUID] = set()
    already = cannot = 0
    if shop_ids:
        names = await names_by_id(session, list(batches))
        for batch_id in body.batch_ids:
            if batch_id in seen:
                continue
            seen.add(batch_id)
            work = await _work(session, tenant, batches[batch_id], "spread", zone, only_shops=shop_ids)
            works.append(work)
            left_out += [LeftOut(title=x.title, batch=names.get(batch_id) or "Batch", reason=x.reason) for x in work.left_out]
            to_do = {(c.id, s.id) for c, s, _ in work.actions}
            for row in work.rows:
                already += sum(1 for cell in row.cells if cell.state in ("draft", "live", "scheduled") and (row.content_id, cell.shop_id) not in to_do)
                for cell in row.cells:
                    if cell.state == "cannot":
                        cannot += 1
                        left_out.append(LeftOut(title=row.title, batch=names.get(batch_id) or "Batch", reason=f"{cell.shop_name}: {cell.reason}"))
                skip = {s: "already there" for s in shop_ids if (row.content_id, s) not in to_do}
                listings.append(G.Listing(content_id=row.content_id, title=row.title, group_id="season", skip=skip))
    plan: P.SeasonPlan | None = None
    days = P.Window(first, last).days if first is not None and disabled is None else []
    rows: list[ShopRow] = []
    not_fitting: list[NotFitting] = []
    if not shop_ids:
        disabled = disabled or "Choose where these go: a shop group or a shop."
    elif not any(work.actions for work in works):
        if already and disabled is None:
            disabled = "Nothing new to schedule: these listings are already in these shops."
        elif disabled is None:
            disabled = "Nothing can be scheduled: only approved listings can be, and the list below says why."
    if disabled is None and first is not None:
        world = await _world(session, tenant)
        # The rules every schedule keeps: never closer than a chosen shop's own minutes between.
        spacing = max([body.spacing_minutes, *(world.rules.spacing.get(s, 1) for s in shop_ids)])
        if spacing != body.spacing_minutes:
            conflicts.append(f"{spacing} minutes between listings, not {body.spacing_minutes}: a chosen shop's group asks for that.")
        settings = G.Settings(start=first, per_shop_per_day=body.per_shop_per_day, window_start=body.window_start, window_end=body.window_end,
                              spacing_minutes=spacing, stagger_minutes=body.stagger_minutes, zone=zone)
        plan_shops = [G.Shop(id=s, name=shop_label(ours[s]), group_id="season", daily=world.rules.daily.get(s)) for s in shop_ids]
        existing = [(i.shop_id, i.at) for i in _items(world)]
        now = datetime.now(timezone.utc)
        per_draft = (await request_cost.draft_estimate(session)).per_draft
        capacity, _ = await _capacity(session, tenant, quota, now, per_draft)
        try:
            plan = P.spread(P.Window(first, last), settings, listings, plan_shops, taken=P.local_minutes(existing, zone), capacity=capacity, now=now, draft_requests=per_draft)
        except G.PlanRefused as exc:
            disabled = str(exc)[:1].upper() + str(exc)[1:] + "."
    if plan is not None:
        limits = {s: world.rules.daily.get(s) for s in shop_ids}
        for shop in plan.shops:
            on_day = [DayOut(day=d, new=shop.new.get(d, 0), taken=shop.taken.get(d, 0)) for d in days]
            rows.append(ShopRow(shop_id=shop.shop_id, name=shop.name, total=shop.total, cap=shop.cap, limit=limits.get(shop.shop_id), moved=shop.moved,
                                taken=sum(shop.taken.values()), days=on_day))
            taken = sum(shop.taken.values())
            if taken:
                conflicts.append(f"{shop.name} already has {_plural(taken, 'listing')} going live in this window. They keep their times, and these go around them.")
            if shop.moved:
                conflicts.append(f"{_plural(shop.moved, 'listing')} in {shop.name} moved to a nearby day because its own day was full.")
        if plan.fits_per_day < body.per_shop_per_day:
            conflicts.append(f"The daily hours fit {plan.fits_per_day} a day per shop with these minutes between, not {body.per_shop_per_day}.")
        not_fitting = [NotFitting(title=m.title, shop_name=m.shop_name, reason=WHY_NOT[m.why]) for m in plan.did_not_fit]
        if not_fitting:
            conflicts.append(f"{_plural(len(not_fitting), 'listing')} will not fit in this window. Allow more a day, or longer hours or dates.")
        if not plan.slots and disabled is None:
            disabled = "None of these fit in this window. Allow more a day, or longer hours or dates."
    if already:
        conflicts.append(f"{already} already there (a draft, live or scheduled): not sent again.")
    total = len(plan.slots) if plan else 0
    count = len({s.content_id for s in plan.slots}) if plan else 0
    used = len({s.shop_id for s in plan.slots}) if plan else 0
    span = ""
    if plan and plan.slots:
        a, b = min(s.local_day for s in plan.slots), max(s.local_day for s in plan.slots)
        span = f", {_short(a)}" + (f" – {_short(b)}" if a != b else "")
    sentence = f"{_plural(count, 'listing')} → {_plural(used, 'shop')}: {_plural(total, 'go-live', 'go-lives')}{span}."
    return PreviewOut(
        holiday=holiday.key, first_day=first, last_day=last, days=days, time_zone=zone, listings=count, shops=used, total=total,
        fingerprint=plan.fingerprint() if plan else None, sentence=sentence, button=f"Schedule {_plural(total, 'go-live', 'go-lives')}",
        disabled=disabled, rows=rows, conflicts=conflicts, did_not_fit=not_fitting, left_out=left_out,
    ), plan, works, batches


@router.post("/preview", response_model=PreviewOut)
async def preview(body: PlanIn, session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
                  quota: DailyQuota = Depends(get_quota)) -> PreviewOut:
    out, _, _, _ = await _plan(session, tenant, quota, body)
    await examples.refuse(session, list(body.batch_ids))  # the example designs never reach Etsy
    return out


@router.post("/save", response_model=SavedOut)
async def save(body: SaveIn, session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant),
               quota: DailyQuota = Depends(get_quota), enqueuer: Enqueuer = Depends(get_enqueuer)) -> SavedOut:
    out, plan, works, batches = await _plan(session, tenant, quota, body)  # the batches are the account's
    await examples.refuse(session, list(body.batch_ids))  # the example designs never reach Etsy
    if out.disabled or plan is None:
        raise HTTPException(status_code=409, detail=out.disabled or CHANGED)
    if out.fingerprint != body.fingerprint:
        raise HTTPException(status_code=409, detail=CHANGED)
    by_content = {c.id: c for work in works for c, _, _ in work.actions}
    per_batch: dict[uuid.UUID, list[G.Slot]] = {}
    for slot in plan.slots:
        per_batch.setdefault(by_content[slot.content_id].batch_id, []).append(slot)
    settings = {
        "mode": "season", "holiday": out.holiday, "first_day": out.first_day.isoformat() if out.first_day else None,
        "last_day": out.last_day.isoformat() if out.last_day else None, "per_shop_per_day": body.per_shop_per_day,
        "window_start": body.window_start.strftime("%H:%M"), "window_end": body.window_end.strftime("%H:%M"),
        "spacing_minutes": body.spacing_minutes, "stagger_minutes": body.stagger_minutes, "time_zone": out.time_zone,
    }
    plan_ids: list[uuid.UUID] = []
    for batch_id, slots in per_batch.items():
        row = GroupPlan(tenant_id=tenant.id, batch_id=batch_id, settings=settings)
        session.add(row)
        await session.flush()
        plan_ids.append(row.id)
        for slot in slots:
            session.add(PlannedSlot(tenant_id=tenant.id, plan_id=row.id, content_id=slot.content_id, connection_id=slot.shop_id,
                                    draft_at=slot.draft_at, publish_at=slot.publish_at))
    from app.pipeline import funnel

    await funnel.record(session, tenant.id, funnel.FIRST_SENT)
    audit.record(session, "schedule.season_planned", actor=tenant, target_tenant_id=tenant.id, holiday=out.holiday, slots=len(plan.slots), batches=len(per_batch))
    setups: dict[uuid.UUID, tuple] = {}
    for work in works:
        for profile_id, (profile, shops) in work.setups.items():
            entry = setups.setdefault(profile_id, (profile, []))
            entry[1].extend(s for s in shops if s not in entry[1])
    started = [profile for profile, shops in setups.values() if await profile_shops.start_links(session, profile, shops)]
    await session.commit()
    for profile in started:
        await enqueuer.enqueue("link_profile", str(profile.id))
    from app.workers.plans import release_due

    await release_due(session, enqueuer.enqueue, tenant_id=tenant.id, submit=lambda t, j: submit_draft(enqueuer, t, j))
    return SavedOut(message=f"{_plural(len(plan.slots), 'go-live', 'go-lives')} scheduled for {C.BY_KEY[out.holiday].name}. They are on the Scheduled page, where you can move them.",
                    plan_ids=plan_ids, scheduled=len(plan.slots))

