"""Part B: the design fills its print area (B1), designs can be deleted before they are a
listing's (B2), and the occluder only holds hair, skin and face that reach the area from
outside (B4). pipeline/mockups.py, pipeline/occlusion.py, workers/mockups.py,
api/mockups.py, ops/mockup_occlusion.py.

Synthetic images only; no network.
"""

from __future__ import annotations

import io
import json
import socket
import uuid
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import select

from app.db.models import Asset, GeneratedContent, MockupDesign, MockupDesignVersion, MockupOutput, MockupPhoto
from app.pipeline import mockups, occlusion
from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_mockups import _new_batch, mw  # noqa: F401  (fixture)
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_print_geometry import _prepared, _template

CASES = json.loads((Path(__file__).parent / "fixtures" / "placement_cases.json").read_text())["cases"]


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _padded(size=(1000, 1000), box=(200, 300, 800, 700), soft_row: int | None = 299) -> bytes:
    """An opaque red rectangle in a transparent canvas; one faint (alpha 20) row above it."""
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    l, t, r, b = box
    im.paste(Image.new("RGBA", (r - l, b - t), (220, 30, 30, 255)), (l, t))
    if soft_row is not None:
        im.paste(Image.new("RGBA", (r - l, 1), (220, 30, 30, 20)), (l, soft_row))
    return _png(im)


# --- B1: the design fills the area -----------------------------------------------------------------------


def test_transparent_padding_is_trimmed_with_a_safety_edge_and_soft_pixels_kept() -> None:
    trim = mockups.design_trim(_padded())
    # The faint row (alpha 20 > 8) is a drawn pixel and stays; 2 px safety edge around it all.
    assert trim == {"box": [198, 297, 802, 702], "size": [1000, 1000]}
    image = mockups.load_design(_padded(), trim)
    assert (image.width, image.height) == (604, 405)
    # Without a stored box the same box is measured.
    again = mockups.load_design(_padded())
    assert (again.width, again.height) == (604, 405)


async def test_an_uploaded_design_keeps_its_file_and_stores_its_trim_box(mw) -> None:  # noqa: F811
    batch = await _new_batch(mw)
    res = await mw["a"].post(f"/api/batches/{batch}/designs", files={"file": ("PAD1.png", _padded(), "image/png")})
    assert res.status_code == 201, res.text
    async with mw["sm"]() as s:
        design = await s.get(MockupDesign, uuid.UUID(res.json()["id"]))
        assert design.trims["front"]["box"] == [198, 297, 802, 702]
        stored = Image.open(io.BytesIO(mw["storage"].get(design.design_key)))
        assert stored.size == (1000, 1000)  # the original canvas, untouched


def test_a_design_of_the_areas_proportions_fills_it_corner_to_corner() -> None:
    area = [(200.0, 200.0), (600.0, 200.0), (600.0, 500.0), (200.0, 500.0)]  # 400 x 300
    placed = mockups.place(area, (800, 600), mockups.DEFAULT_SIZE)
    assert mockups.DEFAULT_SIZE == 1.0
    assert placed.box == pytest.approx((0.0, 0.0, 1.0, 1.0))
    corners = [mockups.apply(placed.matrix, x, y) for x, y in ((0, 0), (placed.scaled[0], 0), placed.scaled, (0, placed.scaled[1]))]
    assert [v for c in corners for v in c] == pytest.approx([v for c in area for v in c], abs=1e-6)


@pytest.mark.parametrize(("design", "box"), [
    ((800, 400), (0.0, 0.0, 1.0, 2 / 3)),  # wider: touches left and right, top-aligned
    ((300, 600), (0.3125, 0.0, 0.375, 1.0)),  # taller: touches top and bottom, centred
])
def test_otherwise_it_touches_the_area_on_its_limiting_side_never_stretched(design, box) -> None:  # noqa: ANN001
    area = [(200.0, 200.0), (600.0, 200.0), (600.0, 500.0), (200.0, 500.0)]
    placed = mockups.place(area, design, 1.0)
    assert placed.box == pytest.approx(box)
    u0, v0, du, dv = placed.box
    # No stretch: the placed box keeps the design's proportions (in photo pixels).
    assert (du * 400) / (dv * 300) == pytest.approx(design[0] / design[1])
    assert placed.scaled[0] / placed.scaled[1] == pytest.approx(design[0] / design[1], rel=0.01)


