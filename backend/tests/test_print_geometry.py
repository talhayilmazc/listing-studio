"""The print area from each garment's own geometry, the hard guards, and the seller's own
placement (pipeline/garments.py, pipeline/print_rule.py, pipeline/placement.py).

Synthetic garments drawn here, with the foreground given as drawn (a stub for the saliency
model, no segmenter), so each rule is tested by itself and without the bundled models:

* a tilted garment gets a tilted print area; a narrow and a wide garment get print areas in
  proportion (and so the same design in proportion);
* the area never leaves the garment, never reaches the collar, its centre stays in the chest
  band and it is never narrower than the minimum; a guard that changes it lowers the
  confidence;
* a worn tee's shoulders are not a hood; a real hood mass beside the neck is;
* several garments get one area each; a prop beside a garment gets none;
* the seller's confirmed placement changes their next suggestions, and nobody else's; Reset
  forgets it;
* no network at runtime, and the same answer twice.
"""

from __future__ import annotations

import io
import math
import socket
import uuid

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app.pipeline import garments, mockups, placement, print_rule
from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_mockups import mw  # noqa: F401  (fixture)
from tests.test_multi_shop import world  # noqa: F401  (fixture)

BACKGROUND = (226, 228, 232)


class Drawn:
    """The saliency model's place: the foreground exactly as drawn."""

    def __init__(self, mask: np.ndarray) -> None:
        self.mask = mask.astype(np.float32)

    def __call__(self, rgb: np.ndarray) -> np.ndarray:
        return self.mask


def _tee(size: tuple[int, int], box: tuple[float, float, float, float], colour=(200, 70, 60),
         angle: float = 0.0, folded: bool = False, extra=None):  # noqa: ANN001, ANN202
    """(rgb, mask): a tee (flat with sleeves, or folded: a panel with a neckline) inside
    ``box`` (x0, y0, x1, y1 as shares), turned ``angle`` degrees (counter-clockwise, as PIL)."""
    w, h = size
    shape = Image.new("L", size, 0)
    d = ImageDraw.Draw(shape)
    x0, y0, x1, y1 = box[0] * w, box[1] * h, box[2] * w, box[3] * h
    bw, bh = x1 - x0, y1 - y0
    if folded:
        d.rectangle([x0, y0, x1, y1], fill=255)
        d.ellipse([x0 + 0.38 * bw, y0 - 0.06 * bh, x0 + 0.62 * bw, y0 + 0.07 * bh], fill=0)  # the neckline
    else:
        d.polygon([(x0 + px * bw, y0 + py * bh) for px, py in (
            (0.30, 0.0), (0.42, 0.04), (0.50, 0.06), (0.58, 0.04), (0.70, 0.0), (1.0, 0.13), (0.92, 0.33),
            (0.78, 0.28), (0.78, 1.0), (0.22, 1.0), (0.22, 0.28), (0.08, 0.33), (0.0, 0.13))], fill=255)
    if angle:
        shape = shape.rotate(angle, resample=Image.NEAREST, center=((x0 + x1) / 2, (y0 + y1) / 2))
    img = Image.new("RGB", size, BACKGROUND)
    img.paste(Image.new("RGB", size, colour), (0, 0), shape)
    mask = np.asarray(shape) > 127
    if extra is not None:
        extra(img, mask)
    return np.asarray(img), mask


def _detect(rgb: np.ndarray, mask: np.ndarray, **kwargs):  # noqa: ANN003, ANN202
    return garments.detect(rgb, None, Drawn(mask), "", **kwargs)


def _angle(corners) -> float:  # noqa: ANN001
    (ax, ay), (bx, by) = corners[0], corners[1]
    return math.degrees(math.atan2(by - ay, bx - ax))


def _outside(corners, mask: np.ndarray) -> float:  # noqa: ANN001
    h, w = mask.shape
    im = Image.new("1", (w, h), 0)
    ImageDraw.Draw(im).polygon([(float(x), float(y)) for x, y in corners], fill=1)
    box = np.asarray(im, dtype=bool)
    return float((box & ~mask).sum() / max(box.sum(), 1))


