"""Helpers for building authenticated test clients.

Every endpoint now resolves its tenant from a server-side session, so tests must
open one. These helpers create a real tenant row and a real session through
:class:`SessionStore` on the fake Redis, then attach the cookie — the same path a
browser takes, so the tests exercise the actual auth code rather than a bypass.
"""

from __future__ import annotations

import uuid

from fakeredis import FakeAsyncRedis
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.sessions import SESSION_COOKIE, SessionStore
from app.db.models import Tenant, TenantStatus

# argon2 is slow by design; tests that do not exercise login use this marker
# directly so fixtures are not paying a KDF per tenant.
UNUSABLE_HASH = "!"


async def make_tenant(
    sm: async_sessionmaker,
    email: str,
    *,
    daily_quota: int = 2000,
    password_hash: str = UNUSABLE_HASH,
    must_change_password: bool = False,
) -> uuid.UUID:
    """Insert a tenant and return its id."""
    async with sm() as session:
        tenant = Tenant(
            email=email,
            password_hash=password_hash,
            status=TenantStatus.active,
            daily_quota=daily_quota,
            must_change_password=must_change_password,
        )
        session.add(tenant)
        await session.commit()
        return tenant.id


async def open_session(redis: FakeAsyncRedis, tenant_id: uuid.UUID) -> str:
    """Create a real session for ``tenant_id`` and return its token."""
    return await SessionStore(redis).create(tenant_id)


def authenticate(client: AsyncClient, token: str) -> None:
    """Attach a session cookie to every subsequent request from ``client``."""
    client.cookies.set(SESSION_COOKIE, token)


def sign_out(client: AsyncClient) -> None:
    client.cookies.delete(SESSION_COOKIE)
