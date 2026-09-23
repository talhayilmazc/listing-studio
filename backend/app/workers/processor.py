"""Job processing: quota -> token bucket -> Etsy call -> retry/requeue.

This is the heart of work-order step 3 and is deliberately decoupled from arq so
it can be unit-tested without the worker runtime: construct a
:class:`JobProcessor` with a fake Etsy client and a fake Redis, then call
:meth:`process`.

Flow per the architecture rule (docs/data-model.md §2):

    tenant + global daily quota  ->  global token bucket (8 req/s)  ->  Etsy API
                                                                          |
                            429 / 5xx  ->  backoff (or Retry-After) + requeue
                            4xx        ->  permanent failure
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import Job, JobStatus, Tenant, TenantStatus
from app.etsy.client import EtsyClient
from app.etsy.errors import EtsyClientError, EtsyRateLimited, EtsyServerError
from app.etsy.rate_limiter import DailyQuota, TokenBucket
from app.etsy.retry import backoff_seconds
from app.etsy.usage import UsageRecorder
from app.workers.gate import SUSPENDED_MESSAGE


class ProcessResult(str, enum.Enum):
    succeeded = "succeeded"
    failed = "failed"
    retry_scheduled = "retry_scheduled"
    deferred = "deferred"
    skipped = "skipped"


@dataclass
class Outcome:
    """What happened to a job, plus how long to wait before requeueing."""

    result: ProcessResult
    delay: float | None = None


def _next_utc_midnight(now: datetime) -> datetime:
    tomorrow = (now + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)


class JobProcessor:
    def __init__(
        self,
        sessionmaker: async_sessionmaker,
        bucket: TokenBucket,
        quota: DailyQuota,
        client: EtsyClient,
        usage: UsageRecorder,
        *,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self._sm = sessionmaker
        self._bucket = bucket
        self._quota = quota
        self._client = client
        self._usage = usage
        self._now = now_func or (lambda: datetime.now(timezone.utc))

    async def process(self, job_id: uuid.UUID) -> Outcome:
        async with self._sm() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status in (
                JobStatus.succeeded,
                JobStatus.failed,
                JobStatus.cancelled,
            ):
                return Outcome(ProcessResult.skipped)

            tenant = await session.get(Tenant, job.tenant_id)
            if tenant is None:
                job.status = JobStatus.failed
                job.last_error = "tenant_missing"
                job.finished_at = self._now()
                await session.commit()
                return Outcome(ProcessResult.failed)

            # 0) A suspended tenant's queued work is dropped, never run.
            if tenant.status is TenantStatus.suspended:
                job.status = JobStatus.cancelled
                job.last_error = SUSPENDED_MESSAGE
                job.finished_at = self._now()
                await session.commit()
                return Outcome(ProcessResult.skipped)

            # 1) Daily quota (tenant + global). Exceeded -> defer to next reset.
            if not await self._quota.reserve(job.tenant_id, tenant.daily_quota):
                reset_at = _next_utc_midnight(self._now())
                job.status = JobStatus.queued
                job.scheduled_at = reset_at
                await session.commit()
                return Outcome(
                    ProcessResult.deferred,
                    delay=(reset_at - self._now()).total_seconds(),
                )

            # 2) Global 8 req/s token bucket (blocks until a token is free).
            await self._bucket.acquire()

            # 3) The Etsy call itself (mocked in tests; real client in step 4).
            job.status = JobStatus.running
            job.started_at = self._now()
            await session.commit()

            try:
                await self._client.execute(job.type, job.payload)
            except EtsyRateLimited as exc:
                return await self._schedule_retry(session, job, retry_after=exc.retry_after)
            except EtsyServerError:
                return await self._schedule_retry(session, job, retry_after=None)
            except EtsyClientError as exc:
                # 4xx (non-429) is permanent.
                job.attempts += 1
                job.status = JobStatus.failed
                job.last_error = f"client_error:{exc.status_code}"
                job.finished_at = self._now()
                await session.commit()
                return Outcome(ProcessResult.failed)

            # Success.
            self._usage.record(job.tenant_id, self._now().date())
            job.status = JobStatus.succeeded
            job.finished_at = self._now()
            await session.commit()
            await self._usage.maybe_flush(session)
            return Outcome(ProcessResult.succeeded)

    async def _schedule_retry(
        self, session, job: Job, *, retry_after: float | None
    ) -> Outcome:
        job.attempts += 1
        if job.attempts >= job.max_attempts:
            job.status = JobStatus.failed
            job.last_error = "max_attempts_exceeded"
            job.finished_at = self._now()
            await session.commit()
            return Outcome(ProcessResult.failed)

        delay = retry_after if retry_after is not None else backoff_seconds(job.attempts)
        job.status = JobStatus.queued
        job.scheduled_at = self._now() + timedelta(seconds=delay)
        await session.commit()
        return Outcome(ProcessResult.retry_scheduled, delay=delay)