def test_no_design_pixel_is_cropped_when_printed() -> None:
    w, h = 900, 1000
    blank = samples.blank((240, 240, 238), size=(w, h))
    mask = _png(Image.new("L", (w, h), 255))
    corners = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.6], [0.3, 0.6]]  # 360 x 300 px
    design = mockups.load_design(_png(Image.new("RGBA", (720, 600), (20, 20, 200, 255))))  # same proportions
    maps = mockups.build_maps(mockups.prepare_blank(blank).image, mask, None, [corners])
    out = np.asarray(Image.open(io.BytesIO(mockups.render(maps, design, mockups.Settings(corners, 1.0, 0.0)))).convert("RGB")).astype(int)
    # Blue reaches every corner of the area (inside by 4 px) and stops at its edges.
    for x, y in ((0.3 * w + 4, 0.3 * h + 4), (0.7 * w - 5, 0.3 * h + 4), (0.7 * w - 5, 0.6 * h - 5), (0.3 * w + 4, 0.6 * h - 5)):
        px = out[int(y), int(x)]
        assert px[2] > px[0] + 60, (x, y, px)
    outside = out[int(0.6 * h + 8), int(0.5 * w)]
    assert abs(outside[2] - outside[0]) < 25


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_editor_preview_and_the_render_place_the_design_identically(case) -> None:  # noqa: ANN001
    # The same numbers are asserted by the editor's test (frontend/lib/areaEditor.test.ts).
    placed = mockups.place([tuple(c) for c in case["quad"]], tuple(case["design"]), case["size"])
    sw, sh = placed.scaled
    corners = [mockups.apply(placed.matrix, x, y) for x, y in ((0, 0), (sw, 0), (sw, sh), (0, sh))]
    flat = [v for c in corners for v in c]
    assert flat == pytest.approx([v for c in case["corners"] for v in c], abs=0.001)


async def test_new_photos_fill_by_default(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Fill")
    photo = await _prepared(mw, "a", t, "tee", samples.blank((200, 60, 60), size=(700, 800)))
    assert photo["size"] == 1.0


# --- B2: deleting designs ----------------------------------------------------------------------------------


SLOTS = json.dumps([{"name": "White", "suffix": "-white", "use_on": "light"},
                    {"name": "Black", "suffix": "-black", "use_on": "dark"}])


async def _upload(mw, batch, name: str, colour=(20, 20, 30), slots: bool = False) -> dict:  # noqa: ANN001, F811
    png = _png(Image.new("RGBA", (900, 900), (*colour, 255)))
    res = await mw["a"].post(f"/api/batches/{batch}/designs", data={"subgroups": SLOTS} if slots else None,
                             files={"file": (name, png, "image/png")})
    assert res.status_code == 201, res.text
    return res.json()


async def test_a_design_is_deleted_with_its_files_and_mockups(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Del")
    photo = await _prepared(mw, "a", t, "tee", samples.blank((238, 238, 236), size=(700, 800)))
    a = photo["areas"][0]
    await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": [
        {"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default"}]})
    batch = await _new_batch(mw)
    design = await _upload(mw, batch, "BR1.png")
    run = (await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [t]})).json()
    async with mw["sm"]() as s:
        await work.run(s, mw["storage"], uuid.UUID(run["id"]))
        row = await s.get(MockupDesign, uuid.UUID(design["id"]))
        keys = [row.design_key, *[o.key for o in (await s.execute(select(MockupOutput).where(MockupOutput.design_id == row.id))).scalars()]]
    assert len(keys) == 2 and all(mw["storage"].exists(k) for k in keys)
    assert (await mw["b"].delete(f"/api/mockup-designs/{design['id']}")).status_code == 404  # another account
    res = await mw["a"].delete(f"/api/mockup-designs/{design['id']}")
    assert res.status_code == 200 and res.json()["deleted"] is True
    assert not any(mw["storage"].exists(k) for k in keys)
    async with mw["sm"]() as s:
        assert await s.get(MockupDesign, uuid.UUID(design["id"])) is None
        assert (await s.execute(select(MockupOutput).where(MockupOutput.design_id == uuid.UUID(design["id"])))).first() is None


async def test_one_file_of_a_paired_design_can_be_removed(mw) -> None:  # noqa: F811
    batch = await _new_batch(mw)
    white = await _upload(mw, batch, "BR2-white.png", (20, 20, 30), slots=True)
    paired = await _upload(mw, batch, "BR2-black.png", (240, 240, 235), slots=True)
    assert paired["id"] == white["id"] and len(paired["versions"]) == 1
    version = paired["versions"][0]["id"]
    assert (await mw["b"].delete(f"/api/mockup-design-versions/{version}")).status_code == 404
    out = (await mw["a"].delete(f"/api/mockup-design-versions/{version}")).json()
    assert out["versions"] == [] and out["subgroup"] == "White"
    async with mw["sm"]() as s:
        assert await s.get(MockupDesignVersion, uuid.UUID(version)) is None
    # Its own file, with another file left: that file takes its place.
    await _upload(mw, batch, "BR2-black.png", (240, 240, 235), slots=True)
    res = (await mw["a"].delete(f"/api/mockup-designs/{white['id']}?only_file=true")).json()
    assert res["deleted"] is False and res["design"]["subgroup"] == "Black" and res["design"]["versions"] == []
    # Its own file and nothing left: the whole design goes.
    res = (await mw["a"].delete(f"/api/mockup-designs/{white['id']}?only_file=true")).json()
    assert res["deleted"] is True


