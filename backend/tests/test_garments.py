"""Garments in a template photo (pipeline/garments.py, pipeline/print_rule.py) and what the
templates and runs do with them.

* The print rule: the template standard, upright on a worn or hanging garment, along a flat
  one only when it clearly lies at an angle, below the collar, stopping above a lower edge
  (a knot, a fold, a pocket) and shrinking rather than crossing it.
* Detection on synthetic photos: several garments, each its own area; front or back from
  the pose; the hoodie pocket; an object held over the garment goes in front of the print.
* Templates and runs: the seller changes a photo's view; a run prints front, back or both;
  the same design on every garment, or one design per garment in order.
* No network at runtime, no Etsy or AI call, the same answer twice.

The real models do not see a drawing as a person: person cases give the analysis the
drawing's own answer (``samples.TruthRunner``); everything else uses the bundled models.
"""

from __future__ import annotations

import io
import math
import socket
import uuid

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.pipeline import garments, mockups, print_rule
from app.pipeline import mockup_vision as vision
from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_mockups import _NoCalls, _confirm, _designs, _new_batch, _new_template, _photo, _template, mw  # noqa: F401  (fixtures)
from tests.test_multi_shop import world  # noqa: F401  (fixture)


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _tees(colours=((200, 60, 60), (50, 90, 200)), size=(1400, 760)) -> bytes:
    """Two flat-lay tees side by side on a plain background (each drawn from the
    single-tee sample, its own background cut away)."""
    w, h = size
    canvas = Image.new("RGB", size, (232, 233, 236))
    for i, colour in enumerate(colours):
        tee = Image.open(io.BytesIO(samples.blank(colour, size=(640, 740), background=(232, 233, 236)))).convert("RGB")
        canvas.paste(tee, (40 + i * 700, 10))
    return _png(canvas)


# --- the print rule ---------------------------------------------------------------------------


def test_a_worn_print_is_upright_and_a_flat_one_follows_a_clear_tilt() -> None:
    neck, arms = (500.0, 100.0), ((300.0, 300.0), (700.0, 360.0))  # the shoulder line tilts 8.5 degrees
    worn = print_rule.print_area(neck, arms, kind="t-shirt", layout="on a person")
    assert worn.info["angle"] == 0.0
    (_, y0), (_, y1) = worn.corners[0], worn.corners[1]
    assert abs(y1 - y0) < 1e-6  # not rotated: pose and camera tilt the shoulders, not the print
    flat = print_rule.print_area(neck, arms, kind="t-shirt", layout="flat-lay")
    assert flat.info["angle"] == pytest.approx(math.degrees(math.atan2(60, 400)), abs=0.1)
    slight = print_rule.print_area(neck, ((300.0, 300.0), (700.0, 320.0)), kind="t-shirt", layout="flat-lay")
    assert slight.info["angle"] == 0.0  # within 6 degrees: upright


def test_the_template_standard_sets_size_and_place() -> None:
    neck, arms = (500.0, 100.0), ((300.0, 250.0), (700.0, 250.0))
    area = print_rule.print_area(neck, arms, kind="t-shirt", layout="on a person")
    (tlx, tly), (trx, _), _, (_, bly) = area.corners
    ratios = print_rule.STANDARD["t-shirt worn"]
    assert (trx - tlx) / 400 == pytest.approx(ratios["width"], abs=1e-3)
    assert (tly - 100) / 400 == pytest.approx(ratios["top"], abs=1e-3)
    assert (bly - tly) / (trx - tlx) == pytest.approx(ratios["aspect"], abs=1e-3)
    assert (tlx + trx) / 2 == pytest.approx(500, abs=1e-6)  # centred between the armpits


def test_the_knot_rule_stops_the_print_above_its_lower_edge_and_keeps_its_shape() -> None:
    neck, arms = (500.0, 100.0), ((300.0, 250.0), (700.0, 250.0))
    free = print_rule.print_area(neck, arms, kind="t-shirt", layout="flat-lay")
    knot = (500.0, 330.0)
    cut = print_rule.print_area(neck, arms, kind="t-shirt", layout="flat-lay", bottom=knot)
    bottom = cut.corners[2][1]
    assert bottom <= knot[1] - print_rule.EDGE_MARGIN * 400 + 1e-6  # above the knot, with a margin
    assert cut.corners[0][1] == pytest.approx(free.corners[0][1])  # the top kept
    w_free = free.corners[1][0] - free.corners[0][0]
    w_cut = cut.corners[1][0] - cut.corners[0][0]
    h_cut = cut.corners[3][1] - cut.corners[0][1]
    # A flat-lay uses the worn/flat ratios (a folded garment's are its own).
    assert w_cut < w_free and h_cut / w_cut == pytest.approx(print_rule.STANDARD["t-shirt worn"]["aspect"], abs=1e-3)


