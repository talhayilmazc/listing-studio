"""One product in the photo is one print area; the seller has the final say; photos arrive in pieces
(pipeline/garments.py ``same_garment`` / ``join_pieces`` / ``guess_layout`` / ``whole_at``, api/mockups.py,
pipeline/mockups.py ``with_unmasked_areas``; 18 November 2026).

The customer's report: a mockup photo with ONE shirt was given SEVERAL print areas, and a photo of five sat at
"100%" with no word. On the seller's own evaluation photos (outside the repo; docs/mockups.md) 4 of 62
one-product photos were given two or three areas: a folded shirt cut in two by a necklace, a backdrop of the
shirt's colour read as a second shirt, a hat and sandals read as garments. Here the same shapes are drawn:

* a folded shirt with a necklace across it and a hat beside it is ONE garment with ONE area; two regions that
  are pieces of one shirt are one garment (read again from the two together); a backdrop of the shirt's colour
  along the photo's edges is not its back;
* two shirts of one colour lying side by side stay two; props the analysis is not sure of do not make "several";
* "This photo has one product": one click, one area, the seller's; a later analysis changes neither it nor a
  hand-set area;
* a photo goes up in pieces: a piece sent again changes nothing, a gap is refused with where to go on, the same
  upload asked for twice is one photo; a small photo sent twice with its upload id is one photo;
* inside its print area a design is never cut off by a hole in the garment's outline.

No Etsy call, no model call, no network.
"""

from __future__ import annotations

import io
import uuid

import numpy as np
from PIL import Image, ImageDraw
from sqlalchemy import func, select

from app.api import mockups as api
from app.db.models import MockupPhoto
from app.ops import mockup_redetect
from app.pipeline import garments, mockups
from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_mockups import AREA, _new_template, _photo, mw  # noqa: F401  (fixture)
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_print_geometry import BACKGROUND, Drawn, _prepared, _tee, _two_tees

SHIRT = (118, 110, 196)


def _folded_with_necklace(size=(1000, 860)):  # noqa: ANN202
    """(rgb, foreground): one folded shirt, a thin gold necklace lying right across it (it cuts the fabric's
    colour in two), and a brown hat beside it: the seller's "VIOLET" photo, drawn."""
    def extra(img: Image.Image, mask: np.ndarray) -> None:
        d = ImageDraw.Draw(img)
        w, h = img.size
        d.line([(0.22 * w, 0.40 * h), (0.80 * w, 0.36 * h)], fill=(214, 180, 96), width=7)  # the necklace
        hat = Image.new("L", img.size, 0)
        ImageDraw.Draw(hat).ellipse([-0.12 * w, -0.05 * h, 0.2 * w, 0.42 * h], fill=255)
        img.paste(Image.new("RGB", img.size, (150, 84, 46)), (0, 0), hat)
        mask |= np.asarray(hat) > 127

    return _tee(size, (0.24, 0.16, 0.78, 0.88), colour=SHIRT, folded=True, extra=extra)


def _rect(shape: tuple[int, int], box: tuple[int, int, int, int]) -> np.ndarray:
    m = np.zeros(shape, bool)
    x0, y0, x1, y1 = box
    m[y0:y1, x0:x1] = True
    return m


# --- one garment is one area ---------------------------------------------------------------------------


def _found(region: np.ndarray, layout: str = "folded", confidence: float = 0.75) -> garments.Found:
    ys, xs = np.where(region)
    x0, y0, x1, y1 = float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())
    return garments.Found(corners=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)], confidence=confidence, kind="t-shirt", view="front",
                          layout=layout, neck=((x0 + x1) / 2, y0), armpits=((x0, y0 + 40), (x1, y0 + 40)), bottom=None,
                          region=region, allowed=region)


def test_a_drawn_folded_shirt_with_a_necklace_and_a_hat_is_one_garment_with_one_area() -> None:
    rgb, mask = _folded_with_necklace()
    found = garments.detect(rgb, None, Drawn(mask), "", several=True)
    assert len(found.candidates) == 1  # the shirt: not two halves of it, and not the hat
    assert (found.layout, found.layout_confident) == ("single", True)
    xs = [x for x, _ in found.candidates[0].corners]
    assert 0.24 * rgb.shape[1] <= min(xs) and max(xs) <= 0.78 * rgb.shape[1]  # on the shirt


