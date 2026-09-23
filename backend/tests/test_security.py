"""HTTP hardening (production-spec D): CSRF, headers, errors, CORS, limits."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.core.config import Settings, set_settings_override
from app.core.passwords import hash_password
from app.core.sessions import SESSION_COOKIE, SessionStore
from app.db.base import Base
from app.etsy.rate_limiter import DailyQuota
from app.main import create_app
from app.pipeline.images import ImageProcessor, PillowBackend
from app.pipeline.ingest import BatchIngestor
from app.pipeline.sku import SkuParser
from app.pipeline.storage import LocalStorage
from tests.auth_support import BROWSER_HEADERS, BROWSER_ORIGIN, make_tenant, open_session

ADMIN = "sec-admin-token"
PASSWORD = "correct-horse-battery-staple"


@pytest_asyncio.fixture()
async def sec(test_settings: Settings, tmp_path) -> AsyncIterator[dict]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    redis = FakeAsyncRedis()
    set_settings_override(test_settings.model_copy(update={"admin_token": ADMIN}))

    async def _session():
        async with sm() as s:
            yield s

    app = create_app()

    # A route that fails the way an unexpected bug does, carrying the kind of
    # detail that must never reach a caller.
    @app.get("/api/_boom")
    async def _boom() -> None:
        raise RuntimeError("db at /data/storage/secret password=hunter2 user@example.com")

    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_redis] = lambda: redis
    app.dependency_overrides[deps.get_session_store] = lambda: SessionStore(redis)
    app.dependency_overrides[deps.get_storage] = lambda: LocalStorage(tmp_path)
    app.dependency_overrides[deps.get_quota] = lambda: DailyQuota(redis, global_daily_limit=5000)
    app.dependency_overrides[deps.get_ingestor] = lambda: BatchIngestor(
        sessionmaker=sm,
        storage=LocalStorage(tmp_path),
        processor=ImageProcessor(backend=PillowBackend()),
        sku_parser=SkuParser(),
    )

    tenant_id = await make_tenant(sm, "owner@example.com", password_hash=hash_password(PASSWORD))
    token = await open_session(redis, tenant_id)
    transport = ASGITransport(app=app)
    # Two clients: one behaves like a browser, the other sends only what a
    # test tells it to — the shape of a forged or scripted request.
    async with AsyncClient(transport=transport, base_url="http://test", headers=BROWSER_HEADERS) as browser, \
            AsyncClient(transport=transport, base_url="http://test") as raw:
        browser.cookies.set(SESSION_COOKIE, token)
        yield {
            "app": app,
            "browser": browser,
            "raw": raw,
            "token": token,
            "redis": redis,
            "settings": test_settings,
        }
    await engine.dispose()


def _with_cookie(token: str, extra: dict | None = None) -> dict:
    """Headers for a request carrying the session cookie, and nothing else implied."""
    return {"cookie": f"{SESSION_COOKIE}={token}", **(extra or {})}


def _with_settings(sec: dict, **changes) -> None:
    set_settings_override(sec["settings"].model_copy(update={"admin_token": ADMIN, **changes}))


# --- CSRF -------------------------------------------------------------------
async def test_cookie_bearing_post_without_origin_is_refused(sec) -> None:
    raw = sec["raw"]
    resp = await raw.post("/api/batches", headers=_with_cookie(sec["token"]))
    assert resp.status_code == 403
    assert resp.json()["detail"] == "cross-site request blocked"


async def test_foreign_origin_is_refused(sec) -> None:
    raw = sec["raw"]
    resp = await raw.post(
        "/api/batches",
        headers=_with_cookie(sec["token"], {"origin": "https://evil.example"}),
    )
    assert resp.status_code == 403


async def test_null_origin_is_refused(sec) -> None:
    """Sandboxed frames send Origin: null; it matches nothing of ours."""
    resp = await sec["raw"].post(
        "/api/batches", headers=_with_cookie(sec["token"], {"origin": "null"})
    )
    assert resp.status_code == 403


async def test_own_origin_is_allowed(sec) -> None:
    assert (await sec["browser"].post("/api/batches")).status_code == 201


async def test_referer_is_the_fallback_when_origin_is_absent(sec) -> None:
    raw, token = sec["raw"], sec["token"]
    ok = await raw.post(
        "/api/batches",
        headers=_with_cookie(token, {"referer": f"{BROWSER_ORIGIN}/upload"}),
    )
    assert ok.status_code == 201
    bad = await raw.post(
        "/api/batches",
        headers=_with_cookie(token, {"referer": "https://evil.example/page"}),
    )
    assert bad.status_code == 403


async def test_login_csrf_is_blocked_even_without_a_cookie(sec) -> None:
    """Logging a victim into the attacker's account is a CSRF too."""
    resp = await sec["raw"].post(
        "/api/account/login",
        json={"email": "owner@example.com", "password": PASSWORD},
        headers={"origin": "https://evil.example"},
    )
    assert resp.status_code == 403


