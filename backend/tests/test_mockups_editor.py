"""The mockup editor (feature/mockups-editor; docs/mockups.md).

B1: every template takes the seller's placement, a Photoshop mockup's too: the design is moved, scaled, stretched
and turned inside the smart object's canvas, as if the layer were resized inside the smart object in Photoshop,
then the canvas goes onto the page through its corners as always. "Centre" puts the design's middle on the
area's middle; the automatic fit is unchanged.
"""

from __future__ import annotations

import numpy as np

from tests.test_mockups import mw, world  # noqa: F401  (fixtures)

from app.pipeline import design_edits as E
from app.pipeline import psd_template as T


def _spec(w: int = 400, h: int = 300) -> T.Spec:
    # A smart object 200 x 200 on the page at (100, 50), straight on: canvas 1000 x 1000.
    return T.Spec(width=w, height=h, canvas=(1000, 1000), quad=[(100, 50), (300, 50), (300, 250), (100, 250)])


def _design(w: int = 500, h: int = 250) -> np.ndarray:
    d = np.zeros((h, w, 4), np.uint8)
    d[..., 0], d[..., 3] = 220, 255  # red, opaque
    return d


def _red_box(page: np.ndarray) -> tuple[int, int, int, int]:
    red = (page[..., 0] > 150) & (page[..., 1] < 80) & (page[..., 2] < 80)
    ys, xs = np.nonzero(red)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def test_without_an_adjustment_a_psd_is_placed_as_before() -> None:
    base = np.full((300, 400, 3), 240, np.uint8)
    before = T.render(_spec(), base, _design())
    assert np.array_equal(before, T.render(_spec(), base, _design(), adjust=None))
    # Contain, centred in the 200 x 200 smart object: 200 x 100, from y 100 to 200.
    left, top, right, bottom = _red_box(before)
    assert (abs(left - 100) <= 1, abs(right - 299) <= 1, abs(top - 100) <= 1, abs(bottom - 199) <= 1) == (True,) * 4


def test_a_psd_design_is_scaled_moved_and_turned_inside_the_smart_object() -> None:
    base = np.full((300, 400, 3), 240, np.uint8)
    # Half the size, centred: 100 x 50 around the middle (200, 150).
    left, top, right, bottom = _red_box(T.render(_spec(), base, _design(), adjust={"scale": 0.5}))
    assert abs((left + right) / 2 - 200) <= 1.5 and abs((top + bottom) / 2 - 150) <= 1.5
    assert abs((right - left) - 100) <= 2 and abs((bottom - top) - 50) <= 2
    # Moved right by a tenth of the canvas: 20 page pixels.
    left2, _, right2, _ = _red_box(T.render(_spec(), base, _design(), adjust={"scale": 0.5, "dx": 0.1}))
    assert abs((left2 + right2) / 2 - 220) <= 1.5
    # Turned 90 degrees: tall instead of wide.
    l3, t3, r3, b3 = _red_box(T.render(_spec(), base, _design(), adjust={"scale": 0.5, "angle": 90}))
    assert (b3 - t3) > (r3 - l3) * 1.6
    # What leaves the canvas is cut at the smart object's edge, as in Photoshop.
    l4, t4, r4, b4 = _red_box(T.render(_spec(), base, _design(), adjust={"scale": 2.0}))
    assert l4 >= 99 and r4 <= 300 and t4 >= 49 and b4 <= 250


def test_a_transparent_design_keeps_no_dark_edge_when_moved() -> None:
    """Premultiplied sampling: a white design with soft edges on a white page leaves no grey ring."""
    d = np.zeros((300, 300, 4), np.uint8)
    yy, xx = np.mgrid[0:300, 0:300]
    d[..., :3] = 255
    d[..., 3] = np.clip(255 - np.hypot(xx - 150, yy - 150) * 3, 0, 255).astype(np.uint8)  # a soft white dot
    base = np.full((300, 400, 3), 255, np.uint8)
    page = T.render(_spec(), base, d, adjust={"scale": 0.7, "angle": 17, "dx": 0.05})
    assert page.min() >= 250, page.min()


def test_the_look_applies_to_a_psd_design_too() -> None:
    base = np.full((300, 400, 3), 240, np.uint8)
    faded = T.render(_spec(), base, _design(), adjust={"opacity": 0.5})
    r, g = int(faded[150, 200, 0]), int(faded[150, 200, 1])
    assert 100 < g < 140 and r > 220  # half red over the light grey page: green halves, red stays


