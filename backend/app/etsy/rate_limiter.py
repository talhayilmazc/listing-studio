"""Redis-backed rate limiting for Etsy calls.

Two independent guards, applied in this order by the worker:

1. :class:`DailyQuota` -- per-tenant and global daily request budgets
   (Personal App allows 5.000/day per *app*; we target 5.000 with monitoring).
2. :class:`TokenBucket` -- a global 4 req/s token bucket shared by all workers,
   refilled atomically in Redis via a Lua script (Etsy's per-second limit is 5;
   4 leaves margin).

Both live in Redis so the limits hold across worker processes.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
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


class TokenBucket:
    """Global token bucket enforcing a steady requests-per-second ceiling."""

    def __init__(
        self,
        redis: Redis,
        *,
        rate: float = 4.0,
        capacity: float = 4.0,
        key: str = "bucket:global",
        time_func: Callable[[], float] | None = None,
    ) -> None:
        self._redis = redis
        self._rate = rate
        self._capacity = capacity
        self._key = key
        # Injectable monotonic clock (seconds) for deterministic tests.
        self._now = time_func or time.monotonic
        self._script = redis.register_script(_BUCKET_LUA)

    async def try_acquire(self, tokens: float = 1.0) -> tuple[bool, float]:
        """Attempt to take ``tokens``. Returns (allowed, seconds_until_available)."""
        now_ms = int(self._now() * 1000)
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
            await asyncio.sleep(wait)


class DailyQuota:
    """Per-tenant and global daily request budgets, tracked in Redis counters."""

    def __init__(
        self,
        redis: Redis,
        *,
        global_daily_limit: int = 5000,
        ttl_seconds: int = 48 * 3600,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self._redis = redis
        self._global_limit = global_daily_limit
        self._ttl = ttl_seconds
        self._now = now_func or (lambda: datetime.now(timezone.utc))

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

    async def reserve(self, tenant_id: uuid.UUID, tenant_limit: int) -> bool:
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

        return True

    @staticmethod
    def _to_int(value: object) -> int:
        if value is None:
            return 0
        if isinstance(value, (bytes, bytearray)):
            value = value.decode()
        return int(value)

    async def usage(self, tenant_id: uuid.UUID) -> tuple[int, int]:
        """Return (tenant_used, global_used) today -- for the UI quota display."""
        day = self._day()
        tenant_used = await self._redis.get(self._tenant_key(tenant_id, day))
        global_used = await self._redis.get(self._global_key(day))
        return (self._to_int(tenant_used), self._to_int(global_used))
