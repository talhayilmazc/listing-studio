"""GET /api/account/session: is this cookie a live session of an active account?

The public site's "/" asks it on every visit with a cookie (frontend/middleware.ts):
a stale cookie must read as "not signed in", so the visitor sees the landing page.
"""

from __future__ import annotations

from sqlalchemy import update

from app.core.sessions import SESSION_COOKIE
from app.db.models import Tenant, TenantStatus
from tests.test_admin import world  # noqa: F401  (fixture)


async def test_a_live_session_is_204_and_not_cached(world) -> None:  # noqa: F811
    r = await world["b"].get("/api/account/session")
    assert r.status_code == 204 and r.content == b""
    assert r.headers["cache-control"] == "no-store"


async def test_no_cookie_an_unknown_one_or_an_ended_one_is_401(world) -> None:  # noqa: F811
    assert (await world["anon"].get("/api/account/session")).status_code == 401
    world["anon"].cookies.set(SESSION_COOKIE, "not-a-real-session")
    assert (await world["anon"].get("/api/account/session")).status_code == 401
    # Signed out elsewhere: the token in the browser no longer names a session.
    await world["b"].post("/api/account/logout")
    assert (await world["b"].get("/api/account/session")).status_code == 401


async def test_a_suspended_account_is_not_signed_in(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        await s.execute(update(Tenant).where(Tenant.id == world["bob"].tenant_id).values(status=TenantStatus.suspended))
        await s.commit()
    assert (await world["b"].get("/api/account/session")).status_code == 401
