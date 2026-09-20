"""Shared FastAPI dependencies.

Every request resolves its tenant from the server-side session cookie
(production-spec B1). There is no default or fallback tenant: without a valid
session the request is rejected with 401 before any handler runs, so no endpoint
can silently operate on somebody else's data.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from collections.abc import Callable

import httpx

from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.core.sessions import SESSION_COOKIE, SessionStore
from app.db.models import Tenant, TenantStatus
from app.db.session import get_sessionmaker
from app.etsy.connection import ConnectionService
from app.etsy.rate_limiter import DailyQuota
from app.pipeline.cost import CostCalculator
from app.pipeline.images import ImageProcessor
from app.pipeline.ingest import BatchIngestor
from app.pipeline.sku import SkuParser
from app.pipeline.storage import LocalStorage, Storage

async def get_session() -> AsyncIterator[AsyncSession]:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        yield session


def get_session_store() -> SessionStore:
    return SessionStore(
        get_redis(), ttl_seconds=get_settings().session_ttl_days * 24 * 3600
    )


async def current_tenant(
    request: Request,
    session: AsyncSession = Depends(get_session),
    sessions: SessionStore = Depends(get_session_store),
) -> Tenant:
    """The tenant owning this request, from its session cookie.

    401 when there is no usable session, when the tenant behind it no longer
    exists, or when the account is suspended. There is deliberately no
    get-or-create and no default tenant: a missing session must fail, never fall
    back to somebody's data.
    """
    record = await sessions.read(request.cookies.get(SESSION_COOKIE, ""))
    if record is None:
        raise HTTPException(status_code=401, detail="not authenticated")

    tenant = await session.get(Tenant, record.tenant_id)
    if tenant is None:
        # The account was deleted while the session lived on.
        await sessions.destroy(record.token)
        raise HTTPException(status_code=401, detail="not authenticated")
    if tenant.status is not TenantStatus.active:
        raise HTTPException(status_code=403, detail="account suspended")
    return tenant


async def active_tenant(tenant: Tenant = Depends(current_tenant)) -> Tenant:
    """A tenant that may use the product, i.e. not sitting on a temporary password.

    Everything except the account endpoints depends on this, so an admin-issued
    temporary password cannot be used to browse around (production-spec A4).
    """
    if tenant.must_change_password:
        raise HTTPException(status_code=403, detail="password change required")
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
