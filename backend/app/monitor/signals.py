"""What each process counts, written to Redis every few seconds. In memory first: a page must not pay
a Redis round trip to be counted, and counting must never break the page."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

#: A request's time falls in the first bucket it fits (ms); a p95 is said as that bucket's top.
BUCKETS_MS = (25, 50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600, 60000)
#: Kept this long: a digest reads yesterday and today.
KEEP_SECONDS = 3 * 86400
MINUTE_KEEP_SECONDS = 3600

WORKER_BEAT = "monitor:beat:worker"
SCHEDULER_BEAT = "monitor:beat:scheduler"

_ID = re.compile(r"/(?:[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}|\d+)(?=/|$)")

_requests: Counter[int] = Counter()
_errors: Counter[int] = Counter()
_latency: Counter[tuple[str, str, int]] = Counter()
_idle_fired: Counter[int] = Counter()
_pool_peak = 0
_pool_capacity = 0


def route_of(path: str) -> str:
    """The path without its ids: one line per endpoint, whatever was asked for."""
    return _ID.sub("/{id}", path)


def _bucket(ms: float) -> int:
    for n, top in enumerate(BUCKETS_MS):
        if ms <= top:
            return n
    return len(BUCKETS_MS) - 1


def request_done(path: str, status: int, seconds: float, now: float | None = None) -> None:
    """One answered request. Only /api: the health check and a scanner's guesses are not traffic."""
    if not path.startswith("/api/"):
        return
    now = time.time() if now is None else now
    minute = int(now // 60)
    _requests[minute] += 1
    if status >= 500:
        _errors[minute] += 1
    if status != 404:  # (404s are whatever anyone typed: unbounded)
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        _latency[(day, route_of(path), _bucket(seconds * 1000))] += 1


def idle_timeout_fired(now: float | None = None) -> None:
    _idle_fired[int((time.time() if now is None else now) // 60)] += 1


def pool_in_use(checked_out: int, capacity: int) -> None:
    global _pool_peak, _pool_capacity
    _pool_peak = max(_pool_peak, checked_out)
    _pool_capacity = capacity


async def flush(redis: Any, kind: str, now: float | None = None) -> None:
    """Write what was counted. ``kind``: "api" or "worker". Never raises; what cannot be written now
    is written next time."""
    global _pool_peak
    now = time.time() if now is None else now
    requests, errors, latency, idle = dict(_requests), dict(_errors), dict(_latency), dict(_idle_fired)
    peak, capacity = _pool_peak, _pool_capacity
    try:
        pipe = redis.pipeline(transaction=False)
        for minute, n in requests.items():
            pipe.hincrby(f"monitor:http:{minute}", "total", n)
            pipe.hincrby(f"monitor:http:{minute}", "errors", errors.get(minute, 0))
            pipe.expire(f"monitor:http:{minute}", MINUTE_KEEP_SECONDS)
        for (day, route, bucket), n in latency.items():
            pipe.hincrby(f"monitor:latency:{day}", f"{route}|{bucket}", n)
            pipe.expire(f"monitor:latency:{day}", KEEP_SECONDS)
        for minute, n in idle.items():
            pipe.incrby(f"monitor:idle:{minute}", n)
            pipe.expire(f"monitor:idle:{minute}", MINUTE_KEEP_SECONDS)
        if capacity:
            key = f"monitor:pool:{kind}:{os.getpid()}"
            pipe.set(key, json.dumps({"peak": peak, "capacity": capacity}), ex=300)
        await pipe.execute()
    except Exception:  # noqa: BLE001 - counting never breaks anything
        logger.warning("monitor: could not write what was counted", exc_info=True)
        return
    for counter, seen in ((_requests, requests), (_errors, errors), (_latency, latency), (_idle_fired, idle)):
        counter.subtract(seen)
        for key in [k for k, v in counter.items() if v <= 0]:
            del counter[key]
    _pool_peak = 0


def reset() -> None:
    """For tests."""
    global _pool_peak, _pool_capacity
    for counter in (_requests, _errors, _latency, _idle_fired):
        counter.clear()
    _pool_peak = _pool_capacity = 0


async def errors_over(redis: Any, minutes: int, now: float) -> tuple[int, int]:
    """Requests and 5xx answers in the last ``minutes`` minutes."""
    current = int(now // 60)
    pipe = redis.pipeline(transaction=False)
    for minute in range(current - minutes + 1, current + 1):
        pipe.hgetall(f"monitor:http:{minute}")
    total = errors = 0
    for row in await pipe.execute():
        for field, value in row.items():
            name = field.decode() if isinstance(field, bytes) else field
            if name == "total":
                total += int(value)
            elif name == "errors":
                errors += int(value)
    return total, errors


async def idle_fired_over(redis: Any, minutes: int, now: float) -> int:
    current = int(now // 60)
    values = await redis.mget([f"monitor:idle:{m}" for m in range(current - minutes + 1, current + 1)])
    return sum(int(v) for v in values if v)


async def pools(redis: Any) -> list[dict[str, Any]]:
    """Each process's fullest moment of its pool, since its last report."""
    out = []
    async for key in redis.scan_iter(match="monitor:pool:*", count=100):
        raw = await redis.get(key)
        if raw:
            name = key.decode() if isinstance(key, bytes) else key
            out.append({"kind": name.split(":")[2], **json.loads(raw)})
    return out


async def slowest(redis: Any, days: list[str], *, top: int = 8) -> list[tuple[str, int, int]]:
    """The slowest endpoints: (route, p95 in ms, requests). The p95 is its bucket's top."""
    counts: dict[str, Counter[int]] = {}
    for day in days:
        for field, value in (await redis.hgetall(f"monitor:latency:{day}")).items():
            name = field.decode() if isinstance(field, bytes) else field
            route, bucket = name.rsplit("|", 1)
            counts.setdefault(route, Counter())[int(bucket)] += int(value)
    rows = []
    for route, buckets in counts.items():
        total = sum(buckets.values())
        seen = 0
        for bucket in sorted(buckets):
            seen += buckets[bucket]
            if seen >= total * 0.95:
                rows.append((route, BUCKETS_MS[bucket], total))
                break
    return sorted(rows, key=lambda r: (-r[1], -r[2]))[:top]


async def beat(redis: Any, key: str, now: float | None = None) -> None:
    try:
        await redis.set(key, str(time.time() if now is None else now), ex=86400)
    except Exception:  # noqa: BLE001
        logger.warning("monitor: could not write the heartbeat %s", key)
