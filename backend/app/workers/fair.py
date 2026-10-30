"""Fair turns between accounts for the draft queue, and a global limit on heavy mockup jobs.

**Drafts.** arq takes jobs first in, first out: one seller's "Create 3,000 drafts" put 3,000
jobs ahead of every other seller's draft and go-live (each draft is ~15–20 Etsy requests at
10 a second: about 90 minutes). Now each account has at most ``PER_ACCOUNT`` drafts handed to
arq at a time (``fair:out:{tenant}``); the rest wait in the account's own list
(``fair:held:{tenant}``) and the next is handed over when one finishes (:func:`after_run`), so
another seller's go-live waits behind a few drafts per busy account, never behind thousands.
A cron (:func:`top_up`, every minute) hands over what a lost worker never did and lets go of
slots whose job ended or is paused for long (the daily budget, a personalization held for the
seller). The limit is soft: two submits at the same moment can hand over one extra.
Group-schedule drafts and go-lives are not held: they have a time to keep.

**Heavy mockup jobs** (making mockups, preparing a template photo, a ZIP): each takes about a
gigabyte at its peak; at most ``HEAVY_LIMIT`` run at once in a worker (:func:`heavy`), the next
one is put back in the queue for ``HEAVY_DEFER_SECONDS`` instead of holding a worker slot.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, JobStatus

#: Drafts of one account handed to the worker at a time.
PER_ACCOUNT = 4
#: Heavy mockup jobs running at once in one worker, and how long the next one waits.
HEAVY_LIMIT = 2
HEAVY_DEFER_SECONDS = 15.0

OUT = "fair:out:{}"
HELD = "fair:held:{}"
ACCOUNTS = "fair:accounts"
FUNCTION = "run_publish_job"

Enqueue = Callable[..., Awaitable[Any]]


def _s(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def submit(redis: Any, enqueue: Enqueue, tenant_id: uuid.UUID | str, job_id: uuid.UUID | str) -> bool:
    """Hand a draft job to the worker now if the account has a free turn, else hold it.
    Returns whether it was handed over."""
    tenant, job = str(tenant_id), str(job_id)
    if redis is None:
        await enqueue(FUNCTION, job)
        return True
    out = OUT.format(tenant)
    if await redis.sismember(out, job):
        return True  # already handed over (asked twice)
    if await redis.scard(out) < PER_ACCOUNT:
        await redis.sadd(out, job)
        await enqueue(FUNCTION, job)
        return True
    await redis.rpush(HELD.format(tenant), job)
    await redis.sadd(ACCOUNTS, tenant)
    return False


async def _hand_over(redis: Any, enqueue: Enqueue, tenant: str) -> int:
    out, held = OUT.format(tenant), HELD.format(tenant)
    handed = 0
    while await redis.scard(out) < PER_ACCOUNT:
        job = await redis.lpop(held)
        if job is None:
            await redis.srem(ACCOUNTS, tenant)
            break
        job = _s(job)
        if await redis.sismember(out, job):
            continue
        await redis.sadd(out, job)
        await enqueue(FUNCTION, job)
        handed += 1
    return handed


def _lets_go(job: Job | None) -> bool:
    """The job no longer needs its turn: it ended, or it waits for something long."""
    return job is None or job.status not in (JobStatus.queued, JobStatus.running) or (
        job.status is JobStatus.queued and job.paused_reason is not None)


async def after_run(ctx: dict[str, Any], job_id: str) -> None:
    """After a draft job ran: let go of its turn if it no longer needs it, and hand the
    account's next held draft over."""
    redis = ctx.get("redis")
    if redis is None:
        return
    async with ctx["sessionmaker"]() as session:
        job = await session.get(Job, uuid.UUID(job_id))
    if job is None or not _lets_go(job):
        return
    tenant = str(job.tenant_id)
    await redis.srem(OUT.format(tenant), job_id)
    await _hand_over(redis, _enqueue_of(ctx), tenant)


def _enqueue_of(ctx: dict[str, Any]) -> Enqueue:
    enqueue = ctx.get("enqueue")  # test hook
    return enqueue if enqueue is not None else ctx["redis"].enqueue_job


async def top_up_accounts(redis: Any, session: AsyncSession, enqueue: Enqueue) -> int:
    """Let go of turns whose jobs ended or wait long, then hand over held drafts. Returns how
    many were handed over."""
    handed = 0
    accounts = {_s(t) for t in await redis.smembers(ACCOUNTS)}
    for key in [_s(k) async for k in redis.scan_iter(match="fair:out:*")]:
        accounts.add(key.split(":", 2)[2])
    for tenant in accounts:
        out = OUT.format(tenant)
        ids = [_s(j) for j in await redis.smembers(out)]
        if ids:
            jobs = {str(j.id): j for j in (await session.execute(select(Job).where(
                Job.id.in_([uuid.UUID(i) for i in ids])))).scalars()}
            gone = [i for i in ids if _lets_go(jobs.get(i))]
            if gone:
                await redis.srem(out, *gone)
        handed += await _hand_over(redis, enqueue, tenant)
    return handed


async def top_up(ctx: dict[str, Any]) -> int:
    """arq cron entry point (every minute)."""
    async with ctx["sessionmaker"]() as session:
        return await top_up_accounts(ctx["redis"], session, _enqueue_of(ctx))


_heavy = asyncio.Semaphore(HEAVY_LIMIT)


@asynccontextmanager
async def heavy(ctx: dict[str, Any], function: str, *args: str) -> AsyncIterator[bool]:
    """A turn for a heavy mockup job. False: all turns are taken; the job was put back in the
    queue for HEAVY_DEFER_SECONDS and must return without doing anything."""
    if _heavy.locked():
        enqueue = _enqueue_of(ctx)
        await enqueue(function, *args, _defer_by=HEAVY_DEFER_SECONDS,
                      _job_id=f"heavy:{function}:{':'.join(args)}:{int(time.time() * 1000)}")
        yield False
        return
    async with _heavy:
        yield True
