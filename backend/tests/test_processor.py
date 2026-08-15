"""JobProcessor orchestration tests: quota, token bucket, retry policy.

The Etsy call is mocked at the client boundary -- no real API calls.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    ConnectionStatus,
    EtsyConnection,
    Job,
    JobStatus,
    JobType,
    Tenant,
)
from app.etsy.errors import EtsyClientError, EtsyRateLimited, EtsyServerError
from app.etsy.rate_limiter import DailyQuota, TokenBucket
from app.etsy.usage import UsageRecorder
from app.workers.processor import JobProcessor, ProcessResult

NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)


# --- Fake Etsy clients (mock the endpoint) ---------------------------------
class SuccessClient:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, job_type: JobType, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {"ok": True}


class RaisingClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    async def execute(self, job_type: JobType, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        raise self.exc


# --- Helpers ----------------------------------------------------------------
async def _seed_job(
    sm: async_sessionmaker, *, daily_quota: int = 2000, max_attempts: int = 5
) -> uuid.UUID:
    async with sm() as session:
        tenant = Tenant(
            email=f"{uuid.uuid4()}@example.com",
            password_hash="x",
            daily_quota=daily_quota,
        )
        session.add(tenant)
        await session.flush()
        conn = EtsyConnection(tenant_id=tenant.id, status=ConnectionStatus.active)
        session.add(conn)
        await session.flush()
        job = Job(
            tenant_id=tenant.id,
            connection_id=conn.id,
            type=JobType.create_draft,
            max_attempts=max_attempts,
        )
        session.add(job)
        await session.commit()
        return job.id


def _processor(sm: async_sessionmaker, redis: FakeAsyncRedis, client: Any) -> JobProcessor:
    return JobProcessor(
        sessionmaker=sm,
        bucket=TokenBucket(redis),
        quota=DailyQuota(redis),
        client=client,
        usage=UsageRecorder(flush_threshold=1),  # flush immediately so we can assert
        now_func=lambda: NOW,
    )


# --- Tests ------------------------------------------------------------------
async def test_success_records_usage(async_sm: async_sessionmaker, fake_redis: FakeAsyncRedis) -> None:
    client = SuccessClient()
    proc = _processor(async_sm, fake_redis, client)
    job_id = await _seed_job(async_sm)

    outcome = await proc.process(job_id)

    assert outcome.result is ProcessResult.succeeded
    assert client.calls == 1
    async with async_sm() as session:
        job = await session.get(Job, job_id)
        assert job.status is JobStatus.succeeded
        assert job.finished_at is not None


async def test_quota_exhaustion_defers_job(
    async_sm: async_sessionmaker, fake_redis: FakeAsyncRedis
) -> None:
    client = SuccessClient()
    proc = _processor(async_sm, fake_redis, client)
    job_id = await _seed_job(async_sm, daily_quota=0)  # no budget at all

    outcome = await proc.process(job_id)

    assert outcome.result is ProcessResult.deferred
    assert client.calls == 0  # Etsy never called
    assert outcome.delay == 12 * 3600  # NOON -> next UTC midnight
    async with async_sm() as session:
        job = await session.get(Job, job_id)
        assert job.status is JobStatus.queued
        assert job.scheduled_at is not None
        sched = job.scheduled_at
        assert (sched.year, sched.month, sched.day) == (2026, 8, 16)


async def test_429_respects_retry_after(
    async_sm: async_sessionmaker, fake_redis: FakeAsyncRedis
) -> None:
    client = RaisingClient(EtsyRateLimited(retry_after=30))
    proc = _processor(async_sm, fake_redis, client)
    job_id = await _seed_job(async_sm)

    outcome = await proc.process(job_id)

    assert outcome.result is ProcessResult.retry_scheduled
    assert outcome.delay == 30  # Retry-After wins over backoff
    async with async_sm() as session:
        job = await session.get(Job, job_id)
        assert job.attempts == 1
        assert job.status is JobStatus.queued
        elapsed = job.scheduled_at.replace(tzinfo=None) - NOW.replace(tzinfo=None)
        assert elapsed.total_seconds() == 30


async def test_429_without_retry_after_uses_backoff(
    async_sm: async_sessionmaker, fake_redis: FakeAsyncRedis
) -> None:
    client = RaisingClient(EtsyRateLimited(retry_after=None))
    proc = _processor(async_sm, fake_redis, client)
    job_id = await _seed_job(async_sm)

    outcome = await proc.process(job_id)

    assert outcome.result is ProcessResult.retry_scheduled
    assert outcome.delay == 2  # backoff_seconds(attempt=1)


async def test_5xx_is_retried(async_sm: async_sessionmaker, fake_redis: FakeAsyncRedis) -> None:
    client = RaisingClient(EtsyServerError(status_code=503))
    proc = _processor(async_sm, fake_redis, client)
    job_id = await _seed_job(async_sm)

    outcome = await proc.process(job_id)

    assert outcome.result is ProcessResult.retry_scheduled
    assert outcome.delay == 2
    async with async_sm() as session:
        job = await session.get(Job, job_id)
        assert job.attempts == 1
        assert job.status is JobStatus.queued


async def test_max_attempts_marks_failed(
    async_sm: async_sessionmaker, fake_redis: FakeAsyncRedis
) -> None:
    client = RaisingClient(EtsyServerError(status_code=500))
    proc = _processor(async_sm, fake_redis, client)
    job_id = await _seed_job(async_sm, max_attempts=1)  # first failure is terminal

    outcome = await proc.process(job_id)

    assert outcome.result is ProcessResult.failed
    async with async_sm() as session:
        job = await session.get(Job, job_id)
        assert job.status is JobStatus.failed
        assert job.attempts == 1
        assert job.finished_at is not None
        assert job.last_error == "max_attempts_exceeded"


async def test_4xx_is_permanent_failure(
    async_sm: async_sessionmaker, fake_redis: FakeAsyncRedis
) -> None:
    client = RaisingClient(EtsyClientError(status_code=404))
    proc = _processor(async_sm, fake_redis, client)
    job_id = await _seed_job(async_sm)

    outcome = await proc.process(job_id)

    assert outcome.result is ProcessResult.failed
    assert client.calls == 1
    async with async_sm() as session:
        job = await session.get(Job, job_id)
        assert job.status is JobStatus.failed
        assert job.attempts == 1
        assert job.last_error == "client_error:404"


@pytest.mark.parametrize("bad_id", [uuid.uuid4()])
async def test_missing_job_is_skipped(
    async_sm: async_sessionmaker, fake_redis: FakeAsyncRedis, bad_id: uuid.UUID
) -> None:
    proc = _processor(async_sm, fake_redis, SuccessClient())
    outcome = await proc.process(bad_id)
    assert outcome.result is ProcessResult.skipped
