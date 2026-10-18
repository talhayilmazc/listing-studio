"""Each design adjusted on each mockup, its look, recoloured parts and "Apply this change to…"
(seller feedback, Part B; pipeline/design_edits.py, pipeline/mockups.py, workers/mockups.py,
api/mockup_edits.py). Synthetic images only; nothing is sent anywhere.

* every adjustment is stored and rendered; proportional scale; free corners; the look's ranges;
* the three apply scopes are relative changes, and undo puts every target back;
* recolouring keeps alpha and edges and never changes the uploaded file;
* the editor's preview and the render agree (tests/fixtures/design_edit_cases.json, also read by
  frontend lib/designEdits.test.ts; and the rendered design lands where the points say);
* another account sees and changes nothing.
"""

from __future__ import annotations

import io
import json
import uuid
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import select

from app.db.models import MockupAdjustment, MockupDesign, MockupOutput, MockupRecolour, MockupRun
from app.pipeline import design_edits as E
from app.pipeline import mockups as M
from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_mockups import _new_batch, mw  # noqa: F401  (fixture)
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_print_geometry import _prepared, _template

CASES = json.loads((Path(__file__).parent / "fixtures" / "design_edit_cases.json").read_text())


# --- the rules (and the editor's copy of them) ----------------------------------------------------


def test_the_shared_cases_are_what_the_code_gives() -> None:
    """The fixture the editor is tested against is the server's own arithmetic."""
    for c in CASES["points"]:
        pts = [tuple(p) for p in c["quad"]]
        unit = E.unit_points(pts, tuple(c["design"]), c["size"], c["adjust"])
        assert [[round(u, 6), round(v, 6)] for u, v in unit] == c["unit"], c["name"]
    for c in CASES["looks"]:
        d = np.array(c["pixels"], np.uint8).reshape(10, 14, 4)
        assert E.look_rgba(d, c["adjust"]).reshape(-1).tolist() == c["out"], c["name"]
    for c in CASES["regions"]:
        d = np.array(c["pixels"], np.uint8).reshape(c["height"], c["width"], 4)
        assert [int(i) for i in np.flatnonzero(E.region_mask(d, c["region"]))] == c["mask"], c["name"]
        assert E.recolour(d, [c["region"]]).reshape(-1).tolist() == c["recoloured"], c["name"]
    for c in CASES["compose"]:
        assert E.compose(c["target"], E.delta(c["before"], c["after"])) == c["result"], c["name"]


def test_no_adjustment_is_exactly_the_automatic_fit() -> None:
    quad = [(180.0, 210.0), (620.0, 190.0), (640.0, 520.0), (170.0, 500.0)]
    auto = M.place(quad, (640, 480), 0.95)
    via = M.placement(quad, (640, 480), 0.95, None)
    assert via == auto
    pts = E.photo_points(quad, (640, 480), 0.95, None)
    sw, sh = auto.scaled
    for (x, y), (px, py) in zip(((0, 0), (sw, 0), (sw, sh), (0, sh)), pts):
        ax, ay = M.apply(auto.matrix, x, y)
        assert abs(ax - px) < 1e-6 and abs(ay - py) < 1e-6


def test_the_look_stays_in_small_ranges_and_reset_is_nothing_stored() -> None:
    cleaned = E.clean({"opacity": 0.0, "brightness": 1.0, "contrast": -1.0, "scale": 99, "angle": 400})
    assert cleaned["opacity"] == 0.3 and cleaned["brightness"] == 0.25 and cleaned["contrast"] == -0.25
    assert cleaned["scale"] == 4.0 and cleaned["angle"] == 180.0
    assert E.clean({"dx": 0, "scale": 1, "opacity": 1, "corners": [[0, 0]] * 4}) is None
    with pytest.raises(E.EditError):
        E.clean({"corners": [[0, 0]]})
    with pytest.raises(E.EditError):
        E.clean_regions([{"kind": "rect", "points": [[0, 0], [1, 1]], "colour": "red"}])


# --- rendering with an adjustment ------------------------------------------------------------------


def _ink_box(jpeg: bytes, blank: bytes) -> tuple[float, float, float, float, float]:
    """Where the design was printed: the box of pixels that differ from the blank, and how many."""
    a = np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB")).astype(int)
    b = np.asarray(Image.open(io.BytesIO(blank)).convert("RGB")).astype(int)
    diff = np.abs(a - b).max(axis=-1) > 40
    ys, xs = np.nonzero(diff)
    return xs.min(), ys.min(), xs.max(), ys.max(), float(diff.sum())


def _maps_and_design():
    blank = M.prepare_blank(samples.blank((238, 238, 236), size=(800, 900)), runner=None)
    area = [[0.36, 0.25], [0.64, 0.25], [0.64, 0.55], [0.36, 0.55]]
    maps = M.build_maps(blank.image, blank.mask, None, area,
                        design_reach_quads=[[[0.05, 0.05], [0.95, 0.05], [0.95, 0.95], [0.05, 0.95]]])
    return blank, maps, area, M.load_design(samples.design("opaque", size=(600, 600)))


