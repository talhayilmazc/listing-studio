"""Where and when (B4): one panel on the review page sends a batch's approved listings.

``GET  /api/batches/{id}/send``              the shops and groups to choose from, and the choice.
``PUT  /api/batches/{id}/send-to``           where the batch's listings go (groups and shops), or
                                             one listing's own exception (``content_ids``).
``POST /api/batches/{id}/send-to/all-shops`` "Make a group of all my shops": the group "All
                                             shops" (made once), chosen for the batch.
``POST /api/batches/{id}/send/preview``      what sending would do: a sentence, the button's
                                             label (or why it is disabled), and per listing,
                                             each shop and, when spread out, its time.
``POST /api/batches/{id}/send``              the same, done.

**Every approved listing goes to every chosen shop** (the owner, 2026-10-22: no splitting;
6 listings and 2 shops are 12 drafts); several groups give all their shops, each shop once
(pipeline/shop_targets.py). A listing already in a shop is not sent there again, and a
listing already scheduled there keeps its time: nothing is sent twice.

When: ``drafts`` creates the drafts now (the same jobs "Create draft" queues); ``publish``
creates them and takes them live at once (a plan whose every slot is now; going live
re-checks the approval and the compliance findings, workers/schedule.py); ``spread`` spreads
them out (pipeline/group_plan.py: a shop's daily limit, minutes between, shops of one
destination staggered so a design never goes live in two at the same minute). Only approved
listings go (CLAUDE.md rule 3: the seller approved each one and pressed the button).
Etsy request counts are never shown to sellers (A3). The caller's own batches, groups and
shops only (404).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_quota, get_session
from app.api.distribution import ScheduleIn, _capacity, _contents, _why_not
from app.api.publish import _budget, _Plan, _publish, auto_setup
from app.api.shops import shop_label
from app.core import request_cost
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
    ShopGroupMember,
    Tenant,
    UploadBatch,
)
from app.etsy.rate_limiter import DailyQuota
from app.etsy.shops import active_shops, group_shops
from app.pipeline import group_plan as G
from app.pipeline import profile_shops, shop_targets
from app.pipeline.targets import resolve_target

router = APIRouter(prefix="/api", tags=["send"])

#: What "Make a group of all my shops" calls the group.
ALL_SHOPS = "All shops"
#: A spread-out schedule starts from these until the seller changes them (the page remembers theirs).
DEFAULT_SCHEDULE = {"per_shop_per_day": 5, "window_start": "09:00", "window_end": "17:00",
                    "spacing_minutes": 30, "stagger_minutes": 5}


class DestinationIn(BaseModel):
    group_ids: list[uuid.UUID] = Field(default_factory=list)
    connection_ids: list[uuid.UUID] = Field(default_factory=list)


class SendToIn(BaseModel):
    #: None: back to the default (the batch: each listing's profile shop; a listing: its batch's).
    send_to: DestinationIn | None = None
    #: These listings' own exception; omitted: the whole batch.
    content_ids: list[uuid.UUID] | None = None


class ShopChoice(BaseModel):
    id: uuid.UUID
    name: str


class GroupChoice(BaseModel):
    id: uuid.UUID
    name: str
    shop_ids: list[uuid.UUID]


class SendStateOut(BaseModel):
    shops: list[ShopChoice]
    groups: list[GroupChoice]
    #: The batch's choice; None: each listing to its profile's main shop.
    send_to: DestinationIn | None
    label: str
    #: "Make a group of all my shops" is offered (several shops, no groups yet).
    offer_all_shops: bool


class SendIn(BaseModel):
    when: Literal["drafts", "publish", "spread"]
    schedule: ScheduleIn | None = None


class SendCell(BaseModel):
    shop_id: uuid.UUID
    shop_name: str
    #: new (a draft is made) · publish (its draft goes live) · draft / live / scheduled (there
    #: already: nothing is sent) · cannot (with why)
    state: Literal["new", "publish", "draft", "live", "scheduled", "cannot"]
    reason: str | None = None
    #: When it goes live (spread out, or already scheduled), and as the seller's wall clock.
    at: datetime | None = None
    local: str | None = None
    #: Said next to it ("title rewritten once to fit this shop").
    note: str | None = None


class SendRow(BaseModel):
    content_id: uuid.UUID
    title: str
    sku: str | None
    label: str
    #: Its own exception, not the batch's choice.
    custom: bool
    send_to: DestinationIn | None
    cells: list[SendCell]


class LeftOut(BaseModel):
    content_id: uuid.UUID
    title: str
    reason: str


class SendPreviewOut(BaseModel):
    when: str
    rows: list[SendRow]
    left_out: list[LeftOut]
    listings: int
    shops: int
    total: int
    sentence: str
    button: str
    #: Why the button cannot be pressed now; None: it can.
    disabled: str | None
    first_day: date | None = None
    finishes_on: date | None = None
    notes: list[str] = Field(default_factory=list)
    time_zone: str
    #: The schedule used for "spread" (the seller's, else the defaults), to show and change.
    schedule: ScheduleIn | None = None


class SendOut(BaseModel):
    when: str
    #: Draft jobs to follow (``drafts``), as "Create drafts" returns them.
    jobs: list[schemas.PublishJobOut] = Field(default_factory=list)
    skipped: list[schemas.PublishSkipped] = Field(default_factory=list)
    #: ``publish`` and ``spread``: the plan made and how many listings it takes live.
    plan_id: uuid.UUID | None = None
    scheduled: int = 0
    message: str


# --- helpers --------------------------------------------------------------------------------------


async def _batch(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID) -> UploadBatch:
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    return batch


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _day(d: date) -> str:
    return f"{d:%b} {d.day}"


async def _checked(session: AsyncSession, tenant: Tenant, body: DestinationIn) -> DestinationIn:
    """Only the caller's own groups and connected shops (404 otherwise)."""
    for gid in body.group_ids:
        group = await session.get(ShopGroup, gid)
        if group is None or group.tenant_id != tenant.id:
            raise HTTPException(status_code=404, detail="group not found")
    shops = {c.id for c in await active_shops(session, tenant.id)}
    for cid in body.connection_ids:
        if cid not in shops:
            raise HTTPException(status_code=404, detail="shop not found")
    if not body.group_ids and not body.connection_ids:
        raise HTTPException(status_code=422, detail="choose a group or a shop")
    return DestinationIn(group_ids=list(dict.fromkeys(body.group_ids)),
                         connection_ids=list(dict.fromkeys(body.connection_ids)))


