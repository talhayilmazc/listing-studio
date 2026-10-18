"""Mockup output quality and areas (seller feedback, Part A; pipeline/mockups.py, workers/mockups.py,
api/mockups.py, lib/areaEditor.ts).

* A1: colour is kept: a photo or design with an embedded ICC profile (Display P3, CMYK) is
  converted to sRGB with that profile, never by dropping it; every mockup JPEG is 4:4:4 at
  quality 92; outside the print areas a mockup is the photo; black and white ink keep their
  contrast on light, grey and dark garments, on a lit fold too.
* A2: every area on a photo renders, whatever detection found and whichever subgroup each
  area takes.
* A3: "Copy this area to all garments" puts copies only on garments the analysis really found,
  by their own geometry; never where none was found.

Synthetic images only; no model runs; nothing is sent anywhere.
"""

from __future__ import annotations

import io
import json
import uuid

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import select

from app.db.models import MockupOutput, MockupPhoto
from app.pipeline import mockups as M
from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_mockups import _new_batch, mw  # noqa: F401  (fixture)
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_print_geometry import _prepared, _template

AREA = [[0.34, 0.30], [0.66, 0.30], [0.66, 0.62], [0.34, 0.62]]


def _vips():
    import pyvips

    return pyvips


def _photo(colour, *, profile: str | None = None, size=(800, 1000), fold_amp=0.10, light=0.0) -> bytes:
    """A garment on a plain background with folds, weave and (optionally) side light;
    ``profile``: the pixels converted to that colour space and its profile embedded."""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    inside = ((xx - w / 2) / (0.36 * w)) ** 2 + ((yy - h * 0.55) / (0.42 * h)) ** 2 < 1
    folds = 1 + fold_amp * np.sin(xx / 19.0) * np.cos(yy / 27.0) + 0.06 * np.sin((xx + yy) / 45.0)
    folds = folds * (1 + light * (xx / w - 0.5) * 2)
    noise = np.random.default_rng(7).normal(0, 3.0, (h, w)).astype(np.float32)
    garment = np.stack([np.clip(c * folds + noise, 0, 255) for c in colour], axis=-1)
    img = np.where(inside[..., None], garment, np.full((h, w, 3), 236, np.float32)).astype(np.uint8)
    v = _vips().Image.new_from_memory(img.tobytes(), w, h, 3, "uchar").copy(interpretation="srgb")
    if profile:
        v = v.icc_export(output_profile=profile)
    return v.write_to_buffer(".jpg", Q=95)


def _as_seen(data: bytes) -> np.ndarray:
    """The image as a colour-managed screen shows it (sRGB, its own profile applied)."""
    pyvips = _vips()
    img = pyvips.Image.new_from_buffer(data, "")
    if img.get_typeof("icc-profile-data"):
        img = img.icc_transform("srgb", embedded=True, intent="relative")
    return np.frombuffer(img.cast("uchar").write_to_memory(), np.uint8).reshape(img.height, img.width, img.bands)[..., :3]


