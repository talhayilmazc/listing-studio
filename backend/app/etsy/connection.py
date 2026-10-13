"""Persistence and lifecycle for a tenant's Etsy connections, one per shop.

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
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.crypto import TokenCipher
from app.db.models import ConnectionStatus, EtsyConnection, Tenant
from app.etsy.oauth import TokenResponse, refresh_tokens
from app.etsy.shops import ShopLimitReached, ShopTaken, active_shops, shop_slots

#: Why a job, a go-live or a planned draft of a disconnected shop did not happen.
DISCONNECTED = "cancelled: its shop was disconnected"

__all__ = ["ConnectionService", "ShopLimitReached", "ShopTaken"]


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
        """Connect a shop, or reconnect one this account already had.

        One Etsy account owns one shop, so the Etsy user id in the token names the
        shop. Raises :class:`ShopTaken` if another account has it connected, and
        :class:`ShopLimitReached` if a new shop would pass a ceiling (v5 §E).
        Nothing is written in either case.
        """
        now = now or datetime.now(timezone.utc)
        user_id = _parse_user_id(tokens.access_token)
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None

        connection = None
        if user_id is not None:
            elsewhere = await session.execute(
                select(EtsyConnection.id).where(
                    EtsyConnection.etsy_user_id == user_id,
                    EtsyConnection.status == ConnectionStatus.active,
                    EtsyConnection.tenant_id != tenant_id,
                )
            )
            if elsewhere.first() is not None:
                raise ShopTaken()
            mine = await session.execute(
                select(EtsyConnection)
                .where(
                    EtsyConnection.tenant_id == tenant_id,
                    EtsyConnection.etsy_user_id == user_id,
                )
                .order_by(EtsyConnection.connected_at.desc())
            )
            connection = mine.scalars().first()

        if connection is None or connection.status is not ConnectionStatus.active:
            slots = await shop_slots(session, tenant)
            if slots.paused:
                # New connections are paused; a shop that is connected now can
                # still reconnect (to grant a permission), since that adds no shop.
                raise ShopLimitReached("paused")
            if slots.used >= slots.limit:
                raise ShopLimitReached("account")
            if slots.app_used >= slots.app_limit:
                raise ShopLimitReached("app")
        if connection is None:
            last = max((c.position for c in await active_shops(session, tenant_id)), default=-1)
            connection = EtsyConnection(tenant_id=tenant_id, position=last + 1)
            session.add(connection)

        connection.etsy_user_id = user_id
        connection.access_token_enc = self._cipher.encrypt(tokens.access_token)
        connection.refresh_token_enc = self._cipher.encrypt(tokens.refresh_token)
        connection.token_expires_at = now + timedelta(seconds=tokens.expires_in)
        connection.scopes = scopes
        connection.status = ConnectionStatus.active
        connection.connected_at = now
        await session.commit()
        await session.refresh(connection)
        return connection


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

    async def disconnect(
        self, session: AsyncSession, connection: EtsyConnection, *, actor: Tenant | None = None
    ) -> dict[str, int]:
        """Disconnect one shop: drop its tokens, cancel what was waiting for it and
        delete its Etsy-sourced content.

        CLAUDE.md: when a seller disconnects, everything that came from Etsy for
        that shop is deleted, not left to age out. Its tokens go at once, so no
        further call can be made for it; its queued jobs, scheduled go-lives and
        planned drafts are cancelled, and the counts are returned so the seller is
        told what was cancelled. The account's other shops, its uploads and its
        generated content are untouched (v5 §E). One transaction, so a failure
        cannot leave the tokens gone but the content behind.
        """
        from app.db.models import Job, JobStatus, ListingPublication, PlannedSlot
        from app.workers.retention import purge_shop_etsy_content

        now = datetime.now(timezone.utc)
        connection.status = ConnectionStatus.revoked
        connection.access_token_enc = None
        connection.refresh_token_enc = None
        connection.token_expires_at = None
        # What was waiting for this shop. Counted before the purge removes the
        # publication rows the schedules hang on.
        scheduled = int(await session.scalar(
            select(func.count()).select_from(ListingPublication).where(
                ListingPublication.connection_id == connection.id,
                ListingPublication.scheduled_for.is_not(None),
                ListingPublication.state != "active",
            )
        ) or 0)
        jobs = await session.execute(
            update(Job)
            .where(Job.connection_id == connection.id, Job.status == JobStatus.queued)
            .values(status=JobStatus.cancelled, last_error=DISCONNECTED, paused_reason=None, finished_at=now)
            .execution_options(synchronize_session=False)
        )
        slots = await session.execute(
            update(PlannedSlot)
            .where(PlannedSlot.connection_id == connection.id, PlannedSlot.state.in_(("waiting", "drafting")))
            .values(state="cancelled", note=DISCONNECTED)
            .execution_options(synchronize_session=False)
        )
        cancelled = {
            "queued_jobs": jobs.rowcount or 0,
            "scheduled_publishes": scheduled,
            "planned_drafts": slots.rowcount or 0,
        }
        purged = await purge_shop_etsy_content(session, connection.id)
        # Counts only: what went, not what it was.
        audit.destructive(
            session, "shop.disconnected", actor=actor, tenant_id=connection.tenant_id,
            shop_id=connection.id, object_id=connection.id, **purged, **cancelled,
        )
        await session.commit()
        return cancelled
