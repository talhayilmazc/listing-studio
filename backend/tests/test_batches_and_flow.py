"""Batches and flow (seller feedback, Part D; pipeline/batch_status.py, api/batches.py, api/mockups.py,
workers/mockups.py, api/schedules.py). Synthetic rows and images only; nothing is sent anywhere.

* every batch status the app has, several per batch (the Batches page's filter);
* listings made from mockups are scheduled exactly like any other (the same approval rule);
* only the ticked designs get mockups, and the run's counts follow each design's subgroups;
* another account sees none of it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import (
    Asset,
    AssetStatus,
    DraftAttempt,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    MockupDesign,
    MockupOutput,
    UploadBatch,
    UploadBatchStatus,
)
from app.pipeline.batch_status import STATUSES, batch_statuses
from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_mockups import _new_batch, mw  # noqa: F401  (fixture)
from tests.test_multi_shop import PREFIX_A, BODY, world  # noqa: F401  (fixture)
from tests.test_print_geometry import _prepared, _template

NOW = datetime.now(timezone.utc)


async def _batch(s, tenant, status=UploadBatchStatus.ready) -> UploadBatch:  # noqa: ANN001
    b = UploadBatch(tenant_id=tenant, status=status, file_count=1)
    s.add(b)
    await s.flush()
    return b


async def _content(s, tenant, batch, profile, name: str) -> GeneratedContent:  # noqa: ANN001
    a = Asset(tenant_id=tenant, batch_id=batch.id, original_filename=f"{name}.png", storage_key=f"k-{name}",
              status=AssetStatus.processed, rank=1, group_key=name)
    s.add(a)
    await s.flush()
    c = GeneratedContent(tenant_id=tenant, batch_id=batch.id, asset_id=a.id, listing_profile_id=profile,
                         title=f"{PREFIX_A}, {BODY} {name}", tags=["shirt", *[f"t{i}" for i in range(12)]], description="d", approved=True)
    s.add(c)
    await s.flush()
    return c


async def test_every_status_a_batch_can_be_in_several_at_once(world) -> None:  # noqa: F811
    alice, shop, profile = world["alice"], world["a1"], world["pa1"]
    async with world["sm"]() as s:
        live = await _batch(s, alice)
        on = await _content(s, alice, live, profile, "L1")
        await _content(s, alice, live, profile, "L2")  # written, no draft: in review
        s.add(ListingPublication(tenant_id=alice, content_id=on.id, connection_id=shop, etsy_listing_id=1001, state="active"))
        later = await _batch(s, alice)
        sc = await _content(s, alice, later, profile, "S1")
        gone = await _content(s, alice, later, profile, "S2")
        s.add(ListingPublication(tenant_id=alice, content_id=sc.id, connection_id=shop, etsy_listing_id=1002, state="draft",
                                 scheduled_for=NOW + timedelta(days=1)))
        s.add(ListingPublication(tenant_id=alice, content_id=gone.id, connection_id=shop, etsy_listing_id=1003,
                                 state="deleted_on_etsy", deleted_on_etsy_at=NOW))
        draft = await _batch(s, alice)
        dc = await _content(s, alice, draft, profile, "D1")
        s.add(ListingPublication(tenant_id=alice, content_id=dc.id, connection_id=shop, etsy_listing_id=1004, state="draft"))
        empty = await _batch(s, alice)
        uploading = await _batch(s, alice, UploadBatchStatus.uploading)
        designs = await _batch(s, alice, UploadBatchStatus.uploading)
        s.add(MockupDesign(tenant_id=alice, batch_id=designs.id, group_key="BR1", original_filename="BR1.png",
                           design_key="k", width=600, height=600))
        broken = await _batch(s, alice, UploadBatchStatus.failed)
        half = await _batch(s, alice)
        hc = await _content(s, alice, half, profile, "H1")
        fc = await _content(s, alice, half, profile, "F1")
        s.add(DraftAttempt(tenant_id=alice, content_id=hc.id, connection_id=shop, etsy_listing_id=1005, title=hc.title))
        for c, err in ((hc, "Etsy refused it while setting sizes, prices and SKU (it answered 400)"),
                       (fc, "Processing profile is not set up in Frost Tees")):
            s.add(Job(tenant_id=alice, connection_id=shop, type=JobType.create_draft, status=JobStatus.failed, batch_id=half.id,
                      payload={"content_id": str(c.id)}, last_error=err, finished_at=NOW))
        await s.commit()
        got = await batch_statuses(s, alice, [live, later, draft, empty, uploading, designs, broken, half])
    assert got[live.id] == ["in_review", "published"]
    # (S2's draft was deleted on Etsy and it has no other: it is back in review.)
    assert got[later.id] == ["in_review", "scheduled", "deleted"]
    assert got[draft.id] == ["drafted"]
    assert got[empty.id] == ["not_written"]
    assert got[uploading.id] == ["uploading"]
    assert got[designs.id] == ["not_written"]  # a mockup batch with its designs in is not "uploading"
    assert got[broken.id] == ["failed"]
    assert set(got[half.id]) >= {"half_made", "failed", "in_review"}
    assert set().union(*got.values()) == set(STATUSES)  # every status the app has shows up somewhere
    # The list endpoint carries them; another account sees none of these batches.
    listed = {b["id"]: b["statuses"] for b in (await world["a"].get("/api/batches")).json()}
    assert listed[str(live.id)] == ["in_review", "published"]
    theirs = {x["id"] for x in (await world["b"].get("/api/batches")).json()}
    assert str(live.id) not in theirs and str(half.id) not in theirs


# --- mockups: only the ticked designs, counts per subgroup; scheduling ------------------------------


async def _confirmed_template(mw, name: str = "Tees") -> str:  # noqa: F811
    t = await _template(mw, "a", name)
    for photo_name, colour in (("white", (238, 238, 236)), ("sand", (220, 200, 160))):
        photo = await _prepared(mw, "a", t, photo_name, samples.blank(colour, size=(700, 800)))
        a = photo["areas"][0]
        res = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": [
            {"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default"}]})
        assert res.status_code == 200, res.text
    return t


async def test_only_the_ticked_designs_get_mockups_and_the_counts_follow(mw) -> None:  # noqa: F811
    t = await _confirmed_template(mw)
    batch = await _new_batch(mw)
    ids = []
    for name in ("BR100.png", "BR200.png", "BR300.png"):
        res = await mw["a"].post(f"/api/batches/{batch}/designs", files={"file": (name, samples.design("transparent"), "image/png")})
        ids.append(res.json()["id"])
    none = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [t], "design_ids": []})
    assert none.status_code == 409 and "Choose at least one design" in none.json()["detail"]
    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [t], "design_ids": ids[:2]})
    assert res.status_code == 202, res.text
    assert res.json()["total"] == 4  # 2 designs x 2 photos
    run_id = uuid.UUID(res.json()["id"])
    async with mw["sm"]() as s:
        assert (await work.run(s, mw["storage"], run_id)).status == "succeeded"
        made = {str(o.design_id) for o in (await s.execute(select(MockupOutput).where(MockupOutput.run_id == run_id))).scalars()}
    assert made == set(ids[:2])  # the unticked design gets none
    out = (await mw["a"].get(f"/api/mockup-runs/{run_id}")).json()
    assert {d["design_id"] for d in out["designs"]} == set(ids[:2]) and out["done"] == 4 and out["total"] == 4
    assert out["template_ids"] == [t]  # a run reopened from its notification shows its templates chosen


async def test_a_design_made_for_one_subgroup_counts_only_its_photos(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Split")
    tpl = next(x for x in (await mw["a"].get("/api/mockup-templates")).json() if x["id"] == t)
    white = tpl["subgroups"][0]["id"]
    await mw["a"].patch(f"/api/mockup-subgroups/{white}", json={"name": "White", "use_on": "light", "suffix": "-white"})
    black = (await mw["a"].post(f"/api/mockup-templates/{t}/subgroups", json={"name": "Black", "use_on": "dark", "suffix": "-black"})).json()["subgroups"][1]["id"]
    for name, colour, sub in (("w", (238, 238, 236), white), ("b", (35, 35, 38), black)):
        photo = await _prepared(mw, "a", t, name, samples.blank(colour, size=(700, 800)))
        a = photo["areas"][0]
        await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": [
            {"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default"}]})
        await mw["a"].patch(f"/api/mockup-photos/{photo['id']}", json={"subgroup_id": sub})
    batch = await _new_batch(mw)
    import json as _json

    slots = {"subgroups": _json.dumps([{"name": "White", "suffix": "-white", "use_on": "light"},
                                       {"name": "Black", "suffix": "-black", "use_on": "dark"}])}
    await mw["a"].post(f"/api/batches/{batch}/designs", data=slots, files={"file": ("GR1-white.png", samples.design("transparent"), "image/png")})
    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [t]})
    assert res.json()["total"] == 1  # one design x two photos, but it is made only for the white photos


async def test_listings_from_mockups_are_scheduled_like_any_other(mw) -> None:  # noqa: F811
    t = await _confirmed_template(mw, "Sched")
    batch = await _new_batch(mw)
    await mw["a"].post(f"/api/batches/{batch}/designs", files={"file": ("BR500.png", samples.design("transparent"), "image/png")})
    run = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [t]})
    run_id = uuid.UUID(run.json()["id"])
    async with mw["sm"]() as s:
        assert (await work.run(s, mw["storage"], run_id)).status == "succeeded"
    assert (await mw["a"].post(f"/api/mockup-runs/{run_id}/listings", json={})).status_code == 200
    # The listing written by the seller (no AI), its profile chosen, not yet approved.
    groups = (await mw["a"].get(f"/api/batches/{batch}/groups")).json()
    await mw["a"].put(f"/api/batches/{batch}/groups", json={"group_key": groups[0]["group_key"], "profile_id": str(mw["pa1"])})
    made = await mw["a"].post(f"/api/batches/{batch}/manual", json={"group_key": groups[0]["group_key"]})
    assert made.status_code in (200, 202), made.text
    async with mw["sm"]() as s:
        content = (await s.execute(select(GeneratedContent).where(GeneratedContent.batch_id == batch))).scalars().one()
        content.title = f"{PREFIX_A}, {BODY} BR500"
        content.tags = ["shirt", *[f"t{i}" for i in range(12)]]
        # Its draft exists (creating it calls Etsy, which tests never do).
        s.add(ListingPublication(tenant_id=content.tenant_id, content_id=content.id, connection_id=mw["a1"], etsy_listing_id=777001,
                                 state="draft"))
        await s.commit()
        content_id = content.id
    item = {"content_id": str(content_id), "connection_id": str(mw["a1"]), "run_at": (NOW + timedelta(days=2)).isoformat()}
    # Not approved: refused, the same rule as every listing.
    refused = (await mw["a"].post("/api/schedules", json={"items": [item]})).json()
    assert refused["scheduled"] == [] and refused["skipped"]
    async with mw["sm"]() as s:
        (await s.get(GeneratedContent, content_id)).approved = True
        await s.commit()
    ok = (await mw["a"].post("/api/schedules", json={"items": [item]})).json()
    assert len(ok["scheduled"]) == 1, ok
    async with mw["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == content_id))).scalars().one()
        assert pub.scheduled_for is not None
        assets = (await s.execute(select(Asset).where(Asset.batch_id == batch, Asset.mockup_design_id.is_not(None)))).scalars().all()
        assert assets  # the listing's photos are the mockups
    # Another account cannot schedule it.
    other = (await mw["b"].post("/api/schedules", json={"items": [item]})).json()
    assert other["scheduled"] == []
