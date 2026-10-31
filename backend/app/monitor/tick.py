"""The monitoring loop. It runs in the API, not the worker, so a dead worker is still noticed; one API
process at a time holds the turn. If the API itself is down deploy/watch.sh says so from the host."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from app.core import alerts
from app.monitor import digest, notify, rules, signals

logger = logging.getLogger(__name__)

EVERY_SECONDS = 60
FLUSH_EVERY_SECONDS = 15
TURN = "monitor:tick:turn"


async def tick(sessionmaker: Any, redis: Any, *, now: datetime | None = None,
               send: Callable[[str], Awaitable[bool]] | None = None) -> dict[str, int]:
    """One look: what is wrong, what needs saying, and the digest when it is due."""
    now = now or datetime.now(timezone.utc)
    send = send or alerts.send
    async with sessionmaker() as session:
        found = await rules.problems(session, redis, now)
        counts = await notify.apply(redis, found, send, now=now.timestamp())
        day = digest.due(now)
        if day is not None and await redis.set(digest.SENT.format(day=day), "1", nx=True, ex=3 * 86400):
            text = await digest.build(session, redis, now, take_deferred=False)
            if await send(text):
                await notify.take_deferred(redis)
                counts["digest"] = 1
            else:
                await redis.delete(digest.SENT.format(day=day))  # tried again at the next look
    return counts


async def _loop(sessionmaker: Any, redis: Any) -> None:
    next_look = 0.0
    while True:
        await signals.flush(redis, "api")
        if time.time() >= next_look:
            next_look = time.time() + EVERY_SECONDS
            try:
                if await redis.set(TURN, "1", nx=True, ex=EVERY_SECONDS - 5):
                    await tick(sessionmaker, redis)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a look that failed must not end the looking
                logger.exception("monitor: this look failed; trying again in a minute")
        await asyncio.sleep(FLUSH_EVERY_SECONDS)


def start(sessionmaker: Any, redis: Any) -> asyncio.Task[None]:
    return asyncio.create_task(_loop(sessionmaker, redis), name="monitor")
