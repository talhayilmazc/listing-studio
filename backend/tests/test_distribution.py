"""Shop groups and group scheduling (v8 §B; sending is "Where and when", B4: tests/test_send.py).

* nothing is split between groups any more (the owner, 2026-10-22): every listing goes to
  every chosen shop;
* the schedule staggers the shops of a group, spaces each shop's listings, and
  spills to the next day when the day's Etsy requests would pass what the
  account may spend; "finishes on" is the last day;
* sending spread out records the plan; drafts are released at their time and their
  go-live set for the confirmed time; cancelling withdraws what has not happened;
* the listing allowance is not touched by drafts in more shops;
* another account gets 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select

from app.core import allowance
from app.db.models import (
    DesignDistribution,
    GeneratedContent,
    ListingPublication,
    PlannedSlot,
    ProfileShopLink,
    Tenant,
)
from app.pipeline import group_plan as G
from app.workers import plans
from tests.test_multi_shop import _shop, world  # noqa: F401  (fixture)

TOMORROW = (datetime.now(timezone.utc) + timedelta(days=1)).date()


# --- pure ------------------------------------------------------------------------------------


def _settings(**kw) -> G.Settings:
    base = dict(start=TOMORROW, per_shop_per_day=2, window_start=time(9), window_end=time(17), spacing_minutes=60,
                stagger_minutes=10, zone="UTC")
    return G.Settings(**{**base, **kw})


def test_the_shops_of_a_group_are_staggered_and_each_shop_spaced() -> None:
    group = uuid.uuid4()
    shops = [G.Shop(uuid.uuid4(), "One", group), G.Shop(uuid.uuid4(), "Two", group)]
    listings = [G.Listing(uuid.uuid4(), f"L{i}", group) for i in range(3)]
    plan = G.plan(_settings(), listings, shops, capacity=lambda d: 10_000, now=datetime.now(timezone.utc))
    one = [(s.local_day, s.local_time) for s in plan.slots if s.shop_name == "One"]
    two = [(s.local_day, s.local_time) for s in plan.slots if s.shop_name == "Two"]
    assert one == [(TOMORROW, "09:00"), (TOMORROW, "10:00"), (TOMORROW + timedelta(days=1), "09:00")]
    assert two == [(TOMORROW, "09:10"), (TOMORROW, "10:10"), (TOMORROW + timedelta(days=1), "09:10")]
    # The same design never goes live in two shops of a group in the same minute.
    by_minute = {}
    for s in plan.slots:
        assert by_minute.setdefault((s.content_id, s.publish_at), s.shop_id) == s.shop_id
    assert plan.finishes_on == TOMORROW + timedelta(days=1)
    # Each draft is made two hours before it goes live.
    assert all(s.publish_at - s.draft_at == G.DRAFT_LEAD for s in plan.slots)


def test_a_full_day_spills_to_the_next_and_the_capacity_is_never_passed() -> None:
    group = uuid.uuid4()
    shops = [G.Shop(uuid.uuid4(), f"S{i}", group) for i in range(2)]
    listings = [G.Listing(uuid.uuid4(), f"L{i}", group) for i in range(4)]
    per_listing = G.DRAFT_REQUESTS + G.PUBLISH_REQUESTS
    # Room for 3 listing-shops a day: the 4th spills, and so on.
    plan = G.plan(_settings(per_shop_per_day=10), listings, shops, capacity=lambda d: 3 * per_listing,
                  now=datetime.now(timezone.utc))
    assert len(plan.slots) == 8
    assert all(plan.requests[d] <= plan.capacity[d] for d in plan.requests)
    days = sorted({s.local_day for s in plan.slots})
    assert len(days) == 3 and plan.finishes_on == days[-1]


def test_settings_that_cannot_work_are_refused_with_the_reason() -> None:
    group = uuid.uuid4()
    shops = [G.Shop(uuid.uuid4(), "A", group), G.Shop(uuid.uuid4(), "B", group)]
    listing = [G.Listing(uuid.uuid4(), "L", group)]
    for bad, words in ((dict(stagger_minutes=0), "stagger"), (dict(window_end=time(8)), "window"),
                       (dict(per_shop_per_day=0), "at least one")):
        try:
            G.plan(_settings(**bad), listing, shops, capacity=lambda d: 10_000, now=datetime.now(timezone.utc))
        except G.PlanRefused as exc:
            assert words in str(exc)
        else:
            raise AssertionError(bad)
    try:
        G.plan(_settings(), listing, shops, capacity=lambda d: 5, now=datetime.now(timezone.utc))
    except G.PlanRefused as exc:
        assert "more than" in str(exc)


# --- the API ---------------------------------------------------------------------------------


async def _groups(world) -> dict:  # noqa: F811
    """Alice: group A = Frost Tees; group B = Frost Mugs + Frost Caps (profile set up in Caps)."""
    async with world["sm"]() as s:
        caps = await _shop(s, world["alice"], 103, "Frost Caps", 2)
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=world["pa1"], connection_id=caps.id, status="ready"))
        await s.commit()
        caps_id = caps.id
    a = (await world["a"].post("/api/shop-groups", json={"name": "A", "connection_ids": [str(world["a1"])]})).json()
    b = (await world["a"].post("/api/shop-groups", json={"name": "B", "connection_ids": [str(world["a2"]), str(caps_id)]})).json()
    ids = {g["name"]: g["id"] for g in b["groups"]}
    return {"A": ids["A"], "B": ids["B"], "caps": caps_id}


SCHEDULE = {"start_date": TOMORROW.isoformat(), "per_shop_per_day": 5, "window_start": "09:00", "window_end": "17:00",
            "spacing_minutes": 30, "stagger_minutes": 7}


async def _spread(world, group: str, *, only=None) -> dict:  # noqa: F811
    """Send Alice's batch to one group, spread out (``only``: the listings left approved)."""
    if only is not None:
        async with world["sm"]() as s:
            for cid in world["contents"]:
                (await s.get(GeneratedContent, cid)).approved = cid in only
            await s.commit()
    res = await world["a"].put(f"/api/batches/{world['batch']}/send-to", json={"send_to": {"group_ids": [group]}})
    assert res.status_code == 200, res.text
    res = await world["a"].post(f"/api/batches/{world['batch']}/send", json={"when": "spread", "schedule": SCHEDULE})
    assert res.status_code == 200, res.text
    return res.json()


