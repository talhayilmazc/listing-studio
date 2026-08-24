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
from sqlalchemy.exc import IntegrityError
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


async def _fetch_dev_tenant(session: AsyncSession) -> Tenant | None:
    result = await session.execute(select(Tenant).where(Tenant.email == _DEV_TENANT_EMAIL))
    return result.scalar_one_or_none()


async def current_tenant(session: AsyncSession = Depends(get_session)) -> Tenant:
    """Return the development tenant, creating it on first use.

    Idempotent under concurrency: the frontend fires /status, /quota and /meta in
    parallel and each may try to create the tenant. The unique ``email`` makes
    exactly one insert win; the losers catch IntegrityError and re-fetch the row.
    """
    tenant = await _fetch_dev_tenant(session)
    if tenant is not None:
        return tenant

    tenant = Tenant(email=_DEV_TENANT_EMAIL, password_hash="!", daily_quota=2000)
    session.add(tenant)
    try:
        await session.commit()
    except IntegrityError:
        # A concurrent request created it first; roll back and read the winner.
        await session.rollback()
        existing = await _fetch_dev_tenant(session)
        if existing is None:  # pragma: no cover - would mean a different constraint
            raise
        return existing
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


class Enqueuer:
    """Minimal arq enqueue wrapper (overridden with a stub in tests)."""

    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._pool = None

    async def enqueue(self, function: str, *args: object) -> None:
        if self._pool is None:
            from arq import create_pool
            from arq.connections import RedisSettings

            self._pool = await create_pool(RedisSettings.from_dsn(self._url))
        await self._pool.enqueue_job(function, *args)


@lru_cache
def get_enqueuer() -> Enqueuer:
    return Enqueuer(get_settings().redis_url)


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
