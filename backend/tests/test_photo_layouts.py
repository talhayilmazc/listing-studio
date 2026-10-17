"""Each template photo's layout (single garment, front & back, several garments), the back
design, click-to-add, folded garments under props and the size and centre guards
(pipeline/garments.py, pipeline/print_rule.py, pipeline/mockups.py, workers/mockups.py,
api/mockups.py).

Synthetic garments only; the foreground is given as drawn where a rule is tested alone.
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
from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_mockups import _new_batch, mw  # noqa: F401  (fixture)
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_print_geometry import BACKGROUND, Drawn, _prepared, _tee, _template, _two_tees


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _front_and_back(colour=(238, 238, 236)) -> bytes:
    """One photo: the same white tee twice, side by side (its front and its back)."""
    w, h = 1400, 760
    canvas = Image.new("RGB", (w, h), (205, 208, 212))
    for i in range(2):
        tee = Image.open(io.BytesIO(samples.blank(colour, size=(640, 740), background=(205, 208, 212)))).convert("RGB")
        canvas.paste(tee, (40 + i * 700, 10))
    return _png(canvas)


def _width(q) -> float:  # noqa: ANN001
    return math.dist(q[0], q[1])


# --- layouts ------------------------------------------------------------------------------------------


def test_the_back_is_printed_larger_and_closer_to_its_collar() -> None:
    neck, arms = (500.0, 200.0), ((340.0, 330.0), (660.0, 330.0))
    front = print_rule.print_area(neck, arms, kind="t-shirt", view="front", layout="flat")
    back = print_rule.print_area(neck, arms, kind="t-shirt", view="back", layout="flat")
    assert _width(back.corners) > _width(front.corners) * 1.05
    assert back.corners[0][1] < front.corners[0][1]  # its top nearer the collar


async def test_front_and_back_gives_two_areas_with_front_and_back_roles(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "FB")
    photo = await _prepared(mw, "a", t, "tee", _front_and_back())
    assert photo["layout"] == "front_back" and photo["layout_source"] == "auto"
    assert [a["role"] for a in photo["areas"]] == ["front", "back"]
    front, back = (a["print_area"] for a in photo["areas"])
    assert sum(p[0] for p in front) / 4 < 0.5 < sum(p[0] for p in back) / 4  # the front panel is the first
    assert _width(back) > _width(front) and back[0][1] < front[0][1]


async def test_several_garments_found_and_the_layout_is_the_sellers_to_change(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Two tees")
    photo = await _prepared(mw, "a", t, "two", _two_tees())  # a red and a blue tee
    assert photo["layout"] == "several" and len(photo["areas"]) == 2
    single = (await mw["a"].patch(f"/api/mockup-photos/{photo['id']}", json={"layout": "single"})).json()
    assert single["layout"] == "single" and single["layout_source"] == "seller" and len(single["areas"]) == 1
    # Another account cannot change it.
    assert (await mw["b"].patch(f"/api/mockup-photos/{photo['id']}", json={"layout": "several"})).status_code == 404
    # Confirmed areas never change without the seller.
    a = single["areas"][0]
    res = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": [
        {"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default"}]})
    assert res.status_code == 200
    again = (await mw["a"].patch(f"/api/mockup-photos/{photo['id']}", json={"layout": "several"})).json()
    assert again["layout"] == "several" and again["confirmed"] and [x["print_area"] for x in again["areas"]] == [a["print_area"]]


async def test_click_a_garment_to_add_an_area_then_the_last_areas_size(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Click")
    photo = await _prepared(mw, "a", t, "two", _two_tees())
    photo = (await mw["a"].patch(f"/api/mockup-photos/{photo['id']}", json={"layout": "single"})).json()
    have = [photo["areas"][0]["print_area"]]
    other_x = 0.75 if sum(p[0] for p in have[0]) / 4 < 0.5 else 0.25
    found = (await mw["a"].post(f"/api/mockup-photos/{photo['id']}/find", json={"x": other_x, "y": 0.45, "areas": have})).json()
    assert found["found"] and abs(sum(p[0] for p in found["print_area"]) / 4 - other_x) < 0.12  # the garment under the click
    like = have[0]
    miss = (await mw["a"].post(f"/api/mockup-photos/{photo['id']}/find",
                               json={"x": 0.5, "y": 0.5, "areas": have + [found["print_area"]], "like": like})).json()
    if not miss["found"]:  # nothing garment-shaped between the tees: the last area's size, centred on the click
        assert _width(miss["print_area"]) == pytest.approx(_width(like), abs=0.002)
        assert sum(p[0] for p in miss["print_area"]) / 4 == pytest.approx(0.5, abs=0.002)
    assert (await mw["b"].post(f"/api/mockup-photos/{photo['id']}/find", json={"x": 0.5, "y": 0.5})).status_code == 404


def test_like_at_keeps_size_and_angle() -> None:
    like = [[0.1, 0.1], [0.3, 0.12], [0.28, 0.32], [0.08, 0.3]]
    out = work.like_at(0.6, 0.6, like, 1000, 1000)
    assert _width(out) == pytest.approx(_width(like), abs=1e-3)
    assert math.atan2(out[1][1] - out[0][1], out[1][0] - out[0][0]) == pytest.approx(math.atan2(0.02, 0.2), abs=1e-3)


# --- the back design --------------------------------------------------------------------------------


def _solid(colour) -> bytes:  # noqa: ANN001
    return _png(Image.new("RGBA", (900, 900), (*colour, 255)))


def _pixel(mw, run: dict, design_name: str, area) -> tuple[int, int, int]:  # noqa: ANN001, F811
    d = next(d for d in run["designs"] if d["original_filename"] == design_name)
    key = d["outputs"][0]["key"] if "key" in d["outputs"][0] else None
    return key, area


async def test_the_back_area_prints_the_back_design_or_the_templates_fallback(mw) -> None:  # noqa: F811
    from sqlalchemy import select

    from app.db.models import MockupOutput

    t = await _template(mw, "a", "FB run")
    photo = await _prepared(mw, "a", t, "tee", _front_and_back())
    a = photo["areas"]
    res = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": [
        {"print_area": x["print_area"], "detected_area": x["detected_area"], "source": "default", "role": x["role"]} for x in a]})
    assert res.status_code == 200
    batch = await _new_batch(mw)
    red = (await mw["a"].post(f"/api/batches/{batch}/designs", files={"file": ("RED1.png", _solid((220, 30, 30)), "image/png")})).json()
    green = (await mw["a"].post(f"/api/batches/{batch}/designs", files={"file": ("GRN2.png", _solid((30, 170, 60)), "image/png")})).json()
    with_back = (await mw["a"].put(f"/api/mockup-designs/{red['id']}/back", files={"file": ("b.png", _solid((30, 40, 220)), "image/png")})).json()
    assert with_back["has_back"] is True
    assert (await mw["b"].put(f"/api/mockup-designs/{red['id']}/back", files={"file": ("b.png", _solid((1, 2, 3)), "image/png")})).status_code == 404

    async def run_and_read() -> dict[str, np.ndarray]:
        r = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [t]})
        assert r.status_code == 202, r.text
        async with mw["sm"]() as s:
            assert (await work.run(s, mw["storage"], uuid.UUID(r.json()["id"]))).status == "succeeded"
            rows = (await s.execute(select(MockupOutput).where(MockupOutput.run_id == uuid.UUID(r.json()["id"])))).scalars().all()
        return {row.design_id.hex: np.asarray(Image.open(io.BytesIO(mw["storage"].get(row.key))).convert("RGB")) for row in rows}

    def at(img: np.ndarray, q) -> np.ndarray:  # noqa: ANN001 - inside the area, near its top (where a design is)
        h, w = img.shape[:2]
        cx = sum(p[0] for p in q) / 4 * w
        cy = (q[0][1] + 0.3 * (q[3][1] - q[0][1])) * h
        return img[int(cy), int(cx)].astype(int)

    images = await run_and_read()
    front_q, back_q = a[0]["print_area"], a[1]["print_area"]
    r_img, g_img = images[uuid.UUID(red["id"]).hex], images[uuid.UUID(green["id"]).hex]
    assert at(r_img, front_q)[0] > at(r_img, front_q)[2] + 60  # red on the front
    assert at(r_img, back_q)[2] > at(r_img, back_q)[0] + 60  # its back file on the back
    assert at(g_img, back_q)[1] > at(g_img, back_q)[0] + 40  # no back file: the front design (default)
    assert (await mw["a"].patch(f"/api/mockup-templates/{t}", json={"back_without_design": "plain"})).json()["back_without_design"] == "plain"
    images = await run_and_read()
    plain = at(images[uuid.UUID(green["id"]).hex], back_q)
    assert abs(plain[0] - plain[1]) < 25 and plain.min() > 150  # left plain: the white tee shows


def test_design_for_follows_the_role_and_the_fallback() -> None:
    assert work.design_for("front", "F", "B", "plain") == "F"
    assert work.design_for("back", "F", "B", "plain") == "B"
    assert work.design_for("back", "F", None, "front") == "F"
    assert work.design_for("back", "F", None, "plain") is None


# --- folded garments under props, oversized worn tees ------------------------------------------------


def test_a_folded_shirt_under_a_hat_keeps_a_normal_area_below_its_collar() -> None:
    size = (1000, 1000)
    box = (0.28, 0.25, 0.72, 0.75)
    clean, cmask = _tee(size, box, folded=True)
    plain = garments.detect(clean, None, Drawn(cmask), "").garments[0]

    def hat(img: Image.Image, mask: np.ndarray) -> None:  # a hat brim over the top-left corner, and leaves
        d = ImageDraw.Draw(img)
        d.ellipse([200, 160, 470, 390], fill=(200, 170, 110))
        d.polygon([(700, 560), (790, 520), (760, 700)], fill=(120, 140, 70))

    rgb, mask = _tee(size, box, folded=True, extra=hat)
    yy, xx = np.ogrid[:size[1], :size[0]]
    fg = mask | (((xx - 335) ** 2 / 135 ** 2 + (yy - 275) ** 2 / 115 ** 2) <= 1)
    found = garments.detect(rgb, None, Drawn(fg), "").garments
    g = max(found, key=lambda f: _width(f.corners))
    assert _width(g.corners) >= 0.85 * _width(plain.corners)  # not shrunk by the hat
    top = min(y for _, y in g.corners[:2])
    assert top <= min(y for _, y in plain.corners[:2]) + 0.03 * 1000  # as high as without the hat, not low
    assert top < 0.25 * 1000 + 0.5 * 500  # in the upper half of the folded shirt
    assert abs(sum(x for x, _ in g.corners) / 4 - 500) < 0.06 * 1000  # centred on the collar


def test_an_oversized_worn_tee_stays_on_the_neckline_within_the_width_guard() -> None:
    png, truth = samples.person("hair", colour=(90, 110, 160))
    img = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = img.size
    # Dropped shoulders: the tee's fabric hangs well past the wearer's shoulders, both sides.
    extra = [[(w * 0.12, h * 0.30), (w * 0.25, h * 0.27), (w * 0.25, h * 0.95), (w * 0.10, h * 0.95)],
             [(w * 0.88, h * 0.30), (w * 0.75, h * 0.27), (w * 0.75, h * 0.95), (w * 0.90, h * 0.95)]]
    classes = truth["classes"].copy()
    for poly in extra:
        ImageDraw.Draw(img).polygon(poly, fill=(90, 110, 160))
        ImageDraw.Draw(classes).polygon(poly, fill=4)
    small = img.copy()
    small.thumbnail((garments.WORK, garments.WORK))
    sw, sh = small.size
    mask = np.asarray(classes.resize((sw, sh), Image.NEAREST)) > 0
    g = garments.detect(np.asarray(small), samples.TruthRunner({**truth, "classes": classes}), Drawn(mask), "tshirt").garments[0]
    xs = [x / sw for x, _ in g.corners]
    assert abs(sum(xs) / 4 - 0.5) < 0.04  # on the neckline (the photo's middle)
    share = _width(g.corners) / math.dist(*g.armpits)
    assert garments.WIDTH_RANGE[0] <= share <= garments.WIDTH_RANGE[1]


def test_an_area_outside_the_width_guard_is_set_to_the_standard_and_asks_for_a_check() -> None:
    h, w = 1000, 1000
    region = np.zeros((h, w), bool)
    region[200:900, 300:700] = True
    neck, arms = (500.0, 210.0), ((300.0, 330.0), (700.0, 330.0))
    # A lower edge read far too high: the rule shrinks the area to a sliver.
    g = garments._finish(region, np.zeros((h, w), bool), neck, arms, (500.0, 300.0), "t-shirt", "front", "flat")
    assert "too small: standard size" in g.info["guards"] and g.info["on_region"] < 1.0
    std = print_rule.ratios_for("t-shirt", "front", "flat")[0]["width"]
    assert _width(g.corners) / 400 == pytest.approx(std, rel=0.05)


def test_detection_with_layouts_needs_no_network(monkeypatch) -> None:
    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("network used at runtime")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    first = mockups.prepare_blank(_front_and_back())
    again = mockups.prepare_blank(_front_and_back())
    assert first.layout == again.layout == "front_back" and first.garments == again.garments
