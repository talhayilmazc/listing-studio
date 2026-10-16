"""The mockup flow end to end: a template, many photos, each area set and confirmed once,
then designs in bulk landing in the confirmed areas (api/mockups.py, workers/mockups.py).

* Many photos into one template, one failing on its own (size, type) without the others.
* "Apply this area to selected": each target placed by its own garment; still waiting
  for its check; "Confirm selected" confirms them in one step.
* Bulk designs (one by one or a ZIP): N designs x M confirmed photos, grouped per design;
  a design that fails says why; unconfirmed photos are skipped and named.
* What is in front of the garment: decided against the fabric (hair at 0.5 against
  clothes at 0.4 is in front; fabric read as half "accessory" is not); a design enlarged
  into the hair stays behind it in the preview and the mockup; the "Keep in front" brush
  (paint and erase) works where the analysis found nothing, in both.
* Another account's photos and batches are 404; no network, no Etsy or AI call; the same
  bytes twice.
"""

from __future__ import annotations

import io
import uuid
import zipfile

import numpy as np
from PIL import Image

from app.db.models import MockupPhoto
from app.pipeline import mockup_vision as vision
from app.pipeline import mockups
from tests import mockup_samples as samples
from tests.test_mockups import (  # noqa: F401  (fixtures)
    _confirm,
    _new_batch,
    _new_template,
    _NoCalls,
    _photo,
    _run,
    mw,
)
from tests.test_multi_shop import world  # noqa: F401  (fixture)


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _shifted_tee(dx: int, dy: int, colour=(238, 238, 236)) -> bytes:
    """The sample tee placed elsewhere on a bigger backdrop: the same garment, found in another place."""
    tee = Image.open(io.BytesIO(samples.blank(colour, size=(600, 700)))).convert("RGB")
    canvas = Image.new("RGB", (900, 900), (200, 203, 207))
    canvas.paste(tee, (dx, dy))
    return _png(canvas)


def _centre(q: list[list[float]]) -> tuple[float, float]:
    return sum(p[0] for p in q) / 4, sum(p[1] for p in q) / 4


