"""Etsy OAuth 2.0 (PKCE) endpoints.

Flow: ``/start`` builds an authorization URL (state + PKCE) and redirects the
browser to Etsy; Etsy redirects back to ``/callback`` with a code; we exchange it
for tokens and persist an encrypted :class:`EtsyConnection`. Tokens never reach
the frontend.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import (
    Enqueuer,
    active_tenant,
    get_connection_service,
    get_enqueuer,
    get_redis,
    get_session,
    get_token_http_factory,
)
from app.core.config import get_settings
from app.db.models import Tenant
from app.etsy.connection import ConnectionService
from app.etsy.shops import ShopLimitReached, ShopTaken, active_shops
from app.etsy.oauth import (
    OAuthError,
    clean_scopes,
    etsy_reason,
    build_authorize_url,
    exchange_code,
    generate_code_verifier,
    generate_state,
)

router = APIRouter(prefix="/api/auth/etsy", tags=["auth"])
logger = logging.getLogger(__name__)

_STATE_TTL = 600  # seconds a pending authorization may stay unclaimed


def _state_key(state: str) -> str:
    return f"oauth:state:{state}"


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=302)


@router.get("/start")
async def start(
    tenant: Tenant = Depends(active_tenant),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    """Begin the OAuth flow: store PKCE state, redirect the browser to Etsy."""
    settings = get_settings()
    if not settings.etsy_client_id:
        # Can't start without credentials; bounce back with an error marker.
        return _redirect(f"{settings.frontend_url}/connect?status=unconfigured")

    state = generate_state()
    verifier = generate_code_verifier()
    # The verifier is a secret held only server-side, keyed by the opaque state:
    # one key per authorization, so two tabs (a reconnect and a second shop) never
    # overwrite each other. The scopes asked for travel with it.
    try:
        scopes = clean_scopes(settings.etsy_scopes)
    except ValueError as exc:
        logger.error("etsy oauth: %s", exc)
        return _redirect(f"{settings.frontend_url}/connect?status=unconfigured")
    await redis.set(
        _state_key(state), json.dumps({"tenant": str(tenant.id), "verifier": verifier, "scopes": scopes}), ex=_STATE_TTL
    )

    try:
        url = build_authorize_url(
            authorize_url=settings.etsy_oauth_authorize_url,
            client_id=settings.etsy_client_id,
            redirect_uri=settings.etsy_redirect_uri,
            scopes=settings.etsy_scopes,
            state=state,
            verifier=verifier,
        )
    except ValueError as exc:  # ETSY_SCOPES names a scope Etsy does not know
        logger.error("etsy oauth: %s", exc)
        await redis.delete(_state_key(state))
        return _redirect(f"{settings.frontend_url}/connect?status=unconfigured")
    return RedirectResponse(url, status_code=302)


def _refused(back: str, reason: str | None) -> RedirectResponse:
    """Back to the Shops page with Etsy's own reason, shown to the seller."""
    query = "status=etsy_error" + (f"&reason={quote(reason)}" if reason else "")
    return _redirect(f"{back}?{query}")


@router.get("/callback")
async def callback(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
    service: ConnectionService = Depends(get_connection_service),
    client_factory: Callable[[], httpx.AsyncClient] = Depends(get_token_http_factory),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> RedirectResponse:
    """Handle Etsy's redirect back: validate state, exchange code, persist tokens."""
    settings = get_settings()
    back = f"{settings.frontend_url}/connect"

    if error:
        # Etsy sent the browser back with its reason (invalid_scope, access_denied...).
        # Only Etsy's error and description are logged: never a code or verifier.
        reason = etsy_reason(error, error_description)
        logger.warning("etsy oauth: authorization refused: %s", reason)
        if state:
            await redis.delete(_state_key(state))
        if error == "access_denied":
            return _redirect(f"{back}?status=denied")
        return _refused(back, reason)
    if not state or not code:
        return _redirect(f"{back}?status=denied")

    stored = await redis.get(_state_key(state))
    if stored is None:
        return _redirect(f"{back}?status=expired")
    await redis.delete(_state_key(state))

    raw = stored.decode() if isinstance(stored, (bytes, bytearray)) else str(stored)
    if raw.startswith("{"):
        pending = json.loads(raw)
        tenant_id, verifier = uuid.UUID(pending["tenant"]), pending["verifier"]
        asked = list(pending.get("scopes") or settings.etsy_scopes.split())
    else:  # started before this version: "tenant:verifier"
        tenant_id_str, _, verifier = raw.partition(":")
        tenant_id, asked = uuid.UUID(tenant_id_str), settings.etsy_scopes.split()

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
        connection = await service.save_from_tokens(
            # What Etsy says it granted, else what was asked for (Etsy grants all or none).
            session, tenant_id, tokens, tokens.scopes or asked
        )
    except OAuthError as exc:
        # Etsy's error and description only (never tokens, the code or the verifier).
        logger.warning("etsy oauth: token exchange refused: %s", exc.reason)
        return _refused(back, exc.reason)
    except ShopTaken:
        return _redirect(f"{back}?status=taken")
    except ShopLimitReached as exc:
        return _redirect(f"{back}?status=limit_{exc.scope}")

    # Fetch the shop's name and listings straight away, through the queue.
    await enqueuer.enqueue("sync_shop_listings", str(connection.id))
    return _redirect(f"{back}?status=connected&shop={connection.id}")


@router.get("/status", response_model=schemas.ConnectionOut)
async def status(
    tenant: Tenant = Depends(active_tenant),
    session: AsyncSession = Depends(get_session),
) -> schemas.ConnectionOut:
    """Whether any shop is connected; the first shop's details. See /api/shops for all."""
    shops = await active_shops(session, tenant.id)
    if not shops:
        return schemas.ConnectionOut(connected=False)
    connection = shops[0]
    return schemas.ConnectionOut(
        connected=True,
        status=connection.status.value,
        etsy_user_id=connection.etsy_user_id,
        shop_name=connection.display_name or connection.shop_name,
        scopes=list(connection.scopes or []),
        connected_at=connection.connected_at,
        expires_at=connection.token_expires_at,
    )
