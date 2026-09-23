"""One log line per Etsy API request: when, what, for which job, and how busy that second was.

Written for the 429 diagnosis (docs/duzeltmeler-v5.md §A) and kept, because a
rate-limit problem can only be understood from the timeline. Each line records:

* the wall-clock time to the millisecond,
* the method and path (shop and listing ids only, never a token),
* the job the request belongs to (set by the worker gate),
* the HTTP status and how long the request waited for the bucket,
* how many requests *all* workers sent in that same wall-clock second, from a
  shared Redis counter. A 429 is logged with the count for its second and the
  second before it.
"""

from __future__ import annotations

import contextvars
import logging
import time
from datetime import datetime, timezone

from redis.asyncio import Redis

logger = logging.getLogger("app.etsy.calls")

# The job whose Etsy requests are being made, e.g. "run_publish_job:<job id>".
current_job: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_etsy_job", default=None
)

_COUNTER_TTL_SECONDS = 120


class CallLog:
    def __init__(self, redis: Redis | None, *, time_func=None) -> None:  # noqa: ANN001
        self._redis = redis
        self._now = time_func or time.time

    async def _count_this_second(self, second: int) -> int | None:
        if self._redis is None:
            return None
        try:
            key = f"etsy:calls:{second}"
            count = int(await self._redis.incr(key))
            if count == 1:
                await self._redis.expire(key, _COUNTER_TTL_SECONDS)
            return count
        except Exception:  # noqa: BLE001 - diagnostics must never break a request
            return None

    async def _count_of(self, second: int) -> int | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(f"etsy:calls:{second}")
            return int(raw) if raw is not None else 0
        except Exception:  # noqa: BLE001
            return None

    async def sent(self) -> tuple[float, int | None]:
        """Record that a request is going out now. Returns (time, count in this second)."""
        now = self._now()
        return now, await self._count_this_second(int(now))

    async def record(
        self,
        *,
        sent_at: float,
        in_second: int | None,
        method: str,
        path: str,
        status: int,
        waited: float,
        attempt: int,
    ) -> None:
        stamp = datetime.fromtimestamp(sent_at, tz=timezone.utc).isoformat(timespec="milliseconds")
        job = current_job.get() or "-"
        if status == 429:
            before = await self._count_of(int(sent_at) - 1)
            logger.warning(
                "etsy call %s %s %s job=%s status=429 attempt=%d waited=%.3fs "
                "calls_this_second=%s calls_previous_second=%s",
                stamp, method, path, job, attempt, waited, in_second, before,
            )
            return
        logger.info(
            "etsy call %s %s %s job=%s status=%d attempt=%d waited=%.3fs calls_this_second=%s",
            stamp, method, path, job, status, attempt, waited, in_second,
        )
