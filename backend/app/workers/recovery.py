"""What happens to a publish job that did not finish (Priority 1, 2026-10).

Creating a draft and going live are resumable (etsy/publisher.py), so a job
that could not finish for a reason that will pass is run again rather than
failed: the seller sees "waiting", not an error, and nothing is created twice.

- Etsy still answering 429 after the client's own retries: again after the wait
  Etsy asked for (at least a minute).
- No answer or a 5xx that outlasted the client's retries, or the publisher
  saying "not yet": again after 30 s, 1, 2, 4 minutes.
- The app's daily Etsy budget running out partway: again after the daily reset,
  like a job that had not started.
- The worker stopping, or the job passing arq's time limit: again shortly.

Each of these counts an attempt; after ``job.max_attempts`` the job fails with
what kept happening, and the seller's "Try again" carries on from there.
Anything else is a real failure and is explained on the card at once.

A job never runs twice at the same moment: :func:`holding` takes a lock in
Redis for the job's id, so arq re-running a cancelled job while another copy is
still going (or two queued copies) cannot both write to Etsy.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Job, JobStatus, JobType, Tenant
from app.etsy.api import RateLimitExceeded
from app.etsy.errors import EtsyRateLimited, EtsyServerError
from app.etsy.publisher import NotYet
from app.workers import gate
from app.workers.guards import public_error

logger = logging.getLogger(__name__)

#: Why a job is waiting to run again; job.paused_reason, turned into a sentence by api/pauses.py.
WAIT_ETSY_RATE = "etsy_rate_limit"
WAIT_ETSY_DOWN = "etsy_unavailable"
WAIT_INTERRUPTED = "interrupted"

MIN_RATE_WAIT = 60.0
MAX_RATE_WAIT = 15 * 60.0
INTERRUPTED_WAIT = 20.0

#: The arq function for each resumable job type.
FUNCTIONS: dict[JobType, str] = {
    JobType.create_draft: "run_publish_job",
    JobType.publish_live: "run_publish_live_job",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def outage_wait(attempt: int) -> float:
    """30 s, 1, 2, 4 minutes for attempts 1, 2, 3, 4."""
    return min(30.0 * 2 ** (max(1, attempt) - 1), 300.0)


@asynccontextmanager
async def holding(ctx: dict[str, Any], job_id: str) -> AsyncIterator[bool]:
    """Take the run lock for a job; yields whether this call holds it."""
    redis = ctx.get("redis")
    if redis is None:  # unit tests of the job bodies
        yield True
        return
    key = f"job-run:{job_id}"
    held = bool(await redis.set(key, "1", nx=True, ex=int(get_settings().worker_job_timeout) + 60))
    try:
        yield held
    finally:
        if held:
            try:
                await asyncio.shield(redis.delete(key))
            except Exception:  # noqa: BLE001 - the lock expires on its own
                logger.warning("could not release the run lock of job %s", job_id)


async def _enqueue(ctx: dict[str, Any], function: str, job_id: str, delay: float) -> None:
    enqueue = ctx.get("enqueue")  # test hook
    if enqueue is not None:
        await enqueue(function, job_id, _defer_by=delay)
        return
    await ctx["redis"].enqueue_job(function, job_id, _defer_by=delay)


async def _again(
    ctx: dict[str, Any], session: AsyncSession, job: Job, function: str, *, reason: str, delay: float, exhausted: str
) -> str:
    """Queue the job to run again after ``delay``, or fail it once it has had its attempts."""
    job.attempts = (job.attempts or 0) + 1
    if job.attempts >= (job.max_attempts or 5):
        job.status = JobStatus.failed
        job.paused_reason = None
        job.last_error = exhausted
        job.finished_at = _now()
        await session.commit()
        return "failed"
    job.status = JobStatus.queued
    job.paused_reason = reason
    job.scheduled_at = _now() + timedelta(seconds=delay)
    await session.commit()
    await _enqueue(ctx, function, str(job.id), delay)
    return "deferred"


async def after_failure(
    ctx: dict[str, Any], session: AsyncSession, job_id: Any, exc: Exception, function: str
) -> str:
    """Decide what a job that raised becomes: waiting to run again, or failed with the reason."""
    await session.rollback()  # whatever the failing step left half-done
    job = await session.get(Job, job_id, populate_existing=True)
    assert job is not None

    if isinstance(exc, RateLimitExceeded):
        # The app's own daily budget ran out partway. Nothing is lost: it waits
        # for the reset like a job that had not started, then carries on.
        tenant = await session.get(Tenant, job.tenant_id)
        assert tenant is not None
        verdict = await gate.paused_by_wall(ctx, tenant)
        assert verdict.resumes_at is not None
        job.status = JobStatus.queued
        job.paused_reason = verdict.reason
        job.scheduled_at = verdict.resumes_at
        await session.commit()
        await gate.requeue(ctx, function, str(job.id), resumes_at=verdict.resumes_at)
        return "deferred"

    if isinstance(exc, EtsyRateLimited):
        wait = min(max(exc.retry_after or 0.0, MIN_RATE_WAIT), MAX_RATE_WAIT)
        return await _again(
            ctx, session, job, function, reason=WAIT_ETSY_RATE, delay=wait,
            exhausted="Etsy kept saying requests were coming too fast, so this stopped. Nothing is wrong with "
                      "the listing: try again in a few minutes and it carries on where it stopped.",
        )

    if isinstance(exc, (httpx.TransportError, EtsyServerError, NotYet)):
        delay = exc.seconds if isinstance(exc, NotYet) else outage_wait((job.attempts or 0) + 1)
        return await _again(
            ctx, session, job, function, reason=WAIT_ETSY_DOWN, delay=delay,
            exhausted="Etsy did not answer, or answered with an error of its own, several times in a row, so this "
                      "stopped. Nothing is wrong with the listing: try again and it carries on where it stopped.",
        )

    job.status = JobStatus.failed
    job.paused_reason = None
    job.last_error = public_error(exc)
    job.finished_at = _now()
    await session.commit()
    logger.exception("%s failed for job %s", function, job.id, exc_info=exc)
    return "failed"


async def interrupted(ctx: dict[str, Any], session: AsyncSession, job_id: Any, function: str) -> None:
    """The job was cancelled mid-run (worker stopping, or arq's time limit).

    Runs while the task is being cancelled, so it is shielded and never raises:
    if it cannot finish, :func:`recover_interrupted_jobs` finds the row later.
    """

    async def record() -> None:
        async with ctx["sessionmaker"]() as fresh:  # the cancelled session may be mid-statement
            job = await fresh.get(Job, job_id)
            if job is None or job.status is not JobStatus.running:
                return
            await _again(
                ctx, fresh, job, function, reason=WAIT_INTERRUPTED, delay=INTERRUPTED_WAIT,
                exhausted="This was interrupted several times before it could finish. Try again: it carries on "
                          "where it stopped.",
            )

    try:
        await asyncio.shield(record())
    except BaseException:  # noqa: BLE001 - never mask the cancellation
        logger.warning("could not record the interruption of job %s", job_id)


async def recover_interrupted_jobs(ctx: dict[str, Any]) -> int:
    """Cron: publish jobs left "running" by a worker that died without a word
    (killed, out of memory) are queued to run again; they carry on where they stopped."""
    limit = _now() - timedelta(seconds=get_settings().worker_job_timeout + 120)
    recovered = 0
    async with ctx["sessionmaker"]() as session:
        rows = await session.execute(
            select(Job).where(
                Job.status == JobStatus.running, Job.type.in_(tuple(FUNCTIONS)), Job.started_at < limit
            )
        )
        for job in rows.scalars():
            result = await _again(
                ctx, session, job, FUNCTIONS[job.type], reason=WAIT_INTERRUPTED, delay=INTERRUPTED_WAIT,
                exhausted="This was interrupted several times before it could finish. Try again: it carries on "
                          "where it stopped.",
            )
            recovered += 1
            logger.warning("job %s was left running since %s; %s", job.id, job.started_at, result)
    return recovered