# --- geometry ------------------------------------------------------------------------------------


@pytest.mark.parametrize("folded", [True, False])
def test_a_tilted_garment_gets_a_tilted_print_area(folded: bool) -> None:
    rgb, mask = _tee((1000, 1000), (0.3, 0.25, 0.7, 0.8), angle=15, folded=folded)
    found = _detect(rgb, mask).garments
    assert len(found) == 1
    # PIL turns counter-clockwise: in the photo the right side rises, the armpit line reads -15.
    assert abs(_angle(found[0].corners) - (-15.0)) < 3.0, _angle(found[0].corners)
    upright, umask = _tee((1000, 1000), (0.3, 0.25, 0.7, 0.8), folded=folded)
    assert abs(_angle(_detect(upright, umask).garments[0].corners)) < 1.0


def test_a_narrow_and_a_wide_garment_get_areas_in_proportion() -> None:
    narrow, nmask = _tee((1200, 1000), (0.40, 0.35, 0.60, 0.65))  # the same garment, half the size
    wide, wmask = _tee((1200, 1000), (0.30, 0.2, 0.70, 0.8))
    a = _detect(narrow, nmask).garments[0]
    b = _detect(wide, wmask).garments[0]
    width = lambda f: math.dist(f.corners[0], f.corners[1])  # noqa: E731
    chest = lambda f: math.dist(*f.armpits)  # noqa: E731
    assert width(b) / width(a) == pytest.approx(chest(b) / chest(a), rel=0.08)
    assert width(b) > 1.5 * width(a)
    # The same design, fitted into each (contain, top-anchored): in the same proportion.
    pa = mockups.place(list(a.corners), (900, 600), 0.9)
    pb = mockups.place(list(b.corners), (900, 600), 0.9)
    assert pb.scaled[0] / pa.scaled[0] == pytest.approx(width(b) / width(a), rel=0.05)


def test_the_area_never_leaves_the_garment() -> None:
    def bite(img: Image.Image, mask: np.ndarray) -> None:  # a hole bitten out of the chest's side
        ImageDraw.Draw(img).ellipse([520, 330, 640, 450], fill=BACKGROUND)
        yy, xx = np.ogrid[:mask.shape[0], :mask.shape[1]]
        mask[((xx - 580) ** 2 + (yy - 390) ** 2) <= 60 ** 2] = False

    cases = [
        _tee((1000, 1000), (0.3, 0.2, 0.7, 0.8)),
        _tee((1000, 1000), (0.3, 0.2, 0.7, 0.8), angle=-12),
        _tee((1000, 1000), (0.42, 0.2, 0.58, 0.9)),  # very narrow
        _tee((1000, 1000), (0.3, 0.2, 0.7, 0.8), extra=bite),
        _tee((1000, 1000), (0.25, 0.3, 0.75, 0.62), folded=True),  # short folded panel
    ]
    for rgb, mask in cases:
        found = _detect(rgb, mask).garments
        assert found
        for g in found:
            spill = _outside(g.corners, mask)
            assert spill <= 0.03 or "off the garment" in g.info["guards"], (spill, g.info)


