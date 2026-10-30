"""Fair turns between accounts for drafts and heavy mockup jobs, and a short queue for everyone.

**The queue must stay short.** arq takes jobs first in, first out, crons included: when one load
test queued 1,459 jobs at once, the cron that releases scheduled go-lives itself ran ten minutes
late, and go-lives went out up to 24 minutes after their time. So nothing is allowed to pile up in
arq: drafts and heavy jobs wait in lists here and are handed over a few at a time; go-lives go to the
front (``schedule._enqueue``); the hourly reconcile is spread over the hour.

**Drafts.** Each account has at most ``PER_ACCOUNT`` drafts handed to arq at a time
(``fair:out:{tenant}``), and all accounts together at most ``DRAFTS_IN_QUEUE`` (``fair:out``). The
rest wait in the account's own list (``fair:held:{tenant}``). When one finishes (:func:`after_run`)
the next is handed over, account by account in turn (``fair:rotation``), so "Create 3,000 drafts"
takes its turn with everyone else's two. A cron (:func:`top_up`, every minute) hands over what a
lost worker never did and lets go of turns whose job ended or is paused for long (the daily budget,
a personalization held for the seller). The limits are soft: two submits at the same moment can
hand over one extra. Go-lives are not held: they have a time to keep.

**Heavy mockup jobs** (preparing a template photo, making mockups, a ZIP): each takes about a
gigabyte at its peak and a CPU. They wait the same way: one per account handed over at a time and
``HEAVY_LIMIT`` in all (:func:`submit_heavy`), so a seller's 100 photos take turns with everyone
else's; :func:`positions` says how many are ahead of each one and about how long that is, from how
long these jobs have been taking (``fair:heavy:seconds``). :func:`heavy` stays as the worker's own
guard: never more than ``HEAVY_LIMIT`` at once, whatever arrives.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, JobStatus

#: Drafts of one account handed to the worker at a time, and of all accounts together.
PER_ACCOUNT = 4
DRAFTS_IN_QUEUE = 12
#: Heavy mockup jobs running at once in one worker, and how long the next one waits if more arrive.
HEAVY_LIMIT = 2
HEAVY_DEFER_SECONDS = 15.0
#: Heavy jobs of one account handed over at a time.
HEAVY_PER_ACCOUNT = 1
#: How long a heavy job is taken to last until one has been measured, and how fast the measure moves.
HEAVY_FIRST_GUESS_SECONDS = 30.0
HEAVY_AVERAGE_WEIGHT = 0.2

OUT = "fair:out:{}"
ALL_OUT = "fair:out"
HELD = "fair:held:{}"
ACCOUNTS = "fair:accounts"
ROTATION = "fair:rotation"
FUNCTION = "run_publish_job"
HEAVY_OUT = "fair:heavy:out"
HEAVY_HELD = "fair:heavy:held:{}"
HEAVY_ROTATION = "fair:heavy:rotation"
HEAVY_SECONDS = "fair:heavy:seconds"

Enqueue = Callable[..., Awaitable[Any]]


def _s(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def submit(redis: Any, enqueue: Enqueue, tenant_id: uuid.UUID | str, job_id: uuid.UUID | str) -> bool:
    """Hand a draft job to the worker now if there is room and the account has a free turn, else
    hold it. Returns whether it was handed over."""
    tenant, job = str(tenant_id), str(job_id)
    if redis is None:
        await enqueue(FUNCTION, job)
        return True
    out = OUT.format(tenant)
    if await redis.sismember(out, job):
        return True  # already handed over (asked twice)
    waiting = await redis.llen(HELD.format(tenant))
    if not waiting and await redis.scard(out) < PER_ACCOUNT and await redis.scard(ALL_OUT) < DRAFTS_IN_QUEUE:
        await redis.sadd(out, job)
        await redis.sadd(ALL_OUT, job)
        await enqueue(FUNCTION, job)
        return True
    await redis.rpush(HELD.format(tenant), job)
    if await redis.sadd(ACCOUNTS, tenant):
        await redis.rpush(ROTATION, tenant)
    return False


async def _hand_over(redis: Any, enqueue: Enqueue) -> int:
    """Hand over held drafts while there is room, one account at a time, in turn."""
    handed = 0
    tries = await redis.llen(ROTATION)
    while tries > 0 and await redis.scard(ALL_OUT) < DRAFTS_IN_QUEUE:
        tries -= 1
        raw = await redis.rpoplpush(ROTATION, ROTATION)  # the last becomes the first: a ring
        if raw is None:
            break
        tenant = _s(raw)
        out, held = OUT.format(tenant), HELD.format(tenant)
        if await redis.scard(out) >= PER_ACCOUNT:
            continue
        job = await redis.lpop(held)
        if job is None:
            await redis.srem(ACCOUNTS, tenant)
            await redis.lrem(ROTATION, 0, tenant)
            continue
        job = _s(job)
        if await redis.sismember(out, job):
            continue
        await redis.sadd(out, job)
        await redis.sadd(ALL_OUT, job)
        await enqueue(FUNCTION, job)
        handed += 1
        tries = max(tries, 1)  # room may remain: go round again
    return handed


def _lets_go(job: Job | None) -> bool:
    """The job no longer needs its turn: it ended, or it waits for something long."""
    return job is None or job.status not in (JobStatus.queued, JobStatus.running) or (
        job.status is JobStatus.queued and job.paused_reason is not None)


async def after_run(ctx: dict[str, Any], job_id: str) -> None:
    """After a draft job ran: let go of its turn if it no longer needs it, and hand the next
    account's held draft over."""
    redis = ctx.get("redis")
    if redis is None:
        return
    async with ctx["sessionmaker"]() as session:
        job = await session.get(Job, uuid.UUID(job_id))
    if job is None or not _lets_go(job):
        return
    await redis.srem(OUT.format(str(job.tenant_id)), job_id)
    await redis.srem(ALL_OUT, job_id)
    await _hand_over(redis, _enqueue_of(ctx))


