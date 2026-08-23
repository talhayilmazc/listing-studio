"""Persistence and lifecycle for a tenant's Etsy connection.

Tokens are encrypted at rest with :class:`TokenCipher` (Fernet) and only ever
held in memory long enough to encrypt or to build an outbound request. They are
never logged and never leave the backend.

``get_valid_access_token`` refreshes proactively: if the access token is within
:attr:`REFRESH_MARGIN` of expiry it exchanges the refresh token before returning,
persisting the rotated pair.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenCipher
from app.db.models import ConnectionStatus, EtsyConnection
from app.etsy.oauth import TokenResponse, refresh_tokens


def _parse_user_id(access_token: str) -> int | None:
    """Etsy v3 access tokens are ``{user_id}.{random}``; extract the user id."""
    head = access_token.split(".", 1)[0]
    return int(head) if head.isdigit() else None


class ConnectionService:
    #: Refresh when the access token has this little life left.
    REFRESH_MARGIN = timedelta(seconds=60)

    def __init__(
        self,
        cipher: TokenCipher,
        *,
        client_id: str,
        token_url: str,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._cipher = cipher
        self._client_id = client_id
        self._token_url = token_url
        self._client_factory = client_factory or httpx.AsyncClient

    async def save_from_tokens(
        self,
        session: AsyncSession,
        tenant_id: uuid.UUID,
        tokens: TokenResponse,
        scopes: list[str],
        *,
        now: datetime | None = None,
    ) -> EtsyConnection:
        """Create or update the tenant's connection with encrypted tokens."""
        now = now or datetime.now(timezone.utc)
        connection = await self._existing(session, tenant_id)
        if connection is None:
            connection = EtsyConnection(tenant_id=tenant_id)
            session.add(connection)

        connection.etsy_user_id = _parse_user_id(tokens.access_token)
        connection.access_token_enc = self._cipher.encrypt(tokens.access_token)
        connection.refresh_token_enc = self._cipher.encrypt(tokens.refresh_token)
        connection.token_expires_at = now + timedelta(seconds=tokens.expires_in)
        connection.scopes = scopes
        connection.status = ConnectionStatus.active
        connection.connected_at = now
        await session.commit()
        await session.refresh(connection)
        return connection

    async def get_active(
        self, session: AsyncSession, tenant_id: uuid.UUID
    ) -> EtsyConnection | None:
        rows = await session.execute(
            select(EtsyConnection).where(
                EtsyConnection.tenant_id == tenant_id,
                EtsyConnection.status == ConnectionStatus.active,
            )
        )
        return rows.scalars().first()

    async def get_valid_access_token(
        self,
        session: AsyncSession,
        connection: EtsyConnection,
        *,
        now: datetime | None = None,
    ) -> str:
        """Return a usable access token, refreshing first if near expiry."""
        now = now or datetime.now(timezone.utc)
        expires_at = connection.token_expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at is None or expires_at - now <= self.REFRESH_MARGIN:
            return await self._refresh(session, connection, now=now)

        assert connection.access_token_enc is not None
        return self._cipher.decrypt(connection.access_token_enc)

    async def _refresh(
        self, session: AsyncSession, connection: EtsyConnection, *, now: datetime
    ) -> str:
        assert connection.refresh_token_enc is not None
        refresh_token = self._cipher.decrypt(connection.refresh_token_enc)
        async with self._client_factory() as client:
            tokens = await refresh_tokens(
                client,
                token_url=self._token_url,
                client_id=self._client_id,
                refresh_token=refresh_token,
            )
        connection.access_token_enc = self._cipher.encrypt(tokens.access_token)
        connection.refresh_token_enc = self._cipher.encrypt(tokens.refresh_token)
        connection.token_expires_at = now + timedelta(seconds=tokens.expires_in)
        await session.commit()
        await session.refresh(connection)
        return tokens.access_token

    async def disconnect(self, session: AsyncSession, connection: EtsyConnection) -> None:
        """Revoke a connection and drop its stored tokens.

        Per CLAUDE.md, revoking must also delete the tenant's Etsy-sourced
        content; that cascade lands with the retention/cleanup work (step 6).
        """
        connection.status = ConnectionStatus.revoked
        connection.access_token_enc = None
        connection.refresh_token_enc = None
        await session.commit()

    async def _existing(
        self, session: AsyncSession, tenant_id: uuid.UUID
    ) -> EtsyConnection | None:
        rows = await session.execute(
            select(EtsyConnection)
            .where(EtsyConnection.tenant_id == tenant_id)
            .order_by(EtsyConnection.connected_at.desc())
        )
        return rows.scalars().first()