async def test_a_design_used_by_a_listing_cannot_be_deleted_and_says_why(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Used")
    photo = await _prepared(mw, "a", t, "tee", samples.blank((238, 238, 236), size=(700, 800)))
    a = photo["areas"][0]
    await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": [
        {"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default"}]})
    batch = await _new_batch(mw)
    design = await _upload(mw, batch, "BR3.png")
    run = (await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [t]})).json()
    async with mw["sm"]() as s:
        await work.run(s, mw["storage"], uuid.UUID(run["id"]))
    assert (await mw["a"].post(f"/api/mockup-runs/{run['id']}/listings", json={})).status_code == 200
    res = await mw["a"].delete(f"/api/mockup-designs/{design['id']}")
    assert res.status_code == 409 and "photos of a listing group" in res.json()["detail"]
    async with mw["sm"]() as s:  # once the listing is written, it says so
        asset = (await s.execute(select(Asset).where(Asset.mockup_design_id == uuid.UUID(design["id"])))).scalars().first()
        s.add(GeneratedContent(tenant_id=asset.tenant_id, batch_id=asset.batch_id, asset_id=asset.id, title="t", tags=[],
                               description="d", approved=False))
        await s.commit()
    res = await mw["a"].delete(f"/api/mockup-designs/{design['id']}")
    assert res.status_code == 409 and "used by a listing" in res.json()["detail"]


# --- B4: what may go in front of the design ----------------------------------------------------------------


def _probs(h: int = 200, w: int = 200) -> np.ndarray:
    p = np.zeros((h, w, 6), np.float32)
    p[..., occlusion.CLOTHES] = 1.0
    return p


AREA = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]]


def test_only_a_persons_hair_skin_and_face_can_be_in_front() -> None:
    p = _probs()
    p[40:80, 40:80, occlusion.CLOTHES], p[40:80, 40:80, occlusion.BODY] = 0.1, 0.9  # an arm
    p[100:140, 100:140, occlusion.CLOTHES], p[100:140, 100:140, occlusion.OTHERS] = 0.1, 0.9  # "accessory"
    p[150:190, 10:50, occlusion.CLOTHES], p[150:190, 10:50, occlusion.BODY] = 0.6, 0.4  # more likely fabric
    front = occlusion.person_front(p, person=True)
    assert front[60, 60] > 0.9 and front[120, 120] == 0.0 and front[170, 30] == 0.0
    assert occlusion.person_front(p, person=False).max() == 0.0  # nobody in the photo: nothing in front


def test_on_a_grey_garment_a_blob_inside_the_area_is_dropped_and_one_from_outside_kept() -> None:
    occ = np.zeros((200, 200), np.float32)
    occ[90:110, 90:110] = 1.0  # grey fabric misread as skin, wholly inside the area
    occ[50:70, 0:75] = 1.0  # an arm reaching in from outside
    cleaned, [check] = occlusion.guard(occ, [AREA])
    assert cleaned[100, 100] == 0.0 and cleaned[60, 70] == 1.0
    assert check.occlusion and check.pieces == 1


def test_over_a_quarter_hidden_turns_the_areas_switch_off() -> None:
    occ = np.zeros((200, 200), np.float32)
    occ[0:85, 50:150] = 1.0  # hair from above over the top part of the area: 25 of 80 rows = 31%
    cleaned, [check] = occlusion.guard(occ, [AREA])
    assert not check.occlusion and check.hidden > 0.25 and "hidden" in check.reason
    occ = np.zeros((200, 200), np.float32)
    occ[0:70, 50:150] = 1.0  # 10 of 80 rows: kept
    assert occlusion.guard(occ, [AREA])[1][0].occlusion


def test_a_design_cut_into_pieces_turns_the_areas_switch_off() -> None:
    occ = np.zeros((200, 200), np.float32)
    occ[:, 95:105] = 1.0  # a strap through the whole area: two halves
    _, [check] = occlusion.guard(occ, [AREA])
    assert not check.occlusion and check.pieces == 2 and "pieces" in check.reason