async def test_uncredentialed_scripted_calls_still_work(sec) -> None:
    """No cookie and no origin cannot be a CSRF, so the admin CLI path works."""
    resp = await sec["raw"].post(
        "/api/account/admin/invites", json={}, headers={"X-Admin-Token": ADMIN}
    )
    assert resp.status_code == 201


async def test_safe_methods_are_not_subject_to_the_origin_check(sec) -> None:
    resp = await sec["raw"].get(
        "/api/batches",
        headers=_with_cookie(sec["token"], {"origin": "https://evil.example"}),
    )
    assert resp.status_code == 200  # CORS, not CSRF, governs reads


# --- Security headers -------------------------------------------------------
EXPECTED_HEADERS = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
}


def _assert_hardened(resp) -> None:
    for name, value in EXPECTED_HEADERS.items():
        assert resp.headers.get(name) == value, (name, resp.status_code)
    assert "default-src 'none'" in resp.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    assert resp.headers.get("x-request-id")


async def test_every_kind_of_response_is_hardened(sec) -> None:
    browser, raw = sec["browser"], sec["raw"]
    _assert_hardened(await browser.get("/api/batches"))  # 200
    _assert_hardened(await raw.get("/api/batches"))  # 401
    _assert_hardened(await browser.get("/api/nope"))  # 404
    _assert_hardened(
        await raw.post("/api/batches", headers=_with_cookie(sec["token"]))
    )  # 403 csrf
    _assert_hardened(await browser.get("/api/_boom"))  # 500
    _assert_hardened(await raw.get("/health"))


async def test_tenant_data_is_not_cacheable_by_default(sec) -> None:
    assert (await sec["browser"].get("/api/batches")).headers["cache-control"] == "no-store"


# --- Error hygiene ----------------------------------------------------------
async def test_crash_returns_a_generic_500_and_logs_the_trace(sec, caplog) -> None:
    caplog.set_level(logging.ERROR, logger="app.security")
    resp = await sec["browser"].get("/api/_boom")

    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == "internal error"
    assert body["request_id"] == resp.headers["x-request-id"]
    text = resp.text
    for leaked in ("RuntimeError", "/data/storage", "hunter2", "user@example.com", "Traceback"):
        assert leaked not in text

    # The trace is in the log under the same id, with the secrets scrubbed.
    logged = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert body["request_id"] in logged
    assert "RuntimeError" in logged
    assert "hunter2" not in logged and "user@example.com" not in logged


async def test_validation_errors_do_not_echo_the_submitted_password(sec) -> None:
    resp = await sec["browser"].post(
        "/api/account/login", json={"email": "not-an-email", "password": "hunter2hunter2"}
    )
    assert resp.status_code == 422
    assert "hunter2hunter2" not in resp.text
    assert resp.json()["detail"][0]["loc"] == ["body", "email"]

    missing = await sec["browser"].post("/api/account/login", json={"password": "hunter2hunter2"})
    assert missing.status_code == 422
    assert "hunter2hunter2" not in missing.text  # the "missing" error carries the whole body


async def test_api_docs_are_off_in_production(test_settings: Settings) -> None:
    set_settings_override(test_settings.model_copy(update={"app_env": "production"}))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await c.get(path)).status_code == 404, path