def test_each_adjustment_moves_scales_and_bends_the_print_where_the_points_say() -> None:
    blank, maps, area, design = _maps_and_design()
    W, H = maps.width, maps.height
    px = [(x * W, y * H) for x, y in area]

    def render(adj):  # noqa: ANN001, ANN202
        return _ink_box(M.render(maps, design, M.Settings(area, 1.0, 0.0, True, adj)), blank.image)

    base = render(None)
    moved = render({"dx": 0.2})
    assert abs((moved[0] - base[0]) - 0.2 * (px[1][0] - px[0][0])) < 4  # moved by 20% of the area's width
    half = render({"scale": 0.5})  # proportional: both sides halve
    assert abs((half[2] - half[0]) / (base[2] - base[0]) - 0.5) < 0.03
    assert abs((half[3] - half[1]) / (base[3] - base[1]) - 0.5) < 0.03
    wide = render({"sx": 1.5})  # one side only
    assert abs((wide[2] - wide[0]) / (base[2] - base[0]) - 1.5) < 0.03 and abs((wide[3] - wide[1]) - (base[3] - base[1])) < 4
    corner = {"corners": [[0.0, 0.0], [0.15, -0.1], [0.0, 0.0], [0.0, 0.0]]}  # the top right corner pulled out
    bent = render(corner)
    pts = E.photo_points(px, (design.width, design.height), 1.0, corner)
    assert abs(bent[2] - max(p[0] for p in pts)) < 4 and abs(bent[1] - min(p[1] for p in pts)) < 4
    # The preview's corners (the same points) are where the render printed.
    for adj in ({"dx": -0.1, "dy": 0.1}, {"angle": 20}, {"scale": 1.3, "sy": 0.8}):
        box = render(adj)
        pts = E.photo_points(px, (design.width, design.height), 1.0, adj)
        assert abs(box[0] - min(p[0] for p in pts)) < 4 and abs(box[2] - max(p[0] for p in pts)) < 4, adj
        assert abs(box[1] - min(p[1] for p in pts)) < 4 and abs(box[3] - max(p[1] for p in pts)) < 4, adj


def test_the_look_changes_the_print_only_within_its_ranges() -> None:
    blank, maps, area, design = _maps_and_design()
    plain = np.asarray(Image.open(io.BytesIO(M.render(maps, design, M.Settings(area, 1.0, 0.0)))).convert("RGB")).astype(int)
    faint = np.asarray(Image.open(io.BytesIO(M.render(maps, design, M.Settings(area, 1.0, 0.0, True, {"opacity": 0.0})))).convert("RGB")).astype(int)
    bare = np.asarray(Image.open(io.BytesIO(blank.image)).convert("RGB")).astype(int)
    h, w = plain.shape[:2]
    win = (slice(int(0.3 * h), int(0.4 * h)), slice(int(0.42 * w), int(0.58 * w)))
    # Opacity is clamped at 0.3: still visibly printed, but closer to the fabric.
    d_plain = np.abs(plain[win] - bare[win]).mean()
    d_faint = np.abs(faint[win] - bare[win]).mean()
    assert 0.2 * d_plain < d_faint < 0.45 * d_plain


# --- through the API and the worker -----------------------------------------------------------------


async def _run_with_two_designs(mw) -> dict:  # noqa: F811
    t = await _template(mw, "a", "Edits")
    photos = []
    for name, colour in (("white", (238, 238, 236)), ("sand", (220, 200, 160))):
        photo = await _prepared(mw, "a", t, name, samples.blank(colour, size=(700, 800)))
        a = photo["areas"][0]
        res = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": [
            {"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default"}]})
        assert res.status_code == 200, res.text
        photos.append(res.json())
    batch = await _new_batch(mw)
    designs = []
    for name in ("BR100.png", "BR200.png"):
        res = await mw["a"].post(f"/api/batches/{batch}/designs", files={"file": (name, samples.design("transparent"), "image/png")})
        assert res.status_code == 201, res.text
        designs.append(res.json())
    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [t]})
    assert res.status_code == 202, res.text
    run_id = uuid.UUID(res.json()["id"])
    async with mw["sm"]() as s:
        assert (await work.run(s, mw["storage"], run_id)).status == "succeeded"
    return {"template": t, "photos": photos, "designs": designs, "run": run_id, "batch": batch}


async def _outputs(mw) -> dict:  # noqa: F811
    async with mw["sm"]() as s:
        return {(str(o.design_id), str(o.photo_id)): (o.id, o.key) for o in (await s.execute(select(MockupOutput))).scalars()}