def test_pieces_of_one_garment_are_one_garment_and_two_garments_of_one_colour_stay_two() -> None:
    lab = np.zeros((600, 800, 3), np.float32)
    lab[..., 0] = 50.0
    # What the seller's photos gave: the regions grown from two seeds on one folded shirt run into each other.
    top, bottom = _rect((600, 800), (200, 100, 600, 260)), _rect((600, 800), (200, 220, 600, 520))
    assert garments.same_garment(top, bottom, lab)
    left, right = _rect((600, 800), (60, 100, 380, 520)), _rect((600, 800), (400, 300, 760, 560))
    assert not garments.same_garment(left, right, lab)  # side by side: a short border, an L-shaped union
    beside = _rect((600, 800), (606, 100, 790, 520))
    assert not garments.same_garment(_rect((600, 800), (200, 100, 600, 520)), beside, lab)  # touching along a side, not over each other
    other = lab.copy()
    other[220:520, 200:600, 1] = 40.0
    assert not garments.same_garment(top, bottom & ~top, other)  # another colour: another garment

    # A folded shirt in two pieces is read again from the two together; nothing else is touched.
    asked: list[np.ndarray] = []

    def rebuild(region: np.ndarray) -> garments.Found:
        asked.append(region)
        return _found(region)

    hat = _found(_rect((600, 800), (20, 20, 150, 150)))
    lab_hat = lab.copy()
    lab_hat[20:150, 20:150, 2] = 45.0
    joined = garments.join_pieces([_found(top), hat, _found(bottom)], lab_hat, rebuild)
    assert len(joined) == 2 and len(asked) == 1
    whole = joined[0]
    assert whole.info["pieces"] == 2 and "pieces of one garment joined" in whole.notes
    assert whole.region[110, 400] and whole.region[500, 400]  # both pieces, one garment
    assert joined[1] is hat
    # A garment lying flat is not rebuilt from what lies beside it: the piece is left out.
    flat = garments.join_pieces([_found(bottom, "flat"), _found(top, "flat")], lab, rebuild)
    assert len(flat) == 1 and len(asked) == 1 and flat[0].info["pieces"] == 2 and flat[0].region.sum() == bottom.sum()
    # Two shirts of one colour side by side: both stay.
    assert len(garments.join_pieces([_found(left, "flat"), _found(right, "flat")], lab, rebuild)) == 2

    # And two real tees of one colour lying side by side are found as two.
    canvas = Image.new("RGB", (1400, 760), (232, 233, 236))
    fg = np.zeros((760, 1400), bool)
    for i in range(2):
        tee = Image.open(io.BytesIO(samples.blank((200, 60, 60), size=(640, 740), background=(232, 233, 236)))).convert("RGB")
        canvas.paste(tee, (40 + i * 700, 10))
        fg[10:750, 40 + i * 700:680 + i * 700] |= np.abs(np.asarray(tee).astype(int) - np.array((232, 233, 236))).sum(-1) > 40
    found = garments.detect(np.asarray(canvas), None, Drawn(fg), "", several=True)
    assert len(found.candidates) == 2 and found.layout in ("several", "front_back")


