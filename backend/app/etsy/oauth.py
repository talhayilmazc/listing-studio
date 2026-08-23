"""Etsy OAuth 2.0 with PKCE.

Etsy uses the authorization-code grant with PKCE (S256). The token endpoint
takes the ``client_id`` (keystring) and ``code_verifier`` -- it does NOT take the
shared secret and does NOT need the ``x-api-key`` header (that header is only for
API v3 calls; see ``app.etsy.client``).

Token exchange / refresh are the one architectural exception allowed to call
Etsy directly (not through the job queue). The ``httpx.AsyncClient`` is injected
so tests can mock the token endpoint without any network access.

Access/refresh tokens are secrets: they are returned from this module only to be
encrypted immediately by the caller, and are never logged.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx


class OAuthError(Exception):
    """Token exchange or refresh failed."""


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int  # seconds until the access token expires


# --- PKCE ------------------------------------------------------------------
def generate_code_verifier() -> str:
    """A high-entropy PKCE code verifier (43-128 url-safe chars)."""
    return secrets.token_urlsafe(64)


def code_challenge(verifier: str) -> str:
    """S256 challenge for ``verifier`` (base64url, no padding)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def build_authorize_url(
    *,
    authorize_url: str,
    client_id: str,
    redirect_uri: str,
    scopes: str,
    state: str,
    verifier: str,
) -> str:
    """Build the Etsy authorization URL the browser is redirected to."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    return f"{authorize_url}?{urlencode(params)}"


# --- Token exchange / refresh ----------------------------------------------
def _parse_token(data: dict) -> TokenResponse:
    try:
        return TokenResponse(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_in=int(data.get("expires_in", 3600)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        # Never include the body/tokens in the error message.
        raise OAuthError("token response missing expected fields") from exc


async def exchange_code(
    client: httpx.AsyncClient,
    *,
    token_url: str,
    client_id: str,
    redirect_uri: str,
    code: str,
    verifier: str,
) -> TokenResponse:
    """Exchange an authorization code for tokens (PKCE, no client secret)."""
    resp = await client.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code": code,
            "code_verifier": verifier,
        },
    )
    if resp.status_code != 200:
        raise OAuthError(f"token exchange failed (status {resp.status_code})")
    return _parse_token(resp.json())


async def refresh_tokens(
    client: httpx.AsyncClient,
    *,
    token_url: str,
    client_id: str,
    refresh_token: str,
) -> TokenResponse:
    """Exchange a refresh token for a fresh access (and refresh) token."""
    resp = await client.post(
        token_url,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
        },
    )
    if resp.status_code != 200:
        raise OAuthError(f"token refresh failed (status {resp.status_code})")
    return _parse_token(resp.json())