async def test_spreading_out_releases_drafts_at_their_time_and_sets_the_go_live(world) -> None:  # noqa: F811
    g = await _groups(world)
    c0, c1 = world["contents"]
    out = await _spread(world, g["B"], only=[c0])
    assert out["scheduled"] == 2
    async with world["sm"]() as s:
        slots = list((await s.execute(select(PlannedSlot))).scalars())
        assert {x.state for x in slots} == {"waiting"} and len(slots) == 2  # Mugs and Caps
        assert (await s.execute(select(DesignDistribution))).scalar_one().group_name == "B"
        caps = next(x for x in slots if x.connection_id == g["caps"])
        mugs = next(x for x in slots if x.connection_id == world["a2"])
        assert caps.publish_at != mugs.publish_at  # staggered: never the same minute
    assert not [c for c in world["queue"].calls if c[0] == "run_publish_job"]  # nothing due yet

    # Their time comes: each draft is queued as an ordinary draft job.
    queued: list[tuple] = []

    async def enqueue(fn, *args):  # noqa: ANN001, ANN202
        queued.append((fn, *args))

    later = datetime.now(timezone.utc) + timedelta(days=2)
    async with world["sm"]() as s:
        assert await plans.release_due(s, enqueue, now=later) == 2
        slots = list((await s.execute(select(PlannedSlot))).scalars())
        assert {x.state for x in slots} == {"drafting"} and len(queued) == 2
        # The worker made the Mugs draft: its go-live is set for the confirmed time.
        mugs = next(x for x in slots if x.connection_id == world["a2"])
        s.add(ListingPublication(tenant_id=world["alice"], content_id=c0, connection_id=world["a2"],
                                 etsy_listing_id=4242, state="draft"))
        await s.commit()
        await plans.after_draft(s, c0, world["a2"])
        publication = (await s.execute(select(ListingPublication))).scalar_one()
        assert publication.scheduled_for is not None
        assert (await s.get(PlannedSlot, mugs.id)).state == "scheduled"