def test_several_needs_two_garments_the_analysis_is_sure_of() -> None:
    def candidate(box: tuple[int, int, int, int], confidence: float) -> garments.Found:
        region = _rect((600, 800), box)
        x0, y0, x1, y1 = box
        return garments.Found(corners=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)], confidence=confidence, kind="t-shirt", view="front",
                              layout="flat", neck=((x0 + x1) / 2, y0), armpits=((x0, y0 + 40), (x1, y0 + 40)), bottom=None,
                              region=region, allowed=region)

    lab = np.zeros((600, 800, 3), np.float32)
    lab[:, :400, 1], lab[:, 400:, 1] = -30.0, 30.0  # two different colours
    hat, sandals = candidate((40, 60, 340, 360), 0.38), candidate((460, 200, 740, 480), 0.62)
    assert garments.guess_layout([hat, sandals], 0, lab)[:2] == ("single", True)  # the seller's "34.jpg": props, not garments
    sure = [candidate((40, 60, 340, 360), 0.53), candidate((460, 200, 740, 480), 0.89)]
    assert garments.guess_layout(sure, 0, lab)[:2] == ("several", True)
    # The backdrop is not the shirt's back: a region of the shirt's colour and size that runs along two edges of
    # the photo (the seller's flat-lay on a flag) does not make "front & back".
    plain = np.zeros((600, 800, 3), np.float32)
    shirt, flag = candidate((60, 120, 420, 560), 1.0), candidate((440, 0, 800, 330), 0.96)
    assert garments.guess_layout([shirt, flag], 0, plain)[:2] == ("single", True)
    pair = [candidate((60, 120, 400, 560), 1.0), candidate((440, 140, 760, 560), 0.96)]
    assert garments.guess_layout(pair, 0, plain)[0] == "front_back"  # two shirts of one colour, neither at the edge
    # A cap beside a tee (the seller's "easter" photo: the second candidate 0.38 of the first) is not a second
    # product; many small shirts beside a bigger one still are several.
    tee, cap = candidate((60, 60, 460, 560), 0.68), candidate((520, 60, 760, 250), 0.68)
    assert garments.guess_layout([tee, cap], 0, lab)[:2] == ("single", True)
    small = [candidate((470 + 110 * i, 290, 570 + 110 * i, 570), 0.7) for i in range(3)]  # each 0.41 of the big one
    assert garments.guess_layout([candidate((40, 60, 340, 290), 0.7), *small], 0, lab)[:2] == ("several", True)
    assert garments.LAYOUT["sure"] == 0.45 and garments.LAYOUT["edge"] == 0.15 and garments.LAYOUT["second"] == 0.5
    assert garments.PIECES == {"colour": 8.0, "border": 2.5, "solid": 0.91}


async def test_a_single_product_photo_gets_one_area(mw) -> None:  # noqa: F811
    tee = await _new_template(mw, "One shirt")
    rgb, _ = _folded_with_necklace()
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG")
    for name, png in (("flat tee", samples.blank((214, 60, 60), size=(700, 800))), ("folded, necklace, hat", buf.getvalue())):
        photo = await _prepared(mw, "a", tee["id"], name, png)
        assert len(photo["areas"]) == 1 and photo["layout"] == "single", (name, photo["layout"], len(photo["areas"]))


# --- the seller has the final say ----------------------------------------------------------------------


async def _again(mw, photo_id: str) -> str:  # noqa: F811
    """A later analysis of the photo (what ``mockup-redetect --apply`` does with what it finds)."""
    async with mw["sm"]() as s:
        photo = await s.get(MockupPhoto, uuid.UUID(photo_id))
        prepared = mockups.prepare_blank(mw["storage"].get(photo.image_key), "default", "default", photo.name)
        what = mockup_redetect.apply_found(photo, prepared.garments, prepared.confidence, several=photo.layout)
        changed = api.resuggest(photo, prepared.layout if prepared.layout_confident else "single")
        await s.commit()
        return f"{what}/{changed}"


async def test_this_photo_has_one_product_is_one_click_and_stays(mw) -> None:  # noqa: F811
    t = await _new_template(mw, "Tees")
    photo = await _prepared(mw, "a", t["id"], "two", _two_tees())
    assert photo["layout"] == "several" and len(photo["areas"]) == 2
    one = await mw["a"].post(f"/api/mockup-photos/{photo['id']}/one-product")
    assert one.status_code == 200, one.text
    body = one.json()
    assert len(body["areas"]) == 1 and (body["layout"], body["layout_source"]) == ("single", "seller")
    assert body["areas"][0]["source"] == "seller" and body["needs_check"] and not body["confirmed"]
    area = body["areas"][0]["print_area"]
    assert all(0.0 <= v <= 1.0 for p in area for v in p)
    # A later analysis finds two garments again: the seller's one area and layout stay as they are.
    assert (await _again(mw, photo["id"])).endswith("/False")
    after = (await mw["a"].get(f"/api/mockup-photos/{photo['id']}")).json()
    assert [a["print_area"] for a in after["areas"]] == [area] and (after["layout"], after["layout_source"]) == ("single", "seller")
    # Pressed on a photo that already has one area: the area is kept, the layout is the seller's.
    again = (await mw["a"].post(f"/api/mockup-photos/{photo['id']}/one-product")).json()
    assert [a["print_area"] for a in again["areas"]] == [area]
    # It can still be moved and resized by hand, and confirmed.
    moved = [[0.1, 0.3], [0.3, 0.3], [0.3, 0.6], [0.1, 0.6]]
    done = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"areas": [{"print_area": moved}], "confirm": True})
    assert done.json()["confirmed"] and done.json()["areas"][0]["print_area"] == moved
    # Another account's photo is not there; a Photoshop template's area is the smart object's.
    assert (await mw["b"].post(f"/api/mockup-photos/{photo['id']}/one-product")).status_code == 404


