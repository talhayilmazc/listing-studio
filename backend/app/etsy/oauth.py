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
from urllib.parse import quote, urlencode

import httpx


class OAuthError(Exception):
    """Token exchange or refresh failed.

    ``reason`` is Etsy's own ``error`` and ``error_description`` (never a token,
    code or verifier), safe to log and to show the seller.
    """

    def __init__(self, message: str, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason


#: Every scope Etsy's OAuth server knows (Etsy Open API v3, "Scopes"). A scope
#: outside this list, or one asked twice, makes Etsy refuse the authorization.
KNOWN_SCOPES = frozenset({
    "address_r", "address_w", "billing_r", "cart_r", "cart_w", "email_r", "favorites_r", "favorites_w",
    "feedback_r", "listings_d", "listings_r", "listings_w", "profile_r", "profile_w", "recommend_r",
    "recommend_w", "shops_r", "shops_w", "transactions_r", "transactions_w",
})


def clean_scopes(scopes: str) -> list[str]:
    """The scopes as Etsy must receive them: known, each once, in order.
    ValueError (naming them) for an unknown one: a typo in ETSY_SCOPES would
    otherwise send every seller to an Etsy error page."""
    out: list[str] = []
    for scope in scopes.split():
        if scope not in KNOWN_SCOPES:
            raise ValueError(f"unknown Etsy scope in ETSY_SCOPES: {scope!r}")
        if scope not in out:
            out.append(scope)
    if not out:
        raise ValueError("ETSY_SCOPES is empty")
    return out


def etsy_reason(error: str | None, description: str | None) -> str | None:
    """Etsy's own reason, as one short line (printable, at most 200 characters)."""
    parts = [p.strip() for p in (error, description) if p and p.strip()]
    if not parts:
        return None
    text = ": ".join(dict.fromkeys(parts))
    return "".join(ch for ch in text if ch.isprintable())[:200]


@dataclass(frozen=True)
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int  # seconds until the access token expires
    #: The scopes granted, when the response names them ("scope", space-separated).
    scopes: list[str] | None = None


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
    """Build the Etsy authorization URL the browser is redirected to.

    Scopes are checked and sent once each, space-separated and encoded as %20
    (Etsy's documented form; "+" is form encoding). ``redirect_uri`` goes exactly
    as configured: it must equal a callback URL registered for the app.
    """
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(clean_scopes(scopes)),
        "state": state,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    return f"{authorize_url}?{urlencode(params, quote_via=quote)}"


# --- Token exchange / refresh ----------------------------------------------
def _error_reason(resp: httpx.Response) -> str | None:
    """Etsy's ``error``/``error_description`` from a refused token request. Only
    those two fields are read: nothing else of the body (which echoes nothing
    secret, but is not needed) is kept."""
    try:
        body = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}"
    if not isinstance(body, dict):
        return f"HTTP {resp.status_code}"
    return etsy_reason(str(body.get("error") or ""), str(body.get("error_description") or "")) or f"HTTP {resp.status_code}"


def _parse_token(data: dict) -> TokenResponse:
    try:
        return TokenResponse(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_in=int(data.get("expires_in", 3600)),
            scopes=str(data["scope"]).split() if data.get("scope") else None,
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
        raise OAuthError(f"token exchange failed (status {resp.status_code})", _error_reason(resp))
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
        raise OAuthError(f"token refresh failed (status {resp.status_code})", _error_reason(resp))
    return _parse_token(resp.json())
