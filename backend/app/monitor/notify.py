"""One alert per problem. The same problem, grouped per account, is said once until it is fixed or a
day has passed; at most ``MAX_PER_HOUR`` messages an hour, and what does not fit goes in the next
digest; "resolved" when a problem has stayed away for ``RESOLVE_AFTER_SECONDS``."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

MAX_PER_HOUR = 20
REPEAT_AFTER_SECONDS = 86400
#: A problem that comes and goes is one problem: it counts as gone after this long without it.
RESOLVE_AFTER_SECONDS = 600

OPEN = "monitor:open"
DEFERRED = "monitor:deferred"
PREFIX = "[Listyro]"


@dataclass(frozen=True)
class Problem:
    #: The same key is the same problem: one kind of thing, for one account (or the app).
    key: str
    #: What it is, for the group ("go-live failed").
    kind: str
    #: Everything the operator needs, in a line. No token, no buyer data, emails masked.
    line: str
    tenant_id: str | None = None


def _hour_key(now: float) -> str:
    return f"monitor:sent:{int(now // 3600)}"


async def _room(redis: Any, now: float) -> bool:
    used = await redis.get(_hour_key(now))
    return int(used or 0) < MAX_PER_HOUR


async def _sent(redis: Any, now: float) -> None:
    key = _hour_key(now)
    await redis.incr(key)
    await redis.expire(key, 7200)


async def _load(redis: Any) -> dict[str, dict[str, Any]]:
    out = {}
    for key, raw in (await redis.hgetall(OPEN)).items():
        out[key.decode() if isinstance(key, bytes) else key] = json.loads(raw)
    return out


async def apply(redis: Any, problems: list[Problem], send: Callable[[str], Awaitable[bool]], *,
                now: float | None = None) -> dict[str, int]:
    """Bring the open problems up to date and send what needs saying. A message that could not be
    delivered is tried again next time; nothing is marked as said until it was."""
    now = time.time() if now is None else now
    known = await _load(redis)
    counts = {"alerts": 0, "resolved": 0, "deferred": 0}
    current = {p.key: p for p in problems}
    for key, problem in current.items():
        state = known.get(key) or {"first": now, "alerted": None}
        state.update(kind=problem.kind, line=problem.line, tenant=problem.tenant_id, seen=now)
        alerted = state.get("alerted")
        if alerted is None or now - alerted >= REPEAT_AFTER_SECONDS:
            if not await _room(redis, now):
                state["deferred"] = True
                await redis.hset(DEFERRED, key, problem.line)
                counts["deferred"] += 1
            else:
                prefix = "Still: " if alerted is not None else ""
                if await send(f"{PREFIX} {prefix}{problem.kind}: {problem.line}"):
                    await _sent(redis, now)
                    state["alerted"] = now
                    state.pop("deferred", None)
                    await redis.hdel(DEFERRED, key)
                    counts["alerts"] += 1
        await redis.hset(OPEN, key, json.dumps(state))
    for key, state in known.items():
        if key in current or now - state.get("seen", now) < RESOLVE_AFTER_SECONDS:
            continue
        if state.get("alerted") is not None:
            if not await _room(redis, now):
                continue  # said when there is room; it is still marked open until then
            if not await send(f"{PREFIX} Resolved: {state.get('kind')}: {state.get('line')}"):
                continue
            await _sent(redis, now)
            counts["resolved"] += 1
        await redis.hdel(OPEN, key)
        await redis.hdel(DEFERRED, key)
    return counts


async def still_open(redis: Any) -> list[dict[str, Any]]:
    return sorted((await _load(redis)).values(), key=lambda s: s.get("first", 0))


async def take_deferred(redis: Any) -> list[str]:
    """What the hourly cap kept back, for the digest. Taken once: the digest has said it."""
    lines = [v.decode() if isinstance(v, bytes) else v for v in (await redis.hgetall(DEFERRED)).values()]
    await redis.delete(DEFERRED)
    return sorted(lines)