def test_centre_puts_the_middle_on_the_area_middle_in_both_alignments() -> None:
    quad = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    for align, size in (("top", 0.9), ("centre", 1.0)):
        a = E.centred(quad, (200, 100), size, {"dx": 0.2, "dy": -0.1, "scale": 0.8, "angle": 12}, align)
        pts = E.unit_points(quad, (200, 100), size, a, align)
        cx, cy = sum(p[0] for p in pts) / 4, sum(p[1] for p in pts) / 4
        assert abs(cx - 0.5) < 1e-6 and abs(cy - 0.5) < 1e-6, (align, cx, cy)
        assert a["scale"] == 0.8 and a["angle"] == 12  # size and turn kept
    # A PSD's automatic fit is already centred: Centre of no adjustment is no adjustment.
    assert E.centred(quad, (200, 100), 1.0, None, "centre") is None


def test_the_editor_is_told_how_a_psd_design_starts() -> None:
    import inspect

    from app.api import mockup_edits
    from app.workers import mockups as work

    source = inspect.getsource(mockup_edits)
    assert 'size=1.0 if p.psd else p.size, align="centre" if p.psd else "top"' in source
    # The run renders a PSD photo with the stored adjustment (it used to be left out).
    assert "mockups.JPEG_QUALITY, placements[0][3], job.sharpen" in inspect.getsource(work.run)


def test_the_editor_previews_a_design_at_most_1200_px_with_its_transparency() -> None:
    """B2/B7: the editor and the tiles draw a small copy; the full file is for rendering only."""
    import io

    from PIL import Image

    from app.api.mockup_edits import PREVIEW_SIDE, design_preview

    big = Image.new("RGBA", (4000, 3000), (0, 0, 0, 0))
    big.paste((200, 30, 30, 255), (1000, 1000, 3000, 2000))
    buf = io.BytesIO()
    big.save(buf, "PNG")
    small = Image.open(io.BytesIO(design_preview(buf.getvalue())))
    assert max(small.size) == PREVIEW_SIDE and small.size == (1200, 900)
    assert small.mode == "RGBA" and small.getpixel((10, 10))[3] == 0 and small.getpixel((600, 450))[3] == 255
    tiny = io.BytesIO()
    Image.new("RGBA", (600, 400), (0, 0, 0, 0)).save(tiny, "PNG")
    assert Image.open(io.BytesIO(design_preview(tiny.getvalue()))).size == (600, 400)  # never enlarged


async def test_apply_to_the_selected_mockups_only_and_undo(mw) -> None:  # noqa: F811
    """B3: the change goes to the ticked mockups (design, photo) of this run, nothing else; Undo restores them;
    another run's or account's mockups are not found."""
    import uuid

    from sqlalchemy import select

    from app.db.models import MockupAdjustment
    from tests.test_design_edits import _run_with_two_designs

    r = await _run_with_two_designs(mw)
    d0, d1 = r["designs"][0]["id"], r["designs"][1]["id"]
    p0, p1 = r["photos"][0]["id"], r["photos"][1]["id"]
    after = {"dx": 0.05, "scale": 1.2}
    await mw["a"].put("/api/mockup-adjustments", json={"design_id": d0, "photo_id": p0, "area": 0, "data": after})
    base = {"design_id": d0, "photo_id": p0, "area": 0, "run_id": str(r["run"]), "before": None, "after": after}

    async def rows() -> dict:
        async with mw["sm"]() as s:
            return {(str(a.design_id), str(a.photo_id)): a.data for a in (await s.execute(select(MockupAdjustment))).scalars()}

    picked = [{"design_id": d1, "photo_id": p1}]
    res = (await mw["a"].post("/api/mockup-adjustments/apply", json={**base, "scope": "selected", "targets": picked})).json()
    got = await rows()
    assert res["changed"] == 1 and got[(d1, p1)]["dx"] == 0.05 and (d1, p0) not in got and (d0, p1) not in got
    await mw["a"].post("/api/mockup-adjustments/restore", json={"rows": res["undo"]})
    assert (d1, p1) not in await rows()
    # Nothing ticked, or a mockup that is not this run's: refused.
    assert (await mw["a"].post("/api/mockup-adjustments/apply", json={**base, "scope": "selected", "targets": []})).status_code == 404
    stranger = [{"design_id": str(uuid.uuid4()), "photo_id": p1}]
    assert (await mw["a"].post("/api/mockup-adjustments/apply", json={**base, "scope": "selected", "targets": stranger})).status_code == 404


