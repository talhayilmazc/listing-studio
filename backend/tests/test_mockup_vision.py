"""The template analysis (pipeline/mockup_vision.py) and what it changes in a mockup.

* The print area is found on the chest: from the pose on a person, from the
  outline on a flat lay; low confidence asks the seller to check.
* The design goes behind hair and arms (the occluder), and the seller can switch it off.
* A crease bends and breaks the print; a heathered fabric's speckle is not a crease.
* Anything failing falls back and says so; nothing crashes.
* The same inputs give the same output; nothing goes to the network at runtime;
  no Etsy call and no AI call.

A drawn person is not a photograph: the real models rightly find no person in
it. The person cases give the analysis the drawing's own exact answer (a
stand-in runner); the real bundled models are exercised on the rest.
"""

from __future__ import annotations

import io
import socket
import uuid

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from app.pipeline import mockup_vision as vision
from app.pipeline import mockups
from tests import mockup_samples as samples
from app.db.models import MockupPhoto
from tests.test_mockups import _NoCalls, _confirm, _new_template, _photo, _template, mw  # noqa: F401  (fixtures)
from tests.test_multi_shop import world  # noqa: F401  (fixture)


def _px(jpeg: bytes) -> Image.Image:
    return Image.open(io.BytesIO(jpeg)).convert("RGB")


def _close(a: tuple, b: tuple, tolerance: int = 20) -> bool:
    return sum(abs(x - y) for x, y in zip(a, b)) <= tolerance


class _Broken:
    def segment(self, rgb):  # noqa: ANN001, ANN201
        raise RuntimeError("model fault")

    def landmarks(self, rgb, box):  # noqa: ANN001, ANN201
        raise RuntimeError("model fault")


# --- the print area ---------------------------------------------------------------------------


def test_a_persons_print_area_is_on_the_chest_from_the_pose() -> None:
    png, truth = samples.person("hair")
    prepared = mockups.prepare_blank(png, runner=samples.TruthRunner(truth))
    assert (prepared.method, prepared.mask_source, prepared.view) == ("pose", "segmentation", "front")
    assert prepared.confidence >= vision.CHECK_BELOW
    w, h = truth["classes"].size
    shoulders_y, hips_y = truth["points"][11][1] / h, truth["points"][23][1] / h
    axis = (truth["points"][12][0] + truth["points"][11][0]) / 2 / w
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = prepared.print_area
    assert abs((tlx + trx) / 2 - axis) < 0.03, prepared.print_area  # centred on the garment's axis
    assert shoulders_y < tly and bly < hips_y, prepared.print_area  # on the chest: below the collar, above the hips
    assert bly - tly > 0.15 and trx - tlx > 0.15  # a real chest print, not a sliver
    # Inside the shirt (or under the hair over it), not on the arms or the background.
    classes = np.asarray(truth["classes"].resize((w, h)))
    xs, ys = np.meshgrid(np.linspace(tlx, trx, 20) * (w - 1), np.linspace(tly, bly, 20) * (h - 1))
    on = np.isin(classes[ys.astype(int), xs.astype(int)], (1, 4))
    assert on.mean() > 0.95, on.mean()
    assert prepared.occluder is not None


def test_a_worn_garment_stays_upright_when_the_shoulders_tilt() -> None:
    png, truth = samples.person("hair")
    truth = {**truth, "points": {**truth["points"], 11: (truth["points"][11][0], truth["points"][11][1] + 80),
                                 23: (truth["points"][23][0], truth["points"][23][1] + 80)}}
    area = mockups.prepare_blank(png, runner=samples.TruthRunner(truth)).print_area
    # Pose and camera tilt the shoulder line; a print on a worn garment is not rotated.
    assert abs(area[1][1] - area[0][1]) < 0.005 and abs(area[3][0] - area[0][0]) < 0.005, area