async def test_a_hand_set_area_survives_a_later_analysis(mw) -> None:  # noqa: F811
    t = await _new_template(mw, "Tee")
    photo = await _photo(mw, t["id"], "White", confirm=False)
    mine = [[0.2, 0.3], [0.5, 0.3], [0.5, 0.7], [0.2, 0.7]]
    saved = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"areas": [{"print_area": mine}]})
    assert saved.status_code == 200 and saved.json()["needs_check"]  # set by hand, not yet "Looks right"
    assert (await _again(mw, photo["id"])).endswith("/False")
    after = (await mw["a"].get(f"/api/mockup-photos/{photo['id']}")).json()
    assert [a["print_area"] for a in after["areas"]] == [mine] and after["areas"][0]["source"] == "seller"
    # A second area added by hand and one deleted: both are the seller's and stay.
    two = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"areas": [{"print_area": mine}, {"print_area": AREA}]})
    assert len(two.json()["areas"]) == 2
    await _again(mw, photo["id"])
    assert len((await mw["a"].get(f"/api/mockup-photos/{photo['id']}")).json()["areas"]) == 2
    back = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"areas": [{"print_area": AREA}]})
    assert [a["print_area"] for a in back.json()["areas"]] == [AREA]
    await _again(mw, photo["id"])
    assert [a["print_area"] for a in (await mw["a"].get(f"/api/mockup-photos/{photo['id']}")).json()["areas"]] == [AREA]


# --- uploading in pieces ---------------------------------------------------------------------------------


async def _count(mw) -> int:  # noqa: F811
    async with mw["sm"]() as s:
        return int(await s.scalar(select(func.count()).select_from(MockupPhoto)) or 0)


async def test_a_photo_goes_up_in_pieces_and_a_repeat_never_adds_it_twice(mw) -> None:  # noqa: F811
    t = await _new_template(mw, "Big photos")
    data = samples.blank((30, 60, 120), size=(900, 1000))
    upload, size = str(uuid.uuid4()), len(data)
    third = size // 3
    url = f"/api/mockup-templates/{t['id']}/photos"

    def piece(offset: int, end: int, client: str = "a", up: str = upload):  # noqa: ANN202
        return mw[client].put(f"{url}?upload={up}&offset={offset}&size={size}", content=data[offset:end],
                              headers={"Content-Type": "application/octet-stream"})

    first = await piece(0, third)
    assert first.status_code == 200 and first.json() == {"received": third, "size": size}
    assert (await piece(0, third)).json()["received"] == third  # sent again after a lost answer: nothing changes
    gap = await piece(2 * third, size)
    assert gap.status_code == 409 and gap.json()["detail"]["received"] == third  # where to go on from
    assert (await piece(third, 2 * third)).json()["received"] == 2 * third
    assert (await piece(third - 10, 2 * third)).json()["received"] == 2 * third  # overlapping what is here: no byte twice
    assert (await piece(2 * third, size)).json()["received"] == size
    assert await _count(mw) == 0  # nothing is a photo until it is assembled
    body = {"upload": upload, "filename": "navy tee.png", "name": "", "rights_confirmed": True}
    assert (await mw["a"].post(f"{url}/assembled", json={**body, "rights_confirmed": False})).status_code == 422
    assert (await mw["b"].post(f"{url}/assembled", json=body)).status_code == 404  # another account: not their template
    made = await mw["a"].post(f"{url}/assembled", json=body)
    assert made.status_code == 201, made.text
    photo = made.json()
    assert photo["status"] == "preparing" and photo["name"] == "navy tee"
    assert ("prepare_mockup_template", (photo["id"],)) in mw["queue"].calls
    # The answer was lost and the page asks again: the same photo, not a second one.
    again = await mw["a"].post(f"{url}/assembled", json=body)
    assert again.status_code == 201 and again.json()["id"] == photo["id"] and await _count(mw) == 1
    # The assembled file is the file that was sent, byte for byte: it prepares like any photo.
    async with mw["sm"]() as s:
        prepared = await work.prepare(s, mw["storage"], uuid.UUID(photo["id"]))
        assert prepared.status == "ready", prepared.error
    # An upload nobody sent: said plainly. Too large: refused before a byte is kept. A piece over 8 MB: refused.
    unknown = await mw["a"].post(f"{url}/assembled", json={**body, "upload": str(uuid.uuid4())})
    assert unknown.status_code == 404 and unknown.json()["detail"] == api.UPLOAD_NOT_FOUND
    big = await mw["a"].put(f"{url}?upload={uuid.uuid4()}&offset=0&size={10 ** 9}", content=b"x")
    assert big.status_code == 413 and "limit" in big.json()["detail"]
    assert api.PHOTO_PIECE_BYTES == 8 * 1024 * 1024
    past = await mw["a"].put(f"{url}?upload={uuid.uuid4()}&offset=0&size=4", content=b"12345")
    assert past.status_code == 422