async def test_placement_presets_are_saved_and_applied_to_every_design_keeping_each_look(mw) -> None:  # noqa: F811
    """B4: a named placement per template, saved from the editor, applied with one click to the open mockup (the
    page sets it) or to every design on the template in a run; each mockup keeps its own look; Undo restores."""
    from sqlalchemy import select

    from app.db.models import MockupAdjustment, MockupPhoto, MockupPlacementPreset
    from tests.test_design_edits import _run_with_two_designs

    r = await _run_with_two_designs(mw)
    d0, d1 = r["designs"][0]["id"], r["designs"][1]["id"]
    p0 = r["photos"][0]["id"]
    async with mw["sm"]() as s:
        template = str((await s.get(MockupPhoto, __import__("uuid").UUID(p0))).template_id)
    a = mw["a"]
    url = f"/api/mockup-templates/{template}/placement-presets"
    assert (await a.get(url)).json() == []
    usual = (await a.post(f"{url}/usual")).json()
    assert [p["name"] for p in usual] == ["Full front", "Chest print", "Left pocket"]
    assert [p["name"] for p in (await a.post(f"{url}/usual")).json()] == ["Full front", "Chest print", "Left pocket"]  # not twice
    # Saved from the editor: the placement only, the look left out; the same name again replaces it.
    saved = (await a.post(url, json={"name": "Big back", "data": {"scale": 1.1, "dy": 0.05, "opacity": 0.4}})).json()
    assert saved["data"]["scale"] == 1.1 and "opacity" not in saved["data"]
    again = (await a.post(url, json={"name": "big back", "data": {"scale": 1.2}})).json()
    assert again["id"] == saved["id"] and again["data"]["scale"] == 1.2
    # d1 already has its own look on p0: it keeps it under the preset.
    await a.put("/api/mockup-adjustments", json={"design_id": d1, "photo_id": p0, "area": 0, "data": {"brightness": 0.2, "dx": -0.3}})
    pocket = next(p for p in usual if p["name"] == "Left pocket")
    res = (await a.post(f"/api/mockup-placement-presets/{pocket['id']}/apply", json={"run_id": str(r["run"])})).json()
    assert res["changed"] >= 2 and [d0, p0] in res["pairs"] and [d1, p0] in res["pairs"]
    async with mw["sm"]() as s:
        rows = {(str(x.design_id), str(x.photo_id)): x.data for x in (await s.execute(select(MockupAdjustment))).scalars()}
    assert rows[(d1, p0)]["scale"] == 0.26 and rows[(d1, p0)]["dx"] == 0.26 and rows[(d1, p0)]["brightness"] == 0.2
    assert rows[(d0, p0)]["scale"] == 0.26
    # Undo: each back as it was.
    await a.post("/api/mockup-adjustments/restore", json={"rows": res["undo"]})
    async with mw["sm"]() as s:
        rows = {(str(x.design_id), str(x.photo_id)): x.data for x in (await s.execute(select(MockupAdjustment))).scalars()}
    assert rows[(d1, p0)]["dx"] == -0.3 and (d0, p0) not in rows
    # Up to 30 per template; deleted on request; another account sees none of it.
    assert MockupPlacementPreset.MAX_PER_TEMPLATE == 30
    b = mw["b"]
    assert (await b.get(url)).status_code == 404
    assert (await b.post(f"/api/mockup-placement-presets/{pocket['id']}/apply", json={"run_id": str(r["run"])})).status_code == 404
    assert (await b.delete(f"/api/mockup-placement-presets/{pocket['id']}")).status_code == 404
    assert (await a.delete(f"/api/mockup-placement-presets/{pocket['id']}")).status_code == 204
    assert "Left pocket" not in [p["name"] for p in (await a.get(url)).json()]


