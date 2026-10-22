"""The Scheduled page (B8): every scheduled go-live of the account, edited one by one or in bulk.

``GET  /api/scheduled``                         every upcoming go-live (and the last week's
                                                outcomes), the groups with their settings and
                                                pause, the shops and the profiles to filter by.
``POST /api/scheduled/preview``                 old -> new for an edit, nothing saved.
``POST /api/scheduled/apply``                   the same, saved; returns what undoes it.
``POST /api/scheduled/restore``                 "Undo": the times (and removals) as they were.
``PUT  /api/scheduled/paused``                  "Pause all" / "Resume all".
``PUT  /api/scheduled/groups/{id}``             a group's pause, or its settings.
``POST /api/scheduled/groups/{id}/reflow``      the group's settings applied to its shops'
                                                upcoming listings (``apply``: saved).

A go-live is either a planned slot whose draft is made two hours before (``planned_slot``) or a
draft whose go-live is scheduled (``listing_publication.scheduled_for``). Edits keep the rules
(pipeline/reschedule.py): minutes between listings in a shop, the shop's daily limit across every
schedule, one design never in two shops at the same minute; anything that has to move further
is said in one line. Going live re-checks the approval and compliance at its time (CLAUDE.md
rule 3). A failure never stops the queue: each go-live stands alone. The caller's own items,
groups and shops only (404).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session
from app.api.shops import shop_label
from app.core import audit
from app.core.timezones import valid_zone, zone_abbreviation
from app.db.models import (
    Asset,
    GeneratedContent,
    Job,
    ListingProfile,
    ListingPublication,
    PlannedSlot,
    ShopGroup,
    Tenant,
)
from app.etsy.publisher import link_for
from app.etsy.scheduling import ScheduleBusy, ScheduleRefused, set_schedule, state_of, withdraw
from app.etsy.shops import active_shops, group_shops
from app.pipeline import reschedule as R
from app.pipeline.batch_names import names_by_id
from app.pipeline.group_plan import DRAFT_LEAD

router = APIRouter(prefix="/api/scheduled", tags=["scheduled"])

#: How long a finished go-live stays on the page.
SHOWN_AFTER = timedelta(days=7)
SETTING_KEYS = ("per_shop_per_day", "window_start", "window_end", "spacing_minutes", "stagger_minutes")


class GroupSettings(BaseModel):
    per_shop_per_day: int = Field(ge=1, le=50)
    window_start: str = Field(pattern=r"^\d\d:\d\d$")
    window_end: str = Field(pattern=r"^\d\d:\d\d$")
    spacing_minutes: int = Field(ge=1, le=24 * 60)
    stagger_minutes: int = Field(ge=0, le=24 * 60)


class ItemOut(BaseModel):
    key: str
    kind: Literal["planned", "draft"]
    content_id: uuid.UUID | None
    title: str
    sku: str | None
    batch_id: uuid.UUID | None
    batch_name: str | None
    shop_id: uuid.UUID
    shop_name: str
    group_ids: list[uuid.UUID]
    profile_id: uuid.UUID | None
    profile_name: str | None
    at: datetime
    local: str
    local_day: str
    local_time: str
    #: planned (its draft is made at its time) · drafting · scheduled · publishing · waiting ·
    #: published · failed · not_published
    status: str
    note: str | None
    movable: bool
    paused: bool
    listing_link: str | None


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    shop_ids: list[uuid.UUID]
    settings: GroupSettings | None
    paused: bool
    upcoming: int
    first_local: str | None
    last_local: str | None


class ScheduledOut(BaseModel):
    time_zone: str
    paused_all: bool
    groups: list[GroupOut]
    shops: list[dict[str, Any]]
    profiles: list[dict[str, Any]]
    items: list[ItemOut]


class EditIn(BaseModel):
    op: Literal["times", "set_time", "shift", "spread", "move_day", "publish_now", "remove"]
    keys: list[str] = Field(min_length=1, max_length=5000)
    params: dict[str, Any] = Field(default_factory=dict)


class PreviewRow(BaseModel):
    key: str
    title: str
    shop_name: str
    old_local: str
    new_local: str | None
    moved: str | None = None
    removed: bool = False


class PreviewOut(BaseModel):
    rows: list[PreviewRow]
    sentence: str
    moved_line: str | None


class UndoStep(BaseModel):
    key: str
    at: datetime | None
    state: str


class ApplyOut(BaseModel):
    changed: int
    sentence: str
    moved_line: str | None
    undo: list[UndoStep]


class RestoreIn(BaseModel):
    undo: list[UndoStep] = Field(max_length=5000)


class PausedIn(BaseModel):
    paused: bool


class GroupIn(BaseModel):
    paused: bool | None = None
    settings: GroupSettings | None = None


class ReflowIn(BaseModel):
    settings: GroupSettings
    apply: bool = False


# --- reading ---------------------------------------------------------------------------------------


def _zone(tenant: Tenant) -> str:
    return tenant.time_zone if valid_zone(tenant.time_zone) else "UTC"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _local(at: datetime, zone: str) -> tuple[str, str, str]:
    local = _aware(at).astimezone(ZoneInfo(zone))
    text = f"{local:%a} {local:%b} {local.day}, {local:%I:%M %p}".replace(" 0", " ") + f" {zone_abbreviation(_aware(at), zone)}"
    return text, local.date().isoformat(), local.strftime("%H:%M")


class _World:
    def __init__(self) -> None:
        self.items: list[ItemOut] = []
        self.rows: dict[str, Any] = {}  # key -> the slot or the publication
        self.rules: R.Rules | None = None
        self.groups: list[ShopGroup] = []
        self.members: dict[uuid.UUID, list[uuid.UUID]] = {}


async def _world(session: AsyncSession, tenant: Tenant) -> _World:
    from app.pipeline.schedule_pause import is_paused, paused_shops

    zone = _zone(tenant)
    w = _World()
    shops = {s.id: s for s in await active_shops(session, tenant.id)}
    by_group = await group_shops(session, tenant.id, list(shops.values()))
    w.groups = list((await session.execute(select(ShopGroup).where(ShopGroup.tenant_id == tenant.id)
                                            .order_by(ShopGroup.position, ShopGroup.name))).scalars())
    w.members = {g.id: [c.id for c in by_group.get(g.id, [])] for g in w.groups}
    groups_of: dict[uuid.UUID, list[uuid.UUID]] = {}
    for gid, members in w.members.items():
        for sid in members:
            groups_of.setdefault(sid, []).append(gid)
    # The rules per shop: the shop's own daily limit, else its groups' smallest "a day"; the
    # smallest minutes between of its groups (at least 1: never the same minute).
    settings = {g.id: (g.schedule or {}) for g in w.groups}
    spacing: dict[uuid.UUID, int] = {}
    daily: dict[uuid.UUID, int | None] = {}
    for sid, shop in shops.items():
        mine = [settings[g] for g in groups_of.get(sid, []) if settings[g].get("spacing_minutes")]
        spacing[sid] = min((int(s["spacing_minutes"]) for s in mine), default=1)
        per_day = [int(s["per_shop_per_day"]) for s in mine if s.get("per_shop_per_day")]
        daily[sid] = shop.daily_limit or (min(per_day) if per_day else None)
    w.rules = R.Rules(zone=zone, spacing=spacing, daily=daily)
    paused = await paused_shops(session, {tenant.id})
    now = datetime.now(timezone.utc)
    since = now - SHOWN_AFTER

    contents: dict[uuid.UUID, GeneratedContent] = {}
    slots = list((await session.execute(select(PlannedSlot).where(
        PlannedSlot.tenant_id == tenant.id, PlannedSlot.state.in_(("waiting", "drafting", "failed")),
        PlannedSlot.publish_at >= since))).scalars())
    pubs = list((await session.execute(select(ListingPublication).where(
        ListingPublication.tenant_id == tenant.id, ListingPublication.scheduled_for.is_not(None),
        ListingPublication.scheduled_for >= since))).scalars())
    ids = {s.content_id for s in slots if s.content_id} | {p.content_id for p in pubs if p.content_id}
    if ids:
        contents = {c.id: c for c in (await session.execute(select(GeneratedContent).where(GeneratedContent.id.in_(ids)))).scalars()}
    assets = {a.id: a for a in (await session.execute(select(Asset).where(
        Asset.id.in_([c.asset_id for c in contents.values()])))).scalars()} if contents else {}
    profiles = {p.id: p for p in (await session.execute(select(ListingProfile).where(
        ListingProfile.tenant_id == tenant.id))).scalars()}
    batches = await names_by_id(session, [c.batch_id for c in contents.values()])
    jobs = {j.id: j for j in (await session.execute(select(Job).where(
        Job.id.in_([p.schedule_job_id for p in pubs if p.schedule_job_id])))).scalars()} if pubs else {}

    def add(key: str, kind: str, content_id: uuid.UUID | None, shop_id: uuid.UUID, at: datetime, status: str,
            note: str | None, movable: bool, title: str | None, sku: str | None, link: str | None) -> None:
        shop = shops.get(shop_id)
        if shop is None:
            return
        content = contents.get(content_id) if content_id else None
        asset = assets.get(content.asset_id) if content is not None else None
        profile = profiles.get(content.listing_profile_id) if content is not None and content.listing_profile_id else None
        text, day, clock = _local(at, zone)
        w.items.append(ItemOut(
            key=key, kind=kind, content_id=content_id, title=(content.title if content else None) or title or "Untitled",
            sku=(asset.parsed_sku if asset else None) or sku, batch_id=content.batch_id if content else None,
            batch_name=batches.get(content.batch_id) if content else None, shop_id=shop_id, shop_name=shop_label(shop),
            group_ids=groups_of.get(shop_id, []), profile_id=profile.id if profile else None,
            profile_name=profile.name if profile else None, at=_aware(at), local=text, local_day=day, local_time=clock,
            status=status, note=note, movable=movable, paused=is_paused(tenant.id, shop_id, paused), listing_link=link,
        ))

    for slot in slots:
        if slot.state == "failed":
            add(f"slot:{slot.id}", "planned", slot.content_id, slot.connection_id, slot.publish_at, "failed", slot.note,
                False, None, None, None)
        else:
            add(f"slot:{slot.id}", "planned", slot.content_id, slot.connection_id, slot.publish_at,
                "planned" if slot.state == "waiting" else "drafting", slot.note, True, None, None, None)
        w.rows[f"slot:{slot.id}"] = slot
    for pub in pubs:
        state = state_of(pub, jobs.get(pub.schedule_job_id) if pub.schedule_job_id else None)
        if state is None:
            continue
        add(f"pub:{pub.id}", "draft", pub.content_id, pub.connection_id, pub.scheduled_for, state.status, state.note,
            state.status == "scheduled", pub.title, pub.sku, link_for(pub.etsy_listing_id, pub.state))
        w.rows[f"pub:{pub.id}"] = pub
    w.items.sort(key=lambda i: (i.at, i.shop_name, i.key))
    return w


def _items(w: _World) -> list[R.Item]:
    return [R.Item(key=i.key, shop_id=i.shop_id, content_id=i.content_id, at=i.at, movable=i.movable, shop_name=i.shop_name)
            for i in w.items if i.status not in ("published", "failed", "not_published")]


def _group_out(w: _World, group: ShopGroup, zone: str) -> GroupOut:
    members = w.members.get(group.id, [])
    upcoming = [i for i in w.items if i.shop_id in members and i.movable]
    data = group.schedule or {}
    settings = GroupSettings(**{k: data[k] for k in SETTING_KEYS}) if all(k in data for k in SETTING_KEYS) else None
    return GroupOut(id=group.id, name=group.name, shop_ids=members, settings=settings, paused=bool(data.get("paused")),
                    upcoming=len(upcoming), first_local=upcoming[0].local if upcoming else None,
                    last_local=upcoming[-1].local if upcoming else None)


async def _out(session: AsyncSession, tenant: Tenant) -> ScheduledOut:
    w = await _world(session, tenant)
    zone = _zone(tenant)
    shops = await active_shops(session, tenant.id)
    used = {i.profile_id: i.profile_name for i in w.items if i.profile_id}
    return ScheduledOut(
        time_zone=zone, paused_all=bool(tenant.schedules_paused),
        groups=[_group_out(w, g, zone) for g in w.groups],
        shops=[{"id": str(s.id), "name": shop_label(s), "daily_limit": s.daily_limit} for s in shops],
        profiles=[{"id": str(k), "name": v} for k, v in sorted(used.items(), key=lambda kv: kv[1] or "")],
        items=w.items,
    )


# --- editing ---------------------------------------------------------------------------------------


def _plan(w: _World, body: EditIn, now: datetime) -> tuple[list[R.Change], list[str]]:
    """(changes, keys removed)."""
    items = _items(w)
    known = {i.key for i in w.items}
    for key in body.keys:
        if key not in known:
            raise HTTPException(status_code=404, detail="scheduled listing not found")
    movable = [k for k in body.keys if any(i.key == k and i.movable for i in w.items)]
    if body.op == "remove":
        if not movable:
            raise HTTPException(status_code=422, detail="choose at least one scheduled listing that has not gone live")
        return [], movable
    if body.op == "publish_now":
        if not movable:
            raise HTTPException(status_code=422, detail="choose at least one scheduled listing that has not gone live")
        by = {i.key: i for i in items}
        soon = now + timedelta(minutes=1)
        return [R.Change(k, by[k].at, soon) for k in movable], []
    assert w.rules is not None
    try:
        desired = R.propose(items, body.keys, body.op, body.params, w.rules)
        return R.settle(items, desired, w.rules, now), []
    except R.EditRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _sentence(changes: list[R.Change], removed: list[str], op: str) -> str:
    n = len([c for c in changes if c.new != c.old]) if op != "publish_now" else len(changes)
    if removed:
        return f"{len(removed)} listing{'s' if len(removed) != 1 else ''} leave{'s' if len(removed) == 1 else ''} the schedule (drafts stay drafts)."
    if op == "publish_now":
        return f"{n} listing{'s' if n != 1 else ''} go{'es' if n == 1 else ''} live in the next few minutes."
    if not n:
        return "Nothing changes."
    return f"{n} listing{'s' if n != 1 else ''} move{'s' if n == 1 else ''}."


@router.get("", response_model=ScheduledOut)
async def scheduled(session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)) -> ScheduledOut:
    return await _out(session, tenant)


@router.post("/preview", response_model=PreviewOut)
async def preview(body: EditIn, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)) -> PreviewOut:
    w = await _world(session, tenant)
    zone = _zone(tenant)
    changes, removed = _plan(w, body, datetime.now(timezone.utc))
    by_key = {i.key: i for i in w.items}
    rows = [PreviewRow(key=c.key, title=by_key[c.key].title, shop_name=by_key[c.key].shop_name, old_local=by_key[c.key].local,
                       new_local=_local(c.new, zone)[0], moved=c.moved) for c in changes if c.new != c.old or body.op == "publish_now"]
    rows += [PreviewRow(key=k, title=by_key[k].title, shop_name=by_key[k].shop_name, old_local=by_key[k].local,
                        new_local=None, removed=True) for k in removed]
    assert w.rules is not None
    return PreviewOut(rows=rows, sentence=_sentence(changes, removed, body.op), moved_line=R.moved_line(changes, _items(w), w.rules))


async def _set_time(session: AsyncSession, row: Any, at: datetime, now: datetime) -> None:
    if isinstance(row, PlannedSlot):
        row.publish_at = at
        if row.state == "waiting":
            row.draft_at = max(now, at - DRAFT_LEAD)
        return
    content = await session.get(GeneratedContent, row.content_id) if row.content_id else None
    if content is None:
        raise HTTPException(status_code=409, detail="the listing of a scheduled draft is no longer there")
    try:
        await set_schedule(session, content, row, at)
    except ScheduleRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # The planned slot that made this draft follows it.
    for slot in (await session.execute(select(PlannedSlot).where(
            PlannedSlot.content_id == row.content_id, PlannedSlot.connection_id == row.connection_id,
            PlannedSlot.state == "scheduled"))).scalars():
        slot.publish_at = at


async def _remove(session: AsyncSession, tenant: Tenant, row: Any) -> None:
    if isinstance(row, PlannedSlot):
        row.state, row.note = "cancelled", "removed from the schedule"
        return
    try:
        await withdraw(session, row, actor=tenant, reason="removed on the Scheduled page")
    except ScheduleBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    for slot in (await session.execute(select(PlannedSlot).where(
            PlannedSlot.content_id == row.content_id, PlannedSlot.connection_id == row.connection_id,
            PlannedSlot.state == "scheduled"))).scalars():
        slot.state, slot.note = "cancelled", "removed from the schedule"


@router.post("/apply", response_model=ApplyOut)
async def apply(body: EditIn, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant),
                enqueuer: Enqueuer = Depends(get_enqueuer)) -> ApplyOut:
    w = await _world(session, tenant)
    now = datetime.now(timezone.utc)
    changes, removed = _plan(w, body, now)
    undo: list[UndoStep] = []
    for change in changes:
        if change.new == change.old and body.op != "publish_now":
            continue
        row = w.rows[change.key]
        undo.append(UndoStep(key=change.key, at=change.old, state=getattr(row, "state", "") if isinstance(row, PlannedSlot) else "scheduled"))
        if isinstance(row, PlannedSlot) and body.op == "publish_now" and row.state == "waiting":
            row.draft_at, row.publish_at = now, change.new
        else:
            await _set_time(session, row, change.new, now)
    for key in removed:
        row = w.rows[key]
        undo.append(UndoStep(key=key, at=_aware(row.publish_at if isinstance(row, PlannedSlot) else row.scheduled_for),
                             state=row.state if isinstance(row, PlannedSlot) else "scheduled"))
        await _remove(session, tenant, row)
    audit.record(session, "schedule.edited", actor=tenant, target_tenant_id=tenant.id, op=body.op, count=len(undo))
    await session.commit()
    if body.op == "publish_now":
        from app.workers.plans import release_due

        await release_due(session, enqueuer.enqueue, tenant_id=tenant.id)
    assert w.rules is not None
    return ApplyOut(changed=len(undo), sentence=_sentence(changes, removed, body.op),
                    moved_line=R.moved_line(changes, _items(w), w.rules), undo=undo)


@router.post("/restore", response_model=ApplyOut)
async def restore(body: RestoreIn, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)) -> ApplyOut:
    """Undo: each listing back to its time (and onto the schedule, if it was removed). One that
    has gone live, or whose draft is being made, meanwhile stays as it is."""
    now = datetime.now(timezone.utc)
    done = 0
    for step in body.undo:
        kind, _, raw = step.key.partition(":")
        try:
            rid = uuid.UUID(raw)
        except ValueError:
            raise HTTPException(status_code=404, detail="scheduled listing not found") from None
        row = await session.get(PlannedSlot if kind == "slot" else ListingPublication, rid)
        if row is None or row.tenant_id != tenant.id or step.at is None:
            raise HTTPException(status_code=404, detail="scheduled listing not found")
        at = max(_aware(step.at), now + timedelta(minutes=1))
        if isinstance(row, PlannedSlot):
            if row.state not in ("waiting", "cancelled") or (row.state == "cancelled" and step.state != "waiting"):
                continue
            row.state, row.note = "waiting", None
            row.publish_at, row.draft_at = at, max(now, at - DRAFT_LEAD)
        else:
            if row.state == "active" or row.schedule_job_id is not None:
                continue
            await _set_time(session, row, at, now)
        done += 1
    await session.commit()
    return ApplyOut(changed=done, sentence=f"Undone: {done} listing{'s' if done != 1 else ''} back as they were.",
                    moved_line=None, undo=[])


@router.put("/paused", response_model=ScheduledOut)
async def pause_all(body: PausedIn, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)) -> ScheduledOut:
    tenant.schedules_paused = body.paused
    audit.record(session, "schedule.paused" if body.paused else "schedule.resumed", actor=tenant, target_tenant_id=tenant.id,
                 scope="all")
    await session.commit()
    return await _out(session, tenant)


async def _group(session: AsyncSession, tenant: Tenant, group_id: uuid.UUID) -> ShopGroup:
    group = await session.get(ShopGroup, group_id)
    if group is None or group.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="group not found")
    return group


@router.put("/groups/{group_id}", response_model=ScheduledOut)
async def set_group(group_id: uuid.UUID, body: GroupIn, session: AsyncSession = Depends(get_session),
                    tenant: Tenant = Depends(active_tenant)) -> ScheduledOut:
    group = await _group(session, tenant, group_id)
    data = dict(group.schedule or {})
    if body.paused is not None:
        data["paused"] = body.paused
        audit.record(session, "schedule.paused" if body.paused else "schedule.resumed", actor=tenant,
                     target_tenant_id=tenant.id, scope="group", group_id=str(group.id))
    if body.settings is not None:
        if body.settings.window_end <= body.settings.window_start:
            raise HTTPException(status_code=422, detail="the daily window must end after it starts")
        data.update(body.settings.model_dump())
    group.schedule = data or None
    await session.commit()
    return await _out(session, tenant)


@router.post("/groups/{group_id}/reflow", response_model=PreviewOut)
async def reflow(group_id: uuid.UUID, body: ReflowIn, session: AsyncSession = Depends(get_session),
                 tenant: Tenant = Depends(active_tenant)) -> PreviewOut:
    """The group's new settings applied to its shops' upcoming listings: old -> new first, and
    with ``apply`` saved (the settings with them)."""
    group = await _group(session, tenant, group_id)
    if body.settings.window_end <= body.settings.window_start:
        raise HTTPException(status_code=422, detail="the daily window must end after it starts")
    # The new settings take part in the rules (minutes between, a day); kept only with ``apply``.
    group.schedule = {**(group.schedule or {}), **body.settings.model_dump()}
    await session.flush()
    w = await _world(session, tenant)
    assert w.rules is not None
    now = datetime.now(timezone.utc)
    members = w.members.get(group.id, [])
    items = _items(w)
    try:
        desired = R.reflow(items, members, body.settings.model_dump(), w.rules, now)
        changes = R.settle(items, desired, w.rules, now) if desired else []
    except R.EditRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    zone = _zone(tenant)
    by_key = {i.key: i for i in w.items}
    rows = [PreviewRow(key=c.key, title=by_key[c.key].title, shop_name=by_key[c.key].shop_name, old_local=by_key[c.key].local,
                       new_local=_local(c.new, zone)[0], moved=c.moved) for c in changes if c.new != c.old]
    if body.apply:
        for change in changes:
            if change.new != change.old:
                await _set_time(session, w.rows[change.key], change.new, now)
        audit.record(session, "schedule.reflowed", actor=tenant, target_tenant_id=tenant.id, group_id=str(group.id),
                     count=len(rows))
        await session.commit()
    else:
        await session.rollback()
    return PreviewOut(rows=rows, sentence=_sentence(changes, [], "reflow"), moved_line=R.moved_line(changes, items, w.rules))