async def test_a_small_photo_sent_twice_with_its_upload_id_is_one_photo(mw) -> None:  # noqa: F811
    t = await _new_template(mw, "Small photos")
    upload = str(uuid.uuid4())

    def send():  # noqa: ANN202
        return mw["a"].post(f"/api/mockup-templates/{t['id']}/photos", data={"name": "White", "rights_confirmed": "true", "upload": upload},
                            files={"file": ("White.png", samples.blank(), "image/png")})

    first, second = await send(), await send()
    assert first.status_code == second.status_code == 201 and first.json()["id"] == second.json()["id"]
    assert await _count(mw) == 1
    # Without an id (an older page) every request is its own photo, as before.
    await _photo(mw, t["id"], "Black", (26, 26, 28), ready=False)
    assert await _count(mw) == 2


# --- the design is never cut off inside its area ----------------------------------------------------------


def test_a_design_is_not_cut_off_inside_its_print_area_by_a_hole_in_the_outline() -> None:
    prepared = mockups.prepare_blank(samples.blank(), None, None)
    w, h = prepared.width, prepared.height
    mask = Image.open(io.BytesIO(prepared.mask)).convert("L")
    # A hole in the garment's outline inside the print area (a label, a necklace, a pale edge the analysis missed).
    hole = (int(0.45 * w), int(0.35 * h), int(0.55 * w), int(0.45 * h))
    ImageDraw.Draw(mask).rectangle(hole, fill=0)
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    maps = mockups.build_maps(prepared.image, buf.getvalue(), None, [AREA])
    ink = Image.new("RGBA", (800, 800), (200, 30, 40, 255))
    out = io.BytesIO()
    ink.save(out, format="PNG")
    printed = Image.open(io.BytesIO(mockups.render(maps, mockups.load_design(out.getvalue()), mockups.Settings(AREA)))).convert("RGB")
    blank = Image.open(io.BytesIO(prepared.image)).convert("RGB")
    cx, cy = (hole[0] + hole[2]) // 2, (hole[1] + hole[3]) // 2
    r, g, b = printed.getpixel((cx, cy))
    assert r > g + 60 and r > b + 60, (r, g, b)  # the design is printed in the hole, not cut out of it
    assert sum(abs(a - c) for a, c in zip(printed.getpixel((cx, cy)), blank.getpixel((cx, cy)))) > 150
    # Outside the area nothing is printed.
    far = (int(0.1 * w), int(0.9 * h))
    assert sum(abs(a - c) for a, c in zip(printed.getpixel(far), blank.getpixel(far))) < 12
    assert mockups.ENGINE_VERSION == 5
