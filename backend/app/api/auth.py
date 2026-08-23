"""Etsy OAuth 2.0 (PKCE) endpoints.

Flow: ``/start`` builds an authorization URL (state + PKCE) and redirects the
browser to Etsy; Etsy redirects back to ``/callback`` with a code; we exchange it
for tokens and persist an encrypted :class:`EtsyConnection`. Tokens never reach
the frontend.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import (
    current_tenant,
    get_connection_service,
    get_redis,
    get_session,
    get_token_http_factory,
)
from app.core.config import get_settings
from app.db.models import Tenant
from app.etsy.connection import ConnectionService
from app.etsy.oauth import (
    OAuthError,
    build_authorize_url,
    exchange_code,
    generate_code_verifier,
    generate_state,
)

router = APIRouter(prefix="/api/auth/etsy", tags=["auth"])

_STATE_TTL = 600  # seconds a pending authorization may stay unclaimed


def _state_key(state: str) -> str:
    return f"oauth:state:{state}"


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=302)


@router.get("/start")
async def start(
    tenant: Tenant = Depends(current_tenant),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    """Begin the OAuth flow: store PKCE state, redirect the browser to Etsy."""
    settings = get_settings()
    if not settings.etsy_client_id:
        # Can't start without credentials; bounce back with an error marker.
        return _redirect(f"{settings.frontend_url}/connect?status=unconfigured")

    state = generate_state()
    verifier = generate_code_verifier()
    # The verifier is a secret held only server-side, keyed by the opaque state.
    await redis.set(_state_key(state), f"{tenant.id}:{verifier}", ex=_STATE_TTL)

    url = build_authorize_url(
        authorize_url=settings.etsy_oauth_authorize_url,
        client_id=settings.etsy_client_id,
        redirect_uri=settings.etsy_redirect_uri,
        scopes=settings.etsy_scopes,
        state=state,
        verifier=verifier,
    )
    return RedirectResponse(url, status_code=302)


@router.get("/callback")
async def callback(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    service: ConnectionService = Depends(get_connection_service),
    client_factory: Callable[[], httpx.AsyncClient] = Depends(get_token_http_factory),
) -> RedirectResponse:
    """Handle Etsy's redirect back: validate state, exchange code, persist tokens."""
    settings = get_settings()
    back = f"{settings.frontend_url}/connect"

    if error or not state or not code:
        return _redirect(f"{back}?status=denied")

    stored = await redis.get(_state_key(state))
    if stored is None:
        return _redirect(f"{back}?status=expired")
    await redis.delete(_state_key(state))

    raw = stored.decode() if isinstance(stored, (bytes, bytearray)) else str(stored)
    tenant_id_str, _, verifier = raw.partition(":")
    tenant_id = uuid.UUID(tenant_id_str)

    try:
        async with client_factory() as client:
            tokens = await exchange_code(
                client,
                token_url=settings.etsy_oauth_token_url,
                client_id=settings.etsy_client_id,
                redirect_uri=settings.etsy_redirect_uri,
                code=code,
                verifier=verifier,
            )
        await service.save_from_tokens(
            session, tenant_id, tokens, settings.etsy_scopes.split()
        )
    except OAuthError:
        # Never surface token/exchange internals to the browser.
        return _redirect(f"{back}?status=error")

    return _redirect(f"{back}?status=connected")


@router.get("/status", response_model=schemas.ConnectionOut)
async def status(
    tenant: Tenant = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
    service: ConnectionService = Depends(get_connection_service),
) -> schemas.ConnectionOut:
    connection = await service.get_active(session, tenant.id)
    if connection is None:
        return schemas.ConnectionOut(connected=False)
    return schemas.ConnectionOut(
        connected=True,
        status=connection.status.value,
        etsy_user_id=connection.etsy_user_id,
        shop_name=connection.shop_name,
        scopes=list(connection.scopes or []),
        connected_at=connection.connected_at,
        expires_at=connection.token_expires_at,
    )


@router.post("/disconnect", response_model=schemas.ConnectionOut)
async def disconnect(
    tenant: Tenant = Depends(current_tenant),
    session: AsyncSession = Depends(get_session),
    service: ConnectionService = Depends(get_connection_service),
) -> schemas.ConnectionOut:
    connection = await service.get_active(session, tenant.id)
    if connection is not None:
        await service.disconnect(session, connection)
    return schemas.ConnectionOut(connected=False)
