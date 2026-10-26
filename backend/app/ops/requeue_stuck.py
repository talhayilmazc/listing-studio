"""``python -m app.cli requeue-stuck <email | --all> [--apply]``: approved listings the old rules stuck.

Until 2026-10-26 a listing the app had written was held to the app's own quality rules (exactly
13 tags, the profile's title length, the product type in the title and a tag) at approval,
sending, drafting and going live, even after the seller rewrote its text by hand, while a
listing typed from scratch was held only to Etsy's own refusals. Now every listing is held to
the same rules (``content.check_listing``). This finds the approved listings left behind and
sends them on the normal path, only as far as the seller had already asked:

* a draft the app stopped (a failed draft job with no draft in that shop, not refused by Etsy):
  a new draft job, which makes the draft; its schedule follows as usual;
* a group-schedule slot that failed the same way: it waits again and drafts now, and goes live
  at the next time the shop's rules allow if its time passed (``reschedule.settle``);
* a scheduled go-live the release held for the listing's text ("Not published: …"): released
  again, at the next allowed time if its time passed;
* approved but never sent to a shop: listed only (the seller never pressed send for it).

A listing whose text still breaks a rule (Etsy's own, the trademark filter, a blocking
compliance finding) or whose personalization is held is listed with why and left alone.
**Nothing that is not approved is drafted or published**, and nothing goes live except at a time
the seller confirmed (or the next one the shop's rules allow after it). The dry run, the default,
changes nothing. Every listing says who wrote it ("ai" or "manual").
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import audit
from app.core.logsafety import redact
from app.db.models import (
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    PlannedSlot,
    Tenant,
)
from app.etsy import reconcile
from app.etsy.scheduling import NOT_APPROVED
from app.pipeline import personalization as P

TITLE_CHARS = 40
#: A held go-live whose note is about these is not the listing's text: left alone.
NOT_TEXT = (NOT_APPROVED, "Not published: the account is suspended.", "Not published: the shop is no longer connected.",
            reconcile.DELETED_MARK)

DRAFT = "draft queued again"
SLOT = "group schedule waits again"
GO_LIVE = "go-live released again"
NEVER_SENT = "approved, never sent (send it from the review page)"
STILL = "still stopped"


@dataclass
class Found:
    content: GeneratedContent
    shop: EtsyConnection | None
    action: str
    why: str
    job: Job | None = None
    slot: PlannedSlot | None = None
    publication: ListingPublication | None = None

    @property
    def written_from(self) -> str:
        return "manual" if self.content.source == "manual" else "ai"


@dataclass
class Account:
    tenant: Tenant
    found: list[Found] = field(default_factory=list)
    queued: list[uuid.UUID] = field(default_factory=list)


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


async def _still(session: AsyncSession, content: GeneratedContent) -> str | None:
    """Why it still cannot go (its text, compliance, personalization), or None."""
    from app.compliance.check import listing_problem, personalization_hold

    problem = await listing_problem(session, content)
    if problem:
        return problem
    return await personalization_hold(session, content)


async def _scan(session: AsyncSession, tenant: Tenant) -> Account:
    account = Account(tenant)
    shops = {s.id: s for s in (await session.execute(select(EtsyConnection).where(
        EtsyConnection.tenant_id == tenant.id, EtsyConnection.status == ConnectionStatus.active))).scalars()}
    contents = {c.id: c for c in (await session.execute(select(GeneratedContent).where(
        GeneratedContent.tenant_id == tenant.id, GeneratedContent.approved.is_(True)))).scalars()}
    if not contents:
        return account
    drafts = {(p.content_id, p.connection_id): p for p in (await session.execute(select(ListingPublication).where(
        ListingPublication.tenant_id == tenant.id, ListingPublication.content_id.in_(list(contents)),
        ListingPublication.state != "deleted_on_etsy"))).scalars()}
    newest: dict[tuple[uuid.UUID, uuid.UUID], Job] = {}
    for job in (await session.execute(select(Job).where(
            Job.tenant_id == tenant.id, Job.type == JobType.create_draft).order_by(Job.created_at))).scalars():
        try:
            cid = uuid.UUID(str((job.payload or {}).get("content_id")))
        except ValueError:
            continue
        if cid in contents and job.connection_id is not None:
            newest[(cid, job.connection_id)] = job
    slots: dict[uuid.UUID, list[PlannedSlot]] = defaultdict(list)
    for slot in (await session.execute(select(PlannedSlot).where(
            PlannedSlot.tenant_id == tenant.id, PlannedSlot.content_id.in_(list(contents))))).scalars():
        slots[slot.content_id].append(slot)
    still: dict[uuid.UUID, str | None] = {}

    async def blocked(content: GeneratedContent) -> str | None:
        if content.id not in still:
            still[content.id] = await _still(session, content)
        return still[content.id]

    touched: set[uuid.UUID] = set()
    for (cid, shop_id), job in newest.items():
        content, shop = contents[cid], shops.get(shop_id)
        if shop is None or (cid, shop_id) in drafts or job.status is not JobStatus.failed:
            continue
        if (job.payload or {}).get("etsy_error") or (job.payload or {}).get("planned_slot") or P.is_held(job.last_error):
            continue  # refused by Etsy (the seller's fix), a slot's own draft (below), or the personalization path
        touched.add(cid)
        why = await blocked(content)
        account.found.append(Found(content, shop, STILL if why else DRAFT,
                                   why or f"its draft was stopped before Etsy: {redact(job.last_error or '')[:120]}", job=job))
    for cid, rows in slots.items():
        content = contents[cid]
        for slot in rows:
            shop = shops.get(slot.connection_id)
            if slot.state != "failed" or shop is None or (cid, slot.connection_id) in drafts or P.is_held(slot.note):
                continue
            if "Etsy" in (slot.note or "") and "refus" in (slot.note or "").lower():
                continue
            touched.add(cid)
            why = await blocked(content)
            account.found.append(Found(content, shop, STILL if why else SLOT,
                                       why or f"its group schedule failed: {redact(slot.note or '')[:120]}", slot=slot))
    for (cid, shop_id), pub in drafts.items():
        content, shop = contents[cid], shops.get(shop_id)
        note = pub.schedule_note or ""
        if shop is None or pub.state != "draft" or pub.scheduled_for is None or not note.startswith("Not published: "):
            continue
        if note in NOT_TEXT or note.startswith("Not published: it is "):  # changed on Etsy (etsy/reconcile.py)
            continue
        touched.add(cid)
        why = await blocked(content)
        account.found.append(Found(content, shop, STILL if why else GO_LIVE,
                                   why or f"its go-live was held: {redact(note)[:120]}", publication=pub))
    open_slot = {(s.content_id, s.connection_id) for rows in slots.values() for s in rows
                 if s.state in ("waiting", "drafting", "scheduled")}
    for cid, content in contents.items():
        if cid in touched or any(k[0] == cid for k in drafts) or any(k[0] == cid for k in newest) \
                or any(k[0] == cid for k in open_slot):
            continue
        why = await blocked(content)
        account.found.append(Found(content, None, STILL if why else NEVER_SENT, why or "nothing stops it now"))
    return account


async def _apply(session: AsyncSession, account: Account, now: datetime) -> None:
    from app.api.personalization_fix import _next_allowed

    moved: list[PlannedSlot] = []
    released: list[ListingPublication] = []
    for f in account.found:
        if f.action == DRAFT and f.job is not None:
            again = Job(tenant_id=f.job.tenant_id, connection_id=f.job.connection_id, type=JobType.create_draft,
                        batch_id=f.job.batch_id, status=JobStatus.queued,
                        payload={k: v for k, v in (f.job.payload or {}).items() if k not in ("etsy_error", "left_out")})
            session.add(again)
            await session.flush()
            account.queued.append(again.id)
        elif f.action == SLOT and f.slot is not None:
            f.slot.state, f.slot.note, f.slot.job_id, f.slot.draft_at = "waiting", None, None, now
            moved.append(f.slot)
        elif f.action == GO_LIVE and f.publication is not None:
            f.publication.schedule_note = None
            f.publication.schedule_job_id = None
            released.append(f.publication)
        else:
            continue
        audit.record(session, "listing.requeued", actor=None, target_tenant_id=account.tenant.id, by="system",
                     content_id=str(f.content.id), shop_id=str(f.shop.id) if f.shop else None, step=f.action)
    await session.flush()
    await _next_allowed(session, account.tenant, moved, now)
    await _go_live_next_allowed(session, account.tenant, released, now)


async def _go_live_next_allowed(session: AsyncSession, tenant: Tenant, pubs: list[ListingPublication], now: datetime) -> None:
    """Released go-lives whose time passed: the next time each shop's rules allow."""
    from app.api import scheduled as S
    from app.pipeline import reschedule as R

    late = [p for p in pubs if _aware(p.scheduled_for) < now + R.LEAD]
    if not late:
        return
    world = await S._world(session, tenant)
    items = S._items(world)
    keys = {i.key for i in items}
    desired = {f"pub:{p.id}": now for p in late if f"pub:{p.id}" in keys}
    placed = {c.key: c.new for c in R.settle(items, desired, world.rules, now)} if desired else {}
    for pub in late:
        pub.scheduled_for = placed.get(f"pub:{pub.id}") or (now + R.LEAD)


