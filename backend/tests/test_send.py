"""Where and when (B4, and the owner's change of 2026-10-22: no splitting).

* every approved listing goes to every chosen shop: 6 listings and 2 shops are 12 drafts;
* two chosen groups that share a shop give that shop one draft per listing, not two;
* one listing can go elsewhere ("→ GROUP 1 · change"), the rest follow the batch;
* a listing already scheduled in a shop keeps its slot (an existing split schedule is not
  touched, re-sent or doubled), and asking twice queues nothing twice;
* the sentence and the button count the real total, for drafts, publish now and spread out;
* spread out: a shop's daily limit and minutes between are kept, and one listing never goes
  live in two shops in the same minute;
* "Make a group of all my shops" is one click; a one-shop seller is offered no groups;
* a new batch starts from where the last one was sent;
* another account gets 404.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import (
    Asset,
    AssetStatus,
    GeneratedContent,
    GroupPlan,
    PlannedSlot,
    UploadBatch,
)
from tests.test_multi_shop import BODY, PREFIX_A, world  # noqa: F401  (fixture)

TOMORROW = (datetime.now(timezone.utc) + timedelta(days=1)).date()
SPREAD = {"start_date": TOMORROW.isoformat(), "per_shop_per_day": 2, "window_start": "09:00", "window_end": "17:00",
          "spacing_minutes": 30, "stagger_minutes": 5}


async def _six(world) -> list:  # noqa: F811
    """Four more approved listings in Alice's batch: six in all, each with profile pa1 (main shop Frost Tees)."""
    async with world["sm"]() as s:
        for i in range(2, 6):
            asset = Asset(tenant_id=world["alice"], batch_id=world["batch"], original_filename=f"d{i}.png",
                          storage_key=f"k{i}", status=AssetStatus.processed, rank=i + 1)
            s.add(asset)
            await s.flush()
            s.add(GeneratedContent(tenant_id=world["alice"], batch_id=world["batch"], asset_id=asset.id,
                                   listing_profile_id=world["pa1"], title=f"{PREFIX_A}, {BODY}",
                                   tags=["shirt", *[f"tag{n}" for n in range(12)]], description="Body.", approved=True))
        await s.commit()
        return list((await s.execute(select(GeneratedContent.id).where(GeneratedContent.batch_id == world["batch"]))).scalars())


async def _group(world, name: str, *shops) -> str:  # noqa: F811
    res = await world["a"].post("/api/shop-groups", json={"name": name, "connection_ids": [str(x) for x in shops]})
    assert res.status_code == 201, res.text
    return next(g["id"] for g in res.json()["groups"] if g["name"] == name)


async def _send_to(world, **dest) -> dict:  # noqa: F811
    res = await world["a"].put(f"/api/batches/{world['batch']}/send-to", json={"send_to": dest})
    assert res.status_code == 200, res.text
    return res.json()


async def _preview(world, when: str = "drafts", schedule: dict | None = None) -> dict:  # noqa: F811
    res = await world["a"].post(f"/api/batches/{world['batch']}/send/preview",
                                json={"when": when, **({"schedule": schedule} if schedule else {})})
    assert res.status_code == 200, res.text
    return res.json()


def _drafts(world) -> list[tuple]:  # noqa: F811
    return [c for c in world["queue"].calls if c[0] == "run_publish_job"]


async def test_six_listings_to_two_shops_are_twelve_drafts(world) -> None:  # noqa: F811
    await _six(world)
    both = await _group(world, "GROUP 1", world["a1"], world["a2"])
    state = await _send_to(world, group_ids=[both])
    assert state["label"] == "GROUP 1"
    preview = await _preview(world)
    assert (preview["listings"], preview["shops"], preview["total"]) == (6, 2, 12)
    assert preview["sentence"] == "6 listings → 2 shops: 12 drafts."
    assert preview["button"] == "Create 12 drafts" and preview["disabled"] is None
    assert all(len(r["cells"]) == 2 and r["label"] == "GROUP 1" and not r["custom"] for r in preview["rows"])
    assert "estimated_calls" not in preview and "requests" not in str(preview)  # no request counts for sellers (A3)

    res = await world["a"].post(f"/api/batches/{world['batch']}/send", json={"when": "drafts"})
    assert res.status_code == 200, res.text
    jobs = res.json()["jobs"]
    assert len(jobs) == 12 and len({(j["content_id"], j["connection_id"]) for j in jobs}) == 12
    assert len(_drafts(world)) == 12
    # Asked again: nothing is queued twice (the same unfinished jobs come back).
    again = await world["a"].post(f"/api/batches/{world['batch']}/send", json={"when": "drafts"})
    assert {j["job_id"] for j in again.json()["jobs"]} == {j["job_id"] for j in jobs}
    assert len(_drafts(world)) == 12


