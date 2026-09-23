"""Admin panel: role guard, users, invites, usage, audit, CLI — and isolation.

The last section is B6 for admins: an admin sees account metadata and quota,
never another tenant's designs, batches or generated content.
"""

from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.core.config import Settings, set_settings_override
from app.core.crypto import TokenCipher
from app.core.passwords import WeakPassword, hash_password, verify_password
from app.core.sessions import SESSION_COOKIE, SessionStore
from app.db.base import Base
from app.db.models import ApiUsage, AuditLog, InviteCode, Tenant, TenantStatus
from app.etsy.connection import ConnectionService
from app.etsy.rate_limiter import DailyQuota
from app.main import create_app
from app.pipeline.images import ImageProcessor, PillowBackend
from app.pipeline.ingest import BatchIngestor
from app.pipeline.sku import SkuParser
from app.pipeline.storage import LocalStorage
from tests.auth_support import BROWSER_HEADERS, open_session
from tests.test_tenant_isolation import _seed

PASSWORD = "correct-horse-battery-staple"


@pytest_asyncio.fixture()
async def world(tmp_path, test_settings: Settings) -> AsyncIterator[dict]:
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
    storage = LocalStorage(tmp_path)
    redis = FakeAsyncRedis()
    set_settings_override(test_settings.model_copy(update={"admin_token": "bootstrap-token"}))

    async def _session():
        async with sm() as s:
            yield s

    app = create_app()
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_storage] = lambda: storage
    app.dependency_overrides[deps.get_redis] = lambda: redis
    app.dependency_overrides[deps.get_session_store] = lambda: SessionStore(redis)
    app.dependency_overrides[deps.get_quota] = lambda: DailyQuota(redis, global_daily_limit=5000)
    app.dependency_overrides[deps.get_connection_service] = lambda: ConnectionService(
        TokenCipher(Fernet.generate_key()), client_id="k", token_url="https://t"
    )
    app.dependency_overrides[deps.get_ingestor] = lambda: BatchIngestor(
        sessionmaker=sm,
        storage=storage,
        processor=ImageProcessor(backend=PillowBackend()),
        sku_parser=SkuParser(),
    )

    # Two fully-populated tenants: the admin, and a seller with real resources.
    admin = await _seed(sm, storage, "admin@example.com", 1001)
    bob = await _seed(sm, storage, "bob@example.com", 2002)
    async with sm() as s:
        await s.execute(update(Tenant).values(password_hash=hash_password(PASSWORD)))
        await s.execute(update(Tenant).where(Tenant.id == admin.tenant_id).values(is_admin=True))
        await s.commit()

    transport = ASGITransport(app=app)
    clients = []
    for _ in range(3):
        c = AsyncClient(transport=transport, base_url="http://test", headers=BROWSER_HEADERS)
        clients.append(c)
    admin_c, bob_c, anon = clients
    admin_c.cookies.set(SESSION_COOKIE, await open_session(redis, admin.tenant_id))
    bob_c.cookies.set(SESSION_COOKIE, await open_session(redis, bob.tenant_id))
    try:
        yield {
            "app": app, "sm": sm, "redis": redis, "settings": test_settings,
            "admin": admin, "bob": bob, "a": admin_c, "b": bob_c, "anon": anon,
        }
    finally:
        for c in clients:
            await c.aclose()
        await engine.dispose()


ADMIN_ENDPOINTS = [
    ("GET", "/api/admin/users", None),
    ("GET", "/api/admin/invites", None),
    ("POST", "/api/admin/invites", {}),
    ("GET", "/api/admin/usage", None),
]


def _user_endpoints(target_id) -> list:
    return [
        ("POST", f"/api/admin/users/{target_id}/suspend", None),
        ("POST", f"/api/admin/users/{target_id}/reactivate", None),
        ("POST", f"/api/admin/users/{target_id}/temporary-password", None),
        ("PUT", f"/api/admin/users/{target_id}/quota", {"daily_quota": 5}),
    ]


