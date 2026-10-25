"""Group schedules at work (v8 §B).

``release_planned_drafts`` (cron, every minute): each planned slot whose draft
time has come becomes the same draft job "Create draft" queues, so the gate,
the daily budget (a full day defers it to 00:00 UTC: it spills, it never runs
past the ceiling), the compliance gate and the read-back all apply.

``after_draft`` (called by the draft worker when a draft is made): the
publication is scheduled for the time the seller confirmed; from there the
scheduled-publish cron takes it live, re-checking the approval and the
compliance findings first (workers/schedule.py). A draft made after its time
(it spilled to a later day) goes live at the next release, not never.

Slots live in the database, not in a deferred queue, so a restart or an empty
Redis loses nothing: the cron finds what is due.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    PlannedSlot,
)

logger = logging.getLogger(__name__)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _unfinished_draft(session: AsyncSession, content_id: uuid.UUID, connection_id: uuid.UUID) -> Job | None:
    """A draft job of this listing for this shop that is queued or running, if there is one."""
    jobs = (await session.execute(select(Job).where(
        Job.connection_id == connection_id, Job.type == JobType.create_draft,
        Job.status.in_((JobStatus.queued, JobStatus.running)),
    ))).scalars().all()
    return next((j for j in jobs if (j.payload or {}).get("content_id") == str(content_id)), None)


async def release_due(session: AsyncSession, enqueue: Any, *, tenant_id: uuid.UUID | None = None,
                      now: datetime | None = None) -> int:
    """Queue the draft of every slot that is due. Commits."""
    now = now or datetime.now(timezone.utc)
    query = select(PlannedSlot).where(PlannedSlot.state == "waiting", PlannedSlot.draft_at <= now)
    if tenant_id is not None:
        query = query.where(PlannedSlot.tenant_id == tenant_id)
    released = 0
    from app.pipeline.schedule_pause import is_paused, paused_shops

    due = list((await session.execute(query.order_by(PlannedSlot.draft_at))).scalars())
    paused = await paused_shops(session, {s.tenant_id for s in due}) if due else (set(), set())
    for slot in due:
        if is_paused(slot.tenant_id, slot.connection_id, paused):
            continue  # paused on the Scheduled page: held, not cancelled
        content = await session.get(GeneratedContent, slot.content_id) if slot.content_id else None
        shop = await session.get(EtsyConnection, slot.connection_id)
        if content is None or shop is None or content.tenant_id != slot.tenant_id or shop.tenant_id != slot.tenant_id:
            slot.state, slot.note = "failed", "the listing or the shop is no longer there"
            continue
        if shop.status is not ConnectionStatus.active:
            slot.state, slot.note = "cancelled", "the shop is no longer connected"  # review R15
            continue
        if not content.approved:
            slot.state, slot.note = "cancelled", "the listing is no longer approved"
            continue
        existing = await session.scalar(select(ListingPublication.id).where(
            ListingPublication.content_id == content.id, ListingPublication.connection_id == shop.id))
        if existing is not None:
            # A draft is there already (made by hand meanwhile): schedule it instead.
            await _schedule(session, slot, now)
            continue
        # A draft of this listing for this shop already on its way ("Create drafts" by hand
        # meanwhile): the slot follows that job instead of queueing a second one (review R12).
        unfinished = await _unfinished_draft(session, content.id, shop.id)
        if unfinished is not None:
            slot.job_id, slot.state = unfinished.id, "drafting"
            await session.commit()
            continue
        job = Job(tenant_id=slot.tenant_id, connection_id=shop.id, type=JobType.create_draft,
                  payload={"content_id": str(content.id), "planned_slot": str(slot.id)},
                  batch_id=content.batch_id, status=JobStatus.queued)
        session.add(job)
        await session.flush()
        slot.job_id, slot.state = job.id, "drafting"
        await session.commit()
        await enqueue("run_publish_job", str(job.id))
        released += 1
    # Drafts that failed for good: the slot says why (the card offers the retry). A draft that
    # finished but whose go-live was never set (the worker stopped between the two): set it now,
    # never left "drafting" for ever.
    for slot in list((await session.execute(select(PlannedSlot).where(PlannedSlot.state == "drafting"))).scalars()):
        job = await session.get(Job, slot.job_id) if slot.job_id else None
        if job is not None and job.status in (JobStatus.failed, JobStatus.cancelled):
            slot.state, slot.note = "failed", job.last_error or "the draft could not be created"
        elif job is not None and job.status is JobStatus.succeeded:
            content = await session.get(GeneratedContent, slot.content_id) if slot.content_id else None
            if content is None or not content.approved:
                slot.state, slot.note = "cancelled", "the listing is no longer approved; the draft stays a draft"
            else:
                await _schedule(session, slot, now)
    await session.commit()
    return released


async def _schedule(session: AsyncSession, slot: PlannedSlot, now: datetime) -> None:
    publication = (await session.execute(select(ListingPublication).where(
        ListingPublication.content_id == slot.content_id, ListingPublication.connection_id == slot.connection_id
    ))).scalar_one_or_none()
    if publication is None:
        return
    if publication.state == "active":
        slot.state, slot.note = "scheduled", "already live"
        return
    publish_at = _aware(slot.publish_at)
    # Spilled past its time: the next release takes it live.
    publication.scheduled_for = max(publish_at, now + timedelta(minutes=1))
    publication.schedule_job_id = None
    publication.schedule_note = None
    slot.state = "scheduled"
    if publish_at < now:
        slot.note = "the draft was made after its time (the day's budget was full); it goes live at the next release"


async def after_draft(session: AsyncSession, content_id: uuid.UUID, connection_id: uuid.UUID) -> None:
    """A draft of a planned slot was made: schedule its go-live. Commits."""
    slot = (await session.execute(select(PlannedSlot).where(
        PlannedSlot.content_id == content_id, PlannedSlot.connection_id == connection_id,
        PlannedSlot.state == "drafting",
    ))).scalars().first()
    if slot is None:
        return
    content = await session.get(GeneratedContent, content_id)
    if content is None or not content.approved:
        slot.state, slot.note = "cancelled", "the listing is no longer approved; the draft stays a draft"
    else:
        await _schedule(session, slot, datetime.now(timezone.utc))
    await session.commit()


async def release_planned_drafts(ctx: dict[str, Any]) -> int:
    """arq cron."""
    async def enqueue(function: str, *args: Any) -> None:
        hook = ctx.get("enqueue")
        if hook is not None:
            await hook(function, *args)
        else:
            await ctx["redis"].enqueue_job(function, *args)

    async with ctx["sessionmaker"]() as session:
        released = await release_due(session, enqueue)
    if released:
        logger.info("group schedules: released %d draft(s)", released)
    return released
