"""Etsy OAuth 2.0 (PKCE) endpoints.

Flow: ``/start`` builds an authorization URL (state + PKCE) and redirects the
browser to Etsy; Etsy redirects back to ``/callback`` with a code; we exchange it
for tokens and persist an encrypted :class:`EtsyConnection`. Tokens never reach
the frontend.

**Who may finish a connection (the binding cookie).** The callback is the redirect URI
registered with Etsy, ``https://api.listyro.com/api/auth/etsy/callback``: another host than
the app (``listyro.com``). The session cookie is host-only for ``listyro.com``
(api/accounts.py::_set_session_cookie), so the browser never sends it to the callback; the
check added in 28f4a66 required it and refused every real connection as "wrong_account"
(production, 2026-10-23 to 2026-10-29). Now ``/start`` (which needs a signed-in, active account)
sets a dedicated cookie, ``listyro_oauth``: a random secret whose hash is kept with the state
record, for ``Domain`` = the common parent of the app's and the callback's hosts
(``listyro.com``, so both get it), ``Path=/api/auth/etsy``, ``SameSite=Lax`` (sent on Etsy's
top-level redirect back), ``Secure`` (as the session cookie), ``HttpOnly``, 15 minutes. The
callback requires the state to exist and be unused, this cookie to match that state, and the
starting account to be active; it does not need the session cookie, and it clears the binding
cookie.

Why this keeps 28f4a66's protection: someone who starts a connection in their own account and
sends Etsy's consent link to a seller gets the binding cookie in *their* browser only. It is
HttpOnly, random and never in a URL, and nobody else can set a cookie for ``listyro.com`` in the
seller's browser, so the seller's browser arrives at the callback without it (or with the
seller's own, which does not match the sender's state) and the code is never exchanged: the
seller's shop cannot land in the sender's account (docs/security-incident.md).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import secrets
import uuid
from collections.abc import Callable
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, Depends, Request
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
    get_session_store,
    get_token_http_factory,
)
from app.core.config import get_settings
from app.core.sessions import SESSION_COOKIE, SessionStore
from app.db.models import Tenant, TenantStatus
from app.etsy import guard
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

_STATE_TTL = 900  # seconds a pending authorization may stay unclaimed (the binding cookie lives as long)
BINDING_COOKIE = "listyro_oauth"
BINDING_PATH = "/api/auth/etsy"

#: Why a callback was refused: the Shops page says each in its own words (connect/page.tsx).
NO_BINDING = "no_binding"  # no binding cookie: too long ago, or started in another browser
MISMATCH = "state_mismatch"  # a binding cookie, but for another connection
REUSED = "reused"  # this state was already used
EXPIRED = "expired"  # no such state (too old)
UNAVAILABLE = "unavailable"  # the starting account is suspended or being deleted
OTHER_ACCOUNT = "other_account"  # this browser is signed in to another account than the one that started


def _state_key(state: str) -> str:
    return f"oauth:state:{state}"


def _used_key(state: str) -> str:
    return f"oauth:used:{state}"


def _pending_key(digest: str) -> str:
    """The states still pending for one binding secret: two tabs (a reconnect and a second
    shop) share the browser's secret, and its cookie is cleared only when none is left."""
    return f"oauth:bind:{digest}"


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def binding_domain(frontend_url: str, redirect_uri: str) -> str | None:
    """The cookie Domain that reaches both the app and the callback: their common parent
    (``listyro.com`` for ``listyro.com`` and ``api.listyro.com``); None (host-only) when they are
    the same host (cookies ignore the port: ``localhost:3000`` and ``localhost:8000`` share
    them), an IP address, or share no parent of two labels or more."""
    a = (urlparse(frontend_url).hostname or "").lower()
    b = (urlparse(redirect_uri).hostname or "").lower()
    if not a or not b or a == b:
        return None
    for host in (a, b):
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
    left, right = a.split("."), b.split(".")
    common: list[str] = []
    while left and right and left[-1] == right[-1]:
        common.insert(0, left.pop())
        right.pop()
    return ".".join(common) if len(common) >= 2 else None


def _cookie_options() -> dict:
    settings = get_settings()
    return {"domain": binding_domain(settings.frontend_url, settings.etsy_redirect_uri), "path": BINDING_PATH}


