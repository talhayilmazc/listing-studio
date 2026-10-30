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
    # The app and the callback on the test client's own host, so its cookie jar keeps the
    # binding cookie as a browser would (production: listyro.com and api.listyro.com, its
    # parent domain; tests/test_oauth.py::test_start_sets_the_binding_cookie_as_production_needs).
    test_settings.frontend_url = "http://test"
    test_settings.etsy_redirect_uri = "http://test/api/auth/etsy/callback"
    test_settings.session_cookie_secure = False

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
        ac.redis = fake_redis  # type: ignore[attr-defined]
        ac.queued = _Queue.calls  # type: ignore[attr-defined]
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


async def test_a_shop_just_connected_is_read_at_once_and_its_counts_say_reading_not_zero(
    auth_client: httpx.AsyncClient,
) -> None:
    """Right after connecting, the shop's listings and counts are queued for reading, and every
    page says "reading…" for its counts at once (never 0), without queueing a second read."""
    start = await auth_client.get("/api/auth/etsy/start")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    del auth_client.queued[:]  # type: ignore[attr-defined]
    cb = await auth_client.get(f"/api/auth/etsy/callback?state={state}&code=the-code")
    shop = parse_qs(urlparse(cb.headers["location"]).query)["shop"][0]
    assert ("sync_shop_listings", (shop,)) in auth_client.queued  # type: ignore[attr-defined]
    assert ("sync_shop_counts", (shop,)) in auth_client.queued  # type: ignore[attr-defined]
    assert await auth_client.redis.get(f"summary-sync:{shop}") is not None  # type: ignore[attr-defined]

    summary = (await auth_client.get(f"/api/shop/summary?shop={shop}")).json()
    assert (summary["shop_counts_known"], summary["syncing"], summary["counts_read_at"]) == (False, True, None)
    overview = (await auth_client.get("/api/shop/overview")).json()
    assert overview["shops"][0]["summary"]["syncing"] is True and overview["total"]["shop_counts_known"] is False
    # The read already queued is the one: the summary queues no second.
    assert [c for c in auth_client.queued] == [  # type: ignore[attr-defined]
        ("sync_shop_listings", (shop,)), ("sync_shop_counts", (shop,))]


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


# --- the reconnect loop (v8 Part B5): every cause, one test each ---------------------------------


def test_scopes_are_sent_space_separated_as_percent_20_once_each_and_known() -> None:
    url = oauth.build_authorize_url(
        authorize_url="https://www.etsy.com/oauth/connect", client_id="k",
        redirect_uri="https://api.example.com/api/auth/etsy/callback",
        scopes="listings_r  shops_r listings_r transactions_r", state="s", verifier="v" * 64,
    )
    assert "scope=listings_r%20shops_r%20transactions_r&" in url  # %20, not "+"; the repeat dropped
    assert parse_qs(urlparse(url).query)["redirect_uri"] == ["https://api.example.com/api/auth/etsy/callback"]
    assert "code_challenge_method=S256" in url
    with pytest.raises(ValueError, match="unknown Etsy scope"):
        oauth.clean_scopes("listings_r transaction_r")  # a typo would send every seller to Etsy's 400 page


async def test_two_authorizations_at_once_never_overwrite_each_other(auth_client: httpx.AsyncClient) -> None:
    """A reconnect in one tab and a second shop in another: each has its own state and verifier."""
    first = parse_qs(urlparse((await auth_client.get("/api/auth/etsy/start")).headers["location"]).query)
    second = parse_qs(urlparse((await auth_client.get("/api/auth/etsy/start")).headers["location"]).query)
    assert first["state"] != second["state"] and first["code_challenge"] != second["code_challenge"]
    for q in (second, first):  # finished in either order
        cb = await auth_client.get(f"/api/auth/etsy/callback?state={q['state'][0]}&code=c")
        assert "status=connected" in cb.headers["location"]


async def test_etsys_refusal_is_logged_and_shown_without_secrets(auth_client: httpx.AsyncClient, caplog) -> None:  # noqa: ANN001
    state = parse_qs(urlparse((await auth_client.get("/api/auth/etsy/start")).headers["location"]).query)["state"][0]
    cb = await auth_client.get(f"/api/auth/etsy/callback?state={state}&error=invalid_scope"
                               "&error_description=The+requested+scope+is+invalid")
    location = cb.headers["location"]
    assert "status=etsy_error" in location and "invalid_scope" in location
    assert any("invalid_scope: The requested scope is invalid" in r.getMessage() for r in caplog.records)