def _out(value: dict | None) -> DestinationIn | None:
    chosen = shop_targets.destination(value)
    return DestinationIn(**chosen) if chosen is not None else None


class _Names:
    def __init__(self, groups: dict[uuid.UUID, str], shops: dict[uuid.UUID, str]) -> None:
        self.groups, self.shops = groups, shops

    def label(self, chosen: DestinationIn | None, own: str | None = None) -> str:
        if chosen is None:
            return f"{own} (its profile's shop)" if own else "its profile's shop"
        parts = [self.groups.get(g, "a deleted group") for g in chosen.group_ids]
        parts += [self.shops.get(c, "a disconnected shop") for c in chosen.connection_ids]
        return " + ".join(parts) or "nowhere"


async def _names(session: AsyncSession, tenant: Tenant) -> _Names:
    groups = {g.id: g.name for g in (await session.execute(
        select(ShopGroup).where(ShopGroup.tenant_id == tenant.id))).scalars()}
    shops = {c.id: shop_label(c) for c in await active_shops(session, tenant.id)}
    return _Names(groups, shops)


async def _default_label(session: AsyncSession, contents: list[GeneratedContent], names: _Names) -> str:
    """The batch's label when nothing was chosen: each listing's profile shop, named when one."""
    owns = {(await shop_targets.own_shop(session, c)) for c in contents}
    named = {o.id for o in owns if o is not None}
    if len(named) == 1:
        return names.shops.get(next(iter(named)), "its profile's shop")
    return "each listing's profile shop"


async def _state(session: AsyncSession, tenant: Tenant, batch: UploadBatch) -> SendStateOut:
    shops = await active_shops(session, tenant.id)
    by_group = await group_shops(session, tenant.id, shops)
    names = await _names(session, tenant)
    groups = [GroupChoice(id=g, name=names.groups[g], shop_ids=[c.id for c in members])
              for g, members in by_group.items() if g in names.groups]
    chosen = _out(batch.send_to)
    contents = await _contents(session, tenant, batch, None)
    label = names.label(chosen) if chosen is not None else await _default_label(session, contents, names)
    return SendStateOut(
        shops=[ShopChoice(id=c.id, name=shop_label(c)) for c in shops], groups=groups, send_to=chosen,
        label=label, offer_all_shops=len(shops) > 1 and not groups,
    )