def test_the_guards_keep_collar_chest_band_and_size_and_lower_the_confidence() -> None:
    h, w = 1000, 1000
    region = np.zeros((h, w), bool)
    region[200:900, 300:700] = True
    hair = np.zeros((h, w), bool)
    neck, armpits = (500.0, 210.0), ((300.0, 330.0), (700.0, 330.0))
    plain = garments._finish(region, hair, neck, armpits, None, "t-shirt", "front", "flat")
    assert plain.info["guards"] == [] and plain.info["on_region"] == 1.0
    f = print_rule.frame(neck, armpits, "flat")
    assert min(f.depth(plain.corners[0]), f.depth(plain.corners[1])) >= 0  # below the collar
    # A garment far narrower than its chest: shrunk to the minimum, moved, never thinner.
    narrow = np.zeros((h, w), bool)
    narrow[200:900, 440:560] = True
    g = garments._finish(narrow, hair, neck, armpits, None, "t-shirt", "front", "flat")
    assert math.dist(g.corners[0], g.corners[1]) >= garments.MIN_WIDTH * 400 - 1
    assert g.info["guards"] and g.info["on_region"] < 1.0
    # A collar point far too high (a hood read as fabric): the area is kept in the chest band.
    high = garments._finish(region, hair, neck, armpits, None, "t-shirt", "front", "flat", top=(500.0, 700.0))
    centre = (sum(x for x, _ in high.corners) / 4, sum(y for _, y in high.corners) / 4)
    assert f.depth(centre) <= garments.CENTRE_MAX * 400 + 1
    assert "kept in the chest band" in high.info["guards"]


def test_a_worn_tees_shoulders_are_not_a_hood_but_a_hood_is() -> None:
    h, w = 1000, 1000
    probs = np.zeros((h, w, 6), np.float32)
    probs[..., 0] = 1.0
    yy, xx = np.ogrid[:h, :w]
    face = ((xx - 500) ** 2 / 70 ** 2 + (yy - 200) ** 2 / 90 ** 2) <= 1
    probs[face, 0], probs[face, garments.FACE] = 0.0, 1.0
    shoulders = ((380.0, 360.0), (620.0, 360.0))
    neck = (500.0, 320.0)
    tee = np.zeros((h, w), bool)
    tee[320:900, 330:670] = True  # the body, from the collar down
    tee[300:340, 360:640] &= ~((xx[:, 360:640] - 500) ** 2 < 60 ** 2)[0]  # the shoulders reach the collar
    tee[300:320, 360:440] = True
    tee[300:320, 560:640] = True
    mass = garments._hood_mass(tee, probs, shoulders, neck)
    assert not garments._is_hood(mass), mass
    hood = tee.copy()
    hood[120:320, 380:450] = True  # fabric rising beside the face on both sides
    hood[120:320, 550:620] = True
    hood &= ~face
    mass = garments._hood_mass(hood, probs, shoulders, neck)
    assert garments._is_hood(mass), mass


def test_with_the_switch_several_garments_get_one_area_each_and_a_prop_gets_none() -> None:
    size = (1500, 800)
    img = Image.new("RGB", size, BACKGROUND)
    mask = np.zeros((size[1], size[0]), bool)
    for i, colour in enumerate(((200, 60, 60), (60, 90, 200), (60, 160, 90))):
        rgb, m = _tee(size, (0.03 + i * 0.25, 0.15, 0.25 + i * 0.25, 0.65), colour=colour)
        img.paste(Image.fromarray(rgb), (0, 0), Image.fromarray((m * 255).astype(np.uint8)))
        mask |= m
    d = ImageDraw.Draw(img)
    d.ellipse([1180, 200, 1380, 400], fill=(205, 175, 120))  # a hat beside them
    yy, xx = np.ogrid[:size[1], :size[0]]
    mask |= ((xx - 1280) ** 2 + (yy - 300) ** 2) <= 100 ** 2
    found = _detect(np.asarray(img), mask, several=True).garments
    assert len(found) == 3
    centres = sorted(sum(x for x, _ in g.corners) / 4 / size[0] for g in found)
    for c, (lo, hi) in zip(centres, ((0.03, 0.25), (0.28, 0.50), (0.53, 0.75)), strict=True):
        assert lo < c < hi
    assert all(not (1150 < sum(x for x, _ in g.corners) / 4 < 1400) for g in found)


def test_no_network_and_the_same_answer_twice(monkeypatch) -> None:
    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("network used at runtime")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    rgb, mask = _tee((1000, 1000), (0.3, 0.2, 0.7, 0.8), angle=9)
    mine = {"t-shirt flat": {"width": 0.6, "top": 0.25, "aspect": 1.0, "n": 4}}
    first = [g.corners for g in _detect(rgb, mask, personal=mine).garments]
    again = [g.corners for g in _detect(rgb, mask, personal=mine).garments]
    assert first == again and first


