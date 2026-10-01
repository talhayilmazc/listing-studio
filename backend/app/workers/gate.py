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
* **Scheduled go-lives come first.** The requests the seller's scheduled
  publishes still need before the reset are held back from every other job, so
  a batch run earlier in the day can't use them up and push a scheduled listing
  to the reset (00:00 UTC is 7 PM in US Central summer time: a 5 PM schedule
  would go out two hours late).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, JobStatus, ListingPublication, Tenant, TenantStatus
from app.etsy.calllog import current_job
from app.etsy.rate_limiter import PAUSE_TENANT

# Upper bound on the Etsy requests one run of each job can make. A job starts only
# if this fits in what the tenant has left today.
JOB_COST: dict[str, int] = {
    # shop, section, create, read-back, properties, ~8 attribute writes,
    # inventory, up to 10 new images and the fixed ones, and personalization with
    # its read-back (v7 §D4)
    "run_publish_job": 32,
    "run_publish_live_job": 3,
    # listing, images, up to 10 deletes and 10 uploads, update, inventory
    "run_replace_images_job": 30,
    # shop, listing, inventory, images, properties, personalization (v7 §D4)
    "refresh_profile": 7,
    # the reference's images; when they changed, the full refresh as well (v6 §H)
    "refresh_profile_images": 8,
    # shop, and up to 50 pages of 100 for each of the 5 listing states
    # (profiles.SYNC_STATES, SYNC_MAX_PAGES); one per state for a small shop
    "sync_shop_listings": 251,
    # shop, up to 10 pages of active listings (inventory included), taxonomy, and
    # up to 100 separate inventory reads for listings a page returned without it
    "detect_profiles": 112,
    # shop, and one chunk of the shop's own sales: 20 pages of 100 (workers/sales.py)
    "sync_sales": 21,
    # shop, the first page (count and order), and a binary search for the
    # 13-month edge: one request per step, 21 steps covers two million sales
    "estimate_sales": 23,
    # shop, and one chunk of the payment ledger: 20 pages of 100 (workers/ledger.py)
    "sync_ledger": 21,
    # shop, and one request for the window's entry count
    "estimate_ledger": 2,
}

# Keeping shops and profiles current is the app's upkeep, not the seller's work:
# it counts against Etsy's app-wide budget (and waits at the 90% pause) but not
# against the seller's own daily limit (v7 §D3).
UPKEEP = frozenset(
    {"refresh_profile", "refresh_profile_images", "sync_shop_listings", "detect_profiles", "sync_sales", "estimate_sales",
     "sync_ledger", "estimate_ledger"}
)

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


#: The job a due schedule releases; it may use what is held back for it.
SCHEDULED_GO_LIVE = "run_publish_live_job"


async def scheduled_reserve(ctx: dict[str, Any], tenant_id: Any) -> int:
    """Requests this tenant's scheduled go-lives still need before the next reset."""
    sessionmaker = ctx.get("sessionmaker")
    if sessionmaker is None:
        return 0
    until = next_reset(_now())
    async with sessionmaker() as session:
        due = await session.scalar(
            select(func.count())
            .select_from(ListingPublication)
            .where(
                ListingPublication.tenant_id == tenant_id,
                ListingPublication.scheduled_for.is_not(None),
                ListingPublication.scheduled_for < until,
                ListingPublication.schedule_job_id.is_(None),
                ListingPublication.schedule_note.is_(None),
                ListingPublication.state != "active",
            )
        )
    return int(due or 0) * JOB_COST[SCHEDULED_GO_LIVE]


async def check(ctx: dict[str, Any], tenant: Tenant | None, function: str) -> Verdict:
    """May this tenant's job start now?"""
    if tenant is None or tenant.status is TenantStatus.suspended:
        return Verdict("suspended")
    quota = ctx.get("quota")
    if quota is None:  # no budget wired (unit tests of the job bodies)
        return RUN
    if function in UPKEEP:
        reason = await quota.admission_upkeep(JOB_COST[function])
    else:
        cost = JOB_COST[function]
        reason = await quota.admission(tenant.id, tenant.daily_quota, cost)
        if reason is None and function != SCHEDULED_GO_LIVE:
            # With go-lives waiting, the whole job must fit beside them (no
            # first-job-of-the-day allowance), or it could eat what they need.
            reserve = await scheduled_reserve(ctx, tenant.id)
            if reserve:
                tenant_used, _ = await quota.usage(tenant.id)
                if tenant_used + cost + reserve > tenant.daily_quota:
                    reason = PAUSE_TENANT
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
