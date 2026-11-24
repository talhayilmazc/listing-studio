"""Listing videos that could not be added to their drafts on Etsy, counted for the operator
(feature/listing-video; workers/listing_video.py counts, monitor/rules.py alerts, monitor/digest.py reports).

One entry a failed upload (a time, nothing else: no account, no listing, no file name), kept two days. The
seller already sees each failure on the listing with "Try again"; this is so that many of them at once (Etsy
refusing a kind of file, our own mistake after a deploy) reach us before a seller writes.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

KEY = "monitor:video-failed"
KEEP_SECONDS = 2 * 86400
#: More than this many in an hour is one alert.
ALERT_OVER = 10
HOUR = 3600
DAY = 86400


async def failed(redis: Any, now: float | None = None) -> None:
    """Never raises: counting must not break the job that counts."""
    if redis is None:
        return
    try:
        stamp = time.time() if now is None else now
        await redis.zadd(KEY, {uuid.uuid4().hex: stamp})
        await redis.zremrangebyscore(KEY, 0, stamp - KEEP_SECONDS)
        await redis.expire(KEY, KEEP_SECONDS)
    except Exception:  # noqa: BLE001
        pass


async def count(redis: Any, seconds: int, now: float | None = None) -> int:
    """How many failed in the last ``seconds``."""
    if redis is None:
        return 0
    stamp = time.time() if now is None else now
    return int(await redis.zcount(KEY, stamp - seconds, "+inf") or 0)