class _Work:
    """What sending would do, worked out once for the preview and for the send."""

    def __init__(self) -> None:
        self.rows: list[SendRow] = []
        self.left_out: list[LeftOut] = []
        #: (listing, shop, "new" | "publish")
        self.actions: list[tuple[GeneratedContent, EtsyConnection, str]] = []
        self.contents: dict[uuid.UUID, GeneratedContent] = {}
        self.dest: dict[uuid.UUID, DestinationIn | None] = {}
        #: listing -> its shops (where it goes, in shop order)
        self.shops: dict[uuid.UUID, list[EtsyConnection]] = {}
        self.setups: dict[uuid.UUID, tuple] = {}


async def _work(session: AsyncSession, tenant: Tenant, batch: UploadBatch, when: str, zone: str) -> _Work:
    work = _Work()
    connected = await active_shops(session, tenant.id)
    names = await _names(session, tenant)
    contents = await _contents(session, tenant, batch, None)
    ids = [c.id for c in contents]
    publications = {(p.content_id, p.connection_id): p for p in (await session.execute(
        select(ListingPublication).where(ListingPublication.content_id.in_(ids))))
        .scalars()} if ids else {}
    slots = {(s.content_id, s.connection_id): s for s in (await session.execute(
        select(PlannedSlot).where(PlannedSlot.content_id.in_(ids),
                                  PlannedSlot.state.in_(("waiting", "drafting", "scheduled")))))
        .scalars()} if ids else {}
    for content in contents:
        why = await _why_not(session, content)
        if why:
            work.left_out.append(LeftOut(content_id=content.id, title=content.title or "", reason=why))
            continue
        own_dest, custom = await shop_targets.chosen_for(session, content)
        dest = DestinationIn(**own_dest) if own_dest is not None else None
        mine = await shop_targets.wanted(session, content, connected)
        own = await shop_targets.own_shop(session, content) if dest is None else None
        label = names.label(dest, shop_label(own) if own else None)
        work.contents[content.id], work.dest[content.id], work.shops[content.id] = content, dest, mine
        asset = await session.get(Asset, content.asset_id)
        cells: list[SendCell] = []
        if not mine:
            work.left_out.append(LeftOut(content_id=content.id, title=content.title or "",
                                         reason="its profile's shop is not connected; choose where to send it"))
        for shop in mine:
            name = shop_label(shop)
            pub = publications.get((content.id, shop.id))
            slot = slots.get((content.id, shop.id))
            if pub is not None and pub.state == "active":
                cells.append(SendCell(shop_id=shop.id, shop_name=name, state="live"))
                continue
            if pub is not None and pub.state == "deleted_on_etsy":
                pub = None  # deleted on Etsy: a new draft can be made
            scheduled = slot.publish_at if slot is not None else (
                pub.scheduled_for if pub is not None and pub.schedule_note is None else None)
            if scheduled is not None:
                at = scheduled if scheduled.tzinfo else scheduled.replace(tzinfo=timezone.utc)
                cells.append(SendCell(shop_id=shop.id, shop_name=name, state="scheduled", at=at,
                                      local=_local(at, zone)))
                continue
            if pub is not None:
                if when == "drafts":
                    cells.append(SendCell(shop_id=shop.id, shop_name=name, state="draft"))
                else:
                    cells.append(SendCell(shop_id=shop.id, shop_name=name, state="publish"))
                    work.actions.append((content, shop, "publish"))
                continue
            target = await resolve_target(session, content, shop)
            if not target.ok and auto_setup(target):
                entry = work.setups.setdefault(target.profile.id, (target.profile, []))
                if shop.id not in entry[1]:
                    entry[1].append(shop.id)
            elif not target.ok or target.profile is None:
                cells.append(SendCell(shop_id=shop.id, shop_name=name, state="cannot",
                                      reason=target.reason or "no profile for this shop"))
                continue
            # Another shop's prefix makes the title too long: rewritten once for that shop, never cut.
            note = "title rewritten once to fit this shop" if target.ok and target.title_fit else None
            cells.append(SendCell(shop_id=shop.id, shop_name=name, state="new", note=note))
            work.actions.append((content, shop, "new"))
        work.rows.append(SendRow(content_id=content.id, title=content.title or "", label=label,
                                 sku=asset.parsed_sku if asset is not None else None,
                                 custom=custom, send_to=dest if custom else None, cells=cells))
    return work


def _local(at: datetime, zone: str) -> str:
    local = at.astimezone(ZoneInfo(zone))
    return f"{local:%b} {local.day}, {local:%I:%M %p}".replace(" 0", " ") + f" {zone_abbreviation(at, zone)}"


