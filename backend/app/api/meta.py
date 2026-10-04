"""Quota display and compliance metadata."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import active_tenant, get_quota, get_session
from app.core.config import get_settings
from app.core.errortracking import is_enabled
from app.db.models import ApiUsage, Tenant
from app.api.pauses import pause_out
from app.etsy.shops import owned_shop
from app.etsy.rate_limiter import PAUSE_GLOBAL, PAUSE_TENANT, DailyQuota

from redis.asyncio import Redis

from app.api.deps import get_redis
from app.core import limits, llm_status

router = APIRouter(prefix="/api", tags=["meta"])

HISTORY_DAYS = 7


async def _usage_history(
    session: AsyncSession, tenant_id, today: date, used_today: int
) -> list[schemas.QuotaDay]:
    """The last :data:`HISTORY_DAYS` days of usage, oldest first, gaps zero-filled.

    ``api_usage`` is the durable record but is written in batches, so today's row
    can lag; today's figure therefore comes from the live Redis counter that the
    rest of this response already reports.
    """
    start = today - timedelta(days=HISTORY_DAYS - 1)
    rows = await session.execute(
        select(ApiUsage.usage_date, ApiUsage.request_count).where(
            ApiUsage.tenant_id == tenant_id, ApiUsage.usage_date >= start
        )
    )
    counts = {row[0]: row[1] for row in rows}
    return [
        schemas.QuotaDay(
            date=day.isoformat(),
            count=used_today if day == today else int(counts.get(day, 0)),
        )
        for day in (start + timedelta(days=i) for i in range(HISTORY_DAYS))
    ]


@router.get("/quota", response_model=schemas.QuotaOut)
async def get_quota_status(
    shop: uuid.UUID | None = None,
    tenant: Tenant = Depends(active_tenant),
    quota: DailyQuota = Depends(get_quota),
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> schemas.QuotaOut:
    """The account's Etsy requests today (ToU requires the remaining quota be
    shown to the user): the ceiling, what is used and left, and when it resets.

    The sidebar, the batch page's metric and the review page all show this same
    object (core/limits.py). The app's shared budget is not in it.
    """
    ceiling = await limits.etsy_ceiling(quota, tenant)
    today = datetime.now(timezone.utc).date()
    return schemas.QuotaOut(
        ceiling=schemas.EtsyCeilingOut(**ceiling.out()),
        usage_date=today.strftime("%Y-%m-%d"),
        history=await _usage_history(session, tenant.id, today, ceiling.used),
        shop_used=await _shop_used(session, quota, tenant, shop),
        pause=pause_out(await _pause_reason(quota, tenant, ceiling), tenant=tenant, ceiling=ceiling),
        generation_pause=llm_status.SELLER_MESSAGE if await llm_status.current(redis) else None,
    )


async def _shop_used(
    session: AsyncSession, quota: DailyQuota, tenant: Tenant, shop: uuid.UUID | None
) -> int | None:
    """That shop's share of today's requests; only for one of the caller's own shops."""
    if shop is None:
        return None
    if await owned_shop(session, tenant.id, shop) is None:
        raise HTTPException(status_code=404, detail="shop not found")
    return await quota.shop_usage(shop)


async def _pause_reason(quota: DailyQuota, tenant: Tenant, ceiling: limits.EtsyCeiling) -> str | None:
    """Why new work would wait right now, if it would.

    A job the worker already paused leaves a marker saying why; without one, the
    counters alone tell whether the next job would start.
    """
    marked = await quota.paused_reason(tenant.id)
    if marked:
        return marked
    if (await limits.app_budget(quota)).paused:
        return PAUSE_GLOBAL
    if ceiling.remaining <= 0:
        return PAUSE_TENANT
    return None


@router.get("/meta", response_model=schemas.MetaOut)
async def get_meta() -> schemas.MetaOut:
    settings = get_settings()
    return schemas.MetaOut(
        support_email=settings.support_email,
        operator_name=settings.operator_name,
        operator_location=settings.operator_location,
        governing_law=settings.governing_law,
        dispute_venue=settings.dispute_venue,
        error_tracking=is_enabled(),
    )
