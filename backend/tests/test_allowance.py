"""The product allowance: listings generated plus drafts created, per period.

Per seller (admin panel) with a system default; daily, weekly or monthly in the
seller's own time zone; separate from the Etsy request quota. Hitting it blocks
new work with the reset date; changing it keeps what was used.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core import allowance
from app.db.models import AllowanceUse, AuditLog, Job, JobStatus, JobType, Tenant
from tests.test_publish_api import _add_content, _shop, ctx  # noqa: F401  (fixture)
from tests.test_regenerate import _contents, _content_reply, _generate, _group, llm, VISION  # noqa: F401
from tests.support import fake_response

UTC = timezone.utc
NOW = datetime(2026, 9, 27, 18, 0, tzinfo=UTC)  # Sunday, 1 PM in Chicago


def test_periods_follow_the_sellers_calendar() -> None:
    chi = "America/Chicago"
    assert allowance.period_bounds("daily", chi, NOW) == (datetime(2026, 9, 27, 5, tzinfo=UTC), datetime(2026, 9, 28, 5, tzinfo=UTC))
    # A week starts Monday 00:00 there; a month on the 1st.
    assert allowance.period_bounds("weekly", chi, NOW) == (datetime(2026, 9, 21, 5, tzinfo=UTC), datetime(2026, 9, 28, 5, tzinfo=UTC))
    assert allowance.period_bounds("monthly", chi, NOW) == (datetime(2026, 9, 1, 5, tzinfo=UTC), datetime(2026, 10, 1, 5, tzinfo=UTC))
    # October's month ends at midnight CST, after the clocks go back.
    assert allowance.period_bounds("monthly", chi, datetime(2026, 10, 20, tzinfo=UTC))[1] == datetime(2026, 11, 1, 5, tzinfo=UTC)
    assert allowance.period_bounds("weekly", "Europe/Istanbul", NOW)[0] == datetime(2026, 9, 20, 21, tzinfo=UTC)
    assert allowance.reset_label(datetime(2026, 10, 1, 5, tzinfo=UTC), chi) == "Thu, Oct 1, 12:00 AM CDT"


async def _tenant(ctx, **values) -> Tenant:  # noqa: F811
    async with ctx["sm"]() as s:
        t = await s.get(Tenant, ctx["tenant_id"])
        for k, v in values.items():
            setattr(t, k, v)
        await s.commit()
        return t


async def test_usage_is_a_window_so_changing_the_period_keeps_it(ctx) -> None:  # noqa: F811
    await _tenant(ctx, time_zone="America/Chicago", allowance_amount=10, allowance_period="weekly")
    async with ctx["sm"]() as s:
        for when, kind in ((NOW - timedelta(days=2), "generation"), (NOW - timedelta(hours=1), "draft"),
                           (NOW - timedelta(days=10), "generation")):
            s.add(AllowanceUse(tenant_id=ctx["tenant_id"], kind=kind, at=when))
        shop = await _shop(s, ctx["tenant_id"])
        s.add(Job(tenant_id=ctx["tenant_id"], connection_id=shop, type=JobType.create_draft, status=JobStatus.queued, payload={}))
        await s.commit()
        t = await s.get(Tenant, ctx["tenant_id"])
        week = await allowance.status(s, t, NOW)
        assert (week.generations, week.drafts, week.pending, week.used, week.remaining) == (1, 1, 1, 3, 7)
        t.allowance_period = "monthly"  # this month holds all three
        month = await allowance.status(s, t, NOW)
        assert (month.generations, month.used) == (2, 4)
        t.allowance_period = "daily"
        assert (await allowance.status(s, t, NOW)).used == 2  # the draft today, and the queued one


async def test_generation_stops_at_the_allowance_and_says_when_it_resets(ctx, llm) -> None:  # noqa: F811
    batch, _, profile = await _group(ctx)
    await _tenant(ctx, time_zone="America/Chicago", allowance_amount=1, allowance_period="monthly")
    llm._responses.extend([fake_response(VISION), fake_response(_content_reply())])
    body = await _generate(ctx, batch, profile, replace=True, replace_approved=True)
    assert body["generated"] == 1
    async with ctx["sm"]() as s:
        assert [u.kind for u in (await s.execute(select(AllowanceUse))).scalars()] == ["generation"]

    resp = await ctx["client"].post(f"/api/batches/{batch}/generate", json={"profile_id": str(profile), "group_key": "G1", "replace": True})
    assert resp.status_code == 429
    assert resp.json()["detail"].startswith("You've used your allowance of 1 listings and drafts this month. It resets on ")
    assert "12:00 AM" in resp.json()["detail"]
    mine = (await ctx["client"].get("/api/account/allowance")).json()
    assert (mine["used"], mine["remaining"], mine["period"], mine["custom"]) == (1, 0, "monthly", True)


async def test_drafts_that_would_not_fit_are_refused_before_queueing(ctx) -> None:  # noqa: F811
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"])
    await _tenant(ctx, allowance_amount=0)
    resp = await ctx["client"].post(f"/api/content/{content_id}/publish")
    assert resp.status_code == 429 and "resets on" in resp.json()["detail"]
    async with ctx["sm"]() as s:
        assert (await s.execute(select(Job))).scalars().all() == []


async def _admin(ctx) -> None:  # noqa: F811
    await _tenant(ctx, is_admin=True)


async def test_the_admin_sets_a_sellers_allowance_and_the_default_audited(ctx) -> None:  # noqa: F811
    await _admin(ctx)
    c, tid = ctx["client"], ctx["tenant_id"]
    body = (await c.put(f"/api/admin/users/{tid}/allowance", json={"amount": 40, "period": "weekly"})).json()
    assert body["allowance"]["amount"] == 40 and body["allowance"]["period"] == "weekly" and body["allowance"]["custom"]
    assert (await c.put(f"/api/admin/users/{tid}/allowance", json={"period": "hourly"})).status_code == 422

    assert (await c.get("/api/admin/allowance-default")).json() == {"amount": 500, "period": "monthly"}
    assert (await c.put("/api/admin/allowance-default", json={"amount": 120, "period": "daily"})).status_code == 200
    body = (await c.put(f"/api/admin/users/{tid}/allowance", json={})).json()  # back to the default
    assert (body["allowance"]["amount"], body["allowance"]["period"], body["allowance"]["custom"]) == (120, "daily", False)

    async with ctx["sm"]() as s:
        rows = (await s.execute(select(AuditLog).order_by(AuditLog.created_at))).scalars().all()
    actions = [(r.action, r.details) for r in rows if "allowance" in r.action]
    assert actions == [
        ("user.allowance_changed", {"previous": {"amount": None, "period": None}, "new": {"amount": 40, "period": "weekly"}}),
        ("app.allowance_default_changed", {"previous": {"amount": 500, "period": "monthly"}, "new": {"amount": 120, "period": "daily"}}),
        ("user.allowance_changed", {"previous": {"amount": 40, "period": "weekly"}, "new": {"amount": None, "period": None}}),
    ]


async def test_generating_for_all_with_nothing_to_write_is_not_refused(ctx, llm) -> None:  # noqa: F811
    batch, _, profile = await _group(ctx)  # the one group already has content
    await _tenant(ctx, allowance_amount=0)
    resp = await ctx["client"].post(f"/api/batches/{batch}/generate", json={"profile_id": str(profile)})
    assert resp.status_code == 200 and resp.json()["skipped"] == 1