def test_a_flat_lays_print_area_is_below_the_collar_between_the_side_seams() -> None:
    prepared = mockups.prepare_blank(samples.blank(), runner="default")  # the real models: no person
    assert prepared.method == "outline" and prepared.confidence >= vision.CHECK_BELOW
    assert len(prepared.garments) == 1 and prepared.view == "front"
    # The synthetic tee: collar at 15% of the height, side seams at 29% and 71% of the width.
    for x, y in prepared.print_area:
        assert 0.29 < x < 0.71 and 0.15 < y < 0.9, prepared.print_area
    assert prepared.print_area[0][1] > 0.17  # under the collar, not on it
    assert prepared.occluder is None  # nothing in front of a flat lay


def test_low_confidence_still_makes_a_template_but_asks_for_a_check() -> None:
    png, truth = samples.person("hair")
    unsure = mockups.prepare_blank(png, runner=samples.TruthRunner(truth, presence=0.75, visibility=0.7))
    assert unsure.confidence < vision.CHECK_BELOW and unsure.print_area
    lost = mockups.prepare_blank(png, runner=samples.TruthRunner(truth, presence=0.3, visibility=0.4))
    assert lost.confidence < vision.CHECK_BELOW and lost.print_area


# --- occlusion --------------------------------------------------------------------------------


def test_the_design_goes_behind_a_hair_strand_and_can_be_switched_off() -> None:
    png, truth = samples.person("hair")
    prepared = mockups.prepare_blank(png, runner=samples.TruthRunner(truth))
    area = [[0.30, 0.30], [0.70, 0.30], [0.70, 0.62], [0.30, 0.62]]  # a print under the strand
    design = mockups.load_design(samples.design("opaque"))
    maps = mockups.build_maps(prepared.image, prepared.mask, prepared.occluder)
    behind = _px(mockups.render(maps, design, mockups.Settings(area, 1.0, 0.0)))
    over = _px(mockups.render(maps, design, mockups.Settings(area, 1.0, 0.0, occlusion=False)))
    blank = _px(prepared.image)
    w, h = maps.width, maps.height
    on_hair = (round(0.425 * w), round(0.38 * h))  # inside the strand, inside the print
    assert np.asarray(truth["classes"])[on_hair[1], on_hair[0]] == 1
    assert _close(behind.getpixel(on_hair), blank.getpixel(on_hair)), "the hair stays in front"
    assert not _close(over.getpixel(on_hair), behind.getpixel(on_hair), 30), "without occlusion the ink covers it (darkened by the hair)"
    on_shirt = (round(0.6 * w), round(0.36 * h))
    assert not _close(behind.getpixel(on_shirt), blank.getpixel(on_shirt), 60), "the print is still on the shirt"


def test_the_design_goes_behind_crossed_arms() -> None:
    png, truth = samples.person("arms")
    prepared = mockups.prepare_blank(png, runner=samples.TruthRunner(truth))
    area = [[0.30, 0.45], [0.70, 0.45], [0.70, 0.80], [0.30, 0.80]]  # a large print reaching the arms
    maps = mockups.build_maps(prepared.image, prepared.mask, prepared.occluder)
    out = _px(mockups.render(maps, mockups.load_design(samples.design("opaque")), mockups.Settings(area, 1.0, 0.0)))
    blank = _px(prepared.image)
    w, h = maps.width, maps.height
    on_arm = (round(0.5 * w), round(0.63 * h))
    assert np.asarray(truth["classes"])[on_arm[1], on_arm[0]] == 2
    assert _close(out.getpixel(on_arm), blank.getpixel(on_arm)), "the arm stays in front of the print"


# --- folds and creases ------------------------------------------------------------------------


def _creased(colour=(238, 238, 236)) -> bytes:
    """A flat-lay tee with one sharp vertical crease through the chest."""
    w, h = 1200, 1400
    base = Image.open(io.BytesIO(samples.blank(colour, size=(w, h)))).convert("RGB")
    line = Image.new("L", (w, h), 0)
    ImageDraw.Draw(line).line([(0.5 * w, 0.18 * h), (0.5 * w, 0.62 * h)], fill=255, width=6)
    line = line.filter(ImageFilter.GaussianBlur(1.5))
    base.paste(Image.new("RGB", (w, h), (40, 40, 40)), (0, 0), line)
    buf = io.BytesIO()
    base.save(buf, format="PNG")
    return buf.getvalue()


