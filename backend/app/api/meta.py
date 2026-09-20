"""Quota display and compliance metadata."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import active_tenant, get_quota, get_session
from app.core.config import get_settings
from app.db.models import ApiUsage, Tenant
from app.etsy.rate_limiter import DailyQuota

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
    tenant: Tenant = Depends(active_tenant),
    quota: DailyQuota = Depends(get_quota),
    session: AsyncSession = Depends(get_session),
) -> schemas.QuotaOut:
    """Remaining daily Etsy API quota (ToU requires this be shown to the user)."""
    tenant_used, global_used = await quota.usage(tenant.id)
    settings = get_settings()
    today = datetime.now(timezone.utc).date()
    return schemas.QuotaOut(
        tenant_used=tenant_used,
        tenant_limit=tenant.daily_quota,
        tenant_remaining=max(0, tenant.daily_quota - tenant_used),
        global_used=global_used,
        global_limit=settings.global_daily_limit,
        global_remaining=max(0, settings.global_daily_limit - global_used),
        usage_date=today.strftime("%Y-%m-%d"),
        history=await _usage_history(session, tenant.id, today, tenant_used),
    )


@router.get("/meta", response_model=schemas.MetaOut)
async def get_meta() -> schemas.MetaOut:
    return schemas.MetaOut(support_email=get_settings().support_email)