def _half_black_white(flip: bool = False, size: int = 600) -> bytes:
    arr = np.zeros((size, size, 4), np.uint8)
    arr[:, : size // 2, :3] = 255 if flip else 0
    arr[:, size // 2:, :3] = 0 if flip else 255
    arr[..., 3] = 255
    return _vips().Image.new_from_memory(arr.tobytes(), size, size, 4, "uchar").copy(interpretation="srgb").write_to_buffer(".png")


def _mockup(photo: bytes, design: bytes, area=AREA) -> tuple[bytes, M.Maps]:
    src = M.resize_upload(photo)
    blank = M.prepare_blank(src.image, runner=None, saliency=None)
    maps = M.build_maps(blank.image, blank.mask, None, area)
    return M.render(maps, M.load_design(design), M.Settings(area, size=1.0, fold=0.5)), maps


def _saturation(a: np.ndarray) -> float:
    a = a.astype(np.float32)
    return float(np.mean((a.max(-1) - a.min(-1)) / np.maximum(a.max(-1), 1)))


# --- A1: colour profiles ------------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["p3", "cmyk"])
def test_a_photo_with_an_embedded_profile_is_converted_with_it_never_stripped(profile: str) -> None:
    photo = _photo((200, 30, 40), profile=profile)
    seen = _as_seen(photo)
    blank = M.prepare_blank(M.resize_upload(photo).image, runner=None, saliency=None)
    stored = _as_seen(blank.image)
    assert stored.shape == seen.shape
    # The stored working copy looks like the photo on a colour-managed screen...
    assert float(np.mean(np.abs(stored.astype(int) - seen.astype(int)))) < 1.5
    # ...not like its numbers read as sRGB (the old, faded result).
    raw = _vips().Image.new_from_buffer(photo, "")
    if profile == "p3":
        as_srgb = np.frombuffer(raw.write_to_memory(), np.uint8).reshape(raw.height, raw.width, 3)
        assert _saturation(stored) > _saturation(as_srgb) + 0.05


def test_a_design_with_a_p3_profile_keeps_its_colour() -> None:
    arr = np.zeros((600, 600, 4), np.uint8)
    arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3] = 220, 20, 30, 255
    v = _vips().Image.new_from_memory(arr.tobytes(), 600, 600, 4, "uchar").copy(interpretation="srgb")
    p3 = v.icc_export(output_profile="p3").write_to_buffer(".png")
    loaded = M.load_design(M.prepare_design(p3).image)
    px = np.frombuffer(loaded.write_to_memory(), np.uint8).reshape(loaded.height, loaded.width, 4)
    # The red it was drawn in (a P3 round trip at 8 bits moves a few levels).
    assert abs(int(px[300, 300, 0]) - 220) <= 6 and int(px[300, 300, 1]) <= 28
    assert int(px[300, 300, 3]) == 255  # the alpha band is kept


def test_a_photo_without_a_profile_is_read_as_srgb_unchanged() -> None:
    photo = _photo((120, 160, 90))
    blank = M.prepare_blank(M.resize_upload(photo).image, runner=None, saliency=None)
    assert float(np.mean(np.abs(_as_seen(blank.image).astype(int) - _as_seen(photo).astype(int)))) < 1.0


# --- A1: the output ----------------------------------------------------------------------------------


@pytest.mark.parametrize("colour,profile", [((240, 240, 238), None), ((28, 28, 30), None), ((200, 30, 40), "p3")])
def test_outside_the_print_area_the_mockup_is_the_photo(colour, profile) -> None:  # noqa: ANN001
    photo = _photo(colour, profile=profile)
    out, _ = _mockup(photo, _half_black_white())
    got, ref = _as_seen(out), _as_seen(photo)
    h, w = got.shape[:2]
    keep = np.ones((h, w), bool)
    keep[int(AREA[0][1] * h) - 40:int(AREA[2][1] * h) + 40, int(AREA[0][0] * w) - 40:int(AREA[2][0] * w) + 40] = False
    mad = float(np.mean(np.abs(got.astype(int) - ref.astype(int))[keep]))
    assert mad < 0.9, mad  # was 0.7-1.0 for sRGB and 5.6 for Display P3 before
    assert abs(_saturation(got[keep]) - _saturation(ref[keep])) < 0.01


def test_every_mockup_jpeg_keeps_full_colour_resolution() -> None:
    out, _ = _mockup(_photo((200, 30, 40)), _half_black_white())
    img = _vips().Image.new_from_buffer(out, "")
    assert img.get("jpeg-chroma-subsample") == "4:4:4"
    blank = M.prepare_blank(M.resize_upload(_photo((200, 30, 40))).image, runner=None, saliency=None)
    assert _vips().Image.new_from_buffer(blank.image, "").get("jpeg-chroma-subsample") == "4:4:4"


@pytest.mark.parametrize("label,colour,kw", [
    ("white tee", (240, 240, 238), {}),
    ("heather grey", (150, 150, 150), {}),
    ("black tee", (28, 28, 30), {}),
    ("red tee", (200, 30, 40), {}),
    ("white tee, deep folds", (235, 235, 233), {"fold_amp": 0.35}),
    ("grey tee, black ink on the lit side", (120, 120, 120), {"light": 0.8, "flip": True}),
    ("black tee, black ink on the lit side", (40, 40, 42), {"light": 0.8, "flip": True}),
])
def test_black_and_white_ink_keep_their_contrast(label: str, colour, kw: dict) -> None:  # noqa: ANN001
    flip = kw.pop("flip", False)
    out, _ = _mockup(_photo(colour, **kw), _half_black_white(flip))
    got = _as_seen(out).astype(np.float32)
    h, w = got.shape[:2]
    luma = got[..., 0] * 0.299 + got[..., 1] * 0.587 + got[..., 2] * 0.114
    x0, x1, mid = int(0.36 * w), int(0.64 * w), int(0.5 * w)
    y0, y1 = int(0.30 * h + 0.03 * w), int(0.30 * h + 0.29 * w)  # inside the square design
    left, right = luma[y0:y1, x0:mid - 10], luma[y0:y1, mid + 10:x1]
    black, white = (right, left) if flip else (left, right)
    # Black ink stays black (a lit fold lifted it to 17 on average, 31 at the 95th percentile);
    # white ink stays bright; the two stay far apart.
    assert black.mean() <= 10 and np.percentile(black, 95) <= 18, (label, black.mean())
    assert white.mean() - black.mean() >= 200, (label, white.mean(), black.mean())


# --- A2: every area renders --------------------------------------------------------------------------


def _six_tees() -> bytes:
    """Three white tees above three black tees, on a light background."""
    w, h, tw, th = 1500, 1100, 480, 540
    canvas = Image.new("RGB", (w, h), (225, 226, 229))
    for row, colour in enumerate(((238, 238, 236), (32, 32, 35))):
        for col in range(3):
            tee = Image.open(io.BytesIO(samples.blank(colour, size=(tw, th), background=(225, 226, 229)))).convert("RGB")
            canvas.paste(tee, (10 + col * 495, 10 + row * 545))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _tee_area(col: int, row: int) -> list[list[float]]:
    """A chest area on one of the six tees (shares of the photo)."""
    left, top = (10 + col * 495) / 1500, (10 + row * 545) / 1100
    x0, x1 = left + 0.36 * 480 / 1500, left + 0.64 * 480 / 1500
    y0, y1 = top + 0.24 * 540 / 1100, top + 0.52 * 540 / 1100
    return [[round(x0, 4), round(y0, 4)], [round(x1, 4), round(y0, 4)], [round(x1, 4), round(y1, 4)], [round(x0, 4), round(y1, 4)]]


async def _six_garment_photo(mw, found: int = 3) -> tuple[str, dict, dict]:  # noqa: F811
    """A template "Six" (subgroups White: light garments, Black: dark garments) with the six-tee
    photo, where the analysis found only ``found`` garments (the black tees)."""
    t = await _template(mw, "a", "Six")
    tpl = next(x for x in (await mw["a"].get("/api/mockup-templates")).json() if x["id"] == t)
    white = tpl["subgroups"][0]["id"]
    await mw["a"].patch(f"/api/mockup-subgroups/{white}", json={"name": "White", "use_on": "light", "suffix": "-white"})
    tpl = (await mw["a"].post(f"/api/mockup-templates/{t}/subgroups",
                              json={"name": "Black", "use_on": "dark", "suffix": "-black"})).json()
    groups = {g["name"]: g["id"] for g in tpl["subgroups"]}
    photo = await _prepared(mw, "a", t, "six", _six_tees())
    # What detection found: some garments, each with its own geometry and tone.
    async with mw["sm"]() as s:
        row = await s.get(MockupPhoto, uuid.UUID(photo["id"]))
        rows = [{"print_area": _tee_area(c, 1), "detected_area": _tee_area(c, 1), "source": "default", "tone": "dark",
                 "role": "front", "geometry": {"garment_box": _tee_box(c, 1)}} for c in range(found)]
        row.garments = rows
        row.analysis = {**(row.analysis or {}), "candidates": rows}
        row.print_area, row.layout = rows[0]["print_area"], "several"
        await s.commit()
    return t, groups, (await mw["a"].get(f"/api/mockup-photos/{photo['id']}")).json()


def _tee_box(col: int, row: int) -> list[float]:
    left, top = (10 + col * 495) / 1500, (10 + row * 545) / 1100
    return [round(left, 4), round(top, 4), round(left + 480 / 1500, 4), round(top + 540 / 1100, 4)]


def _printed_on(output: bytes, blank: bytes, area) -> float:  # noqa: ANN001
    """How much the output differs from the blank inside ``area`` (0: nothing printed)."""
    a = np.asarray(Image.open(io.BytesIO(output)).convert("RGB")).astype(int)
    b = np.asarray(Image.open(io.BytesIO(blank)).convert("RGB").resize((a.shape[1], a.shape[0]))).astype(int)
    h, w = a.shape[:2]
    x0, y0, x1, y1 = int(area[0][0] * w), int(area[0][1] * h), int(area[2][0] * w), int(area[2][1] * h)
    return float(np.mean(np.abs(a[y0:y1, x0:x1] - b[y0:y1, x0:x1])))


async def test_every_area_the_seller_set_renders_whatever_detection_found(mw) -> None:  # noqa: F811
    t, groups, photo = await _six_garment_photo(mw, found=3)
    # The seller keeps the three found areas and adds the three white tees' (no detection there).
    areas = [{"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default"} for a in photo["areas"]]
    areas += [{"print_area": _tee_area(c, 0), "source": "tap"} for c in range(3)]
    res = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": areas})
    assert res.status_code == 200 and len(res.json()["areas"]) == 6, res.text
    # One design, made only for the dark garments (its file is the "-black" version).
    batch = await _new_batch(mw)
    slots = {"subgroups": json.dumps([{"name": "White", "suffix": "-white", "use_on": "light"},
                                      {"name": "Black", "suffix": "-black", "use_on": "dark"}])}
    up = await mw["a"].post(f"/api/batches/{batch}/designs", data=slots,
                            files={"file": ("BR100-black.png", samples.design("transparent"), "image/png")})
    assert up.status_code == 201, up.text
    run = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [t]})
    assert run.status_code == 202, run.text
    async with mw["sm"]() as s:
        assert (await work.run(s, mw["storage"], uuid.UUID(run.json()["id"]))).status == "succeeded"
        [out] = (await s.execute(select(MockupOutput))).scalars().all()
        row = await s.get(MockupPhoto, uuid.UUID(photo["id"]))
        blank = mw["storage"].get(row.image_key)
    image = mw["storage"].get(out.key)
    printed = [_printed_on(image, blank, a["print_area"]) for a in areas]
    assert all(p > 3 for p in printed), printed  # all six (an empty area differs by under 1), not only the three found


