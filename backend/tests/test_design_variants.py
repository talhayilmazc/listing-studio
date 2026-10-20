"""Light and dark variants of one design make one listing (pipeline/variants.py, api/mockups.py,
workers/mockups.py). Synthetic images only.

* the suffix parser: every token, separators, case; names that only contain the words;
* light/dark from the garment, with an uncertain band, and the seller's one-click choice;
* "the suffix names the shirt" (default) or "the ink", remembered per account;
* one variant only goes on its own group (the plan says which photos get none and why), and
  "use it on the other group too" fills them; a plain file plus a suffixed one;
* one design, one listing group, one listing to draft; existing designs untouched.
"""

from __future__ import annotations

import io
import uuid
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import func, select

from app.db.models import Asset, GeneratedContent, MockupDesign, MockupOutput, MockupPhoto
from app.pipeline import variants
from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_mockups import _new_batch, mw  # noqa: F401  (fixture)
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_print_geometry import _prepared, _template


def _png(colour: tuple[int, int, int]) -> bytes:
    im = Image.new("RGBA", (900, 900), (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (700, 700), (*colour, 255)), (100, 100))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


RED, BLUE = (210, 30, 30), (30, 50, 220)


# --- rules ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name, expected", [
    ("CDM290-white.png", ("CDM290", "light")), ("CDM290_light.PNG", ("CDM290", "light")),
    ("CDM290 lt.png", ("CDM290", "light")), ("cdm290-WHT.png", ("cdm290", "light")),
    ("CDM290-dark.png", ("CDM290", "dark")), ("CDM290_Black.png", ("CDM290", "dark")),
    ("CDM290 blk.webp", ("CDM290", "dark")), ("CDM290-DK.jpg", ("CDM290", "dark")),
    ("Nurse Life - white.png", ("Nurse Life", "light")),
    # Only a whole token at the end, after a separator.
    ("whitehouse.png", ("whitehouse", None)), ("CDM290-whitehouse.png", ("CDM290-whitehouse", None)),
    ("CDM290white.png", ("CDM290white", None)), ("dark-CDM290.png", ("dark-CDM290", None)),
    ("CDM290-darkness.png", ("CDM290-darkness", None)), ("Black Cat-tee.png", ("Black Cat-tee", None)),
    ("white.png", ("white", None)), ("CDM290.png", ("CDM290", None)),
])
def test_the_suffix_parser(name: str, expected: tuple) -> None:
    assert variants.parse(name) == expected


def test_lightness_thresholds_and_the_uncertain_band() -> None:
    assert variants.classify(92.0) == ("light", False)
    assert variants.classify(18.0) == ("dark", False)
    assert variants.classify(variants.LIGHT_AT) == ("light", False)
    assert variants.classify(variants.DARK_AT) == ("dark", False)
    assert variants.classify(58.0) == ("light", True)  # a heather grey: decided, but the seller is asked
    assert variants.classify(51.0) == ("dark", True)
    assert variants.classify(None) == (None, True)


def test_which_file_a_photo_takes() -> None:
    light = SimpleNamespace(id=1, variant="light", subgroup=None, fill_other=False, original_filename="a-white.png")
    dark = SimpleNamespace(id=2, variant="dark", subgroup=None, original_filename="a-dark.png")
    plain = SimpleNamespace(id=3, variant=None, subgroup=None, original_filename="a.png")
    # Shirt meaning (default): "-white" on light shirts.
    assert variants.pick(light, [dark], "light") is light and variants.pick(light, [dark], "dark") is dark
    # Ink meaning: "-white" (white ink) on dark shirts.
    assert variants.pick(light, [dark], "dark", "ink") is light and variants.pick(light, [dark], "light", "ink") is dark
    # One variant only: its own group; the other group gets nothing, unless the seller fills it.
    assert variants.pick(light, [], "dark") is None
    light.fill_other = True
    assert variants.pick(light, [], "dark") is light
    light.fill_other = False
    # Plain plus suffixed: the suffixed one wins for its group, the plain one for the other.
    assert variants.pick(plain, [dark], "dark") is dark and variants.pick(plain, [dark], "light") is plain
    # A photo whose colour is not known yet: only a plain file.
    assert variants.pick(light, [dark], None) is None and variants.pick(plain, [dark], None) is plain
    # A seller-defined subgroup's version is not a variant.
    sub = SimpleNamespace(id=4, variant=None, subgroup="Black")
    assert not variants.has_variants(SimpleNamespace(variant=None, subgroup=None), [sub])