async def test_two_groups_sharing_a_shop_give_it_one_draft_per_listing(world) -> None:  # noqa: F811
    await _six(world)
    a = await _group(world, "A", world["a1"])
    b = await _group(world, "B", world["a1"], world["a2"])
    assert (await _send_to(world, group_ids=[a, b]))["label"] == "A + B"
    preview = await _preview(world)
    assert (preview["shops"], preview["total"]) == (2, 12)  # not 18: Frost Tees once
    for row in preview["rows"]:
        assert Counter(c["shop_name"] for c in row["cells"]) == {"Frost Tees": 1, "Frost Mugs": 1}


async def test_one_listing_can_go_elsewhere_and_the_rest_follow_the_batch(world) -> None:  # noqa: F811
    ids = await _six(world)
    both = await _group(world, "GROUP 1", world["a1"], world["a2"])
    await _send_to(world, group_ids=[both])
    res = await world["a"].put(f"/api/batches/{world['batch']}/send-to",
                               json={"send_to": {"connection_ids": [str(world["a1"])]}, "content_ids": [str(ids[0])]})
    assert res.status_code == 200
    preview = await _preview(world)
    rows = {r["content_id"]: r for r in preview["rows"]}
    mine = rows[str(ids[0])]
    assert (mine["custom"], mine["label"], [c["shop_name"] for c in mine["cells"]]) == (True, "Frost Tees", ["Frost Tees"])
    assert sum(1 for r in rows.values() if r["custom"]) == 1
    assert preview["total"] == 11 and preview["sentence"] == "6 listings → 2 shops: 11 drafts."
    # Back to the batch's choice.
    await world["a"].put(f"/api/batches/{world['batch']}/send-to", json={"send_to": None, "content_ids": [str(ids[0])]})
    assert (await _preview(world))["total"] == 12


async def test_an_existing_schedule_keeps_its_slot_and_is_not_sent_again(world) -> None:  # noqa: F811
    """A listing an old "Split evenly" plan scheduled in a shop keeps that time; sending the
    batch everywhere adds only the shops it was not scheduled in."""
    c0, c1 = world["contents"]
    old_time = datetime.now(timezone.utc) + timedelta(days=3)
    async with world["sm"]() as s:
        plan = GroupPlan(tenant_id=world["alice"], batch_id=world["batch"], settings={"groups": {"g:x": "B"}})
        s.add(plan)
        await s.flush()
        s.add(PlannedSlot(tenant_id=world["alice"], plan_id=plan.id, content_id=c0, connection_id=world["a2"],
                          draft_at=old_time - timedelta(hours=2), publish_at=old_time))
        await s.commit()
    both = await _group(world, "GROUP 1", world["a1"], world["a2"])
    await _send_to(world, group_ids=[both])
    preview = await _preview(world, "spread", SPREAD)
    cell = next(c for r in preview["rows"] if r["content_id"] == str(c0) for c in r["cells"] if c["shop_id"] == str(world["a2"]))
    assert cell["state"] == "scheduled" and cell["local"]
    assert preview["total"] == 3 and preview["notes"][0] == "1 already there (a draft, live or scheduled): not sent again."
    res = await world["a"].post(f"/api/batches/{world['batch']}/send", json={"when": "spread", "schedule": SPREAD})
    assert res.status_code == 200, res.text
    async with world["sm"]() as s:
        slots = list((await s.execute(select(PlannedSlot))).scalars())
    assert len(slots) == 4  # the old one + 3 new: (c0, Mugs) not doubled
    assert Counter((x.content_id, x.connection_id) for x in slots).most_common(1)[0][1] == 1
    old = next(x for x in slots if x.plan_id == plan.id)
    assert abs((old.publish_at.replace(tzinfo=timezone.utc) - old_time).total_seconds()) < 1 and old.state == "waiting"


