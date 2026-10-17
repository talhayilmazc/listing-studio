"""Templates of many photos, and every photo's print areas confirmed once (api/mockups.py,
workers/mockups.py, pipeline/garments.py ``find_at``).

* A template holds many photos: uploaded into it, reordered, moved, removed; a run places
  every design on every confirmed photo of the chosen templates, in order.
* "Looks right": a photo is used for mockups only once the seller has confirmed its
  print areas; changing them asks again; a run says how many photos it left out.
* "Copy this area to other photos": the areas, size and fold strength go to the chosen
  photos of the same template, each waiting for its own confirmation.
* "Add area": the garment under the seller's tap alone (a touching neighbour is not taken
  in), in the order of the taps; a tap on the background places a box there instead.
* No Etsy call, no model call, no network.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.models import MockupPhoto
from tests.test_garments import _tees
from tests.test_mockups import (  # noqa: F401  (fixtures)
    AREA,
    _confirm,
    _designs,
    _new_batch,
    _new_template,
    _NoCalls,
    _photo,
    _run,
    mw,
)
from tests.test_multi_shop import world  # noqa: F401  (fixture)


def _inside(p: list[float], quad: list[list[float]]) -> bool:
    xs, ys = [c[0] for c in quad], [c[1] for c in quad]
    return min(xs) <= p[0] <= max(xs) and min(ys) <= p[1] <= max(ys)


def _centre(quad: list[list[float]]) -> list[float]:
    return [sum(c[0] for c in quad) / 4, sum(c[1] for c in quad) / 4]


async def test_a_template_holds_many_photos_in_the_sellers_order(mw, monkeypatch) -> None:  # noqa: F811
    guard = _NoCalls(monkeypatch)
    tee = await _new_template(mw, "Comfort Colors 1717")
    colours = [("White", (238, 238, 236)), ("Black", (26, 26, 28)), ("Pepper", (90, 92, 94)), ("Ivory", (240, 234, 220))]
    photos = [await _photo(mw, tee["id"], n, c) for n, c in colours]
    listed = (await mw["a"].get("/api/mockup-templates")).json()
    assert [t["name"] for t in listed] == ["Comfort Colors 1717"]
    assert [p["name"] for p in listed[0]["photos"]] == ["White", "Black", "Pepper", "Ivory"]
    assert (listed[0]["confirmed"], listed[0]["needs_check"]) == (4, 0)
    # Reordered: a design's mockups follow it (the first is the cover).
    order = [photos[2]["id"], photos[0]["id"]]
    res = await mw["a"].put(f"/api/mockup-templates/{tee['id']}/photos/order", json={"ids": order})
    assert [p["name"] for p in res.json()["photos"]] == ["Pepper", "White", "Black", "Ivory"]
    # Moved to another template; removed.
    other = await _new_template(mw, "Sweatshirts")
    moved = await mw["a"].patch(f"/api/mockup-photos/{photos[3]['id']}", json={"template_id": other["id"]})
    assert moved.json()["template_id"] == other["id"]
    assert (await mw["a"].delete(f"/api/mockup-photos/{photos[1]['id']}")).status_code == 204
    batch = await _new_batch(mw)
    await _designs(mw, batch, ["BR1.png", "BR2.png"])
    run = await _run(mw, batch, [tee["id"]])
    assert (run["photos"], run["total"]) == (2, 4)  # 2 designs x 2 photos
    assert [o["photo_name"] for o in run["designs"][0]["outputs"]] == ["Pepper", "White"]
    # Templates are reordered too.
    res = await mw["a"].put("/api/mockup-templates/order", json={"ids": [other["id"], tee["id"]]})
    assert [t["name"] for t in res.json()] == ["Sweatshirts", "Comfort Colors 1717"]
    assert guard.calls == []


async def test_a_photo_is_used_only_after_looks_right_and_a_change_asks_again(mw) -> None:  # noqa: F811
    tee = await _new_template(mw, "Tee")
    checked = await _photo(mw, tee["id"], "White")
    waiting = await _photo(mw, tee["id"], "Black", (26, 26, 28), confirm=False)
    card = next(t for t in (await mw["a"].get("/api/mockup-templates")).json() if t["id"] == tee["id"])
    assert (card["confirmed"], card["needs_check"]) == (1, 1)
    assert [p["needs_check"] for p in card["photos"]] == [False, True]
    batch = await _new_batch(mw)
    await _designs(mw, batch, ["BR1.png"])
    run = await _run(mw, batch, [tee["id"]])
    assert (run["photos"], run["skipped"], run["total"]) == (1, 1, 1)  # the unconfirmed one is left out, and said so
    assert [o["photo_name"] for o in run["designs"][0]["outputs"]] == ["White"]
    # Moving an area of a confirmed photo without "Looks right": it waits for the check again.
    res = await mw["a"].put(f"/api/mockup-photos/{checked['id']}/areas", json={"areas": [{"print_area": AREA}]})
    assert res.json()["needs_check"] is True
    other = await _new_batch(mw)
    await _designs(mw, other, ["BR2.png"])
    res = await mw["a"].post(f"/api/batches/{other}/mockups", json={"template_ids": [tee["id"]]})
    assert res.status_code == 409 and "Looks right" in res.json()["detail"]
    # Both confirmed: both used.
    await _confirm(mw, res_photo := (await mw["a"].get(f"/api/mockup-photos/{checked['id']}")).json())
    await _confirm(mw, waiting)
    assert res_photo["areas"][0]["print_area"] == AREA
    run = await _run(mw, other, [tee["id"]])
    assert (run["photos"], run["skipped"]) == (2, 0)
    # A photo confirmed when the run was asked for but unconfirmed before it runs is left out.
    async with mw["sm"]() as s:
        photo = await s.get(MockupPhoto, uuid.UUID(waiting["id"]))
        photo.confirmed_at = None
        await s.commit()
    third = await _new_batch(mw)
    await _designs(mw, third, ["BR3.png"])
    res = await mw["a"].post(f"/api/batches/{third}/mockups", json={"template_ids": [tee["id"]]})
    assert res.json()["photos"] == 1


async def test_copy_to_other_photos_gives_each_its_own_check(mw, monkeypatch) -> None:  # noqa: F811
    guard = _NoCalls(monkeypatch)
    tee = await _new_template(mw, "Ten colours")
    source = await _photo(mw, tee["id"], "White", confirm=False)
    targets = [await _photo(mw, tee["id"], n, c) for n, c in (("Black", (26, 26, 28)), ("Navy", (30, 40, 80)))]
    elsewhere = await _photo(mw, (await _new_template(mw, "Other"))["id"], "Grey", (150, 150, 150))
    await mw["a"].patch(f"/api/mockup-photos/{source['id']}", json={"size": 0.75, "fold_strength": 0.3})
    await _confirm(mw, source, [{"print_area": AREA, "detected_area": source["areas"][0]["detected_area"]}])
    res = await mw["a"].post(f"/api/mockup-photos/{source['id']}/copy", json={"photo_ids": [t["id"] for t in targets]})
    assert res.status_code == 200, res.text
    for photo in res.json():
        # Placed by each photo's own garment: the same tee in another colour, so (nearly) the same place.
        assert all(abs(p - q) < 0.01 for c, d in zip(photo["areas"][0]["print_area"], AREA) for p, q in zip(c, d))
        assert photo["areas"][0]["source"] == "copied"
        assert (photo["size"], photo["fold_strength"]) == (0.75, 0.3)
        # Each copy is shown on its own photo and waits for its own "Looks right".
        assert photo["needs_check"] and not photo["confirmed"]
    # Each target keeps its own suggestion for "Reset to suggestion".
    assert res.json()[0]["areas"][0]["detected_area"] == targets[0]["areas"][0]["detected_area"]
    # Only to photos of the same template.
    res = await mw["a"].post(f"/api/mockup-photos/{source['id']}/copy", json={"photo_ids": [elsewhere["id"]]})
    assert res.status_code == 422
    # One tap each confirms them.
    for target in targets:
        await _confirm(mw, (await mw["a"].get(f"/api/mockup-photos/{target['id']}")).json())
    card = next(t for t in (await mw["a"].get("/api/mockup-templates")).json() if t["id"] == tee["id"])
    assert (card["confirmed"], card["needs_check"]) == (3, 0)
    assert guard.calls == []


async def test_add_area_finds_the_tapped_garment_alone_in_the_order_of_the_taps(mw, monkeypatch) -> None:  # noqa: F811
    guard = _NoCalls(monkeypatch)
    tee = await _new_template(mw, "Two tees")
    photo = await _photo(mw, tee["id"], "Two tees", blank=_tees(), confirm=False)
    # Two garments in one photo: this photo's layout (one area per photo otherwise).
    photo = (await mw["a"].patch(f"/api/mockup-photos/{photo['id']}", json={"layout": "several"})).json()
    left, right = sorted(photo["areas"], key=lambda a: _centre(a["print_area"])[0])
    # The seller starts from nothing: taps the right tee first, then the left one.
    first = await mw["a"].post(f"/api/mockup-photos/{photo['id']}/find", json={"x": 0.75, "y": 0.45, "areas": []})
    assert first.status_code == 200 and first.json()["found"] is True
    a1 = first.json()["print_area"]
    assert _centre(a1)[0] > 0.5 and all(c[0] > 0.48 for c in a1)  # only the right tee
    second = await mw["a"].post(f"/api/mockup-photos/{photo['id']}/find", json={"x": 0.25, "y": 0.45, "areas": [a1]})
    a2 = second.json()["print_area"]
    assert second.json()["found"] and _centre(a2)[0] < 0.5 and all(c[0] < 0.52 for c in a2)
    # Each found area is close to what the detector suggested for that garment.
    assert abs(_centre(a1)[0] - _centre(right["print_area"])[0]) < 0.05
    assert abs(_centre(a2)[0] - _centre(left["print_area"])[0]) < 0.05
    # Saved in the order of the taps.
    saved = await _confirm(mw, photo, [{"print_area": a1, "detected_area": a1, "source": "tap"},
                                       {"print_area": a2, "detected_area": a2, "source": "tap"}])
    assert [_centre(a["print_area"])[0] > 0.5 for a in saved["areas"]] == [True, False]
    assert [a["source"] for a in saved["areas"]] == ["tap", "tap"]
    # A tap on the background: a box where the seller tapped, said so, as wide as the others.
    miss = (await mw["a"].post(f"/api/mockup-photos/{photo['id']}/find", json={"x": 0.5, "y": 0.97, "areas": [a1, a2]})).json()
    assert miss["found"] is False and miss["note"] and _inside([0.5, 0.9], miss["print_area"])
    # A photo prepared before the foreground map was kept: its garment mask is used.
    async with mw["sm"]() as s:
        row = await s.get(MockupPhoto, uuid.UUID(photo["id"]))
        row.foreground_key = None
        await s.commit()
    again = await mw["a"].post(f"/api/mockup-photos/{photo['id']}/find", json={"x": 0.75, "y": 0.45, "areas": [a2]})
    assert again.json()["found"] and _centre(again.json()["print_area"])[0] > 0.5
    assert guard.calls == []


async def test_the_foreground_map_is_kept_with_the_photo_and_removed_with_it(mw) -> None:  # noqa: F811
    tee = await _new_template(mw, "Tee")
    photo = await _photo(mw, tee["id"], "White")
    async with mw["sm"]() as s:
        row = await s.get(MockupPhoto, uuid.UUID(photo["id"]))
        keys = [row.image_key, row.mask_key, row.foreground_key]
        assert all(keys) and all(mw["storage"].exists(k) for k in keys)
    assert (await mw["a"].delete(f"/api/mockup-templates/{tee['id']}")).status_code == 204
    assert not any(mw["storage"].exists(k) for k in keys)
    async with mw["sm"]() as s:
        assert (await s.execute(select(MockupPhoto))).scalars().all() == []


async def test_limits_of_photos_per_template_and_runs(mw, monkeypatch) -> None:  # noqa: F811
    from app.api import mockups as api_mockups
    from app.workers import mockups as work

    tee = await _new_template(mw, "Tee")
    monkeypatch.setattr(api_mockups, "MAX_PHOTOS_PER_TEMPLATE", 2)
    await _photo(mw, tee["id"], "One")
    await _photo(mw, tee["id"], "Two")
    res = await mw["a"].post(f"/api/mockup-templates/{tee['id']}/photos", data={"name": "Three", "rights_confirmed": "true"},
                             files={"file": ("three.png", b"x", "image/png")})
    assert res.status_code == 409 and "up to 2 photos" in res.json()["detail"]
    monkeypatch.setattr(work, "MAX_PHOTOS", 1)
    batch = await _new_batch(mw)
    await _designs(mw, batch, ["BR1.png"])
    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [tee["id"]]})
    assert res.status_code == 422 and "up to 1 photos" in res.json()["detail"]