def test_the_areas_switch_decides_at_render_time() -> None:
    w, h = 600, 600
    blank = mockups.prepare_blank(samples.blank((240, 240, 238), size=(w, h))).image
    mask = _png(Image.new("L", (w, h), 255))
    occ = Image.new("L", (w, h), 0)
    occ.paste(255, (int(0.3 * w), int(0.3 * h), int(0.7 * w), int(0.45 * h)))
    maps = mockups.build_maps(blank, mask, _png(occ), [AREA])
    design = mockups.load_design(_png(Image.new("RGBA", (400, 400), (20, 20, 200, 255))))
    behind = np.asarray(Image.open(io.BytesIO(mockups.render_many(maps, [(design, AREA, True)], mockups.Settings(AREA, 1.0, 0.0)))).convert("RGB")).astype(int)
    over = np.asarray(Image.open(io.BytesIO(mockups.render_many(maps, [(design, AREA, False)], mockups.Settings(AREA, 1.0, 0.0)))).convert("RGB")).astype(int)
    y, x = int(0.38 * h), int(0.5 * w)
    assert behind[y, x][2] - behind[y, x][0] < 25  # hidden behind
    assert over[y, x][2] - over[y, x][0] > 60  # printed over: the switch is off


async def test_the_switch_is_saved_per_area_and_copied_with_apply_to_selected(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Switch")
    one = await _prepared(mw, "a", t, "one", samples.blank((150, 150, 150), size=(700, 800)))
    two = await _prepared(mw, "a", t, "two", samples.blank((160, 160, 160), size=(700, 800)))
    a = one["areas"][0]
    saved = (await mw["a"].put(f"/api/mockup-photos/{one['id']}/areas", json={"confirm": True, "areas": [
        {"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default", "occlusion": False}]})).json()
    assert saved["areas"][0]["occlusion"] is False
    copied = (await mw["a"].post(f"/api/mockup-photos/{one['id']}/copy", json={"photo_ids": [two["id"]], "fit": True})).json()
    assert copied[0]["areas"][0]["occlusion"] is False
    assert (await mw["b"].put(f"/api/mockup-photos/{one['id']}/areas", json={"areas": [
        {"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default", "occlusion": True}]})).status_code == 404


class _Runner:
    """Stands in for the segmenter: an arm (skin) reaching across the top of the photo's middle."""

    def segment(self, rgb: np.ndarray) -> np.ndarray:
        h, w = rgb.shape[:2]
        p = _probs(h, w)
        p[int(0.30 * h):int(0.40 * h), 0:int(0.55 * w), occlusion.CLOTHES] = 0.05
        p[int(0.30 * h):int(0.40 * h), 0:int(0.55 * w), occlusion.BODY] = 0.95
        return p

    def landmarks(self, rgb, box):  # noqa: ANN001, ANN201
        return [(0, 0, 0)] * 33, 0.0


async def test_the_recheck_updates_waiting_photos_and_only_lists_confirmed_ones(mw, monkeypatch) -> None:  # noqa: F811
    from app.ops import mockup_occlusion
    from app.pipeline import garments

    monkeypatch.setattr(garments, "is_person", lambda rgb, probs, runner: True)
    t = await _template(mw, "a", "Recheck")
    waiting = await _prepared(mw, "a", t, "waiting", samples.blank((150, 150, 150), size=(700, 800)))
    confirmed = await _prepared(mw, "a", t, "confirmed", samples.blank((150, 150, 150), size=(700, 800)))
    a = confirmed["areas"][0]
    await mw["a"].put(f"/api/mockup-photos/{confirmed['id']}/areas", json={"confirm": True, "areas": [
        {"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default"}]})
    async with mw["sm"]() as s:
        before = await s.get(MockupPhoto, uuid.UUID(confirmed["id"]))
        snapshot = (json.dumps(before.garments, sort_keys=True), before.occluder_key, before.occluder_auto_key, before.version)
    dry = await mockup_occlusion.run(mw["sm"], mw["storage"], apply=False, runner=_Runner())
    assert "1 would change" in dry and "run with --apply" in dry and "confirmed" in dry
    report = await mockup_occlusion.run(mw["sm"], mw["storage"], apply=True, runner=_Runner())
    assert "1 updated" in report and "confirmed" in report
    async with mw["sm"]() as s:
        w = await s.get(MockupPhoto, uuid.UUID(waiting["id"]))
        assert w.occluder_auto_key and w.version > waiting["version"]
        c = await s.get(MockupPhoto, uuid.UUID(confirmed["id"]))
        assert (json.dumps(c.garments, sort_keys=True), c.occluder_key, c.occluder_auto_key, c.version) == snapshot


def test_the_guards_need_no_network(monkeypatch) -> None:
    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    p = _probs()
    p[0:70, 40:80, occlusion.BODY], p[0:70, 40:80, occlusion.CLOTHES] = 0.9, 0.05
    occ = occlusion.person_front(p, True)
    assert occlusion.guard(occ, [AREA])[1][0].occlusion
    assert mockups.design_trim(_padded())["box"] == [198, 297, 802, 702]
