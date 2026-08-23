"""Shared FastAPI dependencies.

Auth (OAuth, step 2) isn't built yet, so the API operates against a single
get-or-create **development tenant**. This is a deliberate stand-in until real
authentication lands; every endpoint is already tenant-scoped so swapping in a
real ``current_tenant`` later is a one-line change.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collections.abc import Callable

import httpx

from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.db.models import Tenant
from app.db.session import get_sessionmaker
from app.etsy.connection import ConnectionService
from app.etsy.rate_limiter import DailyQuota
from app.pipeline.cost import CostCalculator
from app.pipeline.images import ImageProcessor
from app.pipeline.ingest import BatchIngestor
from app.pipeline.sku import SkuParser
from app.pipeline.storage import LocalStorage, Storage

_DEV_TENANT_EMAIL = "dev@localhost"


async def get_session() -> AsyncIterator[AsyncSession]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


async def current_tenant(session: AsyncSession = Depends(get_session)) -> Tenant:
    """Return the development tenant, creating it on first use."""
    result = await session.execute(select(Tenant).where(Tenant.email == _DEV_TENANT_EMAIL))
    tenant = result.scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(email=_DEV_TENANT_EMAIL, password_hash="!", daily_quota=2000)
        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)
    return tenant


@lru_cache
def get_storage() -> Storage:
    return LocalStorage(get_settings().storage_dir)


@lru_cache
def get_ingestor() -> BatchIngestor:
    return BatchIngestor(
        sessionmaker=get_sessionmaker(),
        storage=get_storage(),
        processor=ImageProcessor(),
        sku_parser=SkuParser(),
    )


@lru_cache
def get_cost_calculator() -> CostCalculator:
    return CostCalculator()


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url)


def get_quota() -> DailyQuota:
    return DailyQuota(get_redis(), global_daily_limit=get_settings().global_daily_limit)


def get_connection_service() -> ConnectionService:
    settings = get_settings()
    return ConnectionService(
        get_cipher(),
        client_id=settings.etsy_client_id,
        token_url=settings.etsy_oauth_token_url,
    )


def get_token_http_factory() -> Callable[[], httpx.AsyncClient]:
    """Factory for the httpx client used in the OAuth token exchange (mockable)."""
    return httpx.AsyncClient