def test_a_crease_bends_the_print_and_breaks_it_along_its_line() -> None:
    prepared = mockups.prepare_blank(_creased(), runner=None)
    area = [[0.36, 0.22], [0.64, 0.22], [0.64, 0.58], [0.36, 0.58]]
    maps = mockups.build_maps(prepared.image, prepared.mask)
    design = mockups.load_design(samples.design("opaque"))
    flat = np.asarray(_px(mockups.render(maps, design, mockups.Settings(area, 1.0, 0.0))), dtype=np.int32)
    folded = np.asarray(_px(mockups.render(maps, design, mockups.Settings(area, 1.0, 1.0))), dtype=np.int32)
    blank = np.asarray(_px(prepared.image), dtype=np.int32)
    w, h = maps.width, maps.height
    rows = slice(round(0.30 * h), round(0.40 * h))
    centre = round(0.5 * w)
    def red(img: np.ndarray, cols: slice) -> float:  # the design's red ink, not the fabric's grey
        part = img[rows, cols]
        return float((part[..., 0] - part[..., 1]).mean())

    line = slice(centre - 1, centre + 2)
    # Broken along the line: no ink on the crease itself at full fold strength; at 0, ink (dark, as on a crease).
    assert red(folded, line) < 8 and red(flat, line) > 15, (red(folded, line), red(flat, line))
    assert red(blank, line) < 5
    # Bent near it: the print moves there, much more than far from the crease.
    near = np.abs(folded[rows, centre - 30:centre - 6] - flat[rows, centre - 30:centre - 6]).mean()
    far = np.abs(folded[rows, round(0.40 * w):round(0.42 * w)] - flat[rows, round(0.40 * w):round(0.42 * w)]).mean()
    assert near > far * 1.5 and near > 3, (near, far)


def test_a_heathered_fabric_is_not_read_as_creases() -> None:
    prepared = mockups.prepare_blank(samples.blank((150, 152, 156), heather=True), runner=None)
    maps = mockups.build_maps(prepared.image, prepared.mask)
    x0, y0, x1, y1 = (round(0.38 * maps.width), round(0.25 * maps.height), round(0.62 * maps.width), round(0.55 * maps.height))
    hide = maps.hide.crop(x0, y0, x1 - x0, y1 - y0)
    holes = (hide > 0.3).ifthenelse(1, 0).avg()
    assert holes < 0.01, holes


# --- fallback, determinism, no network --------------------------------------------------------


def test_a_model_fault_falls_back_to_the_outline_and_says_so() -> None:
    prepared = mockups.prepare_blank(samples.blank(), runner=_Broken())
    assert prepared.method == "outline" and prepared.print_area
    assert prepared.note == vision.NOTES["fallback"] and prepared.details.get("failed")
    assert prepared.confidence < vision.CHECK_BELOW  # the seller is asked to check
    # No models at all: the outline, as before.
    plain = mockups.prepare_blank(samples.blank(), runner=None)
    assert plain.method == "outline" and plain.details == {"models": False}
    # Nothing to go on (a white garment on white): the old default area, and the seller is told.
    blank = mockups.prepare_blank(samples.blank((246, 246, 246), background=(250, 250, 250)), runner=None)
    assert (blank.method, blank.confidence) == ("default", 0.0) and blank.note == vision.NOTES["default"]