def test_order_mode_fills_every_area_even_with_fewer_designs_than_areas() -> None:
    assert work.order_groups(["a", "b", "c"], 6) == [["a", "b", "c", "a", "b", "c"]]
    assert work.order_groups(["a", "b", "c", "d", "e", "f", "g"], 3) == [["a", "b", "c"], ["d", "e", "f"], ["g", "a", "b"]]
    assert work.order_groups(["a"], 4) == [["a", "a", "a", "a"]]


# --- A3: copies only onto found garments ---------------------------------------------------------------


async def test_the_photo_says_which_garments_were_really_found(mw) -> None:  # noqa: F811
    """The editor copies only onto these (lib/areaEditor.ts ``copyToFound``, tested there)."""
    _, _, photo = await _six_garment_photo(mw, found=3)
    assert photo["garments_found"] == 3 and len(photo["found_areas"]) == 3
    for i, a in enumerate(photo["found_areas"]):  # each on its own black tee
        box = _tee_box(i, 1)
        cx, cy = sum(p[0] for p in a) / 4, sum(p[1] for p in a) / 4
        assert box[0] < cx < box[2] and box[1] < cy < box[3], (i, a)
    # The seller adds areas on the white tees by hand: still three garments found, not six.
    areas = [{"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default"} for a in photo["areas"]]
    areas += [{"print_area": _tee_area(c, 0), "source": "tap"} for c in range(3)]
    saved = (await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"areas": areas})).json()
    assert saved["garments_found"] == 3 and len(saved["areas"]) == 6
    # An added area never takes another garment's suggestion (rows used to be matched by position).
    for added, c in zip(saved["areas"][3:], range(3)):
        assert added["detected_area"] == added["print_area"] == _tee_area(c, 0)
    other = await mw["b"].get(f"/api/mockup-photos/{photo['id']}")
    assert other.status_code == 404