def _settings(body: SendIn, tenant: Tenant, zone: str) -> tuple[G.Settings, ScheduleIn]:
    s = body.schedule
    if s is None:
        today = datetime.now(timezone.utc).astimezone(ZoneInfo(zone)).date()
        d = DEFAULT_SCHEDULE
        s = ScheduleIn(start_date=today, per_shop_per_day=d["per_shop_per_day"],
                       window_start=time.fromisoformat(d["window_start"]), window_end=time.fromisoformat(d["window_end"]),
                       spacing_minutes=d["spacing_minutes"], stagger_minutes=d["stagger_minutes"])
    return G.Settings(start=s.start_date, per_shop_per_day=s.per_shop_per_day, window_start=s.window_start,
                      window_end=s.window_end, spacing_minutes=s.spacing_minutes,
                      stagger_minutes=s.stagger_minutes, zone=zone), s


async def _spread(session: AsyncSession, tenant: Tenant, quota: DailyQuota, work: _Work, settings: G.Settings) -> G.Plan:
    """The schedule: every listing in each of its shops, a shop's daily limit and minutes
    between kept, shops that get the same listings staggered (never the same minute)."""
    # Listings that go to the same shops share a key; the planner gives each shop of a key every
    # listing of that key, and staggers the shops of a key (a shop in two keys: one calendar).
    keys: dict[tuple[uuid.UUID, ...], str] = {}
    listings: list[G.Listing] = []
    plan_shops: list[G.Shop] = []
    to_do = {(c.id, s.id) for c, s, _ in work.actions}
    for row in work.rows:
        shops = work.shops[row.content_id]
        key_shops = tuple(s.id for s in shops)
        if not key_shops:
            continue
        if key_shops not in keys:
            keys[key_shops] = f"k{len(keys)}"
            plan_shops += [G.Shop(id=s.id, name=shop_label(s), group_id=keys[key_shops]) for s in shops]
        skip = {s.id: "already there" for s in shops if (row.content_id, s.id) not in to_do}
        listings.append(G.Listing(content_id=row.content_id, title=row.title, group_id=keys[key_shops], skip=skip))
    now = datetime.now(timezone.utc)
    per_draft = (await request_cost.draft_estimate(session)).per_draft
    capacity, _ = await _capacity(session, tenant, quota, now, per_draft)
    return G.plan(settings, listings, plan_shops, capacity=capacity, now=now, draft_requests=per_draft)


async def _preview(session: AsyncSession, tenant: Tenant, quota: DailyQuota, batch: UploadBatch,
                   body: SendIn) -> tuple[SendPreviewOut, _Work, G.Plan | None]:
    zone = tenant.time_zone if valid_zone(tenant.time_zone) else "UTC"
    work = await _work(session, tenant, batch, body.when, zone)
    listings = len({c.id for c, _, _ in work.actions})
    shops = len({s.id for _, s, _ in work.actions})
    total = len(work.actions)
    plan = None
    schedule = None
    notes: list[str] = []
    disabled: str | None = None
    there = sum(1 for r in work.rows for c in r.cells if c.state in ("draft", "live", "scheduled"))
    cannot = sum(1 for r in work.rows for c in r.cells if c.state == "cannot")
    if there:
        notes.append(f"{there} already there (a draft, live or scheduled): not sent again.")
    if cannot:
        notes.append(f"{_plural(cannot, 'listing')} cannot go to a shop: the list below says why.")
    first = last = None
    if body.when == "spread":
        settings, schedule = _settings(body, tenant, zone)
        if total:
            try:
                plan = await _spread(session, tenant, quota, work, settings)
            except G.PlanRefused as exc:
                disabled = str(exc)[:1].upper() + str(exc)[1:] + "."
            if plan is not None:
                at = {(s.content_id, s.shop_id): s for s in plan.slots}
                for row in work.rows:
                    for cell in row.cells:
                        slot = at.get((row.content_id, cell.shop_id))
                        if slot is not None and cell.state in ("new", "publish"):
                            cell.at, cell.local = slot.publish_at, _local(slot.publish_at, zone)
                days = sorted({s.local_day for s in plan.slots})
                first, last = (days[0], days[-1]) if days else (None, None)
                notes += plan.notes
    elif total:
        # Drafts are made now: they must fit what may still be spent today (v5 §E).
        budget = await _budget(session, quota, tenant, _Plan(jobs=[(c, s, None) for c, s, k in work.actions if k == "new"]))
        if not budget.fits:
            disabled = budget.message
    head = f"{_plural(listings, 'listing')} → {_plural(shops, 'shop')}"
    if body.when == "drafts":
        sentence, button = f"{head}: {_plural(total, 'draft')}.", f"Create {_plural(total, 'draft')}"
    elif body.when == "publish":
        sentence = f"{head}: {_plural(total, 'listing')} {'goes' if total == 1 else 'go'} live now."
        button = f"Publish {_plural(total, 'listing')}"
    else:
        span = (f", {_day(first)}" + (f" – {_day(last)}" if last and last != first else "")) if first else ""
        sentence = f"{head}: {_plural(total, 'listing')}{span}."
        button = f"Schedule {_plural(total, 'listing')}" + (f" from {_day(first)}" if first else "")
    if disabled is None:
        if not work.rows and work.left_out and all(x.reason == "not approved yet" for x in work.left_out):
            disabled = "Approve listings first."
        elif not work.rows and not work.left_out:
            disabled = "No listings yet."
        elif total == 0:
            disabled = "Nothing new to send: these listings are already in these shops." if there else \
                "Nothing can be sent yet: the list below says why."
    return SendPreviewOut(
        when=body.when, rows=work.rows, left_out=work.left_out, listings=listings, shops=shops, total=total,
        sentence=sentence, button=button, disabled=disabled, first_day=first, finishes_on=last, notes=notes,
        time_zone=zone, schedule=schedule,
    ), work, plan