async def test_a_refused_token_exchange_says_etsys_reason_and_logs_no_code(auth_client: httpx.AsyncClient, caplog) -> None:  # noqa: ANN001
    from app.main import create_app  # noqa: F401  (the app is the fixture's)

    transport = auth_client._transport  # type: ignore[attr-defined]
    app = transport.app
    app.dependency_overrides[deps.get_token_http_factory] = lambda: (
        lambda: _token_client({"error": "invalid_grant", "error_description": "code_verifier is invalid"}, status=400)
    )
    state = parse_qs(urlparse((await auth_client.get("/api/auth/etsy/start")).headers["location"]).query)["state"][0]
    cb = await auth_client.get(f"/api/auth/etsy/callback?state={state}&code=SECRET-CODE")
    assert "status=etsy_error" in cb.headers["location"] and "invalid_grant" in cb.headers["location"]
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "invalid_grant: code_verifier is invalid" in logged and "SECRET-CODE" not in logged


async def test_after_a_reconnect_the_shop_has_every_scope_and_is_no_longer_asked(auth_client: httpx.AsyncClient) -> None:
    from app.api.shops import missing_scopes

    async with auth_client.sm() as s:  # type: ignore[attr-defined]
        # Connected before transactions_r was added.
        s.add(EtsyConnection(tenant_id=auth_client.tenant_id, etsy_user_id=555, shop_id=555,  # type: ignore[attr-defined]
                             status=ConnectionStatus.active, scopes=["listings_r", "listings_w", "shops_r", "shops_w"]))
        await s.commit()
        assert missing_scopes((await s.execute(select(EtsyConnection))).scalar_one()) == ["transactions_r"]
    state = parse_qs(urlparse((await auth_client.get("/api/auth/etsy/start")).headers["location"]).query)["state"][0]
    assert "status=connected" in (await auth_client.get(f"/api/auth/etsy/callback?state={state}&code=c")).headers["location"]
    async with auth_client.sm() as s:  # type: ignore[attr-defined]
        shop = (await s.execute(select(EtsyConnection))).scalar_one()  # the same shop, not a second row
        assert missing_scopes(shop) == []


def test_the_token_responses_own_scope_list_is_kept() -> None:
    tokens = oauth._parse_token({"access_token": "a", "refresh_token": "r", "scope": "listings_r shops_r"})
    assert tokens.scopes == ["listings_r", "shops_r"]


async def _started(auth_client: httpx.AsyncClient) -> str:
    start = await auth_client.get("/api/auth/etsy/start")
    return parse_qs(urlparse(start.headers["location"]).query)["state"][0]


async def test_the_callback_on_another_host_connects_without_the_session_cookie(auth_client: httpx.AsyncClient) -> None:
    """Production, 2026-10-23 to 2026-10-29: Etsy sends the seller back to api.listyro.com, where the
    host-only session cookie of listyro.com never arrives; every connection was refused as
    "wrong_account". The binding cookie set by /start is what the callback needs."""
    from app.api.auth import BINDING_COOKIE
    from tests.auth_support import sign_out

    state = await _started(auth_client)
    assert auth_client.cookies.get(BINDING_COOKIE)
    sign_out(auth_client)  # as at api.listyro.com: no session cookie, only the binding cookie
    cb = await auth_client.get(f"/api/auth/etsy/callback?state={state}&code=the-code")
    assert "status=connected" in cb.headers["location"], cb.headers["location"]
    assert f'{BINDING_COOKIE}=""' in cb.headers.get("set-cookie", "")  # cleared after use
    async with auth_client.sm() as s:  # type: ignore[attr-defined]
        shop = (await s.execute(select(EtsyConnection))).scalar_one()
    assert shop.tenant_id == auth_client.tenant_id  # type: ignore[attr-defined]
    # A state is used once: the same link again is refused as reused, not connected twice.
    again = await auth_client.get(f"/api/auth/etsy/callback?state={state}&code=the-code")
    assert again.headers["location"].endswith("/connect?status=reused")