# --- CORS -------------------------------------------------------------------
def test_wildcard_cors_origin_refuses_to_start(test_settings: Settings) -> None:
    set_settings_override(test_settings.model_copy(update={"cors_origins": "*"}))
    with pytest.raises(RuntimeError, match="explicit origins"):
        create_app()


async def test_cors_answers_only_our_origin(sec) -> None:
    raw = sec["raw"]
    preflight = {"access-control-request-method": "POST"}
    ours = await raw.options("/api/batches", headers={"origin": BROWSER_ORIGIN, **preflight})
    theirs = await raw.options(
        "/api/batches", headers={"origin": "https://evil.example", **preflight}
    )
    assert ours.headers.get("access-control-allow-origin") == BROWSER_ORIGIN
    assert "access-control-allow-origin" not in theirs.headers


# --- Body ceilings ----------------------------------------------------------
async def test_declared_oversize_body_is_refused_before_parsing(sec) -> None:
    resp = await sec["browser"].post(
        "/api/account/login",
        content=b"x" * (2 * 1024 * 1024),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 413


async def test_streamed_oversize_body_is_cut_off(sec) -> None:
    """No Content-Length (chunked): the stream is counted as it arrives."""

    async def chunks():
        for _ in range(3):
            yield b"x" * (512 * 1024)

    resp = await sec["browser"].post(
        "/api/account/login", content=chunks(), headers={"content-type": "application/json"}
    )
    assert resp.status_code == 413


# --- Rate limits ------------------------------------------------------------
async def test_general_ceiling(sec) -> None:
    _with_settings(sec, rate_limit_requests=3)
    browser = sec["browser"]
    codes = [(await browser.get("/api/batches")).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    last = await browser.get("/api/batches")
    assert int(last.headers["retry-after"]) >= 1


async def test_auth_endpoints_have_a_tighter_ceiling(sec) -> None:
    _with_settings(sec, auth_rate_limit_requests=2)
    browser = sec["browser"]
    body = {"email": "ghost@example.com", "password": "wrong-password-x"}
    codes = [(await browser.post("/api/account/login", json=body)).status_code for _ in range(3)]
    assert codes == [401, 401, 429]
    # The general tier is untouched by auth traffic.
    assert (await browser.get("/api/batches")).status_code == 200


async def test_health_is_never_rate_limited(sec) -> None:
    _with_settings(sec, rate_limit_requests=1)
    raw = sec["raw"]
    for _ in range(5):
        assert (await raw.get("/health")).status_code == 200


async def test_limiter_fails_open_when_redis_is_down(sec) -> None:
    class BrokenRedis:
        async def incr(self, *_):
            raise RedisError("down")

    sec["app"].dependency_overrides[deps.get_redis] = lambda: BrokenRedis()
    assert (await sec["browser"].get("/api/batches")).status_code == 200


# --- Client IP --------------------------------------------------------------
async def test_forwarded_for_cannot_be_used_to_dodge_the_lockout(sec) -> None:
    """Rotating X-Forwarded-For used to present a fresh 'IP' per attempt."""
    browser = sec["browser"]
    for i in range(5):
        # A different email each time, so only the IP scope can lock.
        await browser.post(
            "/api/account/login",
            json={"email": f"nobody{i}@example.com", "password": "wrong-password-x"},
            headers={"x-forwarded-for": f"203.0.113.{i}"},
        )
    locked = await browser.post(
        "/api/account/login",
        json={"email": "owner@example.com", "password": PASSWORD},
        headers={"x-forwarded-for": "198.51.100.77"},
    )
    assert locked.status_code == 429


async def test_trusted_ip_header_is_honoured(sec) -> None:
    """Behind Cloudflare, distinct cf-connecting-ip values are distinct clients."""
    _with_settings(sec, client_ip_header="cf-connecting-ip")
    browser = sec["browser"]
    for i in range(5):
        await browser.post(
            "/api/account/login",
            json={"email": f"nobody{i}@example.com", "password": "wrong-password-x"},
            headers={"cf-connecting-ip": "203.0.113.9"},
        )
    other = await browser.post(
        "/api/account/login",
        json={"email": "owner@example.com", "password": PASSWORD},
        headers={"cf-connecting-ip": "198.51.100.10"},
    )
    assert other.status_code == 200