def _record(session: AsyncSession, tenant: Tenant, work: _Work, names: _Names) -> None:
    """The design -> destination record (a design sent to another group is said, never blocked)."""
    sent = {c.id for c, _, _ in work.actions}
    for row in work.rows:
        if row.content_id not in sent:
            continue
        dest = work.dest[row.content_id]
        if dest is None:
            continue
        for gid in dest.group_ids:
            session.add(DesignDistribution(tenant_id=tenant.id, content_id=row.content_id, sku=row.sku,
                                           group_id=gid, group_name=names.groups.get(gid, "")))
        for cid in dest.connection_ids:
            session.add(DesignDistribution(tenant_id=tenant.id, content_id=row.content_id, sku=row.sku,
                                           group_id=None, group_name=names.shops.get(cid, "")))


# --- endpoints -----------------------------------------------------------------------------------


@router.get("/batches/{batch_id}/send", response_model=SendStateOut)
async def send_state(batch_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                     tenant: Tenant = Depends(active_tenant)) -> SendStateOut:
    return await _state(session, tenant, await _batch(session, tenant, batch_id))


@router.put("/batches/{batch_id}/send-to", response_model=SendStateOut)
async def set_send_to(batch_id: uuid.UUID, body: SendToIn, session: AsyncSession = Depends(get_session),
                      tenant: Tenant = Depends(active_tenant)) -> SendStateOut:
    """Where the batch's listings go, or one listing's own exception. Nothing is sent."""
    batch = await _batch(session, tenant, batch_id)
    value = None
    if body.send_to is not None:
        chosen = await _checked(session, tenant, body.send_to)
        value = shop_targets.stored_value(chosen.group_ids, chosen.connection_ids)
    if body.content_ids is None:
        batch.send_to = value
    else:
        for content in await _contents(session, tenant, batch, body.content_ids):
            content.send_to = value
    await session.commit()
    return await _state(session, tenant, batch)


@router.post("/batches/{batch_id}/send-to/all-shops", response_model=SendStateOut)
async def send_to_all_shops(batch_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                            tenant: Tenant = Depends(active_tenant)) -> SendStateOut:
    """"Make a group of all my shops": one group with every connected shop, chosen for the batch.
    A group of that name is reused when it has exactly those shops; otherwise a free name."""
    batch = await _batch(session, tenant, batch_id)
    shops = await active_shops(session, tenant.id)
    if len(shops) < 2:
        raise HTTPException(status_code=422, detail="connect a second shop first")
    by_group = await group_shops(session, tenant.id, shops)
    groups = {g.name: g for g in (await session.execute(select(ShopGroup).where(ShopGroup.tenant_id == tenant.id))).scalars()}
    everyone = {s.id for s in shops}
    group = next((g for g in groups.values() if {c.id for c in by_group.get(g.id, [])} == everyone
                  and g.name.startswith(ALL_SHOPS)), None)
    if group is None:
        name = ALL_SHOPS
        n = 2
        while name in groups:
            name, n = f"{ALL_SHOPS} {n}", n + 1
        group = ShopGroup(tenant_id=tenant.id, name=name, position=len(groups))
        session.add(group)
        await session.flush()
        for shop in shops:
            session.add(ShopGroupMember(group_id=group.id, connection_id=shop.id))
    batch.send_to = shop_targets.stored_value([group.id], [])
    await session.commit()
    return await _state(session, tenant, batch)


