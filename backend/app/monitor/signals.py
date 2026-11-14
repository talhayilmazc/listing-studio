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

#: A request's time falls in the first bucket it fits (ms). The buckets count requests; the p95 itself comes
#: from the requests' own times (``SAMPLES_PER_DAY``), since fix/perf-and-memory (14 Nov 2026): a bucket's top
#: said "60,000 ms" for anything over 25.6 s and "3,200 ms" for anything from 1.6 to 3.2 s.
BUCKETS_MS = (25, 50, 100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600, 60000)
#: Kept this long: a digest reads yesterday and today.
KEEP_SECONDS = 3 * 86400
MINUTE_KEEP_SECONDS = 3600
#: The latest requests' own times kept per endpoint and day (the digest's p95) and per hour (the hour's alert).
SAMPLES_PER_DAY = 2000
SAMPLES_PER_HOUR = 1000
HOUR_KEEP_SECONDS = 3 * 3600

WORKER_BEAT = "monitor:beat:worker"
SCHEDULER_BEAT = "monitor:beat:scheduler"

_ID = re.compile(r"/(?:[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}|\d+)(?=/|$)")

_requests: Counter[int] = Counter()
_errors: Counter[int] = Counter()
_latency: Counter[tuple[str, str, int]] = Counter()
#: (day, minute, route) -> the requests' own times, ms (written at the next flush).
_samples: dict[tuple[str, int, str], list[int]] = {}
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


def endpoint(method: str | None, path: str) -> str:
    """The method too ("POST /api/batches/{id}/assets"), so an upload and a list are never one line."""
    return f"{method} {route_of(path)}" if method else route_of(path)


def request_done(path: str, status: int, seconds: float, now: float | None = None, method: str | None = None) -> None:
    """One answered request. Only /api: the health check and a scanner's guesses are not traffic. ``seconds`` is
    our own time: the middleware leaves out the time the request's body took to arrive (a seller's 50 MB photo
    on a slow line is not our slowness)."""
    if not path.startswith("/api/"):
        return
    now = time.time() if now is None else now
    minute = int(now // 60)
    _requests[minute] += 1
    if status >= 500:
        _errors[minute] += 1
    if status != 404:  # (404s are whatever anyone typed: unbounded)
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        route = endpoint(method, path)
        ms = max(0, int(round(seconds * 1000)))
        _latency[(day, route, _bucket(ms))] += 1
        kept = _samples.setdefault((day, minute, route), [])
        if len(kept) < SAMPLES_PER_HOUR:  # a flood between two flushes keeps its first ones
            kept.append(ms)


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
    samples = {k: list(v) for k, v in _samples.items()}
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
        for (day, minute, route), times in samples.items():
            hour = minute // 60
            for key, keep, ttl, values in (
                (f"monitor:ms:{day}:{route}", SAMPLES_PER_DAY, KEEP_SECONDS, [str(t) for t in times]),
                (f"monitor:ms-hour:{hour}:{route}", SAMPLES_PER_HOUR, HOUR_KEEP_SECONDS, [f"{minute}:{t}" for t in times]),
            ):
                pipe.lpush(key, *values)
                pipe.ltrim(key, 0, keep - 1)
                pipe.expire(key, ttl)
            pipe.sadd(f"monitor:ms-routes-hour:{hour}", route)
            pipe.expire(f"monitor:ms-routes-hour:{hour}", HOUR_KEEP_SECONDS)
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
    for key, written in samples.items():
        left = _samples.get(key, [])[len(written):]
        if left:
            _samples[key] = left
        else:
            _samples.pop(key, None)
    _pool_peak = 0


def reset() -> None:
    """For tests."""
    global _pool_peak, _pool_capacity
    for counter in (_requests, _errors, _latency, _idle_fired):
        counter.clear()
    _samples.clear()
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


def p95(times: list[int]) -> int:
    """The 95th percentile of the times, nearest rank: a time some request really took."""
    ordered = sorted(times)
    return ordered[max(0, -(-95 * len(ordered) // 100) - 1)]


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def slowest(redis: Any, days: list[str], *, top: int = 8) -> list[tuple[str, int, int, bool]]:
    """The slowest endpoints: (route, p95 in ms, requests, exact). ``exact``: the p95 is a time a request
    took (the last ``SAMPLES_PER_DAY`` requests of each day); otherwise (counted before the times were kept)
    it is the top of its bucket, and the digest says "up to"."""
    counts: dict[str, Counter[int]] = {}
    for day in days:
        for field, value in (await redis.hgetall(f"monitor:latency:{day}")).items():
            route, bucket = _text(field).rsplit("|", 1)
            counts.setdefault(route, Counter())[int(bucket)] += int(value)
    rows = []
    for route, buckets in counts.items():
        total = sum(buckets.values())
        times: list[int] = []
        for day in days:
            times += [int(_text(t)) for t in await redis.lrange(f"monitor:ms:{day}:{route}", 0, -1)]
        if times:
            rows.append((route, p95(times), total, True))
            continue
        seen = 0
        for bucket in sorted(buckets):
            seen += buckets[bucket]
            if seen >= total * 0.95:
                rows.append((route, BUCKETS_MS[bucket], total, False))
                break
    return sorted(rows, key=lambda r: (-r[1], -r[2]))[:top]


def said(ms: int, exact: bool) -> str:
    """A p95 as the digest says it: "1,234 ms", "14.2 s"; a bucket's top as "up to 3,200 ms" (its only
    honest reading), the last bucket as "over 25.6 s"."""
    if not exact:
        if ms >= BUCKETS_MS[-1]:
            return f"over {BUCKETS_MS[-2] / 1000:.1f} s"
        return f"up to {ms:,} ms"
    return f"{ms:,} ms" if ms < 10_000 else f"{ms / 1000:.1f} s"


async def slow_in_the_last_hour(redis: Any, now: float, *, over_ms: int, at_least: int) -> list[tuple[str, int, int]]:
    """Endpoints whose p95 over the last 60 minutes is above ``over_ms``, with at least ``at_least`` requests:
    (route, p95 ms, requests), slowest first."""
    minute = int(now // 60)
    since = minute - 59
    hours = sorted({since // 60, minute // 60})
    routes: set[str] = set()
    for hour in hours:
        routes |= {_text(r) for r in await redis.smembers(f"monitor:ms-routes-hour:{hour}")}
    out = []
    for route in routes:
        times: list[int] = []
        for hour in hours:
            for entry in await redis.lrange(f"monitor:ms-hour:{hour}:{route}", 0, -1):
                at, ms = _text(entry).split(":", 1)
                if since <= int(at) <= minute:
                    times.append(int(ms))
        if len(times) >= at_least and (worst := p95(times)) > over_ms:
            out.append((route, worst, len(times)))
    return sorted(out, key=lambda r: -r[1])


async def beat(redis: Any, key: str, now: float | None = None) -> None:
    try:
        await redis.set(key, str(time.time() if now is None else now), ex=86400)
    except Exception:  # noqa: BLE001
        logger.warning("monitor: could not write the heartbeat %s", key)
