"""What a seller is told when their Etsy work waits for the daily reset.

The worker records only a reason code (``workers/gate.py``); the sentences live
here, so the job status and the quota banner say the same thing.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.api.schemas import PauseOut
from app.core import limits
from app.db.models import Tenant
from app.etsy.rate_limiter import PAUSE_GLOBAL, PAUSE_TENANT
from app.workers.gate import next_reset


def pause_message(reason: str, *, tenant: Tenant | None, ceiling: limits.EtsyCeiling | None = None) -> str:
    """Which number stopped the work, and when it resumes in the seller's time."""
    if reason == PAUSE_GLOBAL:
        return limits.app_budget_message(tenant, ceiling)
    return limits.ceiling_message(tenant, ceiling.limit if ceiling is not None else (limits.ceiling_limit(tenant) if tenant else 0))


#: A job waiting to run again by itself (workers/recovery.py): not the daily reset.
RETRY_MESSAGES = {
    "etsy_rate_limit": "Etsy asked for requests to slow down. This carries on by itself in a moment, where it stopped.",
    "etsy_unavailable": "Etsy did not answer. This tries again by itself in a moment and carries on where it stopped.",
    "llm_unavailable": (
        "Writing the new listing is paused on our side: the AI service is not accepting our "
        "requests right now. Nothing has changed on Etsy. This tries again by itself."
    ),
    "interrupted": "This was interrupted. It starts again by itself in a moment and carries on where it stopped.",
}


def pause_out(
    reason: str | None, *, tenant: Tenant | None, resumes_at: datetime | None = None, ceiling: limits.EtsyCeiling | None = None
) -> PauseOut | None:
    if reason in RETRY_MESSAGES:
        return PauseOut(
            reason=reason,
            message=RETRY_MESSAGES[reason],
            resumes_at=resumes_at or datetime.now(timezone.utc),
        )
    if reason not in (PAUSE_GLOBAL, PAUSE_TENANT):
        return None
    return PauseOut(
        reason=reason,
        message=pause_message(reason, tenant=tenant, ceiling=ceiling),
        resumes_at=resumes_at or next_reset(datetime.now(timezone.utc)),
    )
