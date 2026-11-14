"""One memory budget for the worker's heavy work (fix/worker-memory-budget, 14 Nov 2026).

A PSD conversion, a mockup run, a template photo's analysis and a large upload being prepared can each take
hundreds of megabytes to a gigabyte, and before this nothing stopped them running at once in one worker. Past
the container's limit (2,460 MB) the kernel kills the worker, and with it the drafts and go-lives it was
running. So each heavy job reserves its estimated peak here before it starts, and lets go when it ends.

**Redis-backed, weighted, leased.** ``BUDGET_KEY`` (a sorted set: lease -> its expiry) and ``SIZES_KEY`` (a
hash: lease -> MB) hold what is reserved. Taking and letting go are one Lua script each, so two jobs never
both fit into the same room. A lease lasts ``LEASE_SECONDS`` and the job renews it every ``RENEW_SECONDS``
while it runs: a job that is killed (OOM, a deploy) stops renewing and its share comes back within a minute
and a half; a job that ends normally, fails, times out or is cancelled lets go at once (``finally``).

**A job that does not fit never fails and never holds a worker slot.** It is queued again ``WAIT_SECONDS``
later (a copy with its own job id: arq's retries are counted, a wait must not be) and returns. A job bigger
than the whole budget runs when nothing else holds any, so it is never stuck.

**Only heavy work takes from it.** Drafts, go-lives, publishing, listing writing, syncs and every other job
never call this module, so a full budget cannot delay them (``tests/test_worker_memory_budget.py``).

**The numbers** (measured in the production image; CLAUDE.md "Bellek", ``scripts/heavy_peak.py``): the worker's
base is ~150 MB, ~780 MB once the mockup analysis models are loaded (they stay); light jobs together ~300 MB;
so the budget is 2,460 - 780 - 300 - 30 (margin) = 1,350 MB. A heavy job gives back what it freed when it
ends (``give_back``): before that a mockup run left 229-520 MB held after it, and the next job's peak landed
on top of it.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__name__)

#: Megabytes of heavy work at once in one worker.
BUDGET_MB = 1350
BUDGET_KEY = "worker:heavy-mb:leases"
SIZES_KEY = "worker:heavy-mb:sizes"
LEASE_SECONDS = 90
RENEW_SECONDS = 30
#: A job that does not fit is queued again this much later.
WAIT_SECONDS = 5.0

# --- each heavy job's estimated peak, MB above the worker's base (models loaded) ---------------------------
#: A PSD conversion: its child's address-space cap (workers/psd.py MEMORY_MB). Measured children: 264-574 MB.
PSD_MB = 1200
#: A mockup run (generate_mockups, rerender_mockups): measured 419-759 MB over what the worker held before it
#: (6 templates x 10 designs of 4000 px; more when the run before it had given its memory back).
MOCKUP_RUN_MB = 800
#: Preparing a template photo (the analysis): measured 366-402 MB once the models are loaded.
TEMPLATE_PREP_MB = 450
#: A ZIP of a run's mockups: the files, read one by one into the archive.
ZIP_MB = 150
#: Preparing an upload: measured extra peak 78-148 MB for JPEGs of 12-108 MP (libvips shrinks on load), and
#: 112 / 205 / 299 / 395 MB for PNGs of 12 / 48 / 100 / 250 MP. A line over the worst of them.
UPLOAD_BASE_MB = 100
UPLOAD_MB_PER_MP = 1.25


#: The mockup jobs' reservations, taken in ``fair.heavy``.
HEAVY_MB = {
    "prepare_mockup_template": TEMPLATE_PREP_MB,
    "generate_mockups": MOCKUP_RUN_MB,
    "rerender_mockups": MOCKUP_RUN_MB,
    "build_mockup_zip": ZIP_MB,
}


def upload_mb(width: int | None, height: int | None) -> int:
    """An upload's preparation, from its pixels: a 12 MP photo 115 MB, a 48 MP one 160 MB, 250 MP 413 MB."""
    megapixels = ((width or 0) * (height or 0)) / 1_000_000
    return int(round(UPLOAD_BASE_MB + UPLOAD_MB_PER_MP * megapixels))


_TAKE = """
local now = tonumber(ARGV[3])
for _, gone in ipairs(redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now)) do
  redis.call('ZREM', KEYS[1], gone)
  redis.call('HDEL', KEYS[2], gone)
end
local held = 0
for _, mb in ipairs(redis.call('HVALS', KEYS[2])) do held = held + tonumber(mb) end
local want = tonumber(ARGV[2])
if held + want <= tonumber(ARGV[4]) or held == 0 then
  redis.call('ZADD', KEYS[1], now + tonumber(ARGV[5]), ARGV[1])
  redis.call('HSET', KEYS[2], ARGV[1], want)
  return 1
end
return 0
"""

