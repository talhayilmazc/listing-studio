"""Redis-backed rate limiting for Etsy calls.

Two independent guards, applied in this order by the worker:

1. :class:`DailyQuota` -- per-tenant and global daily request budgets
   (Personal App allows 5.000/day per *app*). Two thresholds: new jobs pause at
   ``pause_percent`` of the global limit (production-spec C, 90% = 4.500), and
   every single request still stops hard at the limit itself.
2. :class:`TokenBucket` -- a global 3 req/s pacer shared by all workers,
   refilled atomically in Redis via a Lua script. Etsy's per-second limit is 5
   and it counts bursts: the old 4 req/s bucket held 4 tokens, so after an idle
   moment it released 4 at once *plus* the refill, up to 7 in one second under
   concurrent jobs (docs/duzeltmeler-v5.md §A). Capacity is now 1 -- no stored
   burst -- so requests are spaced at least 1/rate apart, and any one-second
   window holds at most ``rate`` of them.

Both live in Redis so the limits hold across worker processes.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from redis.asyncio import Redis

# Atomic token-bucket refill. Returns {allowed(0|1), wait_seconds(string)}.
# Running this in one Lua call keeps refill+consume atomic under concurrency.
_BUCKET_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
if now_ms < 0 then
  -- One clock for every process: Redis's. Per-process monotonic clocks have
  -- unrelated origins, so buckets refilled from them disagree.
  local t = redis.call('TIME')
  now_ms = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end

local bucket = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local ts = tonumber(bucket[2])
if tokens == nil then
  tokens = capacity
  ts = now_ms
end

local elapsed = math.max(0, now_ms - ts) / 1000.0
tokens = math.min(capacity, tokens + elapsed * rate)

local allowed = 0
local wait = 0.0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
else
  wait = (requested - tokens) / rate
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now_ms)
redis.call('PEXPIRE', key, 60000)
return {allowed, tostring(wait)}
"""


# After a 429, push the whole bucket into debt so every worker waits, not just
# the request that was refused.
_PENALIZE_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local seconds = tonumber(ARGV[4])
if now_ms < 0 then
  local t = redis.call('TIME')
  now_ms = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end
local bucket = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local ts = tonumber(bucket[2])
if tokens == nil then
  tokens = capacity
  ts = now_ms