async def run(
    sm: async_sessionmaker,
    email: str | None = None,
    *,
    every: bool = False,
    apply: bool = False,
    enqueue: Any = None,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    lines: list[str] = []
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"ai": 0, "manual": 0})
    queued: list[uuid.UUID] = []
    async with sm() as session:
        query = select(Tenant).order_by(Tenant.email)
        if not every:
            query = query.where(Tenant.email == (email or "").strip().lower())
        tenants = list((await session.execute(query)).scalars())
    if not tenants:
        return f"no account with the address {email}"
    for tenant in tenants:
        async with sm() as session:
            tenant = await session.get(Tenant, tenant.id)
            account = await _scan(session, tenant)
            if not account.found:
                await session.rollback()
                continue
            # Said before the commit or rollback (which expire the rows).
            lines.append(f"== {tenant.email} (account {str(tenant.id)[:8]})")
            for f in account.found:
                totals[f.action][f.written_from] += 1
                shop = f.shop.shop_name if f.shop is not None and f.shop.shop_name else ("no shop" if f.shop is None else "a shop")
                title = redact(f.content.title or "")[:TITLE_CHARS]
                lines.append(f"  {f.action} · {shop} · listing {str(f.content.id)[:8]} · written {f.written_from} · "
                             f"\"{title}\" · {f.why}")
            lines.append("")
            if apply:
                await _apply(session, account, now)
                await session.commit()
                queued += account.queued
            else:
                await session.rollback()  # listing_problem rescans: nothing it writes is kept
    if apply and queued:
        if enqueue is None:
            from app.api.deps import get_enqueuer

            enqueue = get_enqueuer().enqueue
        for job_id in queued:
            await enqueue("run_publish_job", str(job_id))
    mode = ("applied: drafts queued, schedules released; nothing unapproved is touched" if apply
            else "dry run: nothing changed (--apply sends them on)")
    head = [f"requeue-stuck ({'every account' if every else tenants[0].email}): {mode}", "", "== summary (written by the app | by hand)"]
    for action in (DRAFT, SLOT, GO_LIVE, NEVER_SENT, STILL):
        n = totals.get(action)
        if n:
            head.append(f"  {action}: {n['ai'] + n['manual']} (ai {n['ai']}, manual {n['manual']})")
    if not totals:
        head.append("  nothing stuck")
    return "\n".join(head + [""] + lines).rstrip() + "\n"