@router.post("/batches/{batch_id}/send/preview", response_model=SendPreviewOut)
async def send_preview(batch_id: uuid.UUID, body: SendIn, session: AsyncSession = Depends(get_session),
                       tenant: Tenant = Depends(active_tenant), quota: DailyQuota = Depends(get_quota)) -> SendPreviewOut:
    out, _, _ = await _preview(session, tenant, quota, await _batch(session, tenant, batch_id), body)
    return out


@router.post("/batches/{batch_id}/send", response_model=SendOut)
async def send(batch_id: uuid.UUID, body: SendIn, session: AsyncSession = Depends(get_session),
               tenant: Tenant = Depends(active_tenant), quota: DailyQuota = Depends(get_quota),
               enqueuer: Enqueuer = Depends(get_enqueuer)) -> SendOut:
    batch = await _batch(session, tenant, batch_id)
    out, work, plan = await _preview(session, tenant, quota, batch, body)
    if out.disabled:
        raise HTTPException(status_code=409, detail=out.disabled)
    names = await _names(session, tenant)
    if body.when == "drafts":
        pairs = [schemas.PublishPair.model_construct(content_id=c.id, connection_id=s.id, profile_id=None)
                 for c, s, kind in work.actions if kind == "new"]
        request = schemas.PublishRequest.model_construct(targets=None, content_ids=None, pairs=pairs)
        _record(session, tenant, work, names)
        result = await _publish(session, tenant, quota, enqueuer, list(work.contents.values()), request)
        return SendOut(when="drafts", jobs=result.jobs, skipped=result.skipped,
                       message=f"Creating {_plural(len(result.jobs), 'draft')}.")
    now = datetime.now(timezone.utc)
    if body.when == "publish":
        # Every slot is now: the draft is made at once and goes live right after it (a draft
        # already there goes live at the next release). Going live re-checks approval and compliance.
        slots = [(c.id, s.id, now, now + timedelta(minutes=1)) for c, s, _ in work.actions]
        settings = {"mode": "now", "time_zone": out.time_zone}
    else:
        assert plan is not None and out.schedule is not None
        slots = [(x.content_id, x.shop_id, x.draft_at, x.publish_at) for x in plan.slots]
        s = out.schedule
        settings = {"mode": "spread", "start_date": s.start_date.isoformat(), "per_shop_per_day": s.per_shop_per_day,
                    "window_start": s.window_start.strftime("%H:%M"), "window_end": s.window_end.strftime("%H:%M"),
                    "spacing_minutes": s.spacing_minutes, "stagger_minutes": s.stagger_minutes,
                    "time_zone": out.time_zone}
    dests = {str(row.content_id): names.label(work.dest[row.content_id]) for row in work.rows}
    settings["destinations"] = dests
    group_plan = GroupPlan(tenant_id=tenant.id, batch_id=batch.id, settings=settings)
    session.add(group_plan)
    await session.flush()
    for content_id, shop_id, draft_at, publish_at in slots:
        session.add(PlannedSlot(tenant_id=tenant.id, plan_id=group_plan.id, content_id=content_id,
                                connection_id=shop_id, draft_at=draft_at, publish_at=publish_at))
    _record(session, tenant, work, names)
    started = [profile for profile, shops in work.setups.values()
               if await profile_shops.start_links(session, profile, shops)]
    await session.commit()
    for profile in started:
        await enqueuer.enqueue("link_profile", str(profile.id))
    from app.workers.plans import release_due

    await release_due(session, enqueuer.enqueue, tenant_id=tenant.id)
    message = (f"{_plural(len(slots), 'listing')} {'goes' if len(slots) == 1 else 'go'} live in the next few minutes."
               if body.when == "publish" else
               f"{_plural(len(slots), 'listing')} scheduled" + (f", {_day(out.first_day)} – {_day(out.finishes_on)}."
                                                              if out.first_day and out.finishes_on != out.first_day
                                                              else (f" for {_day(out.first_day)}." if out.first_day else ".")))
    return SendOut(when=body.when, plan_id=group_plan.id, scheduled=len(slots), message=message)