async def _audit(sm) -> list[AuditLog]:
    async with sm() as s:
        return list((await s.execute(select(AuditLog).order_by(AuditLog.created_at))).scalars())


# --- The guard: 404 for everyone who is not a signed-in admin -----------------
async def test_admin_api_is_404_to_sellers_and_to_anonymous(world) -> None:
    target = world["admin"].tenant_id
    for client_name in ("b", "anon"):
        client = world[client_name]
        for method, path, body in ADMIN_ENDPOINTS + _user_endpoints(target):
            resp = await client.request(method, path, json=body)
            assert resp.status_code == 404, (client_name, method, path, resp.status_code)
    # And nothing was changed by any of those attempts.
    assert await _audit(world["sm"]) == []


async def test_temporary_password_does_not_unlock_admin(world) -> None:
    async with world["sm"]() as s:
        await s.execute(
            update(Tenant).where(Tenant.id == world["admin"].tenant_id).values(must_change_password=True)
        )
        await s.commit()
    assert (await world["a"].get("/api/admin/users")).status_code == 404


async def test_suspended_admin_is_locked_out(world) -> None:
    async with world["sm"]() as s:
        await s.execute(
            update(Tenant).where(Tenant.id == world["admin"].tenant_id).values(status=TenantStatus.suspended)
        )
        await s.commit()
    assert (await world["a"].get("/api/admin/users")).status_code == 404


async def test_me_reports_the_role(world) -> None:
    assert (await world["a"].get("/api/account/me")).json()["is_admin"] is True
    assert (await world["b"].get("/api/account/me")).json()["is_admin"] is False


# --- Users ----------------------------------------------------------------------
async def test_users_list_shows_metadata(world) -> None:
    users = {u["email"]: u for u in (await world["a"].get("/api/admin/users")).json()}
    assert set(users) == {"admin@example.com", "bob@example.com"}
    bob = users["bob@example.com"]
    assert bob["is_admin"] is False and bob["status"] == "active"
    assert bob["shop_connected"] is True
    assert bob["listings_published"] == 0
    assert bob["daily_quota"] == 2000
    assert set(bob) == {
        "id", "email", "is_admin", "status", "must_change_password", "created_at",
        "shop_name", "shop_connected", "listings_published", "quota_used_today", "daily_quota",
    }