def _clear_binding(response: RedirectResponse) -> RedirectResponse:
    response.delete_cookie(BINDING_COOKIE, httponly=True, secure=get_settings().session_cookie_secure,
                           samesite="lax", **_cookie_options())
    return response


async def _done_with(redis: Redis, request: Request, state: str | None, response: RedirectResponse) -> RedirectResponse:
    """After a callback: this state no longer needs the browser's binding cookie; the cookie is
    cleared unless another connection started in this browser is still waiting for it."""
    cookie = request.cookies.get(BINDING_COOKIE)
    if not cookie:
        return response
    key = _pending_key(_digest(cookie))
    if state:
        await redis.srem(key, state)
    if not await redis.scard(key):
        await redis.delete(key)
        return _clear_binding(response)
    return response


def mask_email(email: str) -> str:
    """``talha@example.com`` -> ``t***@example.com``: says which account without the address."""
    name, _, domain = (email or "").partition("@")
    return f"{name[:1]}***@{domain}" if domain else "another account"


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=302)


@router.get("/start")
async def start(
    request: Request,
    tenant: Tenant = Depends(active_tenant),
    redis: Redis = Depends(get_redis),
) -> RedirectResponse:
    """Begin the OAuth flow: store PKCE state, redirect the browser to Etsy."""
    settings = get_settings()
    if guard.blocked():
        # Development switch: the browser is not sent to Etsy to authorise a shop.
        return _refused(f"{settings.frontend_url}/connect", guard.MESSAGE)
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
    # One secret per browser: a second connection started meanwhile (another tab) shares it.
    binding = request.cookies.get(BINDING_COOKIE) or secrets.token_urlsafe(32)
    await redis.sadd(_pending_key(_digest(binding)), state)
    await redis.expire(_pending_key(_digest(binding)), _STATE_TTL)
    await redis.set(
        _state_key(state),
        json.dumps({"tenant": str(tenant.id), "verifier": verifier, "scopes": scopes, "binding": _digest(binding)}),
        ex=_STATE_TTL,
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
    response = RedirectResponse(url, status_code=302)
    # This browser, and only this one, may finish this connection (see the module docstring).
    response.set_cookie(BINDING_COOKIE, binding, max_age=_STATE_TTL, httponly=True,
                        secure=settings.session_cookie_secure, samesite="lax", **_cookie_options())
    return response


def _refused(back: str, reason: str | None) -> RedirectResponse:
    """Back to the Shops page with Etsy's own reason, shown to the seller."""
    query = "status=etsy_error" + (f"&reason={quote(reason)}" if reason else "")
    return _redirect(f"{back}?{query}")


async def _finisher(
    request: Request, session: AsyncSession, sessions: SessionStore, started_by: uuid.UUID, binding: str | None,
) -> tuple[str, str | None] | None:
    """Why this browser may not finish a connection (the reason, and for ``other_account`` the
    starting account's masked email), or None.

    The tokens go to the account that started the flow (kept with the state). The browser
    finishing it must hold the binding cookie set for this state when it started (so a consent
    link sent to someone else cannot hand their shop to the sender; quality pass 2026-10, review
    R1), and the account must still be usable. The session cookie is not needed: it is host-only
    for the app and never reaches the callback's host. When it is there and names another
    account (this browser was signed in to someone else meanwhile), that is said, not guessed.
    """
    cookie = request.cookies.get(BINDING_COOKIE)
    if not cookie:
        return NO_BINDING, None
    if binding is None or not secrets.compare_digest(_digest(cookie), binding):
        return MISMATCH, None
    tenant = await session.get(Tenant, started_by)
    if tenant is None or tenant.status is not TenantStatus.active or tenant.deletion_requested_at is not None:
        return UNAVAILABLE, None
    record = await sessions.read(request.cookies.get(SESSION_COOKIE, ""))
    if record is not None and record.tenant_id != started_by:
        return OTHER_ACCOUNT, mask_email(tenant.email)
    return None


@router.get("/callback")
async def callback(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    session: AsyncSession = Depends(get_session, scope="function"),
    redis: Redis = Depends(get_redis),
    service: ConnectionService = Depends(get_connection_service),
    client_factory: Callable[[], httpx.AsyncClient] = Depends(get_token_http_factory),
    enqueuer: Enqueuer = Depends(get_enqueuer),
    sessions: SessionStore = Depends(get_session_store),
) -> RedirectResponse:
    """Handle Etsy's redirect back: validate state, check that this browser is signed in to
    the account that started it, exchange code, persist tokens."""
    settings = get_settings()
    back = f"{settings.frontend_url}/connect"
    if guard.blocked():
        if state:
            await redis.delete(_state_key(state))
        return _refused(back, guard.MESSAGE)

    if error:
        # Etsy sent the browser back with its reason (invalid_scope, access_denied...).
        # Only Etsy's error and description are logged: never a code or verifier.
        reason = etsy_reason(error, error_description)
        logger.warning("etsy oauth: authorization refused: %s", reason)
        if state:
            await redis.delete(_state_key(state))
        if error == "access_denied":
            return await _done_with(redis, request, state, _redirect(f"{back}?status=denied"))
        return await _done_with(redis, request, state, _refused(back, reason))
    if not state or not code:
        return await _done_with(redis, request, state, _redirect(f"{back}?status=denied"))

    stored = await redis.get(_state_key(state))
    if stored is None:
        reason = REUSED if await redis.get(_used_key(state)) else EXPIRED
        logger.warning("etsy oauth: callback refused (%s)", reason)
        return await _done_with(redis, request, state, _redirect(f"{back}?status={reason}"))
    # Used once: gone, and remembered as used for as long as it could have lived.
    await redis.delete(_state_key(state))
    await redis.set(_used_key(state), "1", ex=_STATE_TTL)

    raw = stored.decode() if isinstance(stored, (bytes, bytearray)) else str(stored)
    binding = None
    if raw.startswith("{"):
        pending = json.loads(raw)
        tenant_id, verifier = uuid.UUID(pending["tenant"]), pending["verifier"]
        asked = list(pending.get("scopes") or settings.etsy_scopes.split())
        binding = pending.get("binding")
    else:  # started before this version: "tenant:verifier" (no binding: refused, start again)
        tenant_id_str, _, verifier = raw.partition(":")
        tenant_id, asked = uuid.UUID(tenant_id_str), settings.etsy_scopes.split()

    # Before the code is exchanged: the code is never turned into tokens for another account.
    refused = await _finisher(request, session, sessions, tenant_id, binding)
    if refused is not None:
        reason, who = refused
        logger.warning("etsy oauth: callback refused (%s)", reason)
        where = f"{back}?status={reason}" + (f"&who={quote(who)}" if who else "")
        return await _done_with(redis, request, state, _redirect(where))

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
        return await _done_with(redis, request, state, _refused(back, exc.reason))
    except ShopTaken:
        return await _done_with(redis, request, state, _redirect(f"{back}?status=taken"))
    except ShopLimitReached as exc:
        # "account" / "app" (a ceiling), or "paused": new connections are paused
        # by the operator and this was not one of the account's connected shops.
        return await _done_with(redis, request, state, _redirect(f"{back}?status=limit_{exc.scope}"))

    # Fetch the shop's name, listings and per-state counts straight away, through the
    # queue, and say so: until that read lands the shop's counts are "reading…", never 0.
    from app.api.shop import SUMMARY_SYNC_EVERY_SECONDS, mark_reading

    await enqueuer.enqueue("sync_shop_listings", str(connection.id))
    # The per-state counts on their own as well (one request per state): they arrive in
    # seconds even when the full read of a large shop takes longer or is cut short.
    await enqueuer.enqueue("sync_shop_counts", str(connection.id))
    # Every reference listing is used in the shop just connected, with its main shop's
    # shipping, return and processing settings (matched, or copied there): no question.
    from app.pipeline.profile_shops import use_all_in_shop

    for profile_id in await use_all_in_shop(session, connection.tenant_id, connection.id):
        await enqueuer.enqueue("link_profile", str(profile_id))
    # A group of all the seller's shops ("Make a group of all my shops") takes the new shop too (B7).
    from app.api.send import join_all_shops

    await join_all_shops(session, connection)
    marker, now = mark_reading(connection.id)
    await redis.set(marker, now, ex=SUMMARY_SYNC_EVERY_SECONDS)
    return await _done_with(redis, request, state, _redirect(f"{back}?status=connected&shop={connection.id}"))


@router.get("/status", response_model=schemas.ConnectionOut)
async def status(
    tenant: Tenant = Depends(active_tenant),
    session: AsyncSession = Depends(get_session, scope="function"),
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
