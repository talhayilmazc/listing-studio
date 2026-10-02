"""The public "Request an invite" form and the admin's pending list."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.invites import hash_code, redeemable_by
from app.db.models import AuditLog, InviteCode, InviteRequest
from app.workers.retention import purge_expired_rows
from tests.test_admin import world  # noqa: F401  (fixture)

FORM = {"email": "Maker@Example.com", "shop": "Maker Tees  (etsy.com/shop/makertees)", "note": "300 designs a month"}


async def _rows(world):  # noqa: F811
    async with world["sm"]() as s:
        return list((await s.execute(select(InviteRequest))).scalars())


async def test_a_request_is_stored_and_a_repeat_is_not(world) -> None:  # noqa: F811
    for _ in range(2):
        r = await world["anon"].post("/api/invite-requests", json=FORM)
        assert r.status_code == 202 and r.json() == {"received": True}
    rows = await _rows(world)
    assert len(rows) == 1
    assert (rows[0].email, rows[0].shop, rows[0].status) == ("maker@example.com", "Maker Tees (etsy.com/shop/makertees)", "pending")


async def test_the_honeypot_is_answered_the_same_and_stores_nothing(world) -> None:  # noqa: F811
    r = await world["anon"].post("/api/invite-requests", json={**FORM, "website": "http://spam.example"})
    assert r.status_code == 202 and r.json() == {"received": True}
    assert await _rows(world) == []


async def test_one_connection_may_send_three_an_hour(world) -> None:  # noqa: F811
    codes = [
        (await world["anon"].post("/api/invite-requests", json={**FORM, "email": f"p{i}@example.com"})).status_code
        for i in range(4)
    ]
    assert codes == [202, 202, 202, 429]
    assert len(await _rows(world)) == 3


async def test_a_bad_address_or_an_essay_is_refused(world) -> None:  # noqa: F811
    assert (await world["anon"].post("/api/invite-requests", json={**FORM, "email": "nope"})).status_code == 422
    assert (await world["anon"].post("/api/invite-requests", json={**FORM, "note": "x" * 1001})).status_code == 422


async def test_approving_makes_a_code_only_that_address_can_use(world) -> None:  # noqa: F811
    await world["anon"].post("/api/invite-requests", json=FORM)
    listed = (await world["a"].get("/api/admin/invite-requests")).json()
    assert [r["status"] for r in listed] == ["pending"] and listed[0]["has_account"] is False

    approved = (await world["a"].post(f"/api/admin/invite-requests/{listed[0]['id']}/approve")).json()
    assert approved["request"]["status"] == "approved"
    assert approved["invite"]["bound_email"] == "maker@example.com"
    async with world["sm"]() as s:
        invite = (await s.execute(select(InviteCode).where(InviteCode.code_hash == hash_code(approved["code"])))).scalar_one()
        assert redeemable_by(invite, "maker@example.com") and not redeemable_by(invite, "someone@else.com")
        actions = [a.action for a in (await s.execute(select(AuditLog))).scalars()]
        assert "invite_request.approved" in actions
    # Decided once.
    assert (await world["a"].post(f"/api/admin/invite-requests/{listed[0]['id']}/decline")).status_code == 409


async def test_declining_and_who_may_see_the_list(world) -> None:  # noqa: F811
    await world["anon"].post("/api/invite-requests", json={**FORM, "email": "bob@example.com"})
    row = (await world["a"].get("/api/admin/invite-requests")).json()[0]
    assert row["has_account"] is True  # bob already has one: nothing to approve
    assert (await world["a"].post(f"/api/admin/invite-requests/{row['id']}/approve")).status_code == 409
    assert (await world["a"].post(f"/api/admin/invite-requests/{row['id']}/decline")).json()["status"] == "declined"
    # A seller and a visitor cannot read it: an ordinary 404, as every admin route.
    assert (await world["b"].get("/api/admin/invite-requests")).status_code == 404
    assert (await world["anon"].get("/api/admin/invite-requests")).status_code in (401, 404)


async def test_requests_are_not_kept_for_ever(world) -> None:  # noqa: F811
    now = datetime.now(timezone.utc)
    async with world["sm"]() as s:
        s.add_all([
            InviteRequest(email="old-decided@x.com", status="declined", created_at=now - timedelta(days=100), decided_at=now - timedelta(days=91)),
            InviteRequest(email="old-pending@x.com", created_at=now - timedelta(days=181)),
            InviteRequest(email="recent@x.com", created_at=now - timedelta(days=10)),
        ])
        await s.commit()
        await purge_expired_rows(s)
    assert [r.email for r in await _rows(world)] == ["recent@x.com"]