async def test_suspend_ends_sessions_and_blocks_login_until_reactivated(world) -> None:
    a, b, bob = world["a"], world["b"], world["bob"]
    assert (await b.get("/api/batches")).status_code == 200

    resp = await a.post(f"/api/admin/users/{bob.tenant_id}/suspend")
    assert resp.status_code == 200 and resp.json()["status"] == "suspended"

    assert (await b.get("/api/batches")).status_code == 401  # session gone at once
    login = await world["anon"].post(
        "/api/account/login", json={"email": "bob@example.com", "password": PASSWORD}
    )
    assert login.status_code == 403

    assert (await a.post(f"/api/admin/users/{bob.tenant_id}/reactivate")).json()["status"] == "active"
    login = await world["anon"].post(
        "/api/account/login", json={"email": "bob@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200


async def test_an_admin_cannot_suspend_or_reset_themselves(world) -> None:
    a, me = world["a"], world["admin"].tenant_id
    assert (await a.post(f"/api/admin/users/{me}/suspend")).status_code == 409
    assert (await a.post(f"/api/admin/users/{me}/temporary-password")).status_code == 409
    assert (await a.get("/api/admin/users")).status_code == 200  # still in


async def test_temporary_password_forces_a_change(world) -> None:
    a, b, bob = world["a"], world["b"], world["bob"]
    issued = (await a.post(f"/api/admin/users/{bob.tenant_id}/temporary-password")).json()
    temp = issued["temporary_password"]

    assert (await b.get("/api/batches")).status_code == 401  # old sessions ended
    login = await world["anon"].post(
        "/api/account/login", json={"email": "bob@example.com", "password": temp}
    )
    assert login.status_code == 200 and login.json()["must_change_password"] is True
    b.cookies.set(SESSION_COOKIE, login.cookies[SESSION_COOKIE])
    assert (await b.get("/api/batches")).status_code == 403  # until they choose their own


async def test_quota_ceiling_can_be_changed_within_the_app_budget(world) -> None:
    a, bob = world["a"], world["bob"]
    resp = await a.put(f"/api/admin/users/{bob.tenant_id}/quota", json={"daily_quota": 1500})
    assert resp.status_code == 200 and resp.json()["daily_quota"] == 1500
    assert (await a.put(f"/api/admin/users/{bob.tenant_id}/quota", json={"daily_quota": 5001})).status_code == 422
    assert (await a.put(f"/api/admin/users/{bob.tenant_id}/quota", json={"daily_quota": -1})).status_code == 422
    # The seller sees it as their own limit.
    assert (await world["b"].get("/api/quota")).json()["tenant_limit"] == 1500


# --- Invites --------------------------------------------------------------------
async def _register(world, code: str, email: str):
    return await world["anon"].post(
        "/api/account/register", json={"email": email, "password": PASSWORD, "invite_code": code}
    )


async def test_invite_lifecycle(world) -> None:
    a = world["a"]
    created = (await a.post("/api/admin/invites", json={"note": "cousin"})).json()
    code, invite = created["code"], created["invite"]
    assert invite["state"] == "unused" and invite["created_by_email"] == "admin@example.com"

    assert (await _register(world, code, "cousin@example.com")).status_code == 201
    listed = {i["id"]: i for i in (await a.get("/api/admin/invites")).json()}
    assert listed[invite["id"]]["state"] == "used"
    assert listed[invite["id"]]["used_by_email"] == "cousin@example.com"

    # Only the hash was ever stored.
    async with world["sm"]() as s:
        assert code not in (await s.get(InviteCode, __import__("uuid").UUID(invite["id"]))).code_hash


async def test_bound_invite_only_redeems_for_its_address(world) -> None:
    code = (await world["a"].post("/api/admin/invites", json={"email": "Named@Example.com"})).json()["code"]
    assert (await _register(world, code, "someone-else@example.com")).status_code == 400
    assert (await _register(world, code, "named@example.com")).status_code == 201


async def test_cannot_bind_an_invite_to_an_existing_account(world) -> None:
    resp = await world["a"].post("/api/admin/invites", json={"email": "bob@example.com"})
    assert resp.status_code == 409


async def test_revoke_only_unused_codes(world) -> None:
    a = world["a"]
    fresh = (await a.post("/api/admin/invites", json={})).json()
    revoked = await a.post(f"/api/admin/invites/{fresh['invite']['id']}/revoke")
    assert revoked.status_code == 200 and revoked.json()["state"] == "revoked"
    assert (await _register(world, fresh["code"], "late@example.com")).status_code == 400

    used = (await a.post("/api/admin/invites", json={})).json()
    await _register(world, used["code"], "quick@example.com")
    assert (await a.post(f"/api/admin/invites/{used['invite']['id']}/revoke")).status_code == 409


async def test_expired_codes_show_as_expired_and_do_not_redeem(world) -> None:
    created = (await world["a"].post("/api/admin/invites", json={"expires_in_days": 1})).json()
    async with world["sm"]() as s:
        await s.execute(
            update(InviteCode).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        )
        await s.commit()
    states = {i["id"]: i["state"] for i in (await world["a"].get("/api/admin/invites")).json()}
    assert states[created["invite"]["id"]] == "expired"
    assert (await _register(world, created["code"], "tardy@example.com")).status_code == 400


async def test_never_expiring_invite(world) -> None:
    created = (await world["a"].post("/api/admin/invites", json={"expires_in_days": None})).json()
    assert created["invite"]["expires_at"] is None


# --- Usage ----------------------------------------------------------------------
async def test_usage_shows_the_app_budget_per_tenant_with_history(world) -> None:
    quota = DailyQuota(world["redis"], global_daily_limit=5000)
    for _ in range(5):
        await quota.reserve(world["bob"].tenant_id, 2000)
    for _ in range(2):
        await quota.reserve(world["admin"].tenant_id, 2000)
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    async with world["sm"]() as s:
        s.add(ApiUsage(tenant_id=world["bob"].tenant_id, usage_date=yesterday, request_count=40))
        await s.commit()

    usage = (await world["a"].get("/api/admin/usage")).json()
    assert (usage["global_used"], usage["global_limit"], usage["global_remaining"]) == (7, 5000, 4993)
    assert [t["email"] for t in usage["tenants"]] == ["bob@example.com", "admin@example.com"]
    bob = usage["tenants"][0]
    assert bob["used_today"] == 5 and len(bob["history"]) == 7
    by_day = {d["date"]: d["count"] for d in usage["history"]}
    assert by_day[yesterday.isoformat()] == 40
    assert by_day[usage["usage_date"]] == 7


# --- Audit ----------------------------------------------------------------------
async def test_every_admin_action_is_audited_without_personal_data(world) -> None:
    a, bob = world["a"], world["bob"].tenant_id
    await a.post(f"/api/admin/users/{bob}/suspend")
    await a.post(f"/api/admin/users/{bob}/reactivate")
    await a.post(f"/api/admin/users/{bob}/temporary-password")
    await a.put(f"/api/admin/users/{bob}/quota", json={"daily_quota": 900})
    invite = (await a.post("/api/admin/invites", json={"email": "x@example.com"})).json()
    await a.post(f"/api/admin/invites/{invite['invite']['id']}/revoke")

    rows = await _audit(world["sm"])
    assert [r.action for r in rows] == [
        "user.suspended", "user.reactivated", "user.temporary_password",
        "user.quota_changed", "invite.created", "invite.revoked",
    ]
    assert all(r.actor_tenant_id == world["admin"].tenant_id for r in rows)
    assert all(r.created_at is not None for r in rows)
    assert rows[3].details == {"previous": 2000, "new": 900}
    blob = json.dumps([r.details for r in rows])
    assert "@" not in blob and invite["code"] not in blob  # ids only; no emails, no codes


async def test_failed_or_refused_actions_leave_no_audit_row(world) -> None:
    await world["a"].post(f"/api/admin/users/{world['admin'].tenant_id}/suspend")  # 409
    await world["a"].put(f"/api/admin/users/{world['bob'].tenant_id}/quota", json={"daily_quota": 9999})
    assert await _audit(world["sm"]) == []


# --- ADMIN_TOKEN: bootstrap only ---------------------------------------------------
async def test_bootstrap_token_closes_once_an_admin_exists(world) -> None:
    resp = await world["anon"].post(
        "/api/account/admin/invites", json={}, headers={"X-Admin-Token": "bootstrap-token"}
    )
    assert resp.status_code == 404  # an admin exists: use the session

    async with world["sm"]() as s:
        await s.execute(update(Tenant).values(is_admin=False))
        await s.commit()
    resp = await world["anon"].post(
        "/api/account/admin/invites", json={}, headers={"X-Admin-Token": "bootstrap-token"}
    )
    assert resp.status_code == 201  # before any admin: the bootstrap path works


# --- The CLI --------------------------------------------------------------------
async def test_cli_creates_an_admin_who_signs_in_through_the_normal_form(world) -> None:
    from app.cli import create_admin

    msg = await create_admin(world["sm"], "Ops@Example.com", ask_password=lambda _e: "a-long-admin-secret")
    assert "created" in msg
    async with world["sm"]() as s:
        ops = (await s.execute(select(Tenant).where(Tenant.email == "ops@example.com"))).scalar_one()
    assert ops.is_admin and verify_password(ops.password_hash, "a-long-admin-secret")

    login = await world["anon"].post(
        "/api/account/login", json={"email": "ops@example.com", "password": "a-long-admin-secret"}
    )
    assert login.status_code == 200 and login.json()["is_admin"] is True
    rows = await _audit(world["sm"])
    assert rows[-1].action == "admin.created" and rows[-1].actor_tenant_id is None


async def test_cli_enforces_the_password_policy(world) -> None:
    from app.cli import create_admin

    with pytest.raises(WeakPassword):
        await create_admin(world["sm"], "weak@example.com", ask_password=lambda _e: "short")
    async with world["sm"]() as s:
        assert (await s.execute(select(Tenant).where(Tenant.email == "weak@example.com"))).first() is None


async def test_cli_promotes_without_touching_the_password(world) -> None:
    from app.cli import create_admin

    def must_not_ask(_email: str) -> str:
        raise AssertionError("promoting must not prompt for a password")

    msg = await create_admin(world["sm"], "bob@example.com", ask_password=must_not_ask)
    assert "promoted" in msg
    login = await world["anon"].post(
        "/api/account/login", json={"email": "bob@example.com", "password": PASSWORD}
    )
    assert login.json()["is_admin"] is True


async def test_cli_never_removes_the_last_admin(world) -> None:
    from app.cli import create_admin, demote_admin

    with pytest.raises(SystemExit):
        await demote_admin(world["sm"], "admin@example.com")
    await create_admin(world["sm"], "bob@example.com", ask_password=lambda _e: "unused")
    assert "no longer" in await demote_admin(world["sm"], "admin@example.com")


def test_cli_refuses_a_piped_password(monkeypatch) -> None:
    from app import cli

    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("a-long-admin-secret\n"))
    with pytest.raises(SystemExit, match="interactively"):
        cli.prompt_password("x@example.com")


# --- B6 for admins: metadata yes, other tenants' work never ---------------------------
async def test_admin_cannot_reach_another_tenants_designs_batches_or_content(world) -> None:
    a, bob = world["a"], world["bob"]
    for method, path, body in [
        ("GET", f"/api/batches/{bob.batch_id}", None),
        ("GET", f"/api/batches/{bob.batch_id}/groups", None),
        ("GET", f"/api/batches/{bob.batch_id}/content", None),
        ("GET", f"/api/batches/{bob.batch_id}/cost", None),
        ("GET", f"/api/assets/{bob.asset_id}/image", None),
        ("GET", f"/api/assets/{bob.asset_id}/image?w=448&ar=4:5", None),
        ("PATCH", f"/api/content/{bob.content_id}", {"title": "x"}),
        ("POST", f"/api/content/{bob.content_id}/approve", {"approved": False}),
        ("POST", f"/api/content/{bob.content_id}/publish", None),
        ("GET", f"/api/profiles/{bob.profile_id}", None),
        ("PATCH", f"/api/profiles/{bob.profile_id}", {"name": "x"}),
        ("DELETE", f"/api/profiles/{bob.profile_id}", None),
        ("GET", f"/api/jobs/{bob.job_id}", None),
    ]:
        resp = await a.request(method, path, json=body)
        assert resp.status_code == 404, (method, path, resp.status_code)

    # Their own tenant-scoped lists hold only their own work.
    assert [x["id"] for x in (await a.get("/api/batches")).json()] == [str(world["admin"].batch_id)]


async def test_admin_responses_carry_no_tenant_work(world) -> None:
    """Scan every admin payload for any trace of the seller's resources."""
    a, bob = world["a"], world["bob"]
    bodies = [
        (await a.get("/api/admin/users")).text,
        (await a.get("/api/admin/invites")).text,
        (await a.get("/api/admin/usage")).text,
        (await a.put(f"/api/admin/users/{bob.tenant_id}/quota", json={"daily_quota": 100})).text,
    ]
    blob = "\n".join(bodies)
    for resource_id in (bob.batch_id, bob.asset_id, bob.content_id, bob.profile_id, bob.job_id):
        assert str(resource_id) not in blob
    for trace in ("SKU1", "design.jpg", "processed/", "storage_key", "title", "tags", "description"):
        assert trace not in blob, trace
