"""OAuth PKCE, token exchange/refresh, encrypted persistence, and the router.

The Etsy token endpoint is mocked with ``httpx.MockTransport`` — no network,
no real credentials.
"""

import base64
import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fakeredis import FakeAsyncRedis
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.core.crypto import TokenCipher
from app.db.base import Base
from app.db.models import ConnectionStatus, EtsyConnection, Tenant
from app.etsy import oauth
from app.etsy.connection import ConnectionService
from app.core.sessions import SessionStore
from app.main import create_app
from tests.auth_support import BROWSER_HEADERS, authenticate, make_tenant, open_session


# --- PKCE + authorize URL ---------------------------------------------------
def test_code_challenge_is_s256_of_verifier() -> None:
    verifier = oauth.generate_code_verifier()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert oauth.code_challenge(verifier) == expected
    assert "=" not in oauth.code_challenge(verifier)


def test_state_and_verifier_are_random() -> None:
    assert oauth.generate_state() != oauth.generate_state()
    assert len(oauth.generate_code_verifier()) >= 43


def test_authorize_url_has_pkce_params() -> None:
    url = oauth.build_authorize_url(
        authorize_url="https://www.etsy.com/oauth/connect",
        client_id="keystring",
        redirect_uri="http://localhost:8000/api/auth/etsy/callback",
        scopes="listings_r listings_w shops_r shops_w",
        state="the-state",
        verifier="the-verifier",
    )
    q = parse_qs(urlparse(url).query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["keystring"]
    assert q["state"] == ["the-state"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] == [oauth.code_challenge("the-verifier")]
    assert q["scope"] == ["listings_r listings_w shops_r shops_w"]


# --- token exchange / refresh ----------------------------------------------
def _token_client(payload: dict, status: int = 200) -> httpx.AsyncClient:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_exchange_code_parses_tokens() -> None:
    async with _token_client(
        {"access_token": "12345.acc", "refresh_token": "12345.ref", "expires_in": 3600}
    ) as client:
        tokens = await oauth.exchange_code(
            client,
            token_url="https://api.etsy.com/v3/public/oauth/token",
            client_id="keystring",
            redirect_uri="http://localhost:8000/api/auth/etsy/callback",
            code="the-code",
            verifier="the-verifier",
        )
    assert tokens.access_token == "12345.acc"
    assert tokens.refresh_token == "12345.ref"
    assert tokens.expires_in == 3600


async def test_exchange_code_raises_on_error() -> None:
    async with _token_client({"error": "invalid_grant"}, status=400) as client:
        with pytest.raises(oauth.OAuthError):
            await oauth.exchange_code(
                client,
                token_url="https://api.etsy.com/v3/public/oauth/token",
                client_id="keystring",
                redirect_uri="r",
                code="c",
                verifier="v",
            )


# --- ConnectionService ------------------------------------------------------
def _cipher() -> TokenCipher:
    return TokenCipher(Fernet.generate_key())


async def _seed_tenant(sm: async_sessionmaker) -> uuid.UUID:
    async with sm() as s:
        tenant = Tenant(email=f"{uuid.uuid4()}@example.com", password_hash="x")
        s.add(tenant)
        await s.commit()
        return tenant.id


async def test_save_encrypts_tokens(async_sm: async_sessionmaker) -> None:
    cipher = _cipher()
    service = ConnectionService(cipher, client_id="keystring", token_url="https://t")
    tenant_id = await _seed_tenant(async_sm)

    async with async_sm() as s:
        conn = await service.save_from_tokens(
            s,
            tenant_id,
            oauth.TokenResponse(access_token="9911.acc", refresh_token="9911.ref", expires_in=3600),
            ["listings_r", "shops_r"],
        )
        conn_id = conn.id

    async with async_sm() as s:
        row = await s.get(EtsyConnection, conn_id)
        assert row.status is ConnectionStatus.active
        assert row.etsy_user_id == 9911
        assert row.scopes == ["listings_r", "shops_r"]
        # Ciphertext must not contain the plaintext token, and must decrypt back.
        assert b"9911.acc" not in row.access_token_enc
        assert cipher.decrypt(row.access_token_enc) == "9911.acc"
        assert cipher.decrypt(row.refresh_token_enc) == "9911.ref"
        assert row.token_expires_at is not None


async def test_get_valid_token_no_refresh_when_fresh(async_sm: async_sessionmaker) -> None:
    cipher = _cipher()

    def _boom() -> httpx.AsyncClient:  # must not be called
        raise AssertionError("refresh should not happen for a fresh token")

    service = ConnectionService(
        cipher, client_id="keystring", token_url="https://t", client_factory=_boom
    )
    tenant_id = await _seed_tenant(async_sm)
    async with async_sm() as s:
        conn = await service.save_from_tokens(
            s,
            tenant_id,
            oauth.TokenResponse(access_token="1.acc", refresh_token="1.ref", expires_in=3600),
            ["listings_r"],
        )
        token = await service.get_valid_access_token(s, conn)
    assert token == "1.acc"


async def test_get_valid_token_refreshes_near_expiry(async_sm: async_sessionmaker) -> None:
    cipher = _cipher()
    rotated = {"access_token": "1.acc2", "refresh_token": "1.ref2", "expires_in": 3600}
    service = ConnectionService(
        cipher,
        client_id="keystring",
        token_url="https://t",
        client_factory=lambda: _token_client(rotated),
    )
    tenant_id = await _seed_tenant(async_sm)
    async with async_sm() as s:
        conn = await service.save_from_tokens(
            s,
            tenant_id,
            oauth.TokenResponse(access_token="1.acc", refresh_token="1.ref", expires_in=3600),
            ["listings_r"],
        )
        # Force it to look near-expiry.
        conn.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=10)
        await s.commit()
        token = await service.get_valid_access_token(s, conn)
        assert token == "1.acc2"
        await s.refresh(conn)
        assert cipher.decrypt(conn.access_token_enc) == "1.acc2"
        assert cipher.decrypt(conn.refresh_token_enc) == "1.ref2"


