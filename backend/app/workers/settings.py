"""arq worker: job loop + periodic usage flush.

The worker pulls jobs from Redis and runs them through :class:`JobProcessor`.
Retries/deferrals are re-enqueued as *new* deferred jobs (rather than arq's own
retry) so the ``job`` table's ``attempts`` stays the single source of truth.

The Etsy client is a placeholder until step 4; job execution therefore raises
until a real client is injected, but the worker, quota, bucket and usage flush
all boot and run.
"""

from __future__ import annotations

import uuid
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.etsy.client import UnavailableEtsyClient
from app.etsy.rate_limiter import DailyQuota, TokenBucket
from app.etsy.usage import UsageRecorder
from app.workers.processor import JobProcessor, ProcessResult
from app.workers.profiles import detect_profiles, refresh_profile, sync_shop_listings
from app.workers.publish import run_publish_job, run_publish_live_job
from app.workers.replace import run_replace_images_job


async def process_job(ctx: dict[str, Any], job_id: str) -> str:
    """arq entrypoint: process one job, re-enqueueing retries/deferrals."""
    processor: JobProcessor = ctx["processor"]
    outcome = await processor.process(uuid.UUID(job_id))
    if (
        outcome.result in (ProcessResult.retry_scheduled, ProcessResult.deferred)
        and outcome.delay is not None
    ):
        await ctx["redis"].enqueue_job("process_job", job_id, _defer_by=outcome.delay)
    return outcome.result.value


async def flush_usage(ctx: dict[str, Any]) -> None:
    """Periodically persist buffered api_usage counts."""
    async with ctx["sessionmaker"]() as session:
        await ctx["usage"].flush(session)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    redis = ctx["redis"]  # arq provides the pool
    ctx["sessionmaker"] = get_sessionmaker()
    ctx["usage"] = UsageRecorder()
    ctx["bucket"] = TokenBucket(redis)  # 4 req/s
    ctx["quota"] = DailyQuota(redis, global_daily_limit=settings.global_daily_limit)  # 5000/day
    ctx["processor"] = JobProcessor(
        sessionmaker=ctx["sessionmaker"],
        bucket=ctx["bucket"],
        quota=ctx["quota"],
        client=UnavailableEtsyClient(),  # generic single-call path; publish uses run_publish_job
        usage=ctx["usage"],
    )


class WorkerSettings:
    """arq worker configuration."""

    functions = [
        process_job,
        run_publish_job,
        run_publish_live_job,
        refresh_profile,
        sync_shop_listings,
        detect_profiles,
        run_replace_images_job,
    ]
    cron_jobs = [cron(flush_usage, second={0, 15, 30, 45}, run_at_startup=False)]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