_RENEW = """
if redis.call('ZSCORE', KEYS[1], ARGV[1]) then
  redis.call('ZADD', KEYS[1], tonumber(ARGV[2]), ARGV[1])
  return 1
end
return 0
"""

_LET_GO = """
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('HDEL', KEYS[2], ARGV[1])
return 1
"""


async def take(redis: Any, what: str, mb: int, *, budget: int | None = None, now: float | None = None) -> str | None:
    """Reserve ``mb`` for ``what``: the lease's id, or None when it does not fit now."""
    lease = f"{what}:{uuid.uuid4().hex[:12]}"
    fits = await redis.eval(_TAKE, 2, BUDGET_KEY, SIZES_KEY, lease, int(mb), time.time() if now is None else now,
                            BUDGET_MB if budget is None else budget, LEASE_SECONDS)
    return lease if int(fits) else None


async def renew(redis: Any, lease: str, *, now: float | None = None) -> bool:
    return bool(int(await redis.eval(_RENEW, 1, BUDGET_KEY, lease, (time.time() if now is None else now) + LEASE_SECONDS)))


async def let_go(redis: Any, lease: str) -> None:
    await redis.eval(_LET_GO, 2, BUDGET_KEY, SIZES_KEY, lease)


async def held(redis: Any, *, now: float | None = None) -> int:
    """MB reserved now (leases past their expiry are not counted)."""
    now = time.time() if now is None else now
    live = {_s(m) for m in await redis.zrangebyscore(BUDGET_KEY, now, "+inf")}
    sizes = {_s(k): int(v) for k, v in (await redis.hgetall(SIZES_KEY)).items()}
    return sum(mb for lease, mb in sizes.items() if lease in live)


def _s(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def _keep(redis: Any, lease: str) -> None:
    while True:
        await asyncio.sleep(RENEW_SECONDS)
        try:
            await renew(redis, lease)
        except Exception:  # noqa: BLE001 - a missed renewal is retried; the lease has 90 s
            logger.warning("memory budget: could not renew %s", lease.split(":", 1)[0])


@asynccontextmanager
async def reserve(redis: Any, what: str, mb: int) -> AsyncIterator[bool]:
    """``async with reserve(redis, "convert_psd", 1200) as fits``: True, the room is held until the block ends
    (renewed while it runs); False, it does not fit now: the caller queues itself again and returns."""
    if redis is None:  # no Redis (some tests and scripts): nothing to share
        yield True
        return
    lease = await take(redis, what, mb)
    if lease is None:
        yield False
        return
    keeper = asyncio.create_task(_keep(redis, lease))
    try:
        yield True
    finally:
        keeper.cancel()
        # What the job freed goes back to the system before its share does: otherwise it stays the worker's
        # and the next heavy job's peak lands on top of it.
        try:
            await asyncio.to_thread(give_back)
        except Exception:  # noqa: BLE001 - never stops the job from letting go
            logger.warning("memory budget: could not give memory back after %s", what)
        try:
            await let_go(redis, lease)
        except Exception:  # noqa: BLE001 - the lease runs out by itself
            logger.warning("memory budget: could not let go of %s; it expires in %s s", what, LEASE_SECONDS)


Enqueue = Callable[..., Awaitable[Any]]


async def wait_turn(enqueue: Enqueue, function: str, *args: Any) -> None:
    """Queue ``function(*args)`` again in ``WAIT_SECONDS``, as a new job (a wait is not a failed try)."""
    stamp = int(time.time() * 1000)
    await enqueue(function, *args, _defer_by=WAIT_SECONDS,
                  _job_id=f"budget-wait:{function}:{':'.join(str(a) for a in args)}:{stamp}")


def give_back() -> None:
    """Return what a heavy job freed to the system: libvips's operation cache (it keeps references to the
    images of recent operations) and glibc's free heap (``malloc_trim``; freed memory otherwise stays the
    process's and the next job's peak lands on top of it). Measured: a mockup run left +229 MB held after it
    ended, an upload +93 MB (``scripts/heavy_peak.py --alone``)."""
    import ctypes
    import gc

    gc.collect()
    try:
        import pyvips

        pyvips.cache_set_max(0)  # drops what is cached now ...
        pyvips.cache_set_max(100)  # ... and keeps libvips's own default for what comes next
    except Exception:  # noqa: BLE001 - no libvips: nothing cached
        pass
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except OSError:  # not glibc
        pass