# --- the seller's own placement --------------------------------------------------------------------


def test_observe_reads_back_the_ratios_an_area_was_built_with() -> None:
    neck, armpits = (500.0, 200.0), ((340.0, 330.0), (660.0, 330.0))
    area = print_rule.print_area(neck, armpits, kind="t-shirt", layout="flat")
    o = print_rule.observe(neck, armpits, area.corners, kind="t-shirt", layout="flat", suggested=area.corners)
    std = print_rule.STANDARD["t-shirt worn"]
    assert o["key"] == "t-shirt flat"
    assert o["width"] == pytest.approx(std["width"], abs=1e-3) and o["aspect"] == pytest.approx(std["aspect"], abs=1e-3)
    # Replaced, not adjusted (another garment, another place): not learnt from.
    elsewhere = [(x + 600, y) for x, y in area.corners]
    assert print_rule.observe(neck, armpits, elsewhere, kind="t-shirt", layout="flat", suggested=area.corners) is None
    table = print_rule.personal_table([o, {**o, "width": 0.5}, {**o, "width": 0.6}])
    ratios, source = print_rule.ratios_for("t-shirt", None, "flat", personal=table)
    assert source.startswith("yours") and ratios["width"] < std["width"]  # blended toward the seller's
    assert print_rule.ratios_for("t-shirt", None, "on a person", personal=table)[1] == "t-shirt worn"


async def _prepared(mw, client, template_id: str, name: str, png: bytes) -> dict:  # noqa: F811
    res = await mw[client].post(f"/api/mockup-templates/{template_id}/photos", data={"name": name, "rights_confirmed": "true"},
                                files={"file": (f"{name}.png", png, "image/png")})
    assert res.status_code == 201, res.text
    async with mw["sm"]() as s:
        photo = await work.prepare(s, mw["storage"], uuid.UUID(res.json()["id"]))
        assert photo.status == "ready", photo.error
    return (await mw[client].get(f"/api/mockup-photos/{res.json()['id']}")).json()


async def _template(mw, client: str, name: str) -> str:  # noqa: F811
    res = await mw[client].post("/api/mockup-templates", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _moved(area: list, down: float, narrower: float) -> list:
    cx = sum(x for x, _ in area) / 4
    return [[round(cx + (x - cx) * (1 - narrower), 4), round(y + down, 4)] for x, y in area]


async def test_confirmed_placement_changes_this_sellers_next_photo_only_and_reset_forgets_it(mw) -> None:  # noqa: F811
    png = samples.blank((214, 60, 60), size=(700, 800))
    ta, tb = await _template(mw, "a", "Mine"), await _template(mw, "b", "Theirs")
    first = await _prepared(mw, "a", ta, "one", png)
    standard = first["areas"][0]["detected_area"]
    for photo in (first, await _prepared(mw, "a", ta, "two", png)):
        a = photo["areas"][0]
        res = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": [
            {"print_area": _moved(a["detected_area"], 0.03, 0.15), "detected_area": a["detected_area"], "source": "seller"}]})
        assert res.status_code == 200, res.text
    mine = (await mw["a"].get("/api/mockup-placement")).json()
    assert mine["photos"] == 2 and mine["groups"][0]["in_use"]
    third = await _prepared(mw, "a", ta, "three", png)
    learnt = third["areas"][0]["detected_area"]
    assert third["own_placement"] == 2
    width = lambda q: q[1][0] - q[0][0]  # noqa: E731
    assert width(learnt) < width(standard) - 0.01 and learnt[0][1] > standard[0][1] + 0.005
    card = next(t for t in (await mw["a"].get("/api/mockup-templates")).json() if t["id"] == ta)
    assert card["own_placement"] == 2
    # Another account: its own suggestions, untouched; nothing of the first account visible.
    theirs = await _prepared(mw, "b", tb, "one", png)
    assert theirs["areas"][0]["detected_area"] == standard and theirs["own_placement"] is None
    assert (await mw["b"].get("/api/mockup-placement")).json()["photos"] == 0
    assert (await mw["b"].delete("/api/mockup-placement")).status_code == 200
    assert (await mw["a"].get("/api/mockup-placement")).json()["photos"] == 2  # b's reset is b's own
    # Reset: the standard again for new photos.
    assert (await mw["a"].delete("/api/mockup-placement")).json()["photos"] == 0
    fourth = await _prepared(mw, "a", ta, "four", png)
    assert fourth["areas"][0]["detected_area"] == standard and fourth["own_placement"] is None