end
tokens = math.min(capacity, tokens + math.max(0, now_ms - ts) / 1000.0 * rate)
tokens = math.min(tokens, 1 - seconds * rate)
redis.call('HSET', key, 'tokens', tokens, 'ts', now_ms)
redis.call('PEXPIRE', key, 60000 + math.ceil(seconds * 1000))
return 1
"""


class TokenBucket:
    """Global pacer enforcing a steady requests-per-second ceiling, without bursts."""

    def __init__(
        self,
        redis: Redis,
        *,
        rate: float = 3.0,
        capacity: float = 1.0,
        key: str = "bucket:global",
        time_func: Callable[[], float] | None = None,
        sleep_func: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._redis = redis
        self._rate = rate
        self._capacity = capacity
        self._key = key
        # Injectable clock (seconds) and sleep for deterministic tests. Without a
        # clock, the script reads Redis TIME, shared by every worker process.
        self._now = time_func
        self._sleep = sleep_func or asyncio.sleep
        self._script = redis.register_script(_BUCKET_LUA)
        self._penalize = redis.register_script(_PENALIZE_LUA)

    @property
    def redis(self) -> Redis:
        return self._redis

    @property
    def rate(self) -> float:
        return self._rate

    def _now_ms(self) -> int:
        return int(self._now() * 1000) if self._now is not None else -1

    async def penalize(self, seconds: float) -> None:
        """Make every caller wait at least ``seconds`` (after a 429 from Etsy)."""
        await self._penalize(
            keys=[self._key], args=[self._rate, self._capacity, self._now_ms(), seconds]
        )

    async def try_acquire(self, tokens: float = 1.0) -> tuple[bool, float]:
        """Attempt to take ``tokens``. Returns (allowed, seconds_until_available)."""
        now_ms = self._now_ms()
        result = await self._script(
            keys=[self._key],
            args=[self._rate, self._capacity, now_ms, tokens],
        )
        allowed = int(result[0])
        raw_wait = result[1]
        if isinstance(raw_wait, (bytes, bytearray)):
            raw_wait = raw_wait.decode()
        return bool(allowed), float(raw_wait)

    async def acquire(self, tokens: float = 1.0) -> None:
        """Block until ``tokens`` can be taken, sleeping the computed wait."""
        while True:
            allowed, wait = await self.try_acquire(tokens)
            if allowed:
                return
            await self._sleep(wait)


# Why a job is waiting for the next daily reset. Stored on job.paused_reason and
# in the per-tenant pause marker; the API turns them into sentences.
PAUSE_GLOBAL = "global_quota"
PAUSE_TENANT = "tenant_quota"


class DailyQuota:
    """Per-tenant and global daily request budgets, tracked in Redis counters."""

    def __init__(
        self,
        redis: Redis,
        *,
        global_daily_limit: int = 5000,
        pause_percent: int = 100,
        ttl_seconds: int = 48 * 3600,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self._redis = redis
        self._global_limit = global_daily_limit
        self._pause_at = global_daily_limit * pause_percent // 100
        self._ttl = ttl_seconds
        self._now = now_func or (lambda: datetime.now(timezone.utc))

    @property
    def global_limit(self) -> int:
        return self._global_limit

    @property
    def pause_at(self) -> int:
        """App-wide usage at which new jobs stop being started for the day."""
        return self._pause_at

    def _day(self) -> str:
        return self._now().strftime("%Y-%m-%d")

    def _global_key(self, day: str) -> str:
        return f"quota:global:{day}"

    def _tenant_key(self, tenant_id: uuid.UUID, day: str) -> str:
        return f"quota:tenant:{tenant_id}:{day}"

    async def _incr(self, key: str) -> int:
        value = await self._redis.incr(key)
        if value == 1:
            await self._redis.expire(key, self._ttl)
        return int(value)

    def _shop_key(self, shop: uuid.UUID, day: str) -> str:
        return f"quota:shop:{shop}:{day}"

    async def shop_usage(self, shop: uuid.UUID) -> int:
        """Requests made for one shop today. Display only: limits are per account."""
        return self._to_int(await self._redis.get(self._shop_key(shop, self._day())))

    async def reserve(
        self, tenant_id: uuid.UUID, tenant_limit: int, *, shop: uuid.UUID | None = None
    ) -> bool:
        """Reserve one request slot against both budgets.

        Increments global then tenant counters; if either would exceed its
        limit, the increments are rolled back and ``False`` is returned so the
        caller can defer the job. Counting on reserve (not on success) matches
        Etsy's model where every request sent counts toward the limit.
        """
        day = self._day()
        global_key = self._global_key(day)
        tenant_key = self._tenant_key(tenant_id, day)

        global_count = await self._incr(global_key)
        if global_count > self._global_limit:
            await self._redis.decr(global_key)
            return False

        tenant_count = await self._incr(tenant_key)
        if tenant_count > tenant_limit:
            await self._redis.decr(tenant_key)
            await self._redis.decr(global_key)
            return False

        if shop is not None:
            await self._incr(self._shop_key(shop, day))
        return True

    @staticmethod
    def _to_int(value: object) -> int:
        if value is None:
            return 0
        if isinstance(value, (bytes, bytearray)):
            value = value.decode()
        return int(value)

    async def admission(
        self, tenant_id: uuid.UUID, tenant_limit: int, cost: int
    ) -> str | None:
        """Whether a new job may start now: ``None``, or the reason it must wait.

        Reads the counters without reserving anything. ``cost`` is the most
        requests the job can make; it must fit in what the tenant has left, so a
        job is not started only to be cut off halfway by the tenant ceiling.
        """
        tenant_used, global_used = await self.usage(tenant_id)
        if global_used >= self._pause_at:
            return PAUSE_GLOBAL
        if tenant_limit <= 0 or tenant_used + min(cost, tenant_limit) > tenant_limit:
            return PAUSE_TENANT
        return None

    def _pause_key(self, tenant_id: uuid.UUID, day: str) -> str:
        return f"quota:paused:{tenant_id}:{day}"

    async def mark_paused(self, tenant_id: uuid.UUID, reason: str) -> None:
        """Remember that this tenant has work waiting today, and why (for the UI)."""
        await self._redis.set(self._pause_key(tenant_id, self._day()), reason, ex=self._ttl)

    async def paused_reason(self, tenant_id: uuid.UUID) -> str | None:
        raw = await self._redis.get(self._pause_key(tenant_id, self._day()))
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)

    async def usage(self, tenant_id: uuid.UUID) -> tuple[int, int]:
        """Return (tenant_used, global_used) today -- for the UI quota display."""
        day = self._day()
        tenant_used = await self._redis.get(self._tenant_key(tenant_id, day))
        global_used = await self._redis.get(self._global_key(day))
        return (self._to_int(tenant_used), self._to_int(global_used))