def test_the_collar_rule_keeps_the_print_below_the_garments_own_neckline() -> None:
    region = np.zeros((600, 600), bool)
    region[150:560, 150:450] = True  # the garment; its neckline edge at y = 150
    neck, arms = (300.0, 60.0), ((150.0, 120.0), (450.0, 120.0))  # a collar point read far too high
    top = garments._safe_top(region, neck, arms, "flat-lay")
    assert top is not None and top[1] >= 150 + garments.COLLAR_MARGIN * 300 - 2
    found = garments._finish(region, np.zeros_like(region), neck, arms, None, "t-shirt", "front", "flat-lay")
    assert min(y for _, y in found.corners) >= 150 + garments.COLLAR_MARGIN * 300 - 2


def test_a_hoodies_pocket_is_found_and_the_print_stays_above_it() -> None:
    h, w = 800, 600
    region = np.zeros((h, w), bool)
    region[100:780, 100:500] = True
    lab = np.zeros((h, w, 3))
    lab[..., 0] = 60.0
    lab[350:560, 180:420, 0] = 45.0  # the pocket: a darker panel, its top seam 0.7 chest widths below the collar
    f = print_rule.frame((300.0, 140.0), ((150.0, 260.0), (450.0, 260.0)), "on a person")
    pocket = garments._pocket(lab, region, f)
    assert pocket is not None and abs(pocket[1] - 350) < 8
    area = print_rule.print_area(f.origin, ((150.0, 260.0), (450.0, 260.0)), kind="hoodie", layout="on a person",
                                 bottom=pocket)
    assert max(y for _, y in area.corners) < 350


# --- detection --------------------------------------------------------------------------------


def test_several_garments_each_get_their_own_print_area() -> None:
    prepared = mockups.prepare_blank(_tees())
    assert len(prepared.garments) == 2
    assert prepared.garments[0]["print_area"] == prepared.print_area
    centres = [sum(x for x, _ in g["print_area"]) / 4 for g in prepared.garments]
    assert centres[0] < 0.5 < centres[1]  # reading order: left, then right
    for g, (lo, hi) in zip(prepared.garments, ((0.03, 0.49), (0.53, 0.99))):
        for x, y in g["print_area"]:
            assert lo < x < hi, g["print_area"]  # on its own tee, not across to the other


def test_one_design_per_garment_in_order_through_the_renderer() -> None:
    prepared = mockups.prepare_blank(_tees(((238, 238, 236), (238, 238, 236))))
    areas = [g["print_area"] for g in prepared.garments]
    assert len(areas) == 2
    maps = mockups.build_maps(prepared.image, prepared.mask, None, areas)
    red = mockups.load_design(_png(Image.new("RGBA", (400, 400), (220, 30, 30, 255))))
    blue = mockups.load_design(_png(Image.new("RGBA", (400, 400), (20, 40, 220, 255))))
    out = np.asarray(Image.open(io.BytesIO(mockups.render_many(maps, list(zip([red, blue], areas)), mockups.Settings(areas[0], 1.0, 0.0)))))
    def centre(a):  # noqa: ANN001, ANN202
        cx, cy = sum(x for x, _ in a) / 4 * maps.width, (a[0][1] + 0.4 * (a[3][1] - a[0][1])) * maps.height
        return out[int(cy), int(cx)].astype(int)
    left, right = centre(areas[0]), centre(areas[1])
    assert left[0] > left[2] + 40 and right[2] > right[0] + 40  # red on the first garment, blue on the second


def test_front_or_back_from_the_pose() -> None:
    png, truth = samples.person("hair")
    front = mockups.prepare_blank(png, runner=samples.TruthRunner(truth))
    assert front.view == "front"
    # From behind, the person's left shoulder (11) is on the left of the photo.
    pts = dict(truth["points"])
    pts[11], pts[12] = truth["points"][12], truth["points"][11]
    back = mockups.prepare_blank(png, runner=samples.TruthRunner({**truth, "points": pts}))
    assert back.view == "back"


def test_an_object_held_over_the_garment_goes_in_front_of_the_print() -> None:
    w, h = 1200, 1400
    tee = Image.open(io.BytesIO(samples.blank((238, 238, 236), size=(w, h)))).convert("RGB")
    ImageDraw.Draw(tee).ellipse([0.44 * w, 0.30 * h, 0.58 * w, 0.42 * h], fill=(30, 30, 34))  # a mug on the chest
    prepared = mockups.prepare_blank(_png(tee))
    assert prepared.occluder is not None
    occ = Image.open(io.BytesIO(prepared.occluder)).convert("L")
    assert occ.getpixel((round(0.51 * w), round(0.36 * h))) > 200  # the mug is in front
    maps = mockups.build_maps(prepared.image, prepared.mask, prepared.occluder, [prepared.print_area])
    out = Image.open(io.BytesIO(mockups.render(maps, mockups.load_design(samples.design("opaque")),
                                               mockups.Settings(prepared.print_area, 1.0, 0.0))))
    blank = Image.open(io.BytesIO(prepared.image))
    on_mug = (round(0.51 * w), round(0.36 * h))
    assert sum(abs(a - b) for a, b in zip(out.getpixel(on_mug), blank.getpixel(on_mug))) < 20


