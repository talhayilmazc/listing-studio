"""Account endpoints: invite-only registration, login, sessions (production-spec A)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from tests.auth_support import BROWSER_HEADERS
from app.core.config import Settings, set_settings_override
from app.core.passwords import WeakPassword, hash_password, validate_strength, verify_password
from app.core.sessions import SESSION_COOKIE, SessionStore
from app.db.base import Base
from app.db.models import InviteCode, Tenant

ADMIN = "test-admin-token"
GOOD_PASSWORD = "correct-horse-battery-staple"


@pytest_asyncio.fixture()
async def app_ctx(test_settings: Settings) -> AsyncIterator[dict]:
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
    fake_redis = FakeAsyncRedis()

    # Admin endpoints are only reachable when ADMIN_TOKEN is configured.
    set_settings_override(test_settings.model_copy(update={"admin_token": ADMIN}))

    async def _session():
        async with sm() as s:
            yield s

    from app.main import create_app

    app = create_app()
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_redis] = lambda: fake_redis
    app.dependency_overrides[deps.get_session_store] = lambda: SessionStore(fake_redis)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=BROWSER_HEADERS) as client:
        yield {"client": client, "sm": sm, "redis": fake_redis}
    await engine.dispose()


async def _invite(ctx, note: str = "beta user") -> str:
    res = await ctx["client"].post(
        "/api/account/admin/invites",
        json={"note": note},
        headers={"X-Admin-Token": ADMIN},
    )
    assert res.status_code == 201
    return res.json()["code"]


# --- Password rules ---------------------------------------------------------
def test_password_strength_rules() -> None:
    validate_strength(GOOD_PASSWORD)

    with pytest.raises(WeakPassword):
        validate_strength("short")  # under 12
    with pytest.raises(WeakPassword):
        validate_strength("passwordpassword")  # common
    with pytest.raises(WeakPassword):
        validate_strength("aaaaaaaaaaaaaa")  # too few distinct characters
    with pytest.raises(WeakPassword):
        # must not embed the local part of the address
        validate_strength("talhatalha1234", email="talha@example.com")


def test_hashes_are_argon2id_and_salted() -> None:
    a = hash_password(GOOD_PASSWORD)
    b = hash_password(GOOD_PASSWORD)
    assert a.startswith("$argon2id$")
    assert a != b  # per-hash salt
    assert verify_password(a, GOOD_PASSWORD)
    assert not verify_password(a, GOOD_PASSWORD + "!")
    assert not verify_password("not-a-hash", GOOD_PASSWORD)


# --- Registration -----------------------------------------------------------
async def test_register_consumes_the_invite_once(app_ctx) -> None:
    client = app_ctx["client"]
    code = await _invite(app_ctx)

    res = await client.post(
        "/api/account/register",
        json={"email": "Beta@Example.com", "password": GOOD_PASSWORD, "invite_code": code},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["email"] == "beta@example.com"  # normalised
    assert body["must_change_password"] is False
    assert body["daily_quota"] == 1000  # production-spec C
    assert res.cookies.get(SESSION_COOKIE)  # signed in immediately

    # The same code cannot be spent twice.
    again = await client.post(
        "/api/account/register",
        json={"email": "second@example.com", "password": GOOD_PASSWORD, "invite_code": code},
    )
    assert again.status_code == 400

    async with app_ctx["sm"]() as s:
        rows = (await s.execute(select(InviteCode))).scalars().all()
        assert rows[0].used_by_tenant_id is not None and rows[0].used_at is not None
        # Only the hash is stored, never the code itself.
        assert code not in rows[0].code_hash


async def test_register_requires_a_valid_invite(app_ctx) -> None:
    res = await app_ctx["client"].post(
        "/api/account/register",
        json={"email": "nope@example.com", "password": GOOD_PASSWORD, "invite_code": "made-up"},
    )
    assert res.status_code == 400
    async with app_ctx["sm"]() as s:
        assert (await s.execute(select(Tenant))).scalars().all() == []


async def test_register_rejects_a_weak_password(app_ctx) -> None:
    code = await _invite(app_ctx)
    res = await app_ctx["client"].post(
        "/api/account/register",
        json={"email": "weak@example.com", "password": "short", "invite_code": code},
    )
    assert res.status_code == 422
    # A rejected registration must not burn the invite.
    async with app_ctx["sm"]() as s:
        invite = (await s.execute(select(InviteCode))).scalars().one()
        assert invite.used_by_tenant_id is None


async def test_duplicate_email_is_rejected(app_ctx) -> None:
    client = app_ctx["client"]
    first, second = await _invite(app_ctx), await _invite(app_ctx)
    await client.post(
        "/api/account/register",
        json={"email": "dup@example.com", "password": GOOD_PASSWORD, "invite_code": first},
    )
    res = await client.post(
        "/api/account/register",
        json={"email": "dup@example.com", "password": GOOD_PASSWORD, "invite_code": second},
    )
    assert res.status_code == 409


async def test_admin_endpoints_need_the_token(app_ctx) -> None:
    """Without the header the endpoints report 404, not 401: they stay invisible."""
    client = app_ctx["client"]
    assert (await client.post("/api/account/admin/invites", json={})).status_code == 404
    assert (
        await client.post(
            "/api/account/admin/invites", json={}, headers={"X-Admin-Token": "wrong"}
        )
    ).status_code == 404


# --- Login / sessions -------------------------------------------------------
async def _register(ctx, email: str = "user@example.com") -> None:
    code = await _invite(ctx)
    res = await ctx["client"].post(
        "/api/account/register",
        json={"email": email, "password": GOOD_PASSWORD, "invite_code": code},
    )
    assert res.status_code == 201
    ctx["client"].cookies.clear()


async def test_login_logout_round_trip(app_ctx) -> None:
    client = app_ctx["client"]
    await _register(app_ctx)

    assert (await client.get("/api/account/me")).status_code == 401

    res = await client.post(
        "/api/account/login", json={"email": "USER@example.com", "password": GOOD_PASSWORD}
    )
    assert res.status_code == 200
    client.cookies.set(SESSION_COOKIE, res.cookies[SESSION_COOKIE])

    me = await client.get("/api/account/me")
    assert me.status_code == 200 and me.json()["email"] == "user@example.com"
    # A session now unlocks the rest of the API.
    assert (await client.get("/api/batches")).status_code == 200

    assert (await client.post("/api/account/logout")).status_code == 204
    client.cookies.clear()
    assert (await client.get("/api/account/me")).status_code == 401


async def test_wrong_password_and_unknown_email_are_indistinguishable(app_ctx) -> None:
    client = app_ctx["client"]
    await _register(app_ctx)

    wrong = await client.post(
        "/api/account/login", json={"email": "user@example.com", "password": "wrong-password-x"}
    )
    missing = await client.post(
        "/api/account/login", json={"email": "ghost@example.com", "password": GOOD_PASSWORD}
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["detail"] == missing.json()["detail"]


async def test_login_locks_out_after_five_failures(app_ctx) -> None:
    client = app_ctx["client"]
    await _register(app_ctx)

    for _ in range(5):
        res = await client.post(
            "/api/account/login", json={"email": "user@example.com", "password": "bad-password-1"}
        )
        assert res.status_code == 401

    # The sixth attempt is refused even with the right password.
    locked = await client.post(
        "/api/account/login", json={"email": "user@example.com", "password": GOOD_PASSWORD}
    )
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0


async def test_password_change_revokes_every_session(app_ctx) -> None:
    client, redis = app_ctx["client"], app_ctx["redis"]
    await _register(app_ctx)

    # Two devices signed in at once.
    first = (
        await client.post(
            "/api/account/login", json={"email": "user@example.com", "password": GOOD_PASSWORD}
        )
    ).cookies[SESSION_COOKIE]
    second = (
        await client.post(
            "/api/account/login", json={"email": "user@example.com", "password": GOOD_PASSWORD}
        )
    ).cookies[SESSION_COOKIE]
    assert first != second

    client.cookies.set(SESSION_COOKIE, first)
    changed = await client.post(
        "/api/account/password",
        json={"current_password": GOOD_PASSWORD, "new_password": "a-different-long-secret"},
    )
    assert changed.status_code == 200

    # The other device is signed out.
    store = SessionStore(redis)
    assert await store.read(second) is None
    # The caller who changed it keeps working, on a fresh token.
    client.cookies.set(SESSION_COOKIE, changed.cookies[SESSION_COOKIE])
    assert (await client.get("/api/account/me")).status_code == 200

    # And the old password no longer works.
    client.cookies.clear()
    stale = await client.post(
        "/api/account/login", json={"email": "user@example.com", "password": GOOD_PASSWORD}
    )
    assert stale.status_code == 401


async def test_change_password_requires_the_current_one(app_ctx) -> None:
    client = app_ctx["client"]
    await _register(app_ctx)
    token = (
        await client.post(
            "/api/account/login", json={"email": "user@example.com", "password": GOOD_PASSWORD}
        )
    ).cookies[SESSION_COOKIE]
    client.cookies.set(SESSION_COOKIE, token)

    res = await client.post(
        "/api/account/password",
        json={"current_password": "not-the-password", "new_password": "a-different-long-secret"},
    )
    assert res.status_code == 401


# --- Admin reset / forced change (A4) ---------------------------------------
async def test_admin_reset_forces_a_password_change(app_ctx) -> None:
    client = app_ctx["client"]
    await _register(app_ctx)

    reset = await client.post(
        "/api/account/admin/reset-password",
        json={"email": "user@example.com"},
        headers={"X-Admin-Token": ADMIN},
    )
    assert reset.status_code == 200
    temp = reset.json()["temporary_password"]

    logged_in = await client.post(
        "/api/account/login", json={"email": "user@example.com", "password": temp}
    )
    assert logged_in.status_code == 200
    assert logged_in.json()["must_change_password"] is True
    client.cookies.set(SESSION_COOKIE, logged_in.cookies[SESSION_COOKIE])

    # The temporary password unlocks the account endpoints but nothing else.
    assert (await client.get("/api/account/me")).status_code == 200
    assert (await client.get("/api/batches")).status_code == 403
    assert (await client.get("/api/quota")).status_code == 403

    # Setting a real password clears the flag and restores access.
    changed = await client.post(
        "/api/account/password",
        json={"current_password": temp, "new_password": "a-brand-new-long-secret"},
    )
    assert changed.status_code == 200 and changed.json()["must_change_password"] is False
    client.cookies.set(SESSION_COOKIE, changed.cookies[SESSION_COOKIE])
    assert (await client.get("/api/batches")).status_code == 200


async def test_sessions_are_scoped_to_their_tenant(app_ctx) -> None:
    """A token minted for one tenant never resolves to another."""
    redis = app_ctx["redis"]
    store = SessionStore(redis)
    one, two = uuid.uuid4(), uuid.uuid4()

    token_one = await store.create(one)
    token_two = await store.create(two)

    assert (await store.read(token_one)).tenant_id == one
    assert (await store.read(token_two)).tenant_id == two

    await store.destroy_all(one)
    assert await store.read(token_one) is None
    assert (await store.read(token_two)).tenant_id == two  # untouched
