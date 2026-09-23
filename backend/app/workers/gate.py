"""The check every job passes before it may touch Etsy.

Run first by every worker entry point:

* **Suspended tenant.** An admin suspension drops the tenant's work. Queued job
  rows are cancelled when the account is suspended (``api/admin.py``). This check
  catches what was already sitting in the queue, including the jobs that have no
  row.
* **Daily budget (production-spec C).** New work pauses until the next UTC
  midnight in two cases: once the app has used ``global_pause_percent`` of Etsy's
  shared daily limit, or when the tenant's own allowance cannot cover the job.
  Pausing *before* the first request means a multi-request job such as a publish
  is never cut off halfway at the hard wall. The reason is recorded on the job
  row and in a per-tenant marker, so the seller is told why their work waits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, JobStatus, Tenant, TenantStatus
from app.etsy.calllog import current_job
from app.etsy.rate_limiter import PAUSE_TENANT

# Upper bound on the Etsy requests one run of each job can make. A job starts only
# if this fits in what the tenant has left today.
JOB_COST: dict[str, int] = {
    # shop, section, create, read-back, properties, ~8 attribute writes,
    # inventory, up to 10 new images and the fixed ones
    "run_publish_job": 30,
    "run_publish_live_job": 3,
    # listing, images, up to 10 deletes and 10 uploads, update, inventory
    "run_replace_images_job": 30,
    # shop, listing, inventory, images, properties
    "refresh_profile": 6,
    # shop and two listing pages
    "sync_shop_listings": 4,
    # shop, listings, taxonomy, and an inventory read for each of up to 100 listings
    "detect_profiles": 110,
}

# Resume a little after midnight, so the new day's counters are in place.
RESUME_SLACK_SECONDS = 60

SUSPENDED_MESSAGE = "cancelled: the account is suspended"


@dataclass(frozen=True)
class Verdict:
    action: Literal["run", "suspended", "paused"]
    reason: str | None = None
    resumes_at: datetime | None = None


RUN = Verdict("run")


def next_reset(now: datetime) -> datetime:
    """The next UTC midnight, when both daily counters start again."""
    tomorrow = (now + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def check(ctx: dict[str, Any], tenant: Tenant | None, function: str) -> Verdict:
    """May this tenant's job start now?"""
    if tenant is None or tenant.status is TenantStatus.suspended:
        return Verdict("suspended")
    quota = ctx.get("quota")
    if quota is None:  # no budget wired (unit tests of the job bodies)
        return RUN
    reason = await quota.admission(tenant.id, tenant.daily_quota, JOB_COST[function])
    if reason is None:
        return RUN
    await quota.mark_paused(tenant.id, reason)
    return Verdict("paused", reason, next_reset(_now()))


async def paused_by_wall(ctx: dict[str, Any], tenant: Tenant) -> Verdict:
    """A job hit the hard daily limit mid-run. Say which budget ran out."""
    quota = ctx.get("quota")
    reason = None
    if quota is not None:
        reason = await quota.admission(tenant.id, tenant.daily_quota, 1)
    reason = reason or PAUSE_TENANT
    if quota is not None:
        await quota.mark_paused(tenant.id, reason)
    return Verdict("paused", reason, next_reset(_now()))


async def requeue(
    ctx: dict[str, Any],
    function: str,
    *args: str,
    resumes_at: datetime,
    job_key: str | None = None,
) -> None:
    """Run ``function(*args)`` again once the budget resets.

    ``job_key`` collapses repeats: a profile refreshed three times during a pause
    runs once after midnight, not three times.
    """
    delay = max(0.0, (resumes_at - _now()).total_seconds()) + RESUME_SLACK_SECONDS
    kwargs: dict[str, Any] = {"_defer_by": delay}
    if job_key is not None:
        kwargs["_job_id"] = job_key
    enqueue = ctx.get("enqueue")  # test hook
    if enqueue is not None:
        await enqueue(function, *args, **kwargs)
        return
    await ctx["redis"].enqueue_job(function, *args, **kwargs)


def pause_key(function: str, arg: str, resumes_at: datetime) -> str:
    return f"paused:{function}:{arg}:{resumes_at.date().isoformat()}"


async def start_job(
    ctx: dict[str, Any], session: AsyncSession, job: Job, function: str
) -> str | None:
    """Gate a job that has a ``job`` row.

    Returns ``None`` when the job may run; it is then marked running. Otherwise
    the job was skipped, cancelled or paused, and the return value is the worker
    result to report.
    """
    if job.status in (JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled):
        return "skipped"
    tenant = await session.get(Tenant, job.tenant_id)
    verdict = await check(ctx, tenant, function)
    now = _now()

    if verdict.action == "suspended":
        job.status = JobStatus.cancelled
        job.last_error = SUSPENDED_MESSAGE
        job.paused_reason = None
        job.finished_at = now
        await session.commit()
        return "cancelled"

    if verdict.action == "paused":
        assert verdict.resumes_at is not None
        job.status = JobStatus.queued
        job.paused_reason = verdict.reason
        job.scheduled_at = verdict.resumes_at
        await session.commit()
        await requeue(ctx, function, str(job.id), resumes_at=verdict.resumes_at)
        return "deferred"

    job.paused_reason = None
    job.status = JobStatus.running
    job.started_at = now
    await session.commit()
    # Tag every Etsy request this job makes, for the call log.
    current_job.set(f"{function}:{job.id}")
    return None
