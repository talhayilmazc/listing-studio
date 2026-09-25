"""Scheduled publishing (docs/duzeltmeler-v6.md §G).

A seller may choose when an approved listing's draft goes live. That choice is
the seller's explicit confirmation (CLAUDE.md rule 3): a schedule can only be
set on the existing draft of a listing the seller approved one by one, and
nothing the seller did not approve and schedule is ever published.

The database holds the schedule (``listing_publication.scheduled_for``, UTC).
A cron (``workers/schedule.py``) releases each one when it is due, as the same
publish-live job "Publish now" queues, so the daily budget, the pause at 90%,
the compliance check and the go-live read-back all apply unchanged. Approval
and compliance are checked again at that moment.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GeneratedContent, Job, JobStatus, JobType, ListingPublication

#: How far ahead a listing can be scheduled.
MAX_AHEAD = timedelta(days=60)
#: A time this close to now (or just past it) means "at the next release".
GRACE = timedelta(minutes=2)

NOT_APPROVED = "Not published: the listing is no longer approved."
ALREADY_LIVE = "Already live; it was published before its scheduled time."


class ScheduleRefused(Exception):
    """Why this draft cannot be scheduled, worded for the seller."""


class ScheduleBusy(ScheduleRefused):
    """The scheduled job is already running; it can no longer be changed."""


@dataclass(frozen=True)
class ScheduleState:
    #: "scheduled" | "publishing" | "waiting" | "published" | "failed" | "not_published"
    status: str
    note: str | None = None


def check_time(run_at: datetime, now: datetime | None = None) -> datetime:
    """The UTC time to store, or ScheduleRefused."""
    now = now or datetime.now(timezone.utc)
    if run_at.tzinfo is None:
        raise ScheduleRefused("the time must include its time zone")
    run_at = run_at.astimezone(timezone.utc)
    if run_at < now - GRACE:
        raise ScheduleRefused("that time has already passed")
    if run_at > now + MAX_AHEAD:
        raise ScheduleRefused(f"a listing can be scheduled at most {MAX_AHEAD.days} days ahead")
    return run_at


async def _job(session: AsyncSession, publication: ListingPublication) -> Job | None:
    if publication.schedule_job_id is None:
        return None
    return await session.get(Job, publication.schedule_job_id)


async def cancel(session: AsyncSession, publication: ListingPublication) -> None:
    """Withdraw this draft's schedule. A released job that has not started is
    cancelled too; one already running cannot be stopped. Does not commit."""
    job = await _job(session, publication)
    if job is not None and job.status is JobStatus.running:
        raise ScheduleBusy("it is being published right now")
    if job is not None and job.status is JobStatus.queued:
        job.status = JobStatus.cancelled
        job.finished_at = datetime.now(timezone.utc)
        job.last_error = "cancelled: the seller withdrew the schedule"
    publication.scheduled_for = None
    publication.schedule_job_id = None
    publication.schedule_note = None


async def set_schedule(
    session: AsyncSession,
    content: GeneratedContent,
    publication: ListingPublication,
    run_at: datetime,
) -> None:
    """Schedule (or move) this draft's go-live. Does not commit.

    Only an approved listing's existing draft qualifies; the caller has checked
    ownership, the shop and the listing's own text.
    """
    if not content.approved:
        raise ScheduleRefused("approve the listing first")
    if publication.state == "active":
        raise ScheduleRefused("this listing is already live")
    run_at = check_time(run_at)
    await cancel(session, publication)  # moving a schedule replaces it
    publication.scheduled_for = run_at


async def cancel_for_content(session: AsyncSession, content_id: uuid.UUID) -> None:
    """Withdraw every schedule of one listing (it lost its approval). Does not commit."""
    rows = await session.execute(
        select(ListingPublication).where(
            ListingPublication.content_id == content_id,
            ListingPublication.scheduled_for.is_not(None),
            ListingPublication.state != "active",
        )
    )
    for publication in rows.scalars():
        try:
            await cancel(session, publication)
        except ScheduleBusy:
            pass  # already going live; the job re-checks approval itself


def state_of(publication: ListingPublication, job: Job | None) -> ScheduleState | None:
    """Where a schedule stands, for the seller; None when there is none."""
    if publication.scheduled_for is None:
        return None
    if job is not None and job.status is JobStatus.succeeded:
        return ScheduleState("published")
    if publication.state == "active":
        return ScheduleState("published", None if job is not None else ALREADY_LIVE)
    if job is None:
        if publication.schedule_note:
            return ScheduleState("not_published", publication.schedule_note)
        return ScheduleState("scheduled")
    if job.status is JobStatus.queued:
        if job.paused_reason:
            return ScheduleState("waiting", job.paused_reason)
        return ScheduleState("publishing")
    if job.status is JobStatus.running:
        return ScheduleState("publishing")
    if job.status is JobStatus.failed:
        return ScheduleState("failed", job.last_error)
    return ScheduleState("not_published", job.last_error or "cancelled")


def new_publish_job(content: GeneratedContent, publication: ListingPublication) -> Job:
    """The publish-live job a due schedule releases: the same one "Publish now" queues."""
    return Job(
        tenant_id=content.tenant_id,
        connection_id=publication.connection_id,
        type=JobType.publish_live,
        payload={"content_id": str(content.id), "scheduled": True},
        batch_id=content.batch_id,
        status=JobStatus.queued,
        scheduled_at=publication.scheduled_for,
    )