async def test_a_consent_link_sent_to_someone_else_cannot_attach_their_shop(auth_client: httpx.AsyncClient) -> None:
    """Quality pass 2026-10, review R1, kept: account A starts a connection and sends Etsy's consent
    link to a seller signed in to account B. B's browser has no binding cookie for A's state (it
    is HttpOnly, random, never in a URL), so the code is never exchanged; with B's own binding
    cookie (B started a connection too) it does not match A's state."""
    from app.api.auth import BINDING_COOKIE

    state_a = await _started(auth_client)  # A's browser holds A's binding cookie
    other = await make_tenant(auth_client.sm, "seller-b@example.com")  # type: ignore[attr-defined]
    async with httpx.AsyncClient(transport=auth_client._transport, base_url="http://test",  # type: ignore[attr-defined]
                                 follow_redirects=False, headers=BROWSER_HEADERS) as b:
        authenticate(b, await open_session(auth_client.redis, other))  # type: ignore[attr-defined]
        cb = await b.get(f"/api/auth/etsy/callback?state={state_a}&code=the-code")
        assert cb.headers["location"].endswith("/connect?status=no_binding")
        state_a2 = await _started(auth_client)
        await b.get("/api/auth/etsy/start")  # B's own binding cookie, for B's own state
        assert b.cookies.get(BINDING_COOKIE)
        cb = await b.get(f"/api/auth/etsy/callback?state={state_a2}&code=the-code")
        assert cb.headers["location"].endswith("/connect?status=state_mismatch")
    async with auth_client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.execute(select(EtsyConnection))).scalars().first() is None


async def test_signed_in_to_another_account_meanwhile_is_said_with_the_starter_masked(auth_client: httpx.AsyncClient) -> None:
    other = await make_tenant(auth_client.sm, "other@example.com")  # type: ignore[attr-defined]
    state = await _started(auth_client)  # started by owner@example.com
    authenticate(auth_client, await open_session(auth_client.redis, other))  # type: ignore[attr-defined]
    cb = await auth_client.get(f"/api/auth/etsy/callback?state={state}&code=the-code")
    assert cb.headers["location"].endswith("/connect?status=other_account&who=o%2A%2A%2A%40example.com")
    async with auth_client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.execute(select(EtsyConnection))).scalars().first() is None


async def test_an_expired_state_is_refused_clearly(auth_client: httpx.AsyncClient) -> None:
    from app.api.auth import _state_key

    state = await _started(auth_client)
    await auth_client.redis.delete(_state_key(state))  # type: ignore[attr-defined]  # 15 minutes passed
    cb = await auth_client.get(f"/api/auth/etsy/callback?state={state}&code=the-code")
    assert cb.headers["location"].endswith("/connect?status=expired")


def test_the_binding_cookie_reaches_the_app_and_the_callback_host() -> None:
    from app.api.auth import binding_domain

    assert binding_domain("https://listyro.com", "https://api.listyro.com/api/auth/etsy/callback") == "listyro.com"
    assert binding_domain("http://listyro.test:3000", "http://api.listyro.test:8000/x") == "listyro.test"
    assert binding_domain("http://localhost:3000", "http://localhost:8000/x") is None  # one host: host-only
    assert binding_domain("http://127.0.0.1:3000", "http://127.0.0.2:8000/x") is None
    assert binding_domain("https://listyro.com", "https://example.com/x") is None


async def test_start_sets_the_binding_cookie_as_production_needs(auth_client: httpx.AsyncClient, test_settings) -> None:
    test_settings.frontend_url = "https://listyro.com"
    test_settings.etsy_redirect_uri = "https://api.listyro.com/api/auth/etsy/callback"
    test_settings.session_cookie_secure = True
    start = await auth_client.get("/api/auth/etsy/start")
    cookie = start.headers["set-cookie"].lower()
    for part in ("listyro_oauth=", "domain=listyro.com", "path=/api/auth/etsy", "samesite=lax", "secure", "httponly",
                 "max-age=900"):
        assert part in cookie, (part, cookie)


async def test_callback_refuses_an_account_suspended_or_being_deleted_meanwhile(auth_client: httpx.AsyncClient) -> None:
    """Review R14: an account suspended (or asked to be deleted) during the 10-minute window gets no shop."""
    from datetime import datetime, timezone

    from app.db.models import Tenant

    state = await _started(auth_client)
    async with auth_client.sm() as s:  # type: ignore[attr-defined]
        tenant = await s.get(Tenant, auth_client.tenant_id)  # type: ignore[attr-defined]
        tenant.deletion_requested_at = datetime.now(timezone.utc)
        await s.commit()
    cb = await auth_client.get(f"/api/auth/etsy/callback?state={state}&code=the-code")
    assert cb.headers["location"].endswith("/connect?status=unavailable")
    async with auth_client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.execute(select(EtsyConnection))).scalars().first() is None
