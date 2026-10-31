"""Mockup results update live while the design editor stays open (seller feedback, Part A).

The cause of the bug: a seller saves one photo, then applies the change to all photos while the
first mockup is still being made; the second request was refused ("The mockups are still being
made.") so its mockups never changed, and the editor covered the results. Now every request joins
the run's queue (pipeline/mockup_refresh.py) and one worker job takes it in turns:

* a request while a re-render is under way is queued, never refused, and starts no second job;
* every queued pair is made, in turns, including one asked for again during its own turn;
* the run says which mockups are being made again (with the version they had) and which failed;
* a mockup's image URL carries its version: a versioned URL is kept by the browser, any other is
  checked again, and the version changes when the mockup is made again;
* undo and Reset reach the mockups the same way; a failed re-render is reported and can be retried.
Synthetic images only; no Etsy call, no AI call.
"""

from __future__ import annotations

import uuid

from app.pipeline import mockup_refresh
from app.pipeline import mockups as M
from app.workers import mockups as work
from tests.test_design_edits import _outputs, _run_with_two_designs
from tests.test_mockups import mw  # noqa: F401  (fixture)
from tests.test_multi_shop import world  # noqa: F401  (fixture)


def _ctx(mw) -> dict:  # noqa: F811
    return {"sessionmaker": mw["sm"], "redis": mw["redis"], "enqueue": mw["queue"].enqueue}


async def _run_json(mw, run_id) -> dict:  # noqa: F811
    res = await mw["a"].get(f"/api/mockup-runs/{run_id}")
    assert res.status_code == 200, res.text
    return res.json()


def _pairs(rows: list[dict]) -> set[tuple[str, str]]:
    return {(r["design_id"], r["photo_id"]) for r in rows}


async def test_a_second_request_joins_the_queue_and_every_mockup_is_made_again(mw) -> None:  # noqa: F811
    r = await _run_with_two_designs(mw)
    d0, d1 = r["designs"][0]["id"], r["designs"][1]["id"]
    p0, p1 = r["photos"][0]["id"], r["photos"][1]["id"]
    before = await _outputs(mw)
    await mw["a"].put("/api/mockup-adjustments", json={"design_id": d0, "photo_id": p0, "area": 0, "data": {"opacity": 0.5}})
    first = await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0]]})
    assert first.status_code == 202, first.text
    body = first.json()
    assert _pairs(body["refreshing"]) == {(d0, p0)}
    # Each queued mockup carries the version it had: its tile updates when that changes.
    stamps = {o["photo_id"]: o["stamp"] for d in body["designs"] if d["design_id"] == d0 for o in d["outputs"]}
    assert body["refreshing"][0]["from_stamp"] == stamps[p0] != ""
    jobs = [c for c in mw["queue"].calls if c[0] == "rerender_mockups"]
    assert len(jobs) == 1
    # "This design on all photos" while the first is still being made: queued, not refused.
    second = await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0], [d0, p1], [d1, p1]]})
    assert second.status_code == 202, second.text
    assert _pairs(second.json()["refreshing"]) == {(d0, p0), (d0, p1), (d1, p1)}
    assert len([c for c in mw["queue"].calls if c[0] == "rerender_mockups"]) == 1  # the job under way takes them
    # The one worker job makes them all, then lets go of the run.
    assert await work.rerender_mockups(_ctx(mw), str(r["run"])) == "succeeded"
    after = await _outputs(mw)
    for pair in ((d0, p0), (d0, p1), (d1, p1)):
        assert after[pair][0] == before[pair][0] and after[pair][1] != before[pair][1], pair  # same record, new file
    assert after[(d1, p0)] == before[(d1, p0)]  # not asked for: unchanged
    done = await _run_json(mw, r["run"])
    assert done["refreshing"] == [] and done["refresh_failed"] == [] and done["status"] == "succeeded"
    assert not await mockup_refresh.claimed(mw["redis"], r["run"])
    # The next request starts a new job (nobody owns the run any more).
    await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d1, p0]]})
    assert len([c for c in mw["queue"].calls if c[0] == "rerender_mockups"]) == 2


async def test_a_mockup_asked_for_again_during_its_own_turn_is_made_once_more(mw, monkeypatch) -> None:  # noqa: F811
    r = await _run_with_two_designs(mw)
    d0, p0 = r["designs"][0]["id"], r["photos"][0]["id"]
    pair = (uuid.UUID(d0), uuid.UUID(p0))
    await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0]]})
    real = work.run
    turns: list[list] = []

    async def during(session, storage, run_id, refresh=None):  # noqa: ANN001, ANN202
        turns.append(list(refresh or []))
        if len(turns) == 1:  # the seller changes it again while this turn is being made
            await mw["a"].put("/api/mockup-adjustments", json={"design_id": d0, "photo_id": p0, "area": 0, "data": {"dx": 0.1}})
            res = await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0]]})
            assert res.status_code == 202
        return await real(session, storage, run_id, refresh=refresh)

    monkeypatch.setattr(work, "run", during)
    await work.rerender_mockups(_ctx(mw), str(r["run"]))
    assert turns == [[pair], [pair]]  # the newer edit is made too, in the next turn
    assert (await _run_json(mw, r["run"]))["refreshing"] == []


