"""Every call to the AI provider, one row each: admin-only.

The meter sits where every call passes, ``AnthropicLLMClient.complete_json``,
so nothing has to remember to count itself: the design analysis, each attempt
at writing a listing, the size-chart check, a call that was refused, one whose
answer could not be used. A row says who it was for, what it was for, which
model answered, the tokens of each billing class, whether it worked, and what
it cost at the price table in force (``core/ai_prices.py``).

These figures are our cost of goods. They are read only by
``/api/admin/ai-cost``; no seller-facing response carries them
(``tests/test_no_ai_cost_for_sellers.py``).

Who and what
------------
``scope(tenant_id, session)`` is opened by the code doing work for a seller.
Calls made inside it are held and written to that session when it closes,
whether the work succeeded, failed or raised: the spend happened either way,
so the rows are written after a rollback too. ``purpose("vision")`` is set by
the component making the call. A call made outside any scope (a script, an
evaluation, a path someone forgot) is still written, through a session of its
own, with no seller.

A call that never reached the model (a 4xx or 5xx, no connection) is a row with
no tokens and no cost: the provider does not bill it, and it is what explains a
gap between calls made and listings written.
"""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ai_prices
from app.db.models import AiCall

logger = logging.getLogger(__name__)

#: What a call was for. ``content_retry`` is the second attempt after the first
#: failed validation; ``eval`` is our own evaluation runs (no seller).
PURPOSES = {
    "vision": "Design analysis",
    "content": "Listing text",
    "content_retry": "Listing text, second attempt",
    "size_chart": "Size-chart check",
    "eval": "Evaluation runs (ours)",
    "backfill": "Before metering (listings that still existed)",
    "other": "Other",
}

#: Calls outside any scope are written through a session of their own. Tests
#: turn this off: there is no database behind a bare client there.
UNSCOPED_WRITES = True

_purpose: contextvars.ContextVar[str] = contextvars.ContextVar("ai_purpose", default="other")
_origin: contextvars.ContextVar[str | None] = contextvars.ContextVar("ai_origin", default=None)
_scope: contextvars.ContextVar["Meter | None"] = contextvars.ContextVar("ai_scope", default=None)


@dataclass
class Meter:
    """The calls made for one seller during one piece of work."""

    tenant_id: uuid.UUID | None
    rows: list[AiCall] = field(default_factory=list)

    def listing_written(self) -> None:
        """A listing came of these calls: counted once, on the call that wrote it."""
        for row in reversed(self.rows):
            if row.ok and row.purpose.startswith("content"):
                row.listings = 1
                return

    async def flush(self, session: AsyncSession) -> None:
        """Price the held rows and add them to ``session`` (the caller commits)."""
        if not self.rows:
            return
        prices = await ai_prices.load(session)
        for row in self.rows:
            row.cost_usd = ai_prices.cost_of(row, prices)
            session.add(row)
        self.rows = []


@contextmanager
def purpose(name: str) -> Iterator[None]:
    """What the calls made inside are for (set by the component that calls)."""
    token = _purpose.set(name)
    try:
        yield
    finally:
        _purpose.reset(token)


@contextmanager
def origin(name: str) -> Iterator[None]:
    """Everything inside is one thing regardless of the component (``eval``)."""
    token = _origin.set(name)
    try:
        yield
    finally:
        _origin.reset(token)


@asynccontextmanager
async def scope(tenant_id: uuid.UUID | None, session: AsyncSession) -> AsyncIterator[Meter]:
    """Meter the calls made inside for ``tenant_id``; write them when it closes.

    The rows are written even when the work inside raises or its transaction
    was rolled back: the session is rolled back first, then the rows are added
    and committed on their own.
    """
    meter = Meter(tenant_id)
    token = _scope.set(meter)
    try:
        yield meter
    except BaseException:
        _scope.reset(token)
        try:
            await session.rollback()
            await meter.flush(session)
            await session.commit()
        except Exception:  # noqa: BLE001 - metering never hides the real error
            logger.exception("AI calls could not be recorded after a failure")
        raise
    else:
        _scope.reset(token)
        await meter.flush(session)
        await session.commit()


async def record(
    *,
    model: str,
    usage: Any | None,
    ok: bool,
    error: str | None = None,
    started: float | None = None,
) -> None:
    """One call happened. Never raises: metering must not break the call."""
    now = datetime.now(timezone.utc)
    meter = _scope.get()
    one_hour = int(getattr(usage, "cache_creation_1h_input_tokens", 0) or 0)
    row = AiCall(
        at=now,
        day=now.date(),
        tenant_id=meter.tenant_id if meter else None,
        purpose=_origin.get() or _purpose.get(),
        model=model or "unknown",
        ok=ok,
        error=(error or None) and str(error)[:80],
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_write_tokens=max(0, int(getattr(usage, "cache_creation_input_tokens", 0) or 0) - one_hour),
        cache_write_1h_tokens=one_hour,
        cache_read_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        listings=0,
        duration_ms=None if started is None else int((time.monotonic() - started) * 1000),
    )
    if meter is not None:
        meter.rows.append(row)
        return
    if not UNSCOPED_WRITES:
        return
    try:
        from app.db.session import get_sessionmaker

        async with get_sessionmaker()() as session:
            row.cost_usd = ai_prices.cost_of(row, await ai_prices.load(session))
            session.add(row)
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception(
            "an AI call was not recorded: %s %s in=%s out=%s", row.purpose, row.model, row.input_tokens, row.output_tokens
        )
