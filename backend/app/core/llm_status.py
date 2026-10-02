"""When the AI provider refuses us for a reason on OUR side (usage limit reached,
no credit, key rejected), writing listings is paused, not failed.

A design whose listing could not be written because of that is not the
seller's problem and not a fault in their design: nothing is recorded against
it, no allowance is spent, and the seller is told plainly in a banner. The
operator gets ONE alert when it starts, not one per attempt.

The pause is a marker in Redis that lasts ``PROBE_SECONDS``. While it is there
nothing calls the provider. When it runs out the next request tries once: a
success clears everything, a refusal sets the marker again (without a second
alert). So raising the limit or adding credit is picked up within minutes, with
nothing to press. Fails open: if Redis is unreachable, generation is attempted.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from redis.exceptions import RedisError

from app.core import alerts

logger = logging.getLogger(__name__)

OUTAGE_KEY = "llm:outage"
ALERTED_KEY = "llm:outage:alerted"
#: How long nothing is tried after a refusal.
PROBE_SECONDS = 10 * 60
#: A second alert for the same outage only after this long without a success.
ALERT_AGAIN_SECONDS = 7 * 24 * 3600
#: job.paused_reason for a job waiting on this (api/pauses.py has the sentence).
WAIT_LLM = "llm_unavailable"
#: How long a queued job waits before looking again (it reads Etsy first, so
#: it looks less often than a seller's own click does).
JOB_WAIT_SECONDS = 60 * 60

SELLER_MESSAGE = (
    "Writing new listings is paused: the AI service this app uses is not accepting our "
    "requests right now. This is on our side, not yours. Nothing is lost, none of your "
    "allowance was used, and we have been alerted. Your uploads, drafts and published "
    "listings are not affected. Try again in a little while."
)

KIND_TEXT = {
    "usage_limit": "the API key's usage limit is reached",
    "credit": "the credit balance is too low",
    "auth": "the API key was rejected",
}


class LLMUnavailable(Exception):
    """The provider refused the request for an account reason, not a content one."""

    def __init__(self, kind: str, resumes_at: datetime | None = None) -> None:
        super().__init__(KIND_TEXT.get(kind, kind))
        self.kind = kind
        self.resumes_at = resumes_at


_RESUMES = re.compile(r"regain access on (\d{4}-\d{2}-\d{2}) at (\d{2}:\d{2}) UTC")


def classify(status: int | None, message: str) -> LLMUnavailable | None:
    """The outage a provider error means, or None for an ordinary error.

    Only what the provider says about our account counts: an overloaded or
    rate-limited answer passes by itself and stays an ordinary failure.
    """
    text = message.lower()
    if "usage limit" in text:
        found = _RESUMES.search(message)
        when = None
        if found:
            when = datetime.strptime(f"{found[1]} {found[2]}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        return LLMUnavailable("usage_limit", when)
    if "credit balance" in text or "billing" in text:
        return LLMUnavailable("credit")
    if status in (401, 403):
        return LLMUnavailable("auth")
    return None


async def current(redis: Any) -> dict[str, Any] | None:
    """The outage in force, or None."""
    try:
        raw = await redis.get(OUTAGE_KEY)
    except (RedisError, OSError):
        return None
    return json.loads(raw) if raw else None


async def report(redis: Any, exc: LLMUnavailable) -> None:
    """Start (or extend) the pause; alert the operator once per outage."""
    now = datetime.now(timezone.utc)
    try:
        before = await redis.get(OUTAGE_KEY)
        since = json.loads(before)["since"] if before else now.isoformat()
        await redis.set(
            OUTAGE_KEY,
            json.dumps({"kind": exc.kind, "since": since, "resumes_at": exc.resumes_at.isoformat() if exc.resumes_at else None}),
            ex=PROBE_SECONDS,
        )
        first = await redis.set(ALERTED_KEY, now.isoformat(), nx=True, ex=ALERT_AGAIN_SECONDS)
    except (RedisError, OSError):
        logger.warning("could not record the AI-provider outage")
        return
    logger.error("AI provider unavailable (%s); listing generation paused", exc.kind)
    if first:
        back = f" Access returns {exc.resumes_at:%Y-%m-%d %H:%M} UTC unless the limit is raised." if exc.resumes_at else ""
        await alerts.send(
            "Listyro: listing generation is PAUSED. The AI provider refused a request because "
            f"{KIND_TEXT.get(exc.kind, exc.kind)}.{back} Sellers see a banner; nothing is failing or lost. "
            "It resumes by itself within 10 minutes of the account being fixed."
        )


async def cleared(redis: Any) -> None:
    """A request succeeded: whatever outage there was is over."""
    try:
        if await redis.exists(ALERTED_KEY):
            await redis.delete(OUTAGE_KEY, ALERTED_KEY)
            logger.info("AI provider is answering again; listing generation resumed")
    except (RedisError, OSError):
        pass
