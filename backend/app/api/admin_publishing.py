"""Admin → Publishing (fix/usage-and-admin-publishing, Part 2): "did my schedule work?" without the server.

Read only and admin only: nothing here writes or calls Etsy; requeue and reconcile stay in the CLI.
One source of truth: every listing's state and its one-line reason come from publish-report's own code
(``ops/publish_report.py``: ``gather``, ``classify``, ``summary_lines``, ``item_lines``), so the screen and
``python -m app.cli publish-report`` never disagree.

* ``GET /api/admin/publishing``: across all accounts. **Upcoming** (go-live in the next 7 days, one row
  per listing and shop, by account then go-live time, times in Istanbul and in the seller's zone),
  **Needs attention** (missed, failed or refused, approved drafts waiting more than 3 days with no
  go-live time, and what ``requeue-stuck`` lists), **Last 7 days** (per account and day: drafts made,
  gone live through the app, published by the seller in Shop Manager, failures) and the listings counted
  this period (core/usage.py; old and new side by side while USAGE_COUNTER is shadow). Paginated.
* ``GET /api/admin/users/{id}/publishing``: one account, as publish-report shows it: the summary lines,
  then its listings with filters (shop, state, dates) and a search (SKU or Etsy listing id).
* ``GET /api/admin/users/{id}/publishing/detail``: one listing's block, exactly as the report prints it.

Candidates for the all-accounts view are found with targeted queries (a schedule in the window, a
failed attempt, a waiting draft, a never-sent approved listing), then classified by publish-report's
code without the text checks (the slow part; the account view runs them).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.api.deps import get_session
from app.core import allowance
from app.core.timezones import valid_zone, zone_abbreviation
from app.db.models import (
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    PlannedSlot,
    Tenant,
)
from app.etsy.scheduling import MISSED_AFTER
from app.ops import publish_report as R

router = APIRouter(prefix="/api/admin", tags=["admin"])

ISTANBUL = "Europe/Istanbul"
WINDOW_DAYS = 7
WAITING_DAYS = 3
HISTORY_DAYS = 30
PER_PAGE = 50

#: The screen's states, from publish-report's outcomes.
WAITING, DRAFTED, LIVE, MISSED, FAILED, HELD, REFUSED = (
    "waiting", "draft created", "live", "missed", "failed", "held by our check", "refused by Etsy")
STATES = (WAITING, DRAFTED, LIVE, MISSED, FAILED, HELD, REFUSED)
#: Outcomes that need someone to look.
ATTENTION = (R.MISSED, R.REFUSED, R.HELD, R.SEND_REFUSED, R.NOT_LIVE, R.NEVER_SENT)


def state_of(item: R.Item) -> str:
    """The screen's state for a classified item (publish-report's outcome, in fewer words)."""
    o = item.outcome
    if o in (R.PUBLISHED, R.BY_SELLER):
        return LIVE
    if o == R.MISSED:
        return MISSED
    if o == R.REFUSED:
        return REFUSED
    if o in (R.HELD, R.SEND_REFUSED):
        return HELD
    if o in (R.NOT_LIVE, R.DELETED):
        return FAILED
    return DRAFTED if item.publication is not None and item.publication.state == "draft" else WAITING


def _aware(at: datetime | None) -> datetime | None:
    return None if at is None else (at if at.tzinfo else at.replace(tzinfo=timezone.utc))


def local(at: datetime | None, zone: str) -> str | None:
    """"Nov 12, 14:30 GMT+3": a time in a zone, with the zone said."""
    at = _aware(at)
    if at is None:
        return None
    zone = zone if valid_zone(zone) else "UTC"
    here = at.astimezone(ZoneInfo(zone))
    abbr = zone_abbreviation(at, zone)
    if abbr[:1] in "+-" and abbr[1:].isdigit():  # "+03": said as "GMT+3"
        abbr = f"GMT{abbr[0]}{int(abbr[1:3])}" + (f":{abbr[3:5]}" if len(abbr) > 3 and abbr[3:5] != "00" else "")
    return f"{here:%b} {here.day}, {here:%H:%M} {abbr}"


def _zone(tenant: Tenant) -> str:
    return tenant.time_zone if valid_zone(tenant.time_zone) else "UTC"


def _go_live(item: R.Item) -> datetime | None:
    if item.publication is not None and item.publication.scheduled_for is not None:
        return _aware(item.publication.scheduled_for)
    open_slots = [s for s in item.slots if s.state in ("waiting", "drafting", "scheduled", "failed")]
    return _aware(open_slots[-1].publish_at) if open_slots else None


def _draft_time(item: R.Item) -> datetime | None:
    if item.publication is not None:
        return _aware(item.publication.created_at)
    return _aware(item.slots[-1].draft_at) if item.slots else None


class Row(BaseModel):
    account: str
    account_id: uuid.UUID
    shop: str | None
    shop_id: uuid.UUID | None
    content_id: uuid.UUID
    etsy_listing_id: int | None
    sku: str | None
    title: str
    draft_at: datetime | None
    draft_istanbul: str | None
    draft_seller: str | None
    go_live_at: datetime | None
    go_live_istanbul: str | None
    go_live_seller: str | None
    seller_zone: str
    state: str
    outcome: str
    why: str


class ActivityDay(BaseModel):
    account: str
    account_id: uuid.UUID
    day: date
    drafts: int
    live_by_app: int
    by_seller: int
    failures: int


class Counted(BaseModel):
    account: str
    account_id: uuid.UUID
    counter: str
    new: int
    old: int


class Page(BaseModel):
    rows: list[Row]
    total: int
    page: int
    pages: int


class Overview(BaseModel):
    upcoming: Page
    attention: Page
    activity: list[ActivityDay]
    counted: list[Counted]
    states: list[str]


def _row(tenant: Tenant, item: R.Item, why: str | None = None) -> Row:
    zone = _zone(tenant)
    draft, live = _draft_time(item), _go_live(item)
    return Row(
        account=tenant.email, account_id=tenant.id, shop=item.shop.shop_name if item.shop else None,
        shop_id=item.shop.id if item.shop else None, content_id=item.content.id,
        etsy_listing_id=item.publication.etsy_listing_id if item.publication is not None else None,
        sku=item.sku, title=(item.content.title or "")[:R.TITLE_CHARS],
        draft_at=draft, draft_istanbul=local(draft, ISTANBUL), draft_seller=local(draft, zone),
        go_live_at=live, go_live_istanbul=local(live, ISTANBUL), go_live_seller=local(live, zone), seller_zone=zone,
        state=state_of(item), outcome=item.outcome, why=why or item.why,
    )


def _page(rows: list[Row], page: int, per: int) -> Page:
    pages = max(1, -(-len(rows) // per))
    page = min(max(1, page), pages)
    return Page(rows=rows[(page - 1) * per: page * per], total=len(rows), page=page, pages=pages)


async def _read_only(session: AsyncSession) -> None:
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        await session.execute(text("SET TRANSACTION READ ONLY"))


async def _ids(session: AsyncSession, query: Any) -> dict[uuid.UUID, set[uuid.UUID]]:
    """(account, listing) pairs from a query of two columns, as account -> listings."""
    out: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for tenant_id, content_id in (await session.execute(query)).all():
        if content_id is not None:
            out[tenant_id].add(content_id)
    return out


async def _upcoming_ids(session: AsyncSession, now: datetime) -> dict[uuid.UUID, set[uuid.UUID]]:
    end = now + timedelta(days=WINDOW_DAYS)
    out = await _ids(session, select(ListingPublication.tenant_id, ListingPublication.content_id).where(
        ListingPublication.scheduled_for >= now, ListingPublication.scheduled_for < end))
    for t, ids in (await _ids(session, select(PlannedSlot.tenant_id, PlannedSlot.content_id).where(
            PlannedSlot.publish_at >= now, PlannedSlot.publish_at < end,
            PlannedSlot.state.in_(("waiting", "drafting", "scheduled"))))).items():
        out[t] |= ids
    return out


async def _attention_ids(session: AsyncSession, now: datetime) -> dict[uuid.UUID, set[uuid.UUID]]:
    since = now - timedelta(days=HISTORY_DAYS)
    late = now - MISSED_AFTER
    queries = [
        # Missed: a go-live or a group schedule whose time passed without it happening.
        select(ListingPublication.tenant_id, ListingPublication.content_id).where(
            ListingPublication.state == "draft", ListingPublication.scheduled_for >= since,
            ListingPublication.scheduled_for < late),
        select(PlannedSlot.tenant_id, PlannedSlot.content_id).where(
            PlannedSlot.state.in_(("waiting", "drafting", "failed")), PlannedSlot.publish_at >= since,
            PlannedSlot.publish_at < now),
        # A go-live the app held ("Not published: …"), as requeue-stuck lists.
        select(ListingPublication.tenant_id, ListingPublication.content_id).where(
            ListingPublication.state == "draft", ListingPublication.schedule_note.like("Not published: %")),
        # Approved drafts waiting more than 3 days with no go-live time.
        select(ListingPublication.tenant_id, ListingPublication.content_id)
        .join(GeneratedContent, GeneratedContent.id == ListingPublication.content_id).where(
            ListingPublication.state == "draft", ListingPublication.scheduled_for.is_(None),
            ListingPublication.created_at < now - timedelta(days=WAITING_DAYS), GeneratedContent.approved.is_(True),
            ~exists().where(PlannedSlot.content_id == ListingPublication.content_id,
                            PlannedSlot.connection_id == ListingPublication.connection_id,
                            PlannedSlot.state.in_(("waiting", "drafting", "scheduled")))),
        # Approved, never sent anywhere (requeue-stuck lists them).
        select(GeneratedContent.tenant_id, GeneratedContent.id).where(
            GeneratedContent.approved.is_(True),
            ~exists().where(ListingPublication.content_id == GeneratedContent.id),
            ~exists().where(PlannedSlot.content_id == GeneratedContent.id)),
    ]
    out: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for query in queries:
        for t, ids in (await _ids(session, query)).items():
            out[t] |= ids
    # Failed or refused attempts in the window (the listing is in the job's payload).
    for job in (await session.execute(select(Job.tenant_id, Job.payload).where(
            Job.type.in_((JobType.create_draft, JobType.publish_live)), Job.status == JobStatus.failed,
            Job.finished_at >= since))).all():
        try:
            out[job[0]].add(uuid.UUID(str((job[1] or {}).get("content_id"))))
        except ValueError:
            continue
    return out


async def overview(session: AsyncSession, now: datetime, *, page: int = 1, attention_page: int = 1,
                   per: int = PER_PAGE) -> Overview:
    tenants = {t.id: t for t in (await session.execute(select(Tenant))).scalars()}
    upcoming_ids, attention_ids = await _upcoming_ids(session, now), await _attention_ids(session, now)
    since = (now - timedelta(days=HISTORY_DAYS)).date()
    upcoming: list[Row] = []
    attention: list[Row] = []
    for tenant_id in sorted(set(upcoming_ids) | set(attention_ids), key=lambda t: tenants[t].email if t in tenants else ""):
        tenant = tenants.get(tenant_id)
        if tenant is None:
            continue
        ids = upcoming_ids.get(tenant_id, set()) | attention_ids.get(tenant_id, set())
        items = await R.gather(session, tenant, since, now, content_ids=ids, text_checks=False)
        end = now + timedelta(days=WINDOW_DAYS)
        mine_up: list[Row] = []
        mine_att: list[Row] = []
        for item in items:
            item.outcome, item.why = R.classify(item, now)
            live = _go_live(item)
            if item.content.id in upcoming_ids.get(tenant_id, set()) and live is not None and now <= live < end:
                mine_up.append(_row(tenant, item))
            why = attention_reason(item, now)
            if why:
                mine_att.append(_row(tenant, item, why))
        upcoming += sorted(mine_up, key=lambda r: (r.go_live_at or now, r.shop or ""))
        attention += sorted(mine_att, key=lambda r: (r.state, r.shop or "", r.sku or ""))
    return Overview(
        upcoming=_page(upcoming, page, per), attention=_page(attention, attention_page, per),
        activity=await activity(session, now, tenants), counted=await counted(session, tenants, now), states=list(STATES),
    )


def attention_reason(item: R.Item, now: datetime) -> str | None:
    """Why a listing needs someone to look, in one line, or None. The reasons are publish-report's."""
    if item.outcome in ATTENTION:
        return f"{item.outcome}: {item.why}"
    pub = item.publication
    if item.outcome == R.APPROVED and pub is not None and pub.state == "draft" and pub.scheduled_for is None:
        waited = now - _aware(pub.created_at)
        if waited > timedelta(days=WAITING_DAYS):
            return f"approved draft waiting {waited.days} days with no go-live time"
    return None