def _photo_jpeg(colour: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.open(io.BytesIO(samples.blank(colour, size=(600, 700)))).convert("RGB").save(buf, "JPEG", quality=92)
    return buf.getvalue()


@pytest.mark.parametrize("colour, group, uncertain", [
    ((245, 245, 242), "light", False),  # white
    ((236, 214, 176), "light", False),  # sand
    ((30, 30, 34), "dark", False),  # black
    ((28, 40, 90), "dark", False),  # navy
    ((128, 130, 132), None, True),  # a mid heather: close to the line, decided either way and the seller is asked
])
def test_light_and_dark_from_the_garment_on_synthetic_photos(colour, group, uncertain) -> None:
    photo = SimpleNamespace(colour=None, colour_source=None, colour_lightness=None, print_area=[[0.35, 0.3], [0.65, 0.3], [0.65, 0.6], [0.35, 0.6]],
                            garments=None)
    work.measure_colour(photo, _photo_jpeg(colour))
    assert photo.colour == group if group else photo.colour in ("light", "dark")
    assert (photo.colour_source == "uncertain") is uncertain
    # The seller's choice is never measured over.
    photo.colour, photo.colour_source = "light", "seller"
    work.measure_colour(photo, _photo_jpeg((20, 20, 20)))
    assert (photo.colour, photo.colour_source) == ("light", "seller")


# --- through the API and the worker --------------------------------------------------------------


async def _confirmed(mw, t: str, name: str, colour) -> dict:  # noqa: ANN001, F811
    photo = await _prepared(mw, "a", t, name, samples.blank(colour, size=(700, 800)))
    a = photo["areas"][0]
    res = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": [
        {"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default"}]})
    assert res.status_code == 200, res.text
    return {**res.json(), "area": a["print_area"]}


async def _upload(mw, batch, name: str, png: bytes) -> dict:  # noqa: ANN001, F811
    res = await mw["a"].post(f"/api/batches/{batch}/designs", files={"file": (name, png, "image/png")})
    assert res.status_code == 201, res.text
    return res.json()


def _colour_at(mw, row: MockupOutput, q) -> np.ndarray:  # noqa: ANN001, F811
    img = np.asarray(Image.open(io.BytesIO(mw["storage"].get(row.key))).convert("RGB")).astype(int)
    h, w = img.shape[:2]
    return img[int((q[0][1] + 0.4 * (q[3][1] - q[0][1])) * h), int(sum(p[0] for p in q) / 4 * w)]