async def test_the_first_making_of_a_run_is_still_waited_for(mw) -> None:  # noqa: F811
    r = await _run_with_two_designs(mw)
    async with mw["sm"]() as s:
        from app.db.models import MockupRun

        run = await s.get(MockupRun, r["run"])
        run.status = "running"  # being made for the first time (no re-render owns it)
        await s.commit()
    d0, p0 = r["designs"][0]["id"], r["photos"][0]["id"]
    res = await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0]]})
    assert res.status_code == 409


async def test_a_failed_remake_is_reported_and_can_be_retried(mw, monkeypatch) -> None:  # noqa: F811
    r = await _run_with_two_designs(mw)
    d0, p0 = r["designs"][0]["id"], r["photos"][0]["id"]
    before = await _outputs(mw)
    real = M.render_many

    def broken(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise M.MockupError("the photo's file could not be read")

    monkeypatch.setattr(M, "render_many", broken)
    await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0]]})
    await work.rerender_mockups(_ctx(mw), str(r["run"]))
    out = await _run_json(mw, r["run"])
    assert out["refreshing"] == [] and _pairs(out["refresh_failed"]) == {(d0, p0)}
    assert "could not be read" in out["refresh_failed"][0]["reason"]
    assert out["status"] == "succeeded"  # the run's mockups are there; only this update failed
    assert (await _outputs(mw))[(d0, p0)] == before[(d0, p0)]  # the earlier file still shows
    # "Try again": the failure is cleared at once, and this time it is made.
    monkeypatch.setattr(M, "render_many", real)
    again = (await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0]]})).json()
    assert again["refresh_failed"] == [] and _pairs(again["refreshing"]) == {(d0, p0)}
    await work.rerender_mockups(_ctx(mw), str(r["run"]))
    out = await _run_json(mw, r["run"])
    assert out["refresh_failed"] == [] and (await _outputs(mw))[(d0, p0)][1] != before[(d0, p0)][1]


async def test_undo_and_reset_reach_the_mockups_too(mw) -> None:  # noqa: F811
    r = await _run_with_two_designs(mw)
    d0 = r["designs"][0]["id"]
    p0, p1 = r["photos"][0]["id"], r["photos"][1]["id"]
    await mw["a"].put("/api/mockup-adjustments", json={"design_id": d0, "photo_id": p0, "area": 0, "data": {"opacity": 0.6}})
    applied = (await mw["a"].post("/api/mockup-adjustments/apply", json={
        "run_id": str(r["run"]), "design_id": d0, "photo_id": p0, "area": 0, "before": None,
        "after": {"opacity": 0.6}, "scope": "design_photos"})).json()
    await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0], [d0, p1]]})
    await work.rerender_mockups(_ctx(mw), str(r["run"]))
    made = await _outputs(mw)
    # Undo: the other photo goes back, and is made again.
    assert (await mw["a"].post("/api/mockup-adjustments/restore", json={"rows": applied["undo"]})).status_code == 200
    res = (await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p1]]})).json()
    assert _pairs(res["refreshing"]) == {(d0, p1)}
    await work.rerender_mockups(_ctx(mw), str(r["run"]))
    undone = await _outputs(mw)
    assert undone[(d0, p1)][1] != made[(d0, p1)][1]
    # Reset: back to the automatic fit, made again.
    await mw["a"].put("/api/mockup-adjustments", json={"design_id": d0, "photo_id": p0, "area": 0, "data": None})
    await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0]]})
    await work.rerender_mockups(_ctx(mw), str(r["run"]))
    assert (await _outputs(mw))[(d0, p0)][1] != undone[(d0, p0)][1]
    assert (await _run_json(mw, r["run"]))["refreshing"] == []


async def test_image_urls_carry_the_version_and_a_new_version_after_a_remake(mw) -> None:  # noqa: F811
    r = await _run_with_two_designs(mw)
    d0, p0 = r["designs"][0]["id"], r["photos"][0]["id"]

    def output_of(run: dict) -> dict:
        return next(o for d in run["designs"] if d["design_id"] == d0 for o in d["outputs"] if o["photo_id"] == p0)

    old = output_of(await _run_json(mw, r["run"]))
    url = f"/api/mockup-runs/{r['run']}/outputs/{old['id']}/image"
    kept = await mw["a"].get(f"{url}?w=224&v={old['stamp']}")
    assert kept.status_code == 200 and "immutable" in kept.headers["cache-control"]
    assert (await mw["a"].get(f"{url}?w=224")).headers["cache-control"] == "private, no-cache"
    await mw["a"].put("/api/mockup-adjustments", json={"design_id": d0, "photo_id": p0, "area": 0, "data": {"scale": 0.7}})
    await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0]]})
    await work.rerender_mockups(_ctx(mw), str(r["run"]))
    new = output_of(await _run_json(mw, r["run"]))
    assert new["id"] == old["id"] and new["stamp"] and new["stamp"] != old["stamp"]  # same mockup, new URL
    stale = await mw["a"].get(f"{url}?w=224&v={old['stamp']}")
    assert stale.status_code == 200 and stale.headers["cache-control"] == "private, no-cache"  # never kept as new
    assert stale.content != kept.content


async def test_another_account_sees_nothing_of_the_queue(mw) -> None:  # noqa: F811
    r = await _run_with_two_designs(mw)
    d0, p0 = r["designs"][0]["id"], r["photos"][0]["id"]
    assert (await mw["b"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0]]})).status_code == 404
    assert (await mw["b"].get(f"/api/mockup-runs/{r['run']}")).status_code == 404
    assert (await mockup_refresh.state(mw["redis"], r["run"])) == ({}, {})
