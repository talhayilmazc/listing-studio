"""Shop groups, distribution and group scheduling (v8 §B).

* "Split evenly" divides the approved listings across the chosen groups, in order;
* every shop of a group gets the group's listings, and no other group's;
* a design sent to another group before is warned about, not blocked;
* the schedule staggers the shops of a group, spaces each shop's listings, and
  spills to the next day when the day's Etsy requests would pass what the
  account may spend; "finishes on" is the last day;
* confirming records the plan; drafts are released at their time and their
  go-live set for the confirmed time; cancelling withdraws what has not happened;
* the listing allowance is not touched by drafts in more shops;
* another account gets 404.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.core import allowance
from app.db.models import (
    DesignDistribution,
    GeneratedContent,
    Job,
    JobStatus,
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


def test_split_evenly_divides_in_order() -> None:
    ids = [uuid.uuid4() for _ in range(60)]
    groups = [uuid.uuid4() for _ in range(4)]
    out = G.split_evenly(ids, groups)
    assert [sum(1 for v in out.values() if v == g) for g in groups] == [15, 15, 15, 15]
    assert [out[i] for i in ids[:15]] == [groups[0]] * 15 and out[ids[59]] == groups[3]
    odd = G.split_evenly(ids[:7], groups[:3])
    assert [sum(1 for v in odd.values() if v == g) for g in groups[:3]] == [3, 2, 2]


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


async def test_split_evenly_sends_each_listing_to_every_shop_of_its_group_only(world) -> None:  # noqa: F811
    g = await _groups(world)
    res = await world["a"].post(f"/api/batches/{world['batch']}/distribution/preview",
                                json={"mode": "split", "group_ids": [g["A"], g["B"]]})
    assert res.status_code == 200, res.text
    rows = res.json()["rows"]
    assert [r["group_name"] for r in rows] == ["A", "B"]  # in order, one each
    assert [c["shop_name"] for c in rows[0]["shops"]] == ["Frost Tees"]
    assert sorted(c["shop_name"] for c in rows[1]["shops"]) == ["Frost Caps", "Frost Mugs"]
    assert all(c["ok"] for r in rows for c in r["shops"])

    body = {"assignments": [{"content_id": r["content_id"], "group_id": r["group_id"]} for r in rows], "schedule": SCHEDULE}
    plan = (await world["a"].post(f"/api/batches/{world['batch']}/distribution/plan", json=body)).json()
    assert plan["drafts"] == 3 and plan["finishes_on"] == TOMORROW.isoformat()
    shops = {c["shop_name"]: c for c in plan["shops"]}
    assert shops["Frost Mugs"]["days"][0]["slots"][0]["time"] == "09:00"
    assert shops["Frost Caps"]["days"][0]["slots"][0]["time"] == "09:07"  # staggered within group B
    assert all(day["requests"] <= day["capacity"] for day in plan["budget"])
    async with world["sm"]() as s:  # a preview queues nothing
        assert (await s.execute(select(PlannedSlot))).first() is None


async def test_confirming_releases_drafts_at_their_time_and_sets_the_go_live(world) -> None:  # noqa: F811
    g = await _groups(world)
    c0, c1 = world["contents"]
    body = {"assignments": [{"content_id": str(c0), "group_id": g["B"]}], "schedule": SCHEDULE}
    res = await world["a"].post(f"/api/batches/{world['batch']}/distribution/confirm", json=body)
    assert res.status_code == 200, res.text
    async with world["sm"]() as s:
        slots = list((await s.execute(select(PlannedSlot))).scalars())
        assert {x.state for x in slots} == {"waiting"} and len(slots) == 2  # Mugs and Caps
        assert (await s.execute(select(DesignDistribution))).scalar_one().group_name == "B"
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

    # Sending the same design to group A later is allowed, with a warning.
    res = await world["a"].post(f"/api/batches/{world['batch']}/distribution/preview",
                                json={"mode": "assign", "group_ids": [g["A"]], "content_ids": [str(c0)]})
    assert res.status_code == 200 and "sent to B before" in res.json()["rows"][0]["warning"]


async def test_cancelling_a_plan_withdraws_what_has_not_happened(world) -> None:  # noqa: F811
    g = await _groups(world)
    body = {"assignments": [{"content_id": str(world["contents"][0]), "group_id": g["B"]}], "schedule": SCHEDULE}
    plan_id = (await world["a"].post(f"/api/batches/{world['batch']}/distribution/confirm", json=body)).json()["plan_id"]
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
    body = {"assignments": [{"content_id": str(c), "group_id": g["B"]} for c in world["contents"]], "schedule": SCHEDULE}
    assert (await world["a"].post(f"/api/batches/{world['batch']}/distribution/confirm", json=body)).status_code == 200
    async with world["sm"]() as s:
        await plans.release_due(s, lambda *a: _noop(), now=datetime.now(timezone.utc) + timedelta(days=2))
        after = await allowance.status(s, await s.get(Tenant, world["alice"]))
    assert after.used == before.used


async def _noop() -> None:
    return None


async def test_an_unapproved_listing_is_never_planned(world) -> None:  # noqa: F811
    g = await _groups(world)
    async with world["sm"]() as s:
        (await s.get(GeneratedContent, world["contents"][1])).approved = False
        await s.commit()
    res = await world["a"].post(f"/api/batches/{world['batch']}/distribution/preview",
                                json={"mode": "split", "group_ids": [g["A"], g["B"]]})
    body = res.json()
    assert len(body["rows"]) == 1 and body["left_out"][0]["reason"] == "not approved yet"
    res = await world["a"].post(f"/api/batches/{world['batch']}/distribution/plan", json={
        "assignments": [{"content_id": str(world["contents"][1]), "group_id": g["A"]}], "schedule": SCHEDULE})
    assert res.status_code == 422 and "not approved" in res.json()["detail"]


async def test_another_account_gets_404(world) -> None:  # noqa: F811
    g = await _groups(world)
    body = {"assignments": [{"content_id": str(world["contents"][0]), "group_id": g["B"]}], "schedule": SCHEDULE}
    plan_id = (await world["a"].post(f"/api/batches/{world['batch']}/distribution/confirm", json=body)).json()["plan_id"]
    b = world["b"]
    for method, path, payload in [
        ("POST", f"/api/batches/{world['batch']}/distribution/preview", {"mode": "split", "group_ids": [g["A"]]}),
        ("POST", f"/api/batches/{world['batch']}/distribution/plan", body),
        ("POST", f"/api/batches/{world['batch']}/distribution/confirm", body),
        ("DELETE", f"/api/plans/{plan_id}", None),
    ]:
        res = await b.request(method, path, json=payload)
        assert res.status_code == 404, f"{method} {path} -> {res.status_code}"
    assert (await b.get("/api/plans")).json() == []
    # Bob's own batch cannot use Alice's group.
    bob_batch = (await b.post("/api/batches")).json()["id"]
    res = await b.post(f"/api/batches/{bob_batch}/distribution/preview", json={"mode": "split", "group_ids": [g["A"]]})
    assert res.status_code == 404
