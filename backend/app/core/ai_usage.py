"""What the AI provider's work for each account cost us: admin-only.

Token counts, model names and dollar cost are our cost of goods. They are
recorded here per account, per day and per model, and read only by
``/api/admin/ai-cost``. Nothing a seller can call returns any of it
(``tests/test_no_ai_cost_for_sellers.py``).

Every model call counts, including the ones behind a listing that failed
validation and the ones whose listing the seller later deleted: the money was
spent either way. That is why this is its own table and not a sum over
``generated_content``, which goes when a batch is deleted.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AiUsageDaily
from app.pipeline.cost import CostCalculator, UnknownModelError
from app.pipeline.llm import Usage

async def record(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    usages: Iterable[Usage],
    *,
    listings: int = 0,
    day: date | None = None,
) -> None:
    """Add these model calls to the account's totals for the day. Does not commit."""
    day = day or datetime.now(timezone.utc).date()
    by_model: dict[str, list[Usage]] = {}
    for usage in usages:
        by_model.setdefault(usage.model or "unknown", []).append(usage)
    first = True
    for model, calls in by_model.items():
        add = {
            "calls": len(calls),
            "input_tokens": sum(u.input_tokens for u in calls),
            "output_tokens": sum(u.output_tokens for u in calls),
            "cache_write_tokens": sum(u.cache_creation_input_tokens for u in calls),
            "cache_read_tokens": sum(u.cache_read_input_tokens for u in calls),
            # A listing is counted once, against the first model that worked on it.
            "listings": listings if first else 0,
        }
        first = False
        where = (AiUsageDaily.tenant_id == tenant_id, AiUsageDaily.day == day, AiUsageDaily.model == model)
        bump = {getattr(AiUsageDaily, k): getattr(AiUsageDaily, k) + v for k, v in add.items()}
        done = await session.execute(update(AiUsageDaily).where(*where).values(bump))
        if done.rowcount:
            continue
        try:
            async with session.begin_nested():
                session.add(AiUsageDaily(tenant_id=tenant_id, day=day, model=model, **add))
        except IntegrityError:  # another request made the row first: add to it
            await session.execute(update(AiUsageDaily).where(*where).values(bump))


def cost_of(row: AiUsageDaily, calc: CostCalculator) -> Decimal | None:
    """USD for one row, or None when the model has no price entry."""
    try:
        return calc.cost_for(
            Usage(
                model=row.model,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                cache_creation_input_tokens=row.cache_write_tokens,
                cache_read_input_tokens=row.cache_read_tokens,
            )
        )
    except UnknownModelError:
        return None