async def test_cancelling_a_plan_withdraws_what_has_not_happened(world) -> None:  # noqa: F811
    g = await _groups(world)
    plan_id = (await _spread(world, g["B"], only=[world["contents"][0]]))["plan_id"]
    listed = (await world["a"].get("/api/plans")).json()
    assert listed[0]["id"] == plan_id and listed[0]["counts"] == {"waiting": 2}
    res = await world["a"].delete(f"/api/plans/{plan_id}")
    assert res.status_code == 200 and res.json()["cancelled"] is True and res.json()["counts"] == {"cancelled": 2}
    async with world["sm"]() as s:
        assert await plans.release_due(s, lambda *a: None, now=datetime.now(timezone.utc) + timedelta(days=3)) == 0


async def test_drafts_in_more_shops_do_not_touch_the_listing_allowance(world) -> None:  # noqa: F811
    g = await _groups(world)
    async with world["sm"]() as s:
        before = await allowance.status(s, await s.get(Tenant, world["alice"]))
    await _spread(world, g["B"])
    async with world["sm"]() as s:
        await plans.release_due(s, lambda *a: _noop(), now=datetime.now(timezone.utc) + timedelta(days=2))
        after = await allowance.status(s, await s.get(Tenant, world["alice"]))
    assert after.used == before.used


async def _noop() -> None:
    return None


async def test_an_unapproved_listing_is_never_planned(world) -> None:  # noqa: F811
    g = await _groups(world)
    out = await _spread(world, g["A"], only=[world["contents"][0]])
    assert out["scheduled"] == 1
    async with world["sm"]() as s:
        assert {x.content_id for x in (await s.execute(select(PlannedSlot))).scalars()} == {world["contents"][0]}


async def test_another_account_gets_404_on_plans(world) -> None:  # noqa: F811
    g = await _groups(world)
    plan_id = (await _spread(world, g["B"]))["plan_id"]
    b = world["b"]
    assert (await b.delete(f"/api/plans/{plan_id}")).status_code == 404
    assert (await b.get("/api/plans")).json() == []


async def test_a_planned_go_live_whose_listing_is_no_longer_approved_is_not_published(world) -> None:  # noqa: F811
    """Rule 3 (v8 §B): at its time a planned go-live is checked again; a listing
    no longer approved is not published, and the seller is told why."""
    from app.workers.schedule import release_scheduled_publishes

    g = await _groups(world)
    c0 = world["contents"][0]
    await _spread(world, g["A"], only=[c0])
    async with world["sm"]() as s:
        await plans.release_due(s, lambda *a: _noop(), now=datetime.now(timezone.utc) + timedelta(days=2))
        s.add(ListingPublication(tenant_id=world["alice"], content_id=c0, connection_id=world["a1"],
                                 etsy_listing_id=777, state="draft"))
        await s.commit()
        await plans.after_draft(s, c0, world["a1"])
        publication = (await s.execute(select(ListingPublication))).scalar_one()
        publication.scheduled_for = datetime.now(timezone.utc) - timedelta(minutes=1)  # its time has come
        (await s.get(GeneratedContent, c0)).approved = False  # ...but the seller withdrew the approval
        await s.commit()
    queued: list = []

    async def enqueue(*args):  # noqa: ANN002, ANN202
        queued.append(args)

    await release_scheduled_publishes({"sessionmaker": world["sm"], "enqueue": enqueue, "redis": world["redis"]})
    async with world["sm"]() as s:
        publication = (await s.execute(select(ListingPublication))).scalar_one()
        assert publication.state == "draft" and publication.schedule_job_id is None
        assert publication.schedule_note  # the reason, shown on the Scheduled page
    assert not [q for q in queued if "run_publish_live_job" in q]