async def test_unconfirming_a_photo_forgets_what_it_taught(mw) -> None:  # noqa: F811
    png = samples.blank((60, 90, 200), size=(700, 800))
    t = await _template(mw, "a", "Mine")
    photo = await _prepared(mw, "a", t, "one", png)
    a = photo["areas"][0]
    body = {"areas": [{"print_area": _moved(a["detected_area"], 0.02, 0.1), "detected_area": a["detected_area"],
                       "source": "seller"}]}
    assert (await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={**body, "confirm": True})).status_code == 200
    assert (await mw["a"].get("/api/mockup-placement")).json()["photos"] == 1
    assert (await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json=body)).status_code == 200
    assert (await mw["a"].get("/api/mockup-placement")).json()["photos"] == 0


def test_the_labels_say_type_and_layout() -> None:
    assert placement.label("t-shirt worn") == "tees worn" and placement.label("hoodie flat") == "hoodies laid flat"


def test_redetect_keeps_confirmed_areas_and_renews_waiting_suggestions() -> None:
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.ops.mockup_redetect import apply_found

    old = [[0.3, 0.3], [0.6, 0.3], [0.6, 0.6], [0.3, 0.6]]
    moved = [[0.32, 0.35], [0.58, 0.35], [0.58, 0.62], [0.32, 0.62]]
    new = [[0.31, 0.28], [0.61, 0.28], [0.61, 0.63], [0.31, 0.63]]
    found = [{"print_area": new, "confidence": 0.8, "kind": "t-shirt", "geometry": {"neck": [0.45, 0.2]}, "notes": []}]

    def photo(source: str, confirmed: bool) -> SimpleNamespace:
        area = moved if source == "seller" else old
        return SimpleNamespace(garments=[{"print_area": area, "detected_area": old, "source": source}], print_area=area,
                               print_area_source=source, print_area_confidence=0.5, version=1,
                               confirmed_at=datetime.now(timezone.utc) if confirmed else None)

    waiting = photo("default", False)
    assert apply_found(waiting, found, 0.8) == "replaced"
    assert waiting.print_area == new and waiting.garments[0]["geometry"] and waiting.confirmed_at is None
    confirmed = photo("seller", True)
    assert apply_found(confirmed, found, 0.8) == "suggestions"
    assert confirmed.print_area == moved and confirmed.garments[0]["print_area"] == moved  # the seller's area stays
    assert confirmed.garments[0]["detected_area"] == new and confirmed.garments[0]["geometry"]
    assert confirmed.confirmed_at is not None
    two = photo("default", True)
    assert apply_found(two, found + found, 0.8, several=True) == "kept" and two.garments[0]["detected_area"] == old


# --- one area per photo by default; the torso -------------------------------------------------------