async def activity(session: AsyncSession, now: datetime, tenants: dict[uuid.UUID, Tenant]) -> list[ActivityDay]:
    """Per account and Istanbul day, the last 7 days: drafts made, gone live through the app (a schedule or
    Publish now), published by the seller in Shop Manager, failed attempts. Counted in the database."""
    start = now - timedelta(days=WINDOW_DAYS)
    days: dict[tuple[uuid.UUID, date], dict[str, int]] = defaultdict(lambda: {"drafts": 0, "live_by_app": 0, "by_seller": 0, "failures": 0})
    sqlite = session.bind is not None and session.bind.dialect.name == "sqlite"

    def day_of(column):  # noqa: ANN001, ANN202 - Istanbul is UTC+3 all year (no daylight saving since 2016)
        return func.date(column, "+3 hours") if sqlite else func.date(func.timezone(ISTANBUL, column))

    async def count(column, key_of, *where) -> None:  # noqa: ANN001
        day = day_of(column)
        query = select(column.table.c.tenant_id, day, *([column.table.c.published_by] if key_of is None else []), func.count())
        query = query.where(column >= start, *where).group_by(column.table.c.tenant_id, day,
                                                              *([column.table.c.published_by] if key_of is None else []))
        for row in (await session.execute(query)).all():
            tenant_id, when = row[0], row[1]
            key = key_of if key_of is not None else ("by_seller" if row[2] == "seller_on_etsy" else "live_by_app")
            days[(tenant_id, when if isinstance(when, date) else date.fromisoformat(str(when)))][key] += row[-1]

    await count(ListingPublication.__table__.c.created_at, "drafts")
    await count(ListingPublication.__table__.c.published_at, None)
    await count(Job.__table__.c.finished_at, "failures", Job.status == JobStatus.failed,
                Job.type.in_((JobType.create_draft, JobType.publish_live)))
    out = [ActivityDay(account=tenants[t].email if t in tenants else str(t), account_id=t, day=d, **counts)
           for (t, d), counts in days.items()]
    return sorted(out, key=lambda a: (a.account, a.day))