async def test_bug_light_and_dark_variants_make_one_listing_with_every_mockup(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Tees")
    white = await _confirmed(mw, t, "white tee", (240, 240, 238))
    black = await _confirmed(mw, t, "black tee", (32, 32, 36))
    assert (white["colour"], black["colour"]) == ("light", "dark")  # measured when prepared
    batch = await _new_batch(mw)
    first = await _upload(mw, batch, "CDM290-white.png", _png(RED))
    second = await _upload(mw, batch, "CDM290-dark.png", _png(BLUE))
    # Before the fix these were two designs, "CDM290-WHITE" and "CDM290-DARK": two listings.
    assert second["id"] == first["id"]
    designs = (await mw["a"].get(f"/api/batches/{batch}/designs")).json()
    assert len(designs) == 1
    d = designs[0]
    assert (d["parsed_sku"], d["group_key"], d["variant"]) == ("CDM290", "CDM290", "light")
    assert [(v["variant"], v["original_filename"]) for v in d["versions"]] == [("dark", "CDM290-dark.png")]

    # The plan before anything is made: each file on its own colour group, nothing missing.
    plan = (await mw["a"].post(f"/api/batches/{batch}/mockups/plan", json={"template_ids": [t]})).json()
    files = {f["filename"]: f["photos"] for f in plan["designs"][0]["files"]}
    assert files == {"CDM290-white.png": [white["id"]], "CDM290-dark.png": [black["id"]]}
    assert plan["designs"][0]["missing"] == []

    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [t]})
    assert res.status_code == 202 and res.json()["total"] == 2
    run_id = uuid.UUID(res.json()["id"])
    async with mw["sm"]() as s:
        assert (await work.run(s, mw["storage"], run_id)).status == "succeeded"
        rows = {str(r.photo_id): r for r in (await s.execute(select(MockupOutput).where(MockupOutput.run_id == run_id))).scalars()}
    red = _colour_at(mw, rows[white["id"]], white["area"])
    blue = _colour_at(mw, rows[black["id"]], black["area"])
    assert red[0] > red[2] + 60 and blue[2] > blue[0] + 60  # "-white" on the light shirt, "-dark" on the dark one

    made = await mw["a"].post(f"/api/mockup-runs/{run_id}/listings", json={})
    assert made.json() == {"groups": 1, "photos": 2}
    groups = (await mw["a"].get(f"/api/batches/{batch}/groups")).json()
    assert [(g["group_key"], g["image_count"]) for g in groups] == [("CDM290", 2)]
    async with mw["sm"]() as s:  # the cover is the first template photo in the seller's order
        cover = (await s.execute(select(Asset).where(Asset.batch_id == batch).order_by(Asset.rank))).scalars().first()
        assert str(cover.mockup_photo_id) == white["id"]
    # One listing to write and draft.
    res = await mw["a"].put(f"/api/batches/{batch}/groups", json={"connection_id": str(mw["a1"]), "profile_id": str(mw["pa1"])})
    assert res.status_code == 200, res.text
    assert (await mw["a"].post(f"/api/batches/{batch}/manual", json={})).status_code == 200
    async with mw["sm"]() as s:
        assert (await s.execute(select(func.count()).select_from(GeneratedContent).where(GeneratedContent.batch_id == batch))).scalar_one() == 1
    preview = (await mw["a"].post(f"/api/batches/{batch}/publish/preview", json={})).json()
    assert len(preview["rows"]) == 1


async def test_the_flip_setting_is_remembered_and_turns_the_suffix_into_the_ink(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Tees")
    white = await _confirmed(mw, t, "white tee", (240, 240, 238))
    black = await _confirmed(mw, t, "black tee", (32, 32, 36))
    batch = await _new_batch(mw)
    await _upload(mw, batch, "CDM290-white.png", _png((245, 245, 245)))
    await _upload(mw, batch, "CDM290-dark.png", _png((15, 15, 15)))
    assert (await mw["a"].get("/api/mockup-settings")).json() == {"variant_suffix": "shirt"}
    assert (await mw["a"].put("/api/mockup-settings", json={"variant_suffix": "ink"})).status_code == 200
    assert (await mw["a"].get("/api/mockup-settings")).json() == {"variant_suffix": "ink"}
    assert (await mw["b"].get("/api/mockup-settings")).json() == {"variant_suffix": "shirt"}  # per account
    plan = (await mw["a"].post(f"/api/batches/{batch}/mockups/plan", json={"template_ids": [t]})).json()
    files = {f["filename"]: f["photos"] for f in plan["designs"][0]["files"]}
    assert files == {"CDM290-white.png": [black["id"]], "CDM290-dark.png": [white["id"]]}  # white ink on dark
    assert (await mw["a"].put("/api/mockup-settings", json={"variant_suffix": "both"})).status_code == 422


async def test_one_variant_only_says_which_photos_get_none_and_fills_them_on_request(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Tees")
    white = await _confirmed(mw, t, "white tee", (240, 240, 238))
    black = await _confirmed(mw, t, "black tee", (32, 32, 36))
    batch = await _new_batch(mw)
    d = await _upload(mw, batch, "BR100-white.png", _png(RED))
    plan = (await mw["a"].post(f"/api/batches/{batch}/mockups/plan", json={"template_ids": [t]})).json()["designs"][0]
    assert plan["missing"] == [black["id"]] and plan["missing_colours"] == ["dark"] and plan["lacking"] == ["dark"]
    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [t]})
    assert res.json()["total"] == 1  # only the light-shirt photo
    # "Use this design on the other group too".
    assert (await mw["a"].patch(f"/api/mockup-designs/{d['id']}", json={"fill_other": True})).json()["fill_other"] is True
    assert (await mw["a"].get(f"/api/batches/{batch}/designs")).json()[0]["parsed_sku"] == "BR100"  # SKU kept
    plan = (await mw["a"].post(f"/api/batches/{batch}/mockups/plan", json={"template_ids": [t]})).json()["designs"][0]
    assert plan["missing"] == [] and {p for f in plan["files"] for p in f["photos"]} == {white["id"], black["id"]}
    assert (await mw["b"].patch(f"/api/mockup-designs/{d['id']}", json={"fill_other": False})).status_code == 404