def test_one_area_by_default_on_the_main_garment_and_none_on_a_hat() -> None:
    size = (1500, 900)
    img = Image.new("RGB", size, BACKGROUND)
    mask = np.zeros((size[1], size[0]), bool)
    # A large tee in the middle, a smaller one at the edge, a hat and a bag (props).
    for box, colour in (((0.33, 0.12, 0.67, 0.82), (200, 60, 60)), ((0.03, 0.40, 0.22, 0.80), (60, 90, 200))):
        rgb, m = _tee(size, box, colour=colour)
        img.paste(Image.fromarray(rgb), (0, 0), Image.fromarray((m * 255).astype(np.uint8)))
        mask |= m
    d = ImageDraw.Draw(img)
    d.ellipse([1150, 120, 1400, 330], fill=(205, 175, 120))  # a hat
    d.rectangle([1180, 520, 1420, 760], fill=(90, 60, 40))  # a bag
    yy, xx = np.ogrid[:size[1], :size[0]]
    mask |= ((xx - 1275) ** 2 / 125 ** 2 + (yy - 225) ** 2 / 105 ** 2) <= 1
    mask[520:760, 1180:1420] = True
    found = _detect(np.asarray(img), mask)
    assert len(found.garments) == 1 and len(found.candidates) >= 2  # the others are only candidates
    cx = sum(x for x, _ in found.garments[0].corners) / 4 / size[0]
    assert 0.40 < cx < 0.60  # on the main tee
    for g in found.candidates:  # and never an area on the hat or the bag
        gx = sum(x for x, _ in g.corners) / 4
        assert not 1130 < gx < 1430


def test_a_wide_arm_pose_keeps_the_area_on_the_torso() -> None:
    png, truth = samples.person("hair", colour=(90, 110, 160))
    img = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = img.size
    # A long sleeve and arm held out and down against the body on one side, the same fabric:
    # the body's measured width (and so its middle) runs onto the arm.
    arm = [(w * 0.76, h * 0.30), (w * 0.99, h * 0.40), (w * 0.99, h * 0.95), (w * 0.76, h * 0.95)]
    ImageDraw.Draw(img).polygon(arm, fill=(90, 110, 160))
    classes = truth["classes"].copy()
    ImageDraw.Draw(classes).polygon(arm, fill=4)
    truth = {**truth, "classes": classes}
    small = img.copy()
    small.thumbnail((garments.WORK, garments.WORK))
    sw, sh = small.size
    mask = np.asarray(classes.resize((sw, sh), Image.NEAREST)) > 0
    found = garments.detect(np.asarray(small), samples.TruthRunner(truth), Drawn(mask), "tshirt").garments
    assert len(found) == 1
    xs = [x / sw for x, _ in found[0].corners]
    # The torso runs from 0.25 to 0.75 of the photo; the shoulders' midpoint is 0.5.
    assert min(xs) > 0.25 and max(xs) < 0.75, xs
    assert abs(sum(xs) / 4 - 0.5) < 0.05


def _two_tees() -> bytes:
    w, h = 1400, 760
    canvas = Image.new("RGB", (w, h), (232, 233, 236))
    for i, colour in enumerate(((200, 60, 60), (50, 90, 200))):
        tee = Image.open(io.BytesIO(samples.blank(colour, size=(640, 740), background=(232, 233, 236)))).convert("RGB")
        canvas.paste(tee, (40 + i * 700, 10))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


