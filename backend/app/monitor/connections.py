"""Every shop-connection attempt and how it ended (2026-10-23 to 2026-10-29: every connection was
refused for a week and a customer told us). Account ids and reasons only; kept two days."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

KEY = "monitor:connections"
KEEP_SECONDS = 2 * 86400

ATTEMPT = "attempt"
CONNECTED = "connected"
REFUSED = "refused"


@dataclass(frozen=True)
class Event:
    at: float
    kind: str
    tenant_id: str | None
    reason: str | None


async def record(redis: Any, kind: str, tenant_id: Any = None, reason: str | None = None, *,
                 now: float | None = None) -> None:
    """Never raises: a connection is never stopped by being counted."""
    now = time.time() if now is None else now
    member = json.dumps({"id": uuid.uuid4().hex, "at": now, "kind": kind,
                         "tenant": str(tenant_id) if tenant_id else None, "reason": (reason or "")[:200] or None})
    try:
        pipe = redis.pipeline(transaction=False)
        pipe.zadd(KEY, {member: now})
        pipe.zremrangebyscore(KEY, 0, now - KEEP_SECONDS)
        await pipe.execute()
    except Exception:  # noqa: BLE001
        logger.warning("monitor: could not record a shop connection (%s)", kind)


async def since(redis: Any, start: float) -> list[Event]:
    out = []
    for raw in await redis.zrangebyscore(KEY, start, "+inf"):
        row = json.loads(raw)
        out.append(Event(at=row["at"], kind=row["kind"], tenant_id=row.get("tenant"), reason=row.get("reason")))
    return sorted(out, key=lambda e: e.at)