def _enqueue_of(ctx: dict[str, Any]) -> Enqueue:
    enqueue = ctx.get("enqueue")  # test hook
    return enqueue if enqueue is not None else ctx["redis"].enqueue_job


async def top_up_accounts(redis: Any, session: AsyncSession, enqueue: Enqueue) -> int:
    """Let go of turns whose jobs ended or wait long, then hand over held drafts. Returns how
    many were handed over."""
    accounts = {_s(t) for t in await redis.smembers(ACCOUNTS)}
    for key in [_s(k) async for k in redis.scan_iter(match="fair:out:*")]:
        accounts.add(key.split(":", 2)[2])
    still_out: list[str] = []
    for tenant in accounts:
        out = OUT.format(tenant)
        ids = [_s(j) for j in await redis.smembers(out)]
        if ids:
            jobs = {str(j.id): j for j in (await session.execute(select(Job).where(
                Job.id.in_([uuid.UUID(i) for i in ids])))).scalars()}
            gone = [i for i in ids if _lets_go(jobs.get(i))]
            if gone:
                await redis.srem(out, *gone)
            still_out += [i for i in ids if i not in gone]
        if await redis.llen(HELD.format(tenant)) and not await _in_rotation(redis, tenant):
            await redis.rpush(ROTATION, tenant)  # (held from before there was a rotation)
    # The count of everything handed over follows the accounts' own (a lost hand-over).
    await redis.delete(ALL_OUT)
    if still_out:
        await redis.sadd(ALL_OUT, *still_out)
    handed = await _hand_over(redis, enqueue)
    return handed + await _hand_over_heavy(redis, enqueue, forget_after=_lost_after())


async def _in_rotation(redis: Any, tenant: str) -> bool:
    return tenant in {_s(t) for t in await redis.lrange(ROTATION, 0, -1)}


async def top_up(ctx: dict[str, Any]) -> int:
    """arq cron entry point (every minute)."""
    async with ctx["sessionmaker"]() as session:
        return await top_up_accounts(ctx["redis"], session, _enqueue_of(ctx))


# --- heavy mockup jobs ------------------------------------------------------------------------


def _lost_after() -> float:
    """A heavy job handed over this long ago has lost its worker: its turn is let go."""
    from app.core.config import get_settings

    return float(get_settings().worker_job_timeout) + 60.0


def _heavy_key(function: str, ref: str) -> str:
    return f"{function}:{ref}"


async def submit_heavy(redis: Any, enqueue: Enqueue, tenant_id: uuid.UUID | str, function: str, ref: str, *, job_id: str) -> bool:
    """A heavy job takes its turn with every other account's. Returns whether it went at once."""
    tenant, ref = str(tenant_id), str(ref)
    if redis is None:
        await enqueue(function, ref, _job_id=job_id)
        return True
    await redis.rpush(HEAVY_HELD.format(tenant), json.dumps({"function": function, "ref": ref, "job_id": job_id}))
    if not await _in_heavy_rotation(redis, tenant):
        await redis.rpush(HEAVY_ROTATION, tenant)
    return bool(await _hand_over_heavy(redis, enqueue))


