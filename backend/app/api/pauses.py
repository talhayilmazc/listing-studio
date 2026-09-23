"""What a seller is told when their Etsy work waits for the daily reset.

The worker records only a reason code (``workers/gate.py``); the sentences live
here, so the job status and the quota banner say the same thing.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.api.schemas import PauseOut
from app.core.config import get_settings
from app.etsy.rate_limiter import PAUSE_GLOBAL, PAUSE_TENANT
from app.workers.gate import next_reset


def pause_message(reason: str, *, tenant_limit: int) -> str:
    if reason == PAUSE_GLOBAL:
        percent = get_settings().global_pause_percent
        return (
            "All sellers share one daily Etsy request limit, and "
            f"{percent}% of today's is used. New work waits for the daily reset so work "
            "already running can finish, then resumes automatically after 00:00 UTC."
        )
    return (
        f"Your shop has used its daily allowance of {tenant_limit:,} Etsy requests. "
        "New work waits for the daily reset, then resumes automatically after 00:00 UTC."
    )


def pause_out(reason: str | None, *, tenant_limit: int, resumes_at: datetime | None = None) -> PauseOut | None:
    if reason not in (PAUSE_GLOBAL, PAUSE_TENANT):
        return None
    return PauseOut(
        reason=reason,
        message=pause_message(reason, tenant_limit=tenant_limit),
        resumes_at=resumes_at or next_reset(datetime.now(timezone.utc)),
    )
