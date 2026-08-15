"""Batched persistence of per-tenant daily API usage.

Redis counters are the hot path for quota decisions; the ``api_usage`` table is
the durable record. To avoid a DB write per Etsy call, successful calls are
buffered in memory and flushed in batches (on a threshold and on a periodic
cron in the worker).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiUsage


class UsageRecorder:
    """In-memory accumulator of (tenant, day) -> request count."""

    def __init__(self, flush_threshold: int = 50) -> None:
        self._buffer: dict[tuple[uuid.UUID, date], int] = defaultdict(int)
        self._threshold = flush_threshold

    def record(self, tenant_id: uuid.UUID, day: date, count: int = 1) -> None:
        self._buffer[(tenant_id, day)] += count

    @property
    def pending(self) -> int:
        return sum(self._buffer.values())

    async def flush(self, session: AsyncSession) -> None:
        """Upsert buffered counts into ``api_usage`` and clear the buffer."""
        if not self._buffer:
            return
        items = list(self._buffer.items())
        self._buffer.clear()
        for (tenant_id, day), delta in items:
            row = await session.get(ApiUsage, (tenant_id, day))
            if row is None:
                session.add(
                    ApiUsage(tenant_id=tenant_id, usage_date=day, request_count=delta)
                )
            else:
                row.request_count += delta
        await session.commit()

    async def maybe_flush(self, session: AsyncSession) -> None:
        """Flush only once the buffer crosses the threshold."""
        if self.pending >= self._threshold:
            await self.flush(session)