def _pixels(jpeg: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB")).astype(int)


async def test_many_photos_go_into_one_template_and_one_failing_does_not_stop_the_others(mw, monkeypatch) -> None:  # noqa: F811
    guard = _NoCalls(monkeypatch)
    tee = await _new_template(mw, "Comfort Colors 1717")
    sent = [("White", samples.blank((238, 238, 236), size=(600, 700)), "image/png"),
            ("notes.txt", b"just text, not a photo", "text/plain"),
            ("Huge", b"\x89PNG" + b"0" * (30 * 1024 * 1024 + 200_000), "image/png"),
            ("Black", samples.blank((26, 26, 28), size=(600, 700)), "image/png")]
    results = []
    for name, data, mime in sent:
        res = await mw["a"].post(f"/api/mockup-templates/{tee['id']}/photos", data={"name": name, "rights_confirmed": "true"},
                                 files={"file": (f"{name}.png", data, mime)})
        results.append((name, res.status_code, res.json().get("detail")))
    assert [r[1] for r in results] == [201, 415, 413, 201], results
    assert "30 MB" in results[2][2]  # the limit is said
    listed = (await mw["a"].get(f"/api/mockup-templates/{tee['id']}")).json()
    assert [p["name"] for p in listed["photos"]] == ["White", "Black"]
    assert guard.calls == []


async def test_apply_this_area_to_selected_places_each_by_its_own_garment_then_confirm_selected(mw, monkeypatch) -> None:  # noqa: F811
    guard = _NoCalls(monkeypatch)
    tee = await _new_template(mw, "Tee")
    source = await _photo(mw, tee["id"], "Here", blank=_shifted_tee(40, 60), confirm=False)
    target = await _photo(mw, tee["id"], "There", blank=_shifted_tee(260, 180), confirm=False)
    found_s, found_t = source["areas"][0]["detected_area"], target["areas"][0]["detected_area"]
    # The seller moves the source's area down a little from the suggestion and confirms it.
    moved = [[x, round(y + 0.03, 4)] for x, y in found_s]
    await _confirm(mw, source, [{"print_area": moved, "detected_area": found_s}])
    res = await mw["a"].post(f"/api/mockup-photos/{source['id']}/copy", json={"photo_ids": [target["id"]]})
    assert res.status_code == 200, res.text
    [placed] = res.json()
    area = placed["areas"][0]["print_area"]
    # On the target's own garment (found elsewhere on its photo), moved down the same way.
    assert abs(_centre(area)[0] - _centre(found_t)[0]) < 0.01
    assert abs((_centre(area)[1] - _centre(found_t)[1]) - 0.03) < 0.01
    assert placed["needs_check"] and not placed["confirmed"]  # still waiting for its check
    # The same shares of the photo instead, when asked (fit off).
    plain = (await mw["a"].post(f"/api/mockup-photos/{source['id']}/copy", json={"photo_ids": [target["id"]], "fit": False})).json()
    assert plain[0]["areas"][0]["print_area"] == moved
    # "Confirm selected": both at once.
    res = await mw["a"].post(f"/api/mockup-templates/{tee['id']}/confirm", json={"photo_ids": [target["id"], source["id"]]})
    assert res.status_code == 200 and (res.json()["confirmed"], res.json()["needs_check"]) == (2, 0)
    # Only photos of that template.
    other = await _photo(mw, (await _new_template(mw, "Other"))["id"], "Elsewhere", confirm=False)
    assert (await mw["a"].post(f"/api/mockup-templates/{tee['id']}/confirm", json={"photo_ids": [other["id"]]})).status_code == 422
    assert guard.calls == []


async def test_bulk_designs_land_on_every_confirmed_photo_grouped_per_design(mw, monkeypatch) -> None:  # noqa: F811
    guard = _NoCalls(monkeypatch)
    tee = await _new_template(mw, "Tee")
    for name, colour in (("White", (238, 238, 236)), ("Black", (26, 26, 28)), ("Navy", (30, 40, 80))):
        await _photo(mw, tee["id"], name, colour)
    await _photo(mw, tee["id"], "Unchecked", (200, 60, 60), confirm=False)
    batch = await _new_batch(mw)
    # A ZIP of designs: three good, one too small, one not an image, one in a folder.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("BR100.png", samples.design())
        zf.writestr("BR101.png", samples.design("white"))
        zf.writestr("art/BR102.png", samples.design(size=(800, 800)))
        zf.writestr("tiny.png", samples.design(size=(200, 150)))
        zf.writestr("readme.txt", "not an image")
    res = await mw["a"].post(f"/api/batches/{batch}/designs/archive", files={"file": ("designs.zip", buf.getvalue(), "application/zip")})
    assert res.status_code == 201, res.text
    body = res.json()
    assert sorted(d["original_filename"] for d in body["added"]) == ["BR100.png", "BR101.png", "BR102.png"]
    assert sorted(d["parsed_sku"] for d in body["added"]) == ["BR100", "BR101", "BR102"]
    reasons = {f["filename"]: f["reason"] for f in body["failed"]}
    assert "at least 500 px" in reasons["tiny.png"] and "200 x 150" in reasons["tiny.png"]
    assert any("not an image" in r for r in reasons.values())
    # One more, alone: an opaque design is kept (printed as a rectangle; the page says so).
    single = await mw["a"].post(f"/api/batches/{batch}/designs", files={"file": ("BR103.jpg", samples.design("opaque"), "image/png")})
    assert single.status_code == 201 and single.json()["has_alpha"] is False
    run = await _run(mw, batch, [tee["id"]])
    assert (run["total"], run["photos"], run["skipped"], run["skipped_photos"]) == (12, 3, 1, ["Unchecked"])
    assert [d["sku"] for d in run["designs"]] == ["BR100", "BR101", "BR102", "BR103"]
    assert all(len(d["outputs"]) == 3 and d["done"] == d["total"] == 3 for d in run["designs"])  # one group per design
    assert guard.calls == []


def test_in_front_is_decided_against_the_fabric() -> None:
    probs = np.zeros((4, 1, 6), np.float32)
    probs[0, 0, [vision.HAIR, vision.CLOTHES]] = (0.51, 0.41)  # the band around hair on the seller's photos
    probs[1, 0, [vision.OTHERS, vision.CLOTHES]] = (0.46, 0.52)  # plain fabric read as half "accessory"
    probs[2, 0, [vision.HAIR]] = (1.0,)  # hair over the chest
    probs[3, 0, [vision.BACKGROUND, vision.HAIR]] = (0.95, 0.03)  # background
    front = vision.front_of(probs)[:, 0]
    assert front[0] > 0.8 and front[1] == 0.0 and front[2] == 1.0 and front[3] == 0.0


async def test_a_design_enlarged_into_the_hair_stays_behind_it_in_the_preview_and_the_mockup(mw, monkeypatch) -> None:  # noqa: F811
    guard = _NoCalls(monkeypatch)
    png, truth = samples.person("hair")
    monkeypatch.setattr(vision, "default_runner", lambda: samples.TruthRunner(truth))
    tee = await _new_template(mw, "Model")
    photo = await _photo(mw, tee["id"], "Model", blank=png, confirm=False)
    big = [[0.25, 0.2], [0.75, 0.2], [0.75, 0.8], [0.25, 0.8]]  # well past the found area, over the strand of hair
    await _confirm(mw, photo, [{"print_area": big, "detected_area": photo["areas"][0]["detected_area"]}])
    async with mw["sm"]() as s:
        row = await s.get(MockupPhoto, uuid.UUID(photo["id"]))
        blank = _pixels(mw["storage"].get(row.image_key))
    h, w = blank.shape[:2]
    hair = np.asarray(truth["classes"].resize((w, h))) == 1
    inside = np.zeros_like(hair)
    inside[int(0.2 * h):int(0.8 * h), int(0.25 * w):int(0.75 * w)] = True
    hair_in = hair & inside
    assert hair_in.sum() > 1000

    batch = await _new_batch(mw)
    await mw["a"].post(f"/api/batches/{batch}/designs", files={"file": ("BR1.png", samples.design("opaque", size=(900, 900)), "image/png")})
    run = await _run(mw, batch, [tee["id"]])
    async with mw["sm"]() as s:
        from sqlalchemy import select

        from app.db.models import MockupOutput

        [output] = (await s.execute(select(MockupOutput).where(MockupOutput.run_id == uuid.UUID(run["id"])))).scalars().all()
        mockup = _pixels(mw["storage"].get(output.key))
    changed = np.abs(mockup - blank).sum(-1) > 40
    assert changed[inside & ~hair].mean() > 0.3  # the design is there
    assert changed[hair_in].mean() < 0.05  # and behind the hair
    # The editor's preview ("On fabric") the same.
    preview = await mw["a"].post(f"/api/mockup-photos/{photo['id']}/preview", json={"areas": [big], "size": 1.0})
    small = _pixels(preview.content)
    ph, pw = small.shape[:2]
    blank_small = np.asarray(Image.fromarray(blank.astype(np.uint8)).resize((pw, ph))).astype(int)
    hair_small = np.asarray(truth["classes"].resize((pw, ph))) == 1
    inside_small = np.zeros_like(hair_small)
    inside_small[int(0.25 * ph):int(0.75 * ph), int(0.3 * pw):int(0.7 * pw)] = True
    assert (np.abs(small - blank_small).sum(-1) > 60)[hair_small & inside_small].mean() < 0.08
    # The live preview's front layer: the hair, opaque; elsewhere transparent.
    cut = await mw["a"].get(f"/api/mockup-photos/{photo['id']}/front?layer=cutout")
    alpha = np.asarray(Image.open(io.BytesIO(cut.content)).convert("RGBA"))[..., 3]
    assert alpha[hair_in].mean() > 200 and alpha[inside & ~hair].mean() < 30
    assert guard.calls == []


async def test_the_keep_in_front_brush_paints_and_erases_in_the_preview_and_the_mockup(mw, monkeypatch) -> None:  # noqa: F811
    guard = _NoCalls(monkeypatch)
    tee = await _new_template(mw, "Flat")
    photo = await _photo(mw, tee["id"], "Flat", confirm=False)  # a flat-lay tee: nothing in front
    assert photo["has_occluder"] is False
    area = [[0.3, 0.3], [0.7, 0.3], [0.7, 0.7], [0.3, 0.7]]
    await _confirm(mw, photo, [{"print_area": area, "detected_area": photo["areas"][0]["detected_area"]}])
    # Paint a block in the middle of the area "in front" (at the editor's screen size).
    brush = Image.new("L", (300, 350), mockups.BRUSH_UNTOUCHED)
    brush.paste(mockups.BRUSH_FRONT, (120, 140, 180, 210))
    res = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/front", files={"file": ("brush.png", _png(brush), "image/png")})
    assert res.status_code == 200, res.text
    assert res.json()["has_occluder"] and res.json()["has_front_brush"]
    async with mw["sm"]() as s:
        row = await s.get(MockupPhoto, uuid.UUID(photo["id"]))
        blank = _pixels(mw["storage"].get(row.image_key))
    h, w = blank.shape[:2]
    block = np.zeros(blank.shape[:2], bool)
    block[int(0.42 * h):int(0.58 * h), int(0.42 * w):int(0.58 * w)] = True
    around = np.zeros_like(block)
    around[int(0.33 * h):int(0.38 * h), int(0.33 * w):int(0.67 * w)] = True

    async def output_bytes(run: dict) -> bytes:
        async with mw["sm"]() as s:
            from sqlalchemy import select

            from app.db.models import MockupOutput

            [o] = (await s.execute(select(MockupOutput).where(MockupOutput.run_id == uuid.UUID(run["id"])))).scalars().all()
            return mw["storage"].get(o.key)

    batch = await _new_batch(mw)
    await mw["a"].post(f"/api/batches/{batch}/designs", files={"file": ("BR1.png", samples.design("opaque", size=(900, 900)), "image/png")})
    run = await _run(mw, batch, [tee["id"]])
    painted = _pixels(await output_bytes(run))
    changed = np.abs(painted - blank).sum(-1) > 40
    assert changed[block].mean() < 0.05 and changed[around].mean() > 0.5  # in front of the design, the design around it
    prev = _pixels((await mw["a"].post(f"/api/mockup-photos/{photo['id']}/preview", json={})).content)
    ph, pw = prev.shape[:2]
    blank_small = np.asarray(Image.fromarray(blank.astype(np.uint8)).resize((pw, ph))).astype(int)
    small_block = np.zeros((ph, pw), bool)
    small_block[int(0.44 * ph):int(0.56 * ph), int(0.44 * pw):int(0.56 * pw)] = True
    assert (np.abs(prev - blank_small).sum(-1) > 60)[small_block].mean() < 0.1
    # The same brush twice: the same bytes (deterministic).
    again = await mw["a"].get(f"/api/mockup-photos/{photo['id']}/front?layer=combined")
    once = await mw["a"].get(f"/api/mockup-photos/{photo['id']}/front?layer=combined")
    assert again.content == once.content
    # Erase it: the design is printed there again; clearing the brush leaves nothing in front.
    erased = Image.new("L", (300, 350), mockups.BRUSH_UNTOUCHED)
    erased.paste(mockups.BRUSH_BEHIND, (120, 140, 180, 210))
    await mw["a"].put(f"/api/mockup-photos/{photo['id']}/front", files={"file": ("brush.png", _png(erased), "image/png")})
    batch = await _new_batch(mw)
    await mw["a"].post(f"/api/batches/{batch}/designs", files={"file": ("BR1.png", samples.design("opaque", size=(900, 900)), "image/png")})
    run = await _run(mw, batch, [tee["id"]])
    assert (np.abs(_pixels(await output_bytes(run)) - blank).sum(-1) > 40)[block].mean() > 0.5
    cleared = await mw["a"].delete(f"/api/mockup-photos/{photo['id']}/front")
    assert cleared.json()["has_front_brush"] is False and cleared.json()["has_occluder"] is False
    assert guard.calls == []


async def test_another_accounts_photos_brush_and_batches_are_not_found(mw) -> None:  # noqa: F811
    tee = await _new_template(mw, "Tee")
    photo = await _photo(mw, tee["id"], "White")
    batch = await _new_batch(mw)
    b = mw["b"]
    brush = _png(Image.new("L", (10, 10), 255))
    for res in (
        await b.post(f"/api/mockup-templates/{tee['id']}/confirm", json={"photo_ids": [photo["id"]]}),
        await b.put(f"/api/mockup-photos/{photo['id']}/front", files={"file": ("b.png", brush, "image/png")}),
        await b.delete(f"/api/mockup-photos/{photo['id']}/front"),
        await b.get(f"/api/mockup-photos/{photo['id']}/front?layer=brush"),
        await b.post(f"/api/batches/{batch}/designs/archive", files={"file": ("d.zip", b"PK\x03\x04", "application/zip")}),
    ):
        assert res.status_code == 404, res.text


def test_an_area_moved_on_one_garment_lands_the_same_way_on_another() -> None:
    found_a = [[0.30, 0.30], [0.60, 0.30], [0.60, 0.70], [0.30, 0.70]]
    found_b = [[0.50, 0.20], [0.80, 0.20], [0.80, 0.60], [0.50, 0.60]]
    moved = [[0.33, 0.34], [0.57, 0.34], [0.57, 0.66], [0.33, 0.66]]  # inset inside its garment's suggestion
    out = mockups.transfer_area(moved, found_a, found_b, (1000, 1000), (1000, 1000))
    assert out == [[0.53, 0.24], [0.77, 0.24], [0.77, 0.56], [0.53, 0.56]]
    assert mockups.transfer_area(found_a, found_a, found_b, (1000, 1000), (1000, 1000)) == found_b