def test_the_bundled_models_run_offline_and_give_the_same_answer_twice(monkeypatch) -> None:
    runner = vision.default_runner()
    if runner is None:
        pytest.skip("the models are bundled into the image at build time (docker/Dockerfile)")

    def refuse(*args, **kwargs):
        raise AssertionError("a network connection was attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    png, _ = samples.person("hair")
    rgb = np.asarray(Image.open(io.BytesIO(png)).convert("RGB").resize((400, 500)))
    probs = runner.segment(rgb)
    assert probs.shape == (500, 400, 6) and np.allclose(probs.sum(axis=-1), 1.0, atol=1e-3)
    points, presence = runner.landmarks(rgb, (0, 0, 400, 500))
    assert points.shape == (33, 3) and 0.0 <= presence <= 1.0
    first = mockups.prepare_blank(png, runner="default")
    again = mockups.prepare_blank(png, runner="default")
    assert first == again
    design = mockups.load_design(samples.design())
    one = mockups.render(mockups.build_maps(first.image, first.mask, first.occluder), design, mockups.Settings(first.print_area))
    two = mockups.render(mockups.build_maps(again.image, again.mask, again.occluder), design, mockups.Settings(again.print_area))
    assert one == two


def test_maps_keep_only_the_print_window_and_give_the_same_mockup() -> None:
    """A run holds up to 20 templates' maps: only what a print can reach is kept, same pixels."""
    png, truth = samples.person("hair")
    prepared = mockups.prepare_blank(png, runner=samples.TruthRunner(truth))
    whole = mockups.build_maps(prepared.image, prepared.mask, prepared.occluder)
    kept = mockups.build_maps(prepared.image, prepared.mask, prepared.occluder, prepared.print_area)
    assert (kept.left, kept.top) != (0, 0)
    assert kept.mask.width * kept.mask.height < 0.5 * whole.mask.width * whole.mask.height
    for size, fold in ((1.0, 1.0), (0.6, 0.0), (0.2, 0.5)):
        design = mockups.load_design(samples.design())
        settings = mockups.Settings(prepared.print_area, size, fold)
        assert mockups.render(kept, design, settings) == mockups.render(whole, design, settings)


# --- through the worker and the API -----------------------------------------------------------


async def test_a_person_photo_keeps_an_occluder_waits_for_the_check_and_occlusion_can_be_switched_off(mw, monkeypatch) -> None:  # noqa: F811
    guard = _NoCalls(monkeypatch)
    png, truth = samples.person("hair")
    monkeypatch.setattr(vision, "default_runner", lambda: samples.TruthRunner(truth, presence=0.75, visibility=0.7))
    template = await _new_template(mw, "Model")
    card = await _photo(mw, template["id"], "Model, front", blank=png, confirm=False)
    async with mw["sm"]() as s:
        done = await s.get(MockupPhoto, uuid.UUID(card["id"]))
        assert done.occluder_key and mw["storage"].exists(done.occluder_key)
    assert (card["method"], card["needs_check"], card["has_occluder"], card["occlusion"]) == ("pose", True, True, True)
    assert card["analysis_note"] == vision.NOTES["pose"]
    # The seller moves the area and confirms: no longer asked. Occlusion off.
    moved = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.6], [0.3, 0.6]]
    confirmed = await _confirm(mw, card, [{"print_area": moved, "detected_area": card["areas"][0]["detected_area"]}])
    assert confirmed["areas"][0]["detected_area"] == card["areas"][0]["print_area"]  # the suggestion is kept
    off = (await mw["a"].patch(f"/api/mockup-photos/{card['id']}", json={"occlusion": False})).json()
    assert (off["needs_check"], off["occlusion"]) == (False, False)
    # The preview draws, without any network, model API or Etsy call.
    assert (await mw["a"].post(f"/api/mockup-photos/{card['id']}/preview", json={})).status_code == 200
    assert guard.calls == []
    # Another account cannot touch it.
    assert (await mw["b"].patch(f"/api/mockup-photos/{card['id']}", json={"occlusion": True})).status_code == 404


async def test_a_sure_flat_lay_is_still_only_a_suggestion(mw) -> None:  # noqa: F811
    template = await _template(mw, "White tee", confirm=False)
    photo = template["photo"]
    assert photo["method"] == "outline" and photo["has_occluder"] is False and photo["view"] == "front"
    assert len(photo["areas"]) == 1 and photo["areas"][0]["source"] == "default"
    # However sure the analysis is, the seller confirms it once.
    assert photo["needs_check"] is True and photo["confirmed"] is False
