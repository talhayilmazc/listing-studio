"""Work that must not run: suspended tenants, and the 90% daily pause (spec C).

* Suspending an account cancels its queued jobs, and the worker re-checks the
  account before every job, so nothing already in the queue slips through.
* New jobs pause once the app has used 90% of Etsy's shared daily limit, or when
  the tenant's own allowance cannot cover them. They wait for the reset with a
  reason the seller can read. They never time out without an explanation, and
  never start only to die at the hard wall.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from fakeredis import FakeAsyncRedis
from sqlalchemy import select

from app.api import deps
from app.core.crypto import TokenCipher
from app.db.models import AuditLog, Job, JobStatus, JobType, Tenant, TenantStatus
from app.etsy.api import RateLimitExceeded
from app.etsy.rate_limiter import PAUSE_GLOBAL, PAUSE_TENANT, DailyQuota
from app.workers import gate
from app.workers import profiles as profile_worker
from app.workers import publish as publish_worker
from app.workers import replace as replace_worker
from app.workers.guards import public_error
from tests.test_admin import world  # noqa: F401  (fixture)

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _quota(redis: FakeAsyncRedis) -> DailyQuota:
    return DailyQuota(redis, global_daily_limit=5000, pause_percent=90)


async def _use(redis: FakeAsyncRedis, *, global_used: int = 0, tenant=None, tenant_used: int = 0) -> None:
    await redis.set(f"quota:global:{TODAY}", global_used)
    if tenant is not None:
        await redis.set(f"quota:tenant:{tenant}:{TODAY}", tenant_used)


class _Enqueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    async def __call__(self, function: str, *args, **kwargs) -> None:
        self.calls.append((function, args, kwargs))


def _ctx(world: dict, enqueue: _Enqueue) -> dict:  # noqa: F811
    return {
        "sessionmaker": world["sm"],
        "quota": _quota(world["redis"]),
        "bucket": None,
        "enqueue": enqueue,
    }


def _no_etsy(*_a, **_k):
    raise AssertionError("a gated job reached the Etsy client")


# --- the thresholds ---------------------------------------------------------------
async def test_new_work_pauses_at_90_percent_while_requests_still_run_to_the_limit() -> None:
    redis = FakeAsyncRedis()
    quota = _quota(redis)
    tenant = uuid.uuid4()
    assert quota.pause_at == 4500

    await _use(redis, global_used=4499)
    assert await quota.admission(tenant, 1000, 30) is None
    await _use(redis, global_used=4500)
    assert await quota.admission(tenant, 1000, 30) == PAUSE_GLOBAL

    # The hard wall is unchanged: a job already running may use the last 10%.
    await _use(redis, global_used=4999)
    assert await quota.reserve(tenant, 1000) is True
    assert await quota.reserve(tenant, 1000) is False


async def test_a_job_starts_only_if_it_fits_in_the_tenants_allowance() -> None:
    redis = FakeAsyncRedis()
    quota = _quota(redis)
    tenant = uuid.uuid4()

    await _use(redis, tenant=tenant, tenant_used=980)
    assert await quota.admission(tenant, 1000, 30) == PAUSE_TENANT  # a publish would not fit
    assert await quota.admission(tenant, 1000, 3) is None  # a publish-live does
    # A ceiling set below a job's cost does not strand the job forever...
    await _use(redis, tenant=tenant, tenant_used=0)
    assert await quota.admission(tenant, 10, 30) is None
    # ...but a ceiling of zero stops everything.
    assert await quota.admission(tenant, 0, 1) == PAUSE_TENANT


# --- jobs with a row ------------------------------------------------------------------
async def test_a_suspended_tenants_queued_job_is_cancelled_by_the_worker(world, monkeypatch) -> None:  # noqa: F811
    enqueue = _Enqueue()
    monkeypatch.setattr(publish_worker, "get_cipher", lambda: TokenCipher(Fernet.generate_key()))
    monkeypatch.setattr(publish_worker, "EtsyApiClient", _no_etsy)
    async with world["sm"]() as s:
        tenant = await s.get(Tenant, world["bob"].tenant_id)
        tenant.status = TenantStatus.suspended
        await s.commit()

    result = await publish_worker.run_publish_job(_ctx(world, enqueue), str(world["bob"].job_id))

    assert result == "cancelled"
    async with world["sm"]() as s:
        job = await s.get(Job, world["bob"].job_id)
    assert job.status is JobStatus.cancelled
    assert job.last_error == gate.SUSPENDED_MESSAGE
    assert not enqueue.calls


async def test_a_job_pauses_at_90_percent_with_its_reason_and_resumes_at_midnight(world, monkeypatch) -> None:  # noqa: F811
    enqueue = _Enqueue()
    monkeypatch.setattr(publish_worker, "get_cipher", lambda: TokenCipher(Fernet.generate_key()))
    monkeypatch.setattr(publish_worker, "EtsyApiClient", _no_etsy)
    await _use(world["redis"], global_used=4500)

    result = await publish_worker.run_publish_job(_ctx(world, enqueue), str(world["bob"].job_id))

    assert result == "deferred"
    async with world["sm"]() as s:
        job = await s.get(Job, world["bob"].job_id)
    assert job.status is JobStatus.queued  # waiting, not failed
    assert job.paused_reason == PAUSE_GLOBAL
    midnight = gate.next_reset(datetime.now(timezone.utc))
    assert job.scheduled_at.replace(tzinfo=timezone.utc) == midnight
    # Requeued to run again just after the reset.
    [(function, args, kwargs)] = enqueue.calls
    assert function == "run_publish_job" and args == (str(world["bob"].job_id),)
    seconds_to_midnight = (midnight - datetime.now(timezone.utc)).total_seconds()
    assert seconds_to_midnight < kwargs["_defer_by"] <= seconds_to_midnight + 120
    # The tenant is marked, for the banner.
    assert await _quota(world["redis"]).paused_reason(world["bob"].tenant_id) == PAUSE_GLOBAL


async def test_a_resumed_job_clears_its_pause(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        job = await s.get(Job, world["bob"].job_id)
        job.paused_reason = PAUSE_TENANT
        await s.commit()
        assert await gate.start_job(_ctx(world, _Enqueue()), s, job, "run_publish_job") is None
        assert job.status is JobStatus.running and job.paused_reason is None


async def test_finished_or_cancelled_jobs_are_not_rerun(world) -> None:  # noqa: F811
    for status in (JobStatus.cancelled, JobStatus.succeeded, JobStatus.failed):
        async with world["sm"]() as s:
            job = await s.get(Job, world["bob"].job_id)
            job.status = status
            await s.commit()
            assert await gate.start_job(_ctx(world, _Enqueue()), s, job, "run_publish_job") == "skipped"
            assert job.status is status


@pytest.mark.parametrize(
    ("module", "function"),
    [
        (publish_worker, "run_publish_live_job"),
        (replace_worker, "run_replace_images_job"),
    ],
)
async def test_every_job_with_a_row_is_gated(world, monkeypatch, module, function) -> None:  # noqa: F811
    monkeypatch.setattr(module, "get_cipher", lambda: TokenCipher(Fernet.generate_key()))
    monkeypatch.setattr(module, "EtsyApiClient", _no_etsy)
    async with world["sm"]() as s:
        allowance = (await s.get(Tenant, world["bob"].tenant_id)).daily_quota
    await _use(world["redis"], tenant=world["bob"].tenant_id, tenant_used=allowance)
    enqueue = _Enqueue()

    assert await getattr(module, function)(_ctx(world, enqueue), str(world["bob"].job_id)) == "deferred"
    async with world["sm"]() as s:
        job = await s.get(Job, world["bob"].job_id)
    assert job.paused_reason == PAUSE_TENANT
    assert enqueue.calls[0][0] == function


# --- jobs without a row ------------------------------------------------------------------
@pytest.mark.parametrize("function", ["sync_shop_listings", "detect_profiles", "refresh_profile"])
async def test_jobs_without_a_row_pause_once_and_skip_suspended_tenants(world, monkeypatch, function) -> None:  # noqa: F811
    monkeypatch.setattr(profile_worker, "_build_client", _no_etsy)
    arg = str(world["bob"].profile_id if function == "refresh_profile" else world["bob"].tenant_id)
    run = getattr(profile_worker, function)
    enqueue = _Enqueue()

    await _use(world["redis"], global_used=4600)
    assert await run(_ctx(world, enqueue), arg) == "deferred"
    assert await run(_ctx(world, enqueue), arg) == "deferred"
    keys = {kw["_job_id"] for _, _, kw in enqueue.calls}
    assert len(keys) == 1, "repeats during a pause collapse into one run after the reset"

    async with world["sm"]() as s:
        tenant = await s.get(Tenant, world["bob"].tenant_id)
        tenant.status = TenantStatus.suspended
        await s.commit()
    assert await run(_ctx(world, enqueue), arg) == "suspended"


async def test_a_read_job_that_meets_the_wall_midway_is_retried_after_the_reset(world, monkeypatch) -> None:  # noqa: F811
    async def _hit_wall(ctx, tenant_id):  # noqa: ANN001
        raise RateLimitExceeded("daily Etsy API budget exhausted")

    monkeypatch.setattr(profile_worker, "_sync_shop_listings", _hit_wall)
    enqueue = _Enqueue()
    await _use(world["redis"], tenant=world["bob"].tenant_id, tenant_used=999)

    result = await profile_worker.sync_shop_listings(_ctx(world, enqueue), str(world["bob"].tenant_id))

    assert result == "deferred"
    assert enqueue.calls[0][0] == "sync_shop_listings"
    assert await _quota(world["redis"]).paused_reason(world["bob"].tenant_id) == PAUSE_TENANT


def test_a_job_cut_off_at_the_wall_says_so() -> None:
    message = public_error(RateLimitExceeded("daily Etsy API budget exhausted"))
    assert "daily request limit" in message and "00:00 UTC" in message
    assert "unexpectedly" not in message


# --- suspension from the admin panel --------------------------------------------------
async def test_suspending_cancels_only_that_tenants_queued_jobs(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        finished = Job(
            tenant_id=world["bob"].tenant_id,
            connection_id=(await s.get(Job, world["bob"].job_id)).connection_id,
            type=JobType.create_draft,
            status=JobStatus.succeeded,
        )
        s.add(finished)
        await s.commit()
        finished_id = finished.id

    resp = await world["a"].post(f"/api/admin/users/{world['bob'].tenant_id}/suspend")
    assert resp.status_code == 200

    async with world["sm"]() as s:
        bobs = await s.get(Job, world["bob"].job_id)
        admins = await s.get(Job, world["admin"].job_id)
        done = await s.get(Job, finished_id)
        entry = (
            await s.execute(select(AuditLog).where(AuditLog.action == "user.suspended"))
        ).scalar_one()
    assert bobs.status is JobStatus.cancelled and bobs.last_error == gate.SUSPENDED_MESSAGE
    assert admins.status is JobStatus.queued  # another tenant's work is untouched
    assert done.status is JobStatus.succeeded  # history is not rewritten
    assert entry.details["cancelled_jobs"] == 1


# --- what the seller sees -------------------------------------------------------------------
async def test_job_status_explains_a_pause(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        job = await s.get(Job, world["bob"].job_id)
        job.paused_reason = PAUSE_TENANT
        job.scheduled_at = gate.next_reset(datetime.now(timezone.utc))
        await s.commit()

    body = (await world["b"].get(f"/api/jobs/{world['bob'].job_id}")).json()

    assert body["status"] == "queued"
    assert body["pause"]["reason"] == PAUSE_TENANT
    assert "daily allowance" in body["pause"]["message"]
    assert body["pause"]["resumes_at"]


async def test_quota_reports_the_pause_before_any_job_runs(world) -> None:  # noqa: F811
    redis = world["redis"]
    world["app"].dependency_overrides[deps.get_quota] = lambda: _quota(redis)

    calm = (await world["b"].get("/api/quota")).json()
    assert calm["pause"] is None and calm["global_pause_at"] == 4500

    await _use(redis, global_used=4500)
    paused = (await world["b"].get("/api/quota")).json()
    assert paused["pause"]["reason"] == PAUSE_GLOBAL
    assert "90%" in paused["pause"]["message"]


async def test_admin_usage_shows_the_pause_line_and_who_is_waiting(world) -> None:  # noqa: F811
    redis = world["redis"]
    world["app"].dependency_overrides[deps.get_quota] = lambda: _quota(redis)
    await _quota(redis).mark_paused(world["bob"].tenant_id, PAUSE_TENANT)

    body = (await world["a"].get("/api/admin/usage")).json()

    assert body["global_limit"] == 5000 and body["pause_at"] == 4500
    waiting = {t["email"]: t["paused_reason"] for t in body["tenants"]}
    assert waiting == {"bob@example.com": PAUSE_TENANT, "admin@example.com": None}