def test_detection_needs_no_network_and_gives_the_same_answer_twice(monkeypatch) -> None:
    if garments.default_saliency() is None or vision.default_runner() is None:
        pytest.skip("the models are bundled into the image at build time (docker/Dockerfile)")

    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("network used at runtime")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    first = mockups.prepare_blank(_tees())
    again = mockups.prepare_blank(_tees())
    assert first == again and len(first.garments) == 2


def test_a_broken_saliency_model_falls_back_and_nothing_crashes() -> None:
    class Broken:
        def __call__(self, rgb):  # noqa: ANN001, ANN204
            raise RuntimeError("model fault")

    prepared = mockups.prepare_blank(samples.blank(), saliency=Broken())
    assert prepared.print_area and prepared.method in ("outline", "default")


# --- templates and runs -----------------------------------------------------------------------


async def test_the_view_is_changed_by_the_seller_and_a_run_prints_front_back_or_both(mw, monkeypatch) -> None:  # noqa: F811
    guard = _NoCalls(monkeypatch)
    tee = await _new_template(mw, "Tee")
    front = await _photo(mw, tee["id"], "Front")
    back = await _photo(mw, tee["id"], "Back")
    assert front["view"] == "front"
    res = await mw["a"].patch(f"/api/mockup-photos/{back['id']}", json={"view": "back"})
    assert res.status_code == 200 and res.json()["view"] == "back"
    assert (await mw["a"].patch(f"/api/mockup-photos/{back['id']}", json={"view": "side"})).status_code == 422
    batch = await _new_batch(mw)
    await _designs(mw, batch, ["A1.png"])
    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [tee["id"]], "sides": "back"})
    assert res.status_code == 202, res.text
    run = res.json()
    assert (run["templates"], run["photos"]) == (["Tee"], 1)
    async with mw["sm"]() as s:
        done = await work.run(s, mw["storage"], uuid.UUID(run["id"]))
        assert done.status == "succeeded" and done.sides == "back" and done.total == 1
    # A template with only front photos, back asked for: nothing to print, said so.
    fronts = await _template(mw, "Fronts")
    other = await _new_batch(mw)
    await _designs(mw, other, ["B1.png"])
    res = await mw["a"].post(f"/api/batches/{other}/mockups", json={"template_ids": [fronts["id"]], "sides": "back"})
    assert res.status_code == 409 and "back" in res.json()["detail"]
    assert guard.calls == []


async def test_several_garments_get_the_same_design_or_one_each_in_order(mw, monkeypatch) -> None:  # noqa: F811
    guard = _NoCalls(monkeypatch)
    template = await _new_template(mw, "Two tees")
    # Two garments in one photo: the seller says so (one area per photo otherwise).
    assert (await mw["a"].patch(f"/api/mockup-templates/{template["id"]}", json={"several_garments": True})).status_code == 200
    photo = await _photo(mw, template["id"], "Two tees", blank=_tees(), confirm=False)
    assert len(photo["areas"]) == 2
    # The second garment's area is the seller's to move, alone; then "Looks right".
    moved = [[0.6, 0.3], [0.9, 0.3], [0.9, 0.7], [0.6, 0.7]]
    areas = [{"print_area": a["print_area"], "detected_area": a["detected_area"], "source": a["source"]} for a in photo["areas"]]
    areas[1] = {**areas[1], "print_area": moved, "source": "seller"}
    saved = await _confirm(mw, photo, areas)
    assert saved["areas"][1]["print_area"] == moved and saved["areas"][1]["source"] == "seller"
    assert saved["areas"][0]["print_area"] == photo["areas"][0]["print_area"]

    async def run(mode: str) -> dict:
        batch = await _new_batch(mw)
        await _designs(mw, batch, ["A1.png", "B2.png", "C3.png"])
        res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [template["id"]], "garment_mode": mode})
        assert res.status_code == 202
        async with mw["sm"]() as s:
            assert (await work.run(s, mw["storage"], uuid.UUID(res.json()["id"]))).status == "succeeded"
        return (await mw["a"].get(f"/api/mockup-runs/{res.json()['id']}")).json()

    same = await run("same")
    assert all(len(d["outputs"]) == 1 for d in same["designs"])  # each design: one mockup, on both garments
    order = await run("order")
    # One design per garment: A1 and B2 share a mockup, C3 has its own; every design has one.
    assert all(len(d["outputs"]) == 1 for d in order["designs"])
    async with mw["sm"]() as s:
        from sqlalchemy import select

        from app.db.models import MockupOutput

        rows = (await s.execute(select(MockupOutput).where(MockupOutput.run_id == uuid.UUID(order["id"])))).scalars().all()
        assert len({r.key for r in rows}) == 2 and len(rows) == 3
    assert guard.calls == []