async def counted(session: AsyncSession, tenants: dict[uuid.UUID, Tenant], now: datetime) -> list[Counted]:
    """Listings counted in each account's current period, by the one count (core/allowance.py::status)."""
    out = []
    for tenant in sorted(tenants.values(), key=lambda t: t.email):
        s = await allowance.status(session, tenant, now)
        if s.listings or s.old_used:
            out.append(Counted(account=tenant.email, account_id=tenant.id, counter=s.counter, new=s.listings, old=s.old_used))
    return out


@router.get("/publishing", response_model=Overview)
async def publishing(
    page: int = Query(1, ge=1),
    attention_page: int = Query(1, ge=1),
    per: int = Query(PER_PAGE, ge=10, le=200),
    _admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> Overview:
    await _read_only(session)
    try:
        return await overview(session, datetime.now(timezone.utc), page=page, attention_page=attention_page, per=per)
    finally:
        await session.rollback()


# --- one account ------------------------------------------------------------------------------------
class SummaryLine(BaseModel):
    outcome: str
    count: int
    state: str


class AccountItem(BaseModel):
    content_id: uuid.UUID
    shop: str | None
    shop_id: uuid.UUID | None
    etsy_listing_id: int | None
    sku: str | None
    title: str
    outcome: str
    state: str
    why: str


class AccountPublishing(BaseModel):
    account: str
    since: date
    until: date
    #: Exactly as publish-report prints them.
    summary_text: list[str]
    summary: list[SummaryLine]
    shops: list[dict[str, Any]]
    items: list[AccountItem]
    total: int
    page: int
    pages: int


class Detail(BaseModel):
    lines: list[str]


async def _account(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="no such account")
    return tenant


def _matches(item: R.Item, *, shop: uuid.UUID | None, state: str | None, until: datetime, q: str | None) -> bool:
    if shop is not None and (item.shop is None or item.shop.id != shop):
        return False
    if state and state not in (item.outcome, state_of(item)):
        return False
    created = _aware(item.content.created_at)
    if created is not None and created >= until:
        return False
    if q:
        needle = q.strip().lower()
        listing = str(item.publication.etsy_listing_id) if item.publication is not None else ""
        if needle not in (item.sku or "").lower() and needle != listing:
            return False
    return True


@router.get("/users/{tenant_id}/publishing", response_model=AccountPublishing)
async def account_publishing(
    tenant_id: uuid.UUID,
    since: date | None = None,
    until: date | None = None,
    shop: uuid.UUID | None = None,
    state: str | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    per: int = Query(PER_PAGE, ge=10, le=200),
    _admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> AccountPublishing:
    await _read_only(session)
    try:
        now = datetime.now(timezone.utc)
        tenant = await _account(session, tenant_id)
        since = since or (now - timedelta(days=R.DEFAULT_DAYS)).date()
        until = until or now.date()
        items = await R.gather(session, tenant, since, now)
        for item in items:
            item.outcome, item.why = R.classify(item, now)
        end = datetime(until.year, until.month, until.day, tzinfo=timezone.utc) + timedelta(days=1)
        chosen = [i for i in items if _matches(i, shop=shop, state=state, until=end, q=q)]
        pages = max(1, -(-len(chosen) // per))
        page = min(page, pages)
        shops = sorted({(i.shop.id, i.shop.shop_name or "") for i in items if i.shop is not None}, key=lambda s: s[1])
        return AccountPublishing(
            account=tenant.email, since=since, until=until,
            summary_text=R.summary_lines(items),
            summary=[SummaryLine(outcome=o, count=sum(1 for i in items if i.outcome == o),
                                 state=next((state_of(i) for i in items if i.outcome == o), ""))
                     for o in R.OUTCOMES if any(i.outcome == o for i in items)],
            shops=[{"id": str(s[0]), "name": s[1]} for s in shops],
            items=[AccountItem(content_id=i.content.id, shop=i.shop.shop_name if i.shop else None,
                               shop_id=i.shop.id if i.shop else None,
                               etsy_listing_id=i.publication.etsy_listing_id if i.publication is not None else None,
                               sku=i.sku, title=(i.content.title or "")[:R.TITLE_CHARS], outcome=i.outcome,
                               state=state_of(i), why=i.why)
                   for i in chosen[(page - 1) * per: page * per]],
            total=len(chosen), page=page, pages=pages,
        )
    finally:
        await session.rollback()


@router.get("/users/{tenant_id}/publishing/detail", response_model=Detail)
async def account_publishing_detail(
    tenant_id: uuid.UUID,
    content: uuid.UUID,
    shop: uuid.UUID | None = None,
    _admin: Tenant = Depends(require_admin),
    session: AsyncSession = Depends(get_session, scope="function"),
) -> Detail:
    await _read_only(session)
    try:
        now = datetime.now(timezone.utc)
        tenant = await _account(session, tenant_id)
        listing = await session.get(GeneratedContent, content)
        if listing is None or listing.tenant_id != tenant.id:
            raise HTTPException(status_code=404, detail="no such listing")
        items = await R.gather(session, tenant, (now - timedelta(days=R.DEFAULT_DAYS)).date(), now, content_ids={content})
        found = next((i for i in items if (i.shop.id if i.shop else None) == shop), None) or (items[0] if items else None)
        if found is None:
            raise HTTPException(status_code=404, detail="no such listing")
        found.outcome, found.why = R.classify(found, now)
        return Detail(lines=R.item_lines(found))
    finally:
        await session.rollback()

