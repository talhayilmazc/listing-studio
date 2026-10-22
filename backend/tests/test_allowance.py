"""The product allowance: listings generated plus drafts created, per period.

Per seller (admin panel) with a system default; daily, weekly or monthly in the
seller's own time zone; separate from the Etsy request quota. Hitting it blocks
new work with the reset date; changing it keeps what was used.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core import allowance
from app.db.models import AllowanceUse, AuditLog, GeneratedContent, Job, JobStatus, JobType, ListingPublication, Tenant
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
        # Only listings written count: not the draft made an hour ago, not the queued draft.
        assert (week.generations, week.pending, week.used, week.remaining) == (1, 0, 1, 9)
        t.allowance_period = "monthly"  # this month holds both generations
        month = await allowance.status(s, t, NOW)
        assert (month.generations, month.used) == (2, 2)
        t.allowance_period = "daily"
        assert (await allowance.status(s, t, NOW)).used == 0
        # A queued Replace images will write a listing: it is held as used until it has.
        s.add(Job(tenant_id=ctx["tenant_id"], connection_id=shop, type=JobType.replace_images, status=JobStatus.queued, payload={}))
        await s.commit()
        assert (await allowance.status(s, t, NOW)).used == 1


async def _unwritten_group(ctx):  # noqa: F811
    """A design with photos and no listing yet (the shared helper makes one that has one)."""
    batch, old, profile = await _group(ctx)
    async with ctx["sm"]() as s:
        await s.delete(await s.get(GeneratedContent, old))
        await s.commit()
    return batch, profile


async def _uses(ctx) -> list[tuple[str, str | None]]:  # noqa: F811
    async with ctx["sm"]() as s:
        return [(u.kind, u.design) for u in (await s.execute(select(AllowanceUse).order_by(AllowanceUse.at, AllowanceUse.id))).scalars()]


async def test_generation_stops_at_the_allowance_and_says_when_it_resets(ctx, llm) -> None:  # noqa: F811
    batch, profile = await _unwritten_group(ctx)
    await _tenant(ctx, time_zone="America/Chicago", allowance_amount=1, allowance_period="monthly")
    llm._responses.extend([fake_response(VISION), fake_response(_content_reply())])
    body = await _generate(ctx, batch, profile)
    assert body["generated"] == 1
    assert await _uses(ctx) == [("generation", f"{batch}:G1")]  # a first write counts, and says which design

    # Nothing is left for another design; the one already written can still be rewritten (below).
    async with ctx["sm"]() as s:
        for _ in range(allowance.FREE_REWRITES):
            allowance.record(s, ctx["tenant_id"], allowance.REWRITE, design=f"{batch}:G1")
        await s.commit()
    resp = await ctx["client"].post(f"/api/batches/{batch}/generate", json={"profile_id": str(profile), "group_key": "G1", "replace": True})
    assert resp.status_code == 429
    assert resp.json()["detail"].startswith("You've generated your 1 listings this month. More can be generated from ")
    assert "Drafts and publishing are not affected" in resp.json()["detail"]
    assert "12:00 AM" in resp.json()["detail"]
    mine = (await ctx["client"].get("/api/account/allowance")).json()
    assert (mine["used"], mine["remaining"], mine["period"], mine["custom"]) == (1, 0, "monthly", True)


async def test_the_first_three_rewrites_of_a_design_are_free_and_the_fourth_counts(ctx, llm) -> None:  # noqa: F811
    """"Rewriting the same design is free up to 3 times" (the pricing page's line)."""
    batch, profile = await _unwritten_group(ctx)
    await _tenant(ctx, time_zone="America/Chicago", allowance_amount=2, allowance_period="monthly")

    async def write(**flags) -> dict:
        llm._responses.extend([fake_response(VISION), fake_response(_content_reply())])
        return await _generate(ctx, batch, profile, **flags)

    async def mine() -> tuple[int, int, int]:
        body = (await ctx["client"].get("/api/account/allowance")).json()
        assert body["free_rewrites"] == 3
        return body["used"], body["rewrites"], body["remaining"]

    assert (await write())["generated"] == 1
    assert await mine() == (1, 0, 1)
    for n in (1, 2, 3):  # regenerate, three times: nothing is counted
        assert (await write(replace=True, replace_approved=True))["generated"] == 1
        assert await mine() == (1, n, 1)
    assert (await write(replace=True, replace_approved=True))["generated"] == 1  # the fourth counts
    assert await mine() == (2, 3, 0)
    design = f"{batch}:G1"
    # One first write, three free rewrites, and a fourth that counted (rows written in the same second have no order).
    assert sorted(await _uses(ctx)) == sorted([("generation", design)] * 2 + [("rewrite", design)] * 3)
    # The fifth would count too, and nothing is left: refused, with the reset date.
    resp = await ctx["client"].post(f"/api/batches/{batch}/generate", json={"profile_id": str(profile), "group_key": "G1", "replace": True, "replace_approved": True})
    assert resp.status_code == 429 and resp.json()["detail"].startswith("You've generated your 2 listings this month.")
    assert len(await _contents(ctx, batch)) == 1  # the design still has its one listing


async def test_free_rewrites_are_per_design_and_start_again_each_period(ctx) -> None:  # noqa: F811
    tenant = await _tenant(ctx, time_zone="UTC", allowance_amount=10, allowance_period="monthly")
    now = datetime.now(UTC)
    async with ctx["sm"]() as s:
        for at in (now - timedelta(days=40), now - timedelta(days=41), now - timedelta(days=42)):  # last period
            s.add(AllowanceUse(tenant_id=tenant.id, kind=allowance.REWRITE, design="b:G1", at=at))
        s.add(AllowanceUse(tenant_id=tenant.id, kind=allowance.REWRITE, design="b:G1", at=now))
        s.add(AllowanceUse(tenant_id=tenant.id, kind=allowance.REWRITE, design="b:G2", at=now))
        await s.commit()
        tenant = await s.get(Tenant, tenant.id)
        assert await allowance.free_rewrites_left(s, tenant, "b:G1") == 2  # one used this period; last period's do not carry
        assert await allowance.free_rewrites_left(s, tenant, "b:G2") == 2
        assert await allowance.free_rewrites_left(s, tenant, "b:G3") == 3
        assert await allowance.rewrite_kind(s, tenant, "b:G1") == allowance.REWRITE
        for _ in range(2):
            allowance.record(s, tenant.id, allowance.REWRITE, design="b:G1")
        await s.commit()
        assert await allowance.rewrite_kind(s, tenant, "b:G1") == allowance.GENERATION
        assert await allowance.rewrite_kind(s, tenant, "b:G2") == allowance.REWRITE  # another design's are its own
        status = await allowance.status(s, tenant)
        assert (status.used, status.rewrites) == (0, 4)  # free rewrites are recorded, never counted
        assert await allowance.written_before(s, tenant, "b:G1") and not await allowance.written_before(s, tenant, "b:G9")


async def test_the_allowance_says_how_many_were_published_in_the_same_period(ctx) -> None:  # noqa: F811
    """"N written · M published this month": the two numbers a seller compares."""
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"])
    tenant = await _tenant(ctx, time_zone="UTC", allowance_amount=50, allowance_period="monthly")
    now = datetime.now(UTC)
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
        for n, published_at in enumerate((now, now, now - timedelta(days=45), None)):  # two this month, one earlier, one still a draft
            s.add(ListingPublication(tenant_id=tenant.id, content_id=content_id if n == 0 else None, connection_id=shop,
                                     etsy_listing_id=9000 + n, state="active" if published_at else "draft", published_at=published_at))
        for _ in range(5):
            allowance.record(s, tenant.id, allowance.GENERATION, design="b:G1")
        await s.commit()
    mine = (await ctx["client"].get("/api/account/allowance")).json()
    assert (mine["used"], mine["published"], mine["period"]) == (5, 2, "monthly")


async def test_drafts_are_not_limited_by_the_listing_allowance(ctx) -> None:  # noqa: F811
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"])
    await _tenant(ctx, allowance_amount=0, allowance_period="monthly")  # nothing left to generate
    resp = await ctx["client"].post(f"/api/content/{content_id}/publish")
    assert resp.status_code == 200 and len(resp.json()["jobs"]) == 1
    mine = (await ctx["client"].get("/api/account/allowance")).json()
    assert (mine["label"], mine["used"], mine["pending"]) == ("Listings generated", 0, 0) and "drafts" not in mine


async def test_half_an_override_follows_the_default_entirely(ctx) -> None:  # noqa: F811
    """An amount without a period (or the other way round) is not an allowance of
    the account's own: mixing one's amount with the other's period showed
    "500 today" beside an admin panel saying "monthly"."""
    await _tenant(ctx, allowance_amount=None, allowance_period="daily")
    mine = (await ctx["client"].get("/api/account/allowance")).json()
    assert (mine["amount"], mine["period"], mine["custom"]) == (500, "monthly", False)
    await _tenant(ctx, allowance_amount=1500, allowance_period=None)
    mine = (await ctx["client"].get("/api/account/allowance")).json()
    assert (mine["amount"], mine["period"], mine["custom"]) == (500, "monthly", False)
    await _tenant(ctx, allowance_amount=1500, allowance_period="daily")
    mine = (await ctx["client"].get("/api/account/allowance")).json()
    assert (mine["amount"], mine["period"], mine["custom"]) == (1500, "daily", True)


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


async def test_there_is_no_free_path_to_a_listing(ctx, llm) -> None:  # noqa: F811
    """B12 (owner, 2026-10-22: today's rule stays). A hand-written listing counts 1 when it is
    created; generating it later is a rewrite (the first 3 free). With an allowance of 1 used,
    no route gives a second listing: not writing it yourself, not generating it, not "Generate
    the empty ones", not a fourth rewrite; and no rewrite is ever free for a design that was
    never counted."""
    batch, profile = await _unwritten_group(ctx)
    await _tenant(ctx, time_zone="America/Chicago", allowance_amount=1, allowance_period="monthly")
    # A second design in the same batch, with photos and no listing.
    async with ctx["sm"]() as s:
        from app.db.models import Asset

        first = (await s.execute(select(Asset).where(Asset.batch_id == batch, Asset.group_key == "G1"))).scalars().first()
        s.add(Asset(batch_id=batch, tenant_id=ctx["tenant_id"], original_filename="G2/front.png", group_key="G2",
                    storage_key=first.storage_key, processed_key=first.processed_key, status=first.status, rank=1))
        await s.commit()
    client = ctx["client"]
    res = await client.put(f"/api/batches/{batch}/groups", json={"group_keys": ["G1", "G2"], "profile_id": str(profile)})
    assert res.status_code == 200, res.text

    async def used() -> tuple[int, int, int]:
        body = (await client.get("/api/account/allowance")).json()
        return body["used"], body["rewrites"], body["remaining"]

    # Writing it yourself is a listing: it counts 1.
    res = await client.post(f"/api/batches/{batch}/manual", json={"group_key": "G1"})
    assert res.status_code == 200 and res.json()["generated"] == 1, res.text
    assert await used() == (1, 0, 0)
    # The allowance is used: the other design cannot be written by hand or by AI.
    assert (await client.post(f"/api/batches/{batch}/manual", json={"group_key": "G2"})).status_code == 429
    llm._responses.extend([fake_response(VISION), fake_response(_content_reply())])
    assert (await client.post(f"/api/batches/{batch}/generate", json={"profile_id": str(profile), "group_key": "G2"})).status_code == 429
    # Generating the hand-written one ("Generate the empty ones", then Regenerate) is a rewrite of the
    # same design: the first three are free.
    llm._responses.extend([fake_response(VISION), fake_response(_content_reply())])
    assert (await _generate(ctx, batch, profile, only_empty=True))["generated"] == 1
    assert await used() == (1, 1, 0)
    for n in (2, 3):
        llm._responses.extend([fake_response(VISION), fake_response(_content_reply())])
        assert (await _generate(ctx, batch, profile, replace=True, replace_approved=True))["generated"] == 1
        assert await used() == (1, n, 0)
    # The fourth would count, and nothing is left: refused.
    res = await client.post(f"/api/batches/{batch}/generate",
                            json={"profile_id": str(profile), "group_key": "G1", "replace": True, "replace_approved": True})
    assert res.status_code == 429
    assert await used() == (1, 3, 0)
    # Every listing's design was counted once, and every free rewrite belongs to a counted design.
    uses = await _uses(ctx)
    counted = {d for k, d in uses if k == allowance.GENERATION}
    assert counted == {f"{batch}:G1"}
    assert {d for k, d in uses if k == allowance.REWRITE} <= counted
    async with ctx["sm"]() as s:
        designs = {(c.batch_id, "G1") for c in (await s.execute(select(GeneratedContent).where(GeneratedContent.batch_id == batch))).scalars()}
    assert designs == {(batch, "G1")}  # G2 never got a listing