async def test_a_plain_file_and_a_suffixed_one_are_one_design(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Tees")
    white = await _confirmed(mw, t, "white tee", (240, 240, 238))
    black = await _confirmed(mw, t, "black tee", (32, 32, 36))
    batch = await _new_batch(mw)
    plain = await _upload(mw, batch, "GR200.png", _png(RED))
    dark = await _upload(mw, batch, "GR200-blk.png", _png(BLUE))
    assert dark["id"] == plain["id"]
    plan = (await mw["a"].post(f"/api/batches/{batch}/mockups/plan", json={"template_ids": [t]})).json()["designs"][0]
    files = {f["filename"]: f["photos"] for f in plan["files"]}
    assert files == {"GR200.png": [white["id"]], "GR200-blk.png": [black["id"]]}
    # Two plain files of one name, or two of one variant, stay two designs (as before).
    other = await _new_batch(mw)
    a = await _upload(mw, other, "XY1-white.png", _png(RED))
    b = await _upload(mw, other, "XY1-wht.png", _png(BLUE))
    assert a["id"] != b["id"]


async def test_the_seller_sets_a_photos_colour_and_existing_designs_are_untouched(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Tees")
    grey = await _confirmed(mw, t, "grey tee", (128, 130, 132))
    assert grey["colour_source"] == "uncertain"
    res = await mw["a"].patch(f"/api/mockup-photos/{grey['id']}", json={"colour": "light"})
    assert (res.json()["colour"], res.json()["colour_source"]) == ("light", "seller")
    assert (await mw["b"].patch(f"/api/mockup-photos/{grey['id']}", json={"colour": "dark"})).status_code == 404
    # A design made before variants (no variant) keeps printing on every photo; nothing re-pairs it.
    batch = await _new_batch(mw)
    async with mw["sm"]() as s:
        s.add(MockupDesign(tenant_id=mw["alice"], batch_id=batch, group_key="OLD-WHITE", original_filename="OLD-white.png",
                           parsed_sku="OLD-WHITE", design_key=None, width=900, height=900, pair_base="OLD-white"))
        await s.commit()
    await _upload(mw, batch, "NEW1.png", _png(RED))
    async with mw["sm"]() as s:
        old = (await s.execute(select(MockupDesign).where(MockupDesign.group_key == "OLD-WHITE"))).scalar_one()
        assert (old.variant, old.parsed_sku, old.fill_other) == (None, "OLD-WHITE", False)
        # Photos measured before they are used: an old photo without a colour gets one.
        photo = await s.get(MockupPhoto, uuid.UUID(grey["id"]))
        photo.colour = photo.colour_source = None
        await s.commit()
        await work.ensure_colours(s, mw["storage"], [photo])
        assert photo.colour in ("light", "dark") and photo.colour_source == "uncertain"