async def _in_heavy_rotation(redis: Any, tenant: str) -> bool:
    return tenant in {_s(t) for t in await redis.lrange(HEAVY_ROTATION, 0, -1)}


async def _hand_over_heavy(redis: Any, enqueue: Enqueue, *, forget_after: float | None = None) -> int:
    now = time.time()
    out = {_s(k): json.loads(v) for k, v in (await redis.hgetall(HEAVY_OUT)).items()}
    if forget_after is not None:
        for key, entry in list(out.items()):
            if now - float(entry.get("at", now)) > forget_after:
                await redis.hdel(HEAVY_OUT, key)
                del out[key]
    handed = 0
    tries = await redis.llen(HEAVY_ROTATION)
    while tries > 0 and len(out) < HEAVY_LIMIT:
        tries -= 1
        raw = await redis.rpoplpush(HEAVY_ROTATION, HEAVY_ROTATION)
        if raw is None:
            break
        tenant = _s(raw)
        if sum(1 for e in out.values() if e.get("tenant") == tenant) >= HEAVY_PER_ACCOUNT:
            continue
        item = await redis.lpop(HEAVY_HELD.format(tenant))
        if item is None:
            await redis.lrem(HEAVY_ROTATION, 0, tenant)
            continue
        job = json.loads(item)
        key = _heavy_key(job["function"], job["ref"])
        entry = {"tenant": tenant, "at": now}
        await redis.hset(HEAVY_OUT, key, json.dumps(entry))
        out[key] = entry
        await enqueue(job["function"], job["ref"], _job_id=job["job_id"])
        handed += 1
        tries = max(tries, 1)
    return handed


async def after_heavy(ctx: dict[str, Any], function: str, ref: str, seconds: float) -> None:
    """A heavy job ended: its turn goes to the next account, and its time improves the estimate."""
    redis = ctx.get("redis")
    if redis is None:
        return
    await redis.hdel(HEAVY_OUT, _heavy_key(function, str(ref)))
    known = await redis.hget(HEAVY_SECONDS, function)
    average = seconds if known is None else float(known) * (1 - HEAVY_AVERAGE_WEIGHT) + seconds * HEAVY_AVERAGE_WEIGHT
    await redis.hset(HEAVY_SECONDS, function, f"{average:.2f}")
    await _hand_over_heavy(redis, _enqueue_of(ctx))


@dataclass(frozen=True)
class Position:
    #: Jobs ahead of this one, of every account (0: it is being made, or next).
    ahead: int
    #: About how long until it starts, in minutes (0: now).
    minutes: int


async def positions(redis: Any, tenant_id: uuid.UUID | str) -> dict[str, Position]:
    """Where each of this account's waiting heavy jobs stands, by the job's ``ref``. The queue goes
    account by account, so a job with ``i`` of its own account's ahead of it waits for those and for
    up to ``i + 1`` of every other account's. A count, and a time from how long jobs have taken."""
    if redis is None:
        return {}
    tenant = str(tenant_id)
    mine = [json.loads(x) for x in await redis.lrange(HEAVY_HELD.format(tenant), 0, -1)]
    running = {_s(k): json.loads(v) for k, v in (await redis.hgetall(HEAVY_OUT)).items()}
    found = {k.split(":", 1)[1]: Position(0, 0) for k, v in running.items() if v.get("tenant") == tenant}
    if not mine:
        return found
    others: list[list[dict]] = []
    for raw in await redis.lrange(HEAVY_ROTATION, 0, -1):
        other = _s(raw)
        if other != tenant:
            others.append([json.loads(x) for x in await redis.lrange(HEAVY_HELD.format(other), 0, -1)])
    seconds = {_s(k): float(v) for k, v in (await redis.hgetall(HEAVY_SECONDS)).items()}

    def lasts(job: dict) -> float:
        return seconds.get(job["function"], HEAVY_FIRST_GUESS_SECONDS)

    for i, job in enumerate(mine):
        before = [*mine[:i], *(j for held in others for j in held[:i + 1])]
        total = len(running) * lasts(job) / 2 + sum(lasts(j) for j in before)  # those running are half done, on average
        found[job["ref"]] = Position(ahead=len(before) + len(running), minutes=max(1, round(total / HEAVY_LIMIT / 60)))
    return found


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
    started = time.monotonic()
    async with _heavy:
        try:
            yield True
        finally:
            await after_heavy(ctx, function, args[0] if args else "", time.monotonic() - started)