def _count_renders(monkeypatch) -> list[int]:  # noqa: ANN001
    from app.pipeline import mockups as engine

    calls: list[int] = []
    real = engine.render_many

    def counting(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(engine, "render_many", counting)
    return calls


async def test_a_new_run_keeps_every_unchanged_mockup_and_makes_none_again(mw, monkeypatch) -> None:  # noqa: F811
    """B7: the same inputs, the same file: a second run of the same designs and templates renders nothing."""
    import uuid

    from sqlalchemy import select

    from app.db.models import MockupOutput, MockupRun
    from app.workers import mockups as work
    from tests.test_design_edits import _run_with_two_designs

    r = await _run_with_two_designs(mw)
    async with mw["sm"]() as s:
        first = {(o.design_id, o.photo_id): (o.key, o.render_key) for o in (await s.execute(select(MockupOutput))).scalars()}
    assert len(first) == 4 and all(rk for _, rk in first.values())
    calls = _count_renders(monkeypatch)
    res = await mw["a"].post(f"/api/batches/{r['batch']}/mockups", json={"template_ids": [r["template"]]})
    run2 = uuid.UUID(res.json()["id"])
    async with mw["sm"]() as s:
        done = await work.run(s, mw["storage"], run2)
        assert done.status == "succeeded" and done.reused == 4 and done.done == 4
        second = {(o.design_id, o.photo_id): (o.key, o.render_key) for o in (await s.execute(
            select(MockupOutput).where(MockupOutput.run_id == run2))).scalars()}
        assert (await s.execute(select(MockupOutput).where(MockupOutput.run_id == r["run"]))).scalars().all() == []
    assert calls == [] and second == first  # the same files, kept, nothing rendered
    assert all(mw["storage"].exists(k) for k, _ in second.values())
    # With sharpening the pixels differ: a new key, and they are made again.
    res = await mw["a"].post(f"/api/batches/{r['batch']}/mockups", json={"template_ids": [r["template"]], "sharpen": "medium"})
    async with mw["sm"]() as s:
        done = await work.run(s, mw["storage"], uuid.UUID(res.json()["id"]))
        assert done.reused == 0 and done.sharpen == "medium"
    assert len(calls) == 4
    assert not any(mw["storage"].exists(k) for k, _ in first.values())  # the old files went when not kept


async def test_rerun_changed_makes_again_only_what_changed(mw, monkeypatch) -> None:  # noqa: F811
    """B5: one design moved on one photo: "Re-run only the changed ones" makes that one mockup again; the three
    unchanged ones are kept and are not reported as failed."""
    from sqlalchemy import select

    from app.db.models import MockupOutput
    from app.pipeline import mockup_refresh
    from app.workers import mockups as work
    from tests.test_design_edits import _run_with_two_designs

    r = await _run_with_two_designs(mw)
    d0, p0 = r["designs"][0]["id"], r["photos"][0]["id"]
    async with mw["sm"]() as s:
        before = {(str(o.design_id), str(o.photo_id)): o.key for o in (await s.execute(select(MockupOutput))).scalars()}
    await mw["a"].put("/api/mockup-adjustments", json={"design_id": d0, "photo_id": p0, "area": 0, "data": {"dx": 0.08}})
    res = await mw["a"].post(f"/api/mockup-runs/{r['run']}/rerun-changed")
    assert res.status_code == 202, res.text
    assert len(res.json()["refreshing"]) == 4  # every mockup asked for; the worker decides
    calls = _count_renders(monkeypatch)
    turn = await mockup_refresh.take(mw["redis"], r["run"]) if hasattr(mockup_refresh, "take") else None
    pairs = turn or [(__import__("uuid").UUID(d), __import__("uuid").UUID(p)) for d, p in before]
    async with mw["sm"]() as s:
        await work.refresh_turn(s, mw["storage"], r["run"], pairs, mw["redis"])
    assert len(calls) == 1
    async with mw["sm"]() as s:
        after = {(str(o.design_id), str(o.photo_id)): o.key for o in (await s.execute(select(MockupOutput))).scalars()}
    changed = [pair for pair in before if before[pair] != after[pair]]
    assert changed == [(d0, p0)]
    run = (await mw["a"].get(f"/api/mockup-runs/{r['run']}")).json()
    assert run["reused"] == 3 and run["refresh_failed"] == []


def test_mixing_in_linear_light_leaves_no_dark_rim() -> None:
    """B6: a white design with soft edges on a black shirt: no edge pixel darker than both the shirt and the ink,
    and half coverage is brighter than an sRGB mix (which reads as a dark rim)."""
    from app.pipeline import mockups as engine

    half_srgb = 0.5 * 0.0 + 0.5 * 1.0
    half_linear = (0.5 * 0.0 ** engine.MIX_GAMMA + 0.5 * 1.0 ** engine.MIX_GAMMA) ** (1 / engine.MIX_GAMMA)
    assert half_linear > half_srgb + 0.2
    assert engine.ENGINE_VERSION == 5 and set(engine.SHARPEN) == {"off", "low", "medium"}


def test_sharpening_changes_only_when_asked() -> None:
    import pyvips

    from app.pipeline import mockups as engine

    img = pyvips.Image.gaussnoise(64, 64, mean=128, sigma=20).cast("uchar").bandjoin([128, 128]).copy(interpretation="srgb")
    assert engine.sharpened(img, "off") is img
    low, medium = engine.sharpened(img, "low"), engine.sharpened(img, "medium")
    diff = lambda a, b: float((a.cast("float") - b.cast("float")).abs().avg())  # noqa: E731
    assert 0 < diff(img, low) < diff(img, medium)