async def test_one_area_per_photo_unless_the_template_has_several_garments(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Two tees")
    card = next(x for x in (await mw["a"].get("/api/mockup-templates")).json() if x["id"] == t)
    assert card["several_garments"] is False
    waiting = await _prepared(mw, "a", t, "waiting", _two_tees())
    confirmed = await _prepared(mw, "a", t, "kept", _two_tees())
    assert len(waiting["areas"]) == 1 and len(confirmed["areas"]) == 1
    res = await mw["a"].put(f"/api/mockup-photos/{confirmed['id']}/areas", json={"confirm": True, "areas": [
        {"print_area": confirmed["areas"][0]["print_area"], "detected_area": confirmed["areas"][0]["detected_area"],
         "source": "default"}]})
    assert res.status_code == 200
    # Another account cannot switch it.
    assert (await mw["b"].patch(f"/api/mockup-templates/{t}", json={"several_garments": True})).status_code == 404
    on = (await mw["a"].patch(f"/api/mockup-templates/{t}", json={"several_garments": True})).json()
    assert on["several_garments"] is True
    by_id = {p["id"]: p for p in on["photos"]}
    assert len(by_id[waiting["id"]]["areas"]) == 2  # every garment found, still waiting for a check
    assert len(by_id[confirmed["id"]]["areas"]) == 1 and by_id[confirmed["id"]]["confirmed"]  # untouched
    third = await _prepared(mw, "a", t, "new", _two_tees())
    assert len(third["areas"]) == 2  # new photos follow the switch
    off = (await mw["a"].patch(f"/api/mockup-templates/{t}", json={"several_garments": False})).json()
    assert all(len(p["areas"]) == 1 for p in off["photos"])


async def test_the_angle_travels_with_apply_to_selected(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Angles")
    src = await _prepared(mw, "a", t, "one", samples.blank((60, 90, 200), size=(700, 800)))
    dst = await _prepared(mw, "a", t, "two", samples.blank((200, 60, 60), size=(700, 800)))
    a = src["areas"][0]["detected_area"]
    w, h = 700, 800
    cx, cy = sum(p[0] for p in a) / 4 * w, sum(p[1] for p in a) / 4 * h
    hw, hh = (a[1][0] - a[0][0]) * w / 2, (a[3][1] - a[0][1]) * h / 2
    t8 = math.radians(8)
    turned = [[round((cx + su * hw * math.cos(t8) - sv * hh * math.sin(t8)) / w, 4),
               round((cy + su * hw * math.sin(t8) + sv * hh * math.cos(t8)) / h, 4)]
              for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    res = await mw["a"].put(f"/api/mockup-photos/{src['id']}/areas", json={"confirm": True, "areas": [
        {"print_area": turned, "detected_area": a, "source": "seller"}]})
    assert res.status_code == 200
    copied = (await mw["a"].post(f"/api/mockup-photos/{src['id']}/copy", json={"photo_ids": [dst["id"]], "fit": True})).json()
    q = copied[0]["areas"][0]["print_area"]
    angle = math.degrees(math.atan2((q[1][1] - q[0][1]) * h, (q[1][0] - q[0][0]) * w))
    assert angle == pytest.approx(8.0, abs=0.5)
    # Another account cannot copy from it.
    assert (await mw["b"].post(f"/api/mockup-photos/{src['id']}/copy", json={"photo_ids": [dst["id"]]})).status_code == 404


async def test_the_single_area_command_reduces_waiting_photos_only(mw) -> None:  # noqa: F811
    from datetime import datetime, timezone

    from app.db.models import MockupPhoto
    from app.ops.mockup_single_area import run

    t = await _template(mw, "a", "Old")
    waiting = await _prepared(mw, "a", t, "waiting", _two_tees())
    other = await _template(mw, "a", "Several kept")
    several = await _prepared(mw, "a", other, "both", _two_tees())
    async with mw["sm"]() as s:  # as photos prepared before one area per photo: every garment
        for pid, confirm in ((waiting["id"], False), (several["id"], True)):
            photo = await s.get(MockupPhoto, uuid.UUID(pid))
            rows = [dict(c) for c in photo.analysis["candidates"]]
            for row in rows:
                row.pop("main", None)
            photo.garments = rows
            if confirm:
                photo.confirmed_at = datetime.now(timezone.utc)
        await s.commit()
    assert "would be" in await run(mw["sm"], apply=False)
    out = await run(mw["sm"], apply=True)
    assert out.startswith("1 waiting photo(s) reduced") and "1 template(s)" in out, out
    by_id = {p["id"]: p for x in (await mw["a"].get("/api/mockup-templates")).json() for p in x["photos"]}
    assert len(by_id[waiting["id"]]["areas"]) == 1
    assert len(by_id[several["id"]]["areas"]) == 2  # the seller confirmed several: kept, switch on
    kept = next(x for x in (await mw["a"].get("/api/mockup-templates")).json() if x["id"] == other)
    assert kept["several_garments"] is True