async def test_an_adjustment_is_stored_and_only_that_mockup_is_made_again(mw) -> None:  # noqa: F811
    r = await _run_with_two_designs(mw)
    d0, d1 = r["designs"][0]["id"], r["designs"][1]["id"]
    p0, p1 = r["photos"][0]["id"], r["photos"][1]["id"]
    edits = (await mw["a"].get(f"/api/mockup-runs/{r['run']}/edits")).json()
    assert edits["editable"] and len(edits["photos"]) == 2 and len(edits["designs"]) == 2 and edits["adjustments"] == []
    assert edits["designs"][0]["files"][0]["trim"] is not None  # the editor fits the trimmed design, like the engine
    put = await mw["a"].put("/api/mockup-adjustments", json={"design_id": d0, "photo_id": p0, "area": 0,
                                                             "data": {"dx": 0.1, "scale": 0.8, "opacity": 0.9}})
    assert put.status_code == 200 and put.json()["data"]["scale"] == 0.8
    before = await _outputs(mw)
    res = await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0]]})
    assert res.status_code == 202 and mw["queue"].calls[-1][0] == "rerender_mockups"
    async with mw["sm"]() as s:
        done = await work.run(s, mw["storage"], r["run"], refresh=[(uuid.UUID(d0), uuid.UUID(p0))])
        assert done.status == "succeeded"
    after = await _outputs(mw)
    assert after[(d0, p0)][0] == before[(d0, p0)][0] and after[(d0, p0)][1] != before[(d0, p0)][1]  # same record, new file
    assert not mw["storage"].exists(before[(d0, p0)][1])  # the old file is gone
    for other in ((d0, p1), (d1, p0), (d1, p1)):
        assert after[other] == before[other]  # nothing else changed
    out = (await mw["a"].get(f"/api/mockup-runs/{r['run']}")).json()
    stamps = {o["id"]: o["stamp"] for d in out["designs"] for o in d["outputs"]}
    assert stamps[str(after[(d0, p0)][0])] and stamps[str(after[(d0, p0)][0])] in after[(d0, p0)][1]
    # Reset: back to the automatic fit, nothing stored.
    await mw["a"].put("/api/mockup-adjustments", json={"design_id": d0, "photo_id": p0, "area": 0, "data": None})
    async with mw["sm"]() as s:
        assert (await s.execute(select(MockupAdjustment))).scalars().all() == []


async def test_apply_this_change_to_is_relative_and_can_be_undone(mw) -> None:  # noqa: F811
    r = await _run_with_two_designs(mw)
    d0, d1 = r["designs"][0]["id"], r["designs"][1]["id"]
    p0, p1 = r["photos"][0]["id"], r["photos"][1]["id"]
    # d1 on p1 already has its own adjustment; it must keep it under the change.
    await mw["a"].put("/api/mockup-adjustments", json={"design_id": d1, "photo_id": p1, "area": 0, "data": {"dx": -0.2, "scale": 0.5}})
    after = {"dx": 0.05, "scale": 1.2, "brightness": 0.1}
    await mw["a"].put("/api/mockup-adjustments", json={"design_id": d0, "photo_id": p0, "area": 0, "data": after})

    async def rows() -> dict:
        async with mw["sm"]() as s:
            return {(str(a.design_id), str(a.photo_id)): a.data for a in (await s.execute(select(MockupAdjustment))).scalars()}

    base = {"design_id": d0, "photo_id": p0, "area": 0, "run_id": str(r["run"]), "before": None, "after": after}
    # This design on all photos: only d0 elsewhere.
    one = (await mw["a"].post("/api/mockup-adjustments/apply", json={**base, "scope": "design_photos"})).json()
    got = await rows()
    assert one["changed"] == 1 and got[(d0, p1)]["dx"] == 0.05 and got[(d0, p1)]["scale"] == 1.2
    assert (d1, p0) not in got
    await mw["a"].post("/api/mockup-adjustments/restore", json={"rows": one["undo"]})
    assert (d0, p1) not in await rows()
    # All designs on all photos of this template: relative on top of each one's own.
    every = (await mw["a"].post("/api/mockup-adjustments/apply", json={**base, "scope": "template_all"})).json()
    got = await rows()
    assert every["changed"] == 3
    assert abs(got[(d1, p1)]["dx"] - (-0.15)) < 1e-6 and abs(got[(d1, p1)]["scale"] - 0.6) < 1e-6  # its own -0.2 / 0.5 kept
    assert abs(got[(d1, p1)]["brightness"] - 0.1) < 1e-6
    # Undo puts every target back exactly as it was.
    await mw["a"].post("/api/mockup-adjustments/restore", json={"rows": every["undo"]})
    got = await rows()
    assert set(got) == {(d0, p0), (d1, p1)} and got[(d1, p1)]["dx"] == -0.2 and got[(d1, p1)]["scale"] == 0.5
    # All designs on this photo.
    photo = (await mw["a"].post("/api/mockup-adjustments/apply", json={**base, "scope": "photo_designs"})).json()
    assert photo["changed"] == 1 and (await rows())[(d1, p0)]["dx"] == 0.05


