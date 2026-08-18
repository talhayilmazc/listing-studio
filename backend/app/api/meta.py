"""Quota display and compliance metadata."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.api import schemas
from app.api.deps import current_tenant, get_quota
from app.core.config import get_settings
from app.db.models import Tenant
from app.etsy.rate_limiter import DailyQuota

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/quota", response_model=schemas.QuotaOut)
async def get_quota_status(
    tenant: Tenant = Depends(current_tenant),
    quota: DailyQuota = Depends(get_quota),
) -> schemas.QuotaOut:
    """Remaining daily Etsy API quota (ToU requires this be shown to the user)."""
    tenant_used, global_used = await quota.usage(tenant.id)
    settings = get_settings()
    return schemas.QuotaOut(
        tenant_used=tenant_used,
        tenant_limit=tenant.daily_quota,
        tenant_remaining=max(0, tenant.daily_quota - tenant_used),
        global_used=global_used,
        global_limit=settings.global_daily_limit,
        global_remaining=max(0, settings.global_daily_limit - global_used),
        usage_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )


@router.get("/meta", response_model=schemas.MetaOut)
async def get_meta() -> schemas.MetaOut:
    return schemas.MetaOut(support_email=get_settings().support_email)