async def test_spread_out_keeps_the_daily_limit_and_never_the_same_minute(world) -> None:  # noqa: F811
    await _six(world)
    both = await _group(world, "GROUP 1", world["a1"], world["a2"])
    await _send_to(world, group_ids=[both])
    preview = await _preview(world, "spread", SPREAD)
    assert preview["total"] == 12
    day = TOMORROW
    assert preview["button"] == f"Schedule 12 listings from {day:%b} {day.day}"
    assert preview["sentence"].startswith("6 listings → 2 shops: 12 listings, ")
    times = [(r["content_id"], c["shop_id"], c["at"]) for r in preview["rows"] for c in r["cells"]]
    assert all(t for _, _, t in times)
    for row in preview["rows"]:
        assert len({c["at"] for c in row["cells"]}) == 2  # never the same minute in two shops
    per_day = Counter((shop, at[:10]) for _, shop, at in times)
    assert max(per_day.values()) <= 2  # two a day per shop
    res = await world["a"].post(f"/api/batches/{world['batch']}/send", json={"when": "spread", "schedule": SPREAD})
    assert res.status_code == 200 and res.json()["scheduled"] == 12
    assert not _drafts(world)  # nothing due yet


async def test_publish_now_makes_the_drafts_at_once_and_says_so(world) -> None:  # noqa: F811
    both = await _group(world, "GROUP 1", world["a1"], world["a2"])
    await _send_to(world, group_ids=[both])
    preview = await _preview(world, "publish")
    assert preview["button"] == "Publish 4 listings" and preview["sentence"] == "2 listings → 2 shops: 4 listings go live now."
    res = await world["a"].post(f"/api/batches/{world['batch']}/send", json={"when": "publish"})
    assert res.status_code == 200, res.text
    assert res.json()["scheduled"] == 4 and "go live in the next few minutes" in res.json()["message"]
    assert len(_drafts(world)) == 4  # each draft at once; its go-live follows it (workers/plans.after_draft)


async def test_nothing_approved_says_so(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        for cid in world["contents"]:
            (await s.get(GeneratedContent, cid)).approved = False
        await s.commit()
    preview = await _preview(world)
    assert preview["disabled"] == "Approve listings first." and len(preview["left_out"]) == 2
    res = await world["a"].post(f"/api/batches/{world['batch']}/send", json={"when": "drafts"})
    assert res.status_code == 409


async def test_all_my_shops_is_one_click_and_made_once(world) -> None:  # noqa: F811
    state = (await world["a"].get(f"/api/batches/{world['batch']}/send")).json()
    assert state["offer_all_shops"] is True and state["send_to"] is None and state["label"] == "Frost Tees"
    res = await world["a"].post(f"/api/batches/{world['batch']}/send-to/all-shops")
    assert res.status_code == 200
    state = res.json()
    group = next(g for g in state["groups"] if g["name"] == "All shops")
    assert set(group["shop_ids"]) == {str(world["a1"]), str(world["a2"])}
    assert state["send_to"]["group_ids"] == [group["id"]] and state["label"] == "All shops"
    assert state["offer_all_shops"] is False
    again = (await world["a"].post(f"/api/batches/{world['batch']}/send-to/all-shops")).json()
    assert [g["name"] for g in again["groups"]] == ["All shops"]
    # A new batch starts from where the last one was sent.
    fresh = (await world["a"].post("/api/batches")).json()["id"]
    assert (await world["a"].get(f"/api/batches/{fresh}/send")).json()["label"] == "All shops"


async def test_a_one_shop_seller_is_offered_no_groups(world) -> None:  # noqa: F811
    batch = (await world["b"].post("/api/batches")).json()["id"]
    state = (await world["b"].get(f"/api/batches/{batch}/send")).json()
    assert state["offer_all_shops"] is False and state["groups"] == [] and len(state["shops"]) == 1
    assert (await world["b"].post(f"/api/batches/{batch}/send-to/all-shops")).status_code == 422


async def test_another_account_gets_404(world) -> None:  # noqa: F811
    both = await _group(world, "GROUP 1", world["a1"], world["a2"])
    b = world["b"]
    batch = world["batch"]
    for method, path, payload in [
        ("GET", f"/api/batches/{batch}/send", None),
        ("PUT", f"/api/batches/{batch}/send-to", {"send_to": {"group_ids": [both]}}),
        ("POST", f"/api/batches/{batch}/send-to/all-shops", None),
        ("POST", f"/api/batches/{batch}/send/preview", {"when": "drafts"}),
        ("POST", f"/api/batches/{batch}/send", {"when": "drafts"}),
    ]:
        res = await b.request(method, path, json=payload)
        assert res.status_code == 404, f"{method} {path} -> {res.status_code}"
    bob_batch = (await b.post("/api/batches")).json()["id"]
    for dest in ({"group_ids": [both]}, {"connection_ids": [str(world["a1"])]}):
        res = await b.put(f"/api/batches/{bob_batch}/send-to", json={"send_to": dest})
        assert res.status_code == 404
    async with world["sm"]() as s:
        assert (await s.get(UploadBatch, batch)).send_to is None