async def test_disconnect_revokes_and_clears(async_sm: async_sessionmaker) -> None:
    cipher = _cipher()
    service = ConnectionService(cipher, client_id="k", token_url="https://t")
    tenant_id = await _seed_tenant(async_sm)
    async with async_sm() as s:
        conn = await service.save_from_tokens(
            s,
            tenant_id,
            oauth.TokenResponse(access_token="7.acc", refresh_token="7.ref", expires_in=3600),
            ["listings_r"],
        )
        await service.disconnect(s, conn)
        await s.refresh(conn)
        assert conn.status is ConnectionStatus.revoked
        assert conn.access_token_enc is None
        assert conn.refresh_token_enc is None
        from app.etsy.shops import active_shops

        assert await active_shops(s, tenant_id) == []


# --- Router end-to-end ------------------------------------------------------
@pytest_asyncio.fixture()
async def auth_client(test_settings) -> AsyncIterator[httpx.AsyncClient]:
    # Configure Etsy credentials on the isolated settings (installed by the
    # autouse fixture) instead of touching the ambient environment / .env.
    test_settings.etsy_client_id = "test-keystring"
    test_settings.etsy_client_secret = "test-secret"

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    fake_redis = FakeAsyncRedis()
    cipher = _cipher()
    token_payload = {
        "access_token": "555.acc",
        "refresh_token": "555.ref",
        "expires_in": 3600,
    }

    async def _session():
        async with sm() as s:
            yield s

    app = create_app()
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_redis] = lambda: fake_redis
    app.dependency_overrides[deps.get_connection_service] = lambda: ConnectionService(
        cipher, client_id="test-keystring", token_url="https://t"
    )
    app.dependency_overrides[deps.get_token_http_factory] = lambda: (
        lambda: _token_client(token_payload)
    )

    app.dependency_overrides[deps.get_session_store] = lambda: SessionStore(fake_redis)

    class _Queue:
        calls: list = []

        async def enqueue(self, function: str, *args, **kwargs) -> None:
            self.calls.append((function, args))

    # A new shop syncs straight away; never through the real queue in a test.
    app.dependency_overrides[deps.get_enqueuer] = lambda: _Queue()
    tenant_id = await make_tenant(sm, "owner@example.com")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=False,
        headers=BROWSER_HEADERS,
    ) as ac:
        ac.sm = sm  # type: ignore[attr-defined]
        ac.tenant_id = tenant_id  # type: ignore[attr-defined]
        authenticate(ac, await open_session(fake_redis, tenant_id))
        yield ac

    await engine.dispose()


async def test_full_oauth_flow(auth_client: httpx.AsyncClient) -> None:
    # /start redirects to Etsy with a state we can capture.
    start = await auth_client.get("/api/auth/etsy/start")
    assert start.status_code == 302
    loc = start.headers["location"]
    assert loc.startswith("https://www.etsy.com/oauth/connect")
    state = parse_qs(urlparse(loc).query)["state"][0]

    # /callback exchanges the code (mock token endpoint) and persists.
    cb = await auth_client.get(f"/api/auth/etsy/callback?state={state}&code=the-code")
    assert cb.status_code == 302
    assert "/connect?status=connected&shop=" in cb.headers["location"]
    # No token leaks in the redirect.
    assert "555." not in cb.headers["location"]

    # /status reports connected, with no tokens in the payload.
    status = await auth_client.get("/api/auth/etsy/status")
    body = status.json()
    assert body["connected"] is True
    assert body["etsy_user_id"] == 555
    assert body["scopes"] == ["listings_r", "listings_w", "shops_r", "shops_w", "transactions_r"]
    assert "555.acc" not in status.text
    assert "access_token" not in body and "refresh_token" not in body

    # The stored tokens are encrypted, not plaintext.
    async with auth_client.sm() as s:  # type: ignore[attr-defined]
        row = (await s.execute(select(EtsyConnection))).scalars().first()
        assert b"555.acc" not in row.access_token_enc


async def test_callback_rejects_unknown_state(auth_client: httpx.AsyncClient) -> None:
    cb = await auth_client.get("/api/auth/etsy/callback?state=bogus&code=x")
    assert cb.status_code == 302
    assert cb.headers["location"].endswith("status=expired")


async def test_status_without_connection(auth_client: httpx.AsyncClient) -> None:
    res = await auth_client.get("/api/auth/etsy/status")
    assert res.json() == {
        "connected": False,
        "status": None,
        "etsy_user_id": None,
        "shop_name": None,
        "scopes": [],
        "connected_at": None,
        "expires_at": None,
    }