async def test_recolouring_keeps_alpha_and_never_changes_the_file(mw) -> None:  # noqa: F811
    r = await _run_with_two_designs(mw)
    d0, p0 = r["designs"][0]["id"], r["photos"][0]["id"]
    async with mw["sm"]() as s:
        design = await s.get(MockupDesign, uuid.UUID(d0))
        key = design.design_key
    original = mw["storage"].get(key)
    # The whole design turned black (a rectangle over everything), for every subgroup.
    res = await mw["a"].put(f"/api/mockup-designs/{d0}/recolour", json={
        "subgroup": "", "regions": [{"kind": "rect", "points": [[0, 0], [1, 1]], "colour": "#000000"}]})
    assert res.status_code == 200 and res.json()["changed"] == 1
    before = await _outputs(mw)
    assert (await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0]]})).status_code == 202
    async with mw["sm"]() as s:
        assert (await work.run(s, mw["storage"], r["run"], refresh=[(uuid.UUID(d0), uuid.UUID(p0))])).status == "succeeded"
    after = await _outputs(mw)
    assert after[(d0, p0)][1] != before[(d0, p0)][1]
    assert mw["storage"].get(key) == original  # the uploaded file is never changed
    img = np.asarray(Image.open(io.BytesIO(mw["storage"].get(after[(d0, p0)][1]))).convert("RGB")).astype(int)
    old = np.asarray(Image.open(io.BytesIO(mw["storage"].get(before[(d1 := r['designs'][1]['id'], p0)][1]))).convert("RGB")).astype(int)
    h, w = img.shape[:2]
    win = img[int(0.30 * h):int(0.40 * h), int(0.45 * w):int(0.55 * w)]
    assert win.mean() < old[int(0.30 * h):int(0.40 * h), int(0.45 * w):int(0.55 * w)].mean() - 40  # printed dark now
    # The recoloured design keeps its transparency: the shape, not the box, prints.
    rgba = np.asarray(Image.open(io.BytesIO(original)).convert("RGBA"))
    recoloured = E.recolour(rgba, [{"kind": "rect", "points": [[0, 0], [1, 1]], "colour": "#000000"}])
    assert (recoloured[..., 3] == rgba[..., 3]).all() and (recoloured[rgba[..., 3] > 0, :3] == 0).all()
    # The file the editor previews on is the stored file, as uploaded.
    got = await mw["a"].get(f"/api/mockup-designs/{d0}/file")
    assert got.status_code == 200 and got.content == original
    # Recolour on all designs, then undo.
    every = (await mw["a"].put(f"/api/mockup-designs/{d0}/recolour", json={
        "subgroup": "White", "regions": [{"kind": "wand", "seed": [0.5, 0.3], "tolerance": 15, "colour": "#ffffff"}],
        "scope": "template_all"})).json()
    assert every["changed"] == 2
    await mw["a"].post("/api/mockup-recolours/restore", json={"rows": every["undo"]})
    async with mw["sm"]() as s:
        assert {(str(x.design_id), x.subgroup) for x in (await s.execute(select(MockupRecolour))).scalars()} == {(d0, "")}
    del d1


async def test_another_account_cannot_see_or_change_any_edit(mw) -> None:  # noqa: F811
    r = await _run_with_two_designs(mw)
    d0, p0 = r["designs"][0]["id"], r["photos"][0]["id"]
    b = mw["b"]
    place = {"design_id": d0, "photo_id": p0, "area": 0}
    assert (await b.get(f"/api/mockup-runs/{r['run']}/edits")).status_code == 404
    assert (await b.put("/api/mockup-adjustments", json={**place, "data": {"dx": 0.1}})).status_code == 404
    assert (await b.post("/api/mockup-adjustments/apply", json={**place, "run_id": str(r["run"]), "after": {"dx": 0.1},
                                                                 "scope": "template_all"})).status_code == 404
    assert (await b.post("/api/mockup-adjustments/restore", json={"rows": [{**place, "data": {"dx": 0.3}}]})).status_code == 404
    assert (await b.put(f"/api/mockup-designs/{d0}/recolour", json={"regions": []})).status_code == 404
    assert (await b.post(f"/api/mockup-runs/{r['run']}/rerender", json={"pairs": [[d0, p0]]})).status_code == 404
    assert (await b.get(f"/api/mockup-designs/{d0}/file")).status_code == 404
    async with mw["sm"]() as s:
        assert (await s.execute(select(MockupAdjustment))).scalars().all() == []
        assert (await s.get(MockupRun, r["run"])).status == "succeeded"
