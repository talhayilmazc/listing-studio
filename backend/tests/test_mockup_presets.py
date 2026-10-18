"""Areas, subgroups and templates (seller feedback, Part C; api/mockups.py, workers/mockups.py,
pipeline/subgroups.py, pipeline/mockups.py). Synthetic images only; nothing is sent anywhere.

* "Print on all garments": the same design on every area, whatever the run says;
* an area linked to "Any" prints the design's own file whatever its garment's tone;
* a template can have many subgroups: 11 areas print 11 different design versions;
* account presets with suffixes: a new template takes them in one click, files pair by them,
  "Assign subgroups" and "Save as preset";
* the file-name prefix: "CC-BR100-01.jpg", safe characters, never a duplicate in one ZIP;
* another account sees and changes nothing.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile

import numpy as np
from PIL import Image, ImageDraw
from sqlalchemy import select

from app.db.models import Asset, MockupOutput, MockupPhoto
from app.pipeline import mockups as M
from app.pipeline import subgroups
from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_mockups import _new_batch, mw  # noqa: F401  (fixture)
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_print_geometry import _prepared, _template


def _solid(colour: tuple[int, int, int], size: tuple[int, int] = (600, 600)) -> bytes:
    img = Image.new("RGBA", size, (*colour, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _wide_photo(columns: int, size: tuple[int, int] = (2000, 700)) -> bytes:
    """A light grey studio photo with ``columns`` lighter panels (garments) side by side."""
    w, h = size
    img = Image.new("RGB", size, (210, 212, 215))
    draw = ImageDraw.Draw(img)
    step = w / columns
    for c in range(columns):
        draw.rectangle([c * step + 8, 40, (c + 1) * step - 8, h - 40], fill=(236, 236, 234))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _column_area(c: int, columns: int) -> list[list[float]]:
    left, right = (c + 0.2) / columns, (c + 0.8) / columns
    return [[round(left, 4), 0.25], [round(right, 4), 0.25], [round(right, 4), 0.6], [round(left, 4), 0.6]]


async def _photo_with_areas(mw, t: str, columns: int, links: list[dict] | None = None, **extra) -> dict:  # noqa: F811
    photo = await _prepared(mw, "a", t, f"wide{columns}", _wide_photo(columns))
    areas = [{"print_area": _column_area(c, columns), "source": "seller", **((links or [{}] * columns)[c])} for c in range(columns)]
    res = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": areas, **extra})
    assert res.status_code == 200, res.text
    return res.json()


async def _run(mw, t: str, files: list[tuple[str, bytes]], slots: list | None = None, **options) -> uuid.UUID:  # noqa: F811
    batch = await _new_batch(mw)
    for name, data in files:
        form = {"subgroups": json.dumps(slots)} if slots else None
        res = await mw["a"].post(f"/api/batches/{batch}/designs", data=form, files={"file": (name, data, "image/png")})
        assert res.status_code == 201, res.text
    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [t], **options})
    assert res.status_code == 202, res.text
    run_id = uuid.UUID(res.json()["id"])
    async with mw["sm"]() as s:
        assert (await work.run(s, mw["storage"], run_id)).status == "succeeded"
    return run_id


def _colour_in(jpeg: bytes, area) -> np.ndarray:  # noqa: ANN001
    img = np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB")).astype(int)
    h, w = img.shape[:2]
    x = int(sum(p[0] for p in area) / 4 * w)
    y = int((area[0][1] + 0.3 * (area[3][1] - area[0][1])) * h)
    return img[y - 3:y + 3, x - 3:x + 3].reshape(-1, 3).mean(axis=0)


# --- print on all garments, "Any" -----------------------------------------------------------------


async def test_print_on_all_garments_puts_one_design_on_every_area_in_order_mode(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Pairs")
    plain = await _photo_with_areas(mw, t, 2)
    every = await _photo_with_areas(mw, t, 2, print_all=True)
    assert every["print_all"] is True and plain["print_all"] is False
    run_id = await _run(mw, t, [("RED1.png", _solid((220, 30, 30))), ("BLU1.png", _solid((30, 60, 220)))], garment_mode="order")
    async with mw["sm"]() as s:
        rows = (await s.execute(select(MockupOutput).where(MockupOutput.run_id == run_id))).scalars().all()
    by_photo: dict[str, set] = {}
    for r in rows:
        by_photo.setdefault(str(r.photo_id), set()).add(r.key)
    assert len(by_photo[plain["id"]]) == 1  # order mode: one mockup, a design per garment
    assert len(by_photo[every["id"]]) == 2  # print on all garments: each design alone on both areas
    for key in by_photo[every["id"]]:
        jpeg = mw["storage"].get(key)
        a, b = (_colour_in(jpeg, _column_area(c, 2)) for c in range(2))
        assert np.abs(a - b).max() < 25  # the same design on both areas


def test_an_area_linked_to_any_prints_the_designs_own_file() -> None:
    white = type("G", (), {"id": "w", "name": "White", "use_on": "light"})()
    black = type("G", (), {"id": "b", "name": "Black", "use_on": "dark"})()
    picked = subgroups.area_subgroup({"subgroup_id": subgroups.ANY, "tone": "light"}, white, [white, black])
    assert picked.name == subgroups.ANY
    design = type("D", (), {"subgroup": "Black"})()
    version = type("V", (), {"subgroup": "White"})()
    assert subgroups.file_for(design, [version], subgroups.ANY) is design  # its own file, not the White version
    assert subgroups.file_for(design, [version], "White") is version


async def test_the_area_chooser_saves_a_subgroup_or_any(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Chooser")
    tpl = (await mw["a"].post(f"/api/mockup-templates/{t}/subgroups", json={"name": "Black", "use_on": "dark"})).json()
    black = tpl["subgroups"][1]["id"]
    photo = await _photo_with_areas(mw, t, 2, [{"subgroup_id": black}, {"subgroup_any": True}])
    assert photo["areas"][0]["subgroup_id"] == black and photo["areas"][0]["subgroup_any"] is False
    assert photo["areas"][1]["subgroup_id"] is None and photo["areas"][1]["subgroup_any"] is True


# --- many subgroups -----------------------------------------------------------------------------------


async def test_eleven_areas_print_eleven_different_versions(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Eleven")
    tpl = next(x for x in (await mw["a"].get("/api/mockup-templates")).json() if x["id"] == t)
    first = tpl["subgroups"][0]["id"]
    await mw["a"].patch(f"/api/mockup-subgroups/{first}", json={"name": "S1", "suffix": "-s1"})
    for i in range(2, 12):
        res = await mw["a"].post(f"/api/mockup-templates/{t}/subgroups", json={"name": f"S{i}", "suffix": f"-s{i}"})
        assert res.status_code == 201, res.text
    groups = res.json()["subgroups"]
    assert len(groups) == 11  # more than the old 12-at-most would matter; any number in practice
    photo = await _photo_with_areas(mw, t, 11, [{"subgroup_id": g["id"]} for g in groups])
    colours = [((i * 23) % 200 + 30, (i * 67) % 200 + 30, (i * 131) % 200 + 30) for i in range(11)]
    slots = [{"name": g["name"], "suffix": g["suffix"], "use_on": "any"} for g in groups]
    run_id = await _run(mw, t, [(f"BR100-s{i + 1}.png", _solid(c)) for i, c in enumerate(colours)], slots)
    async with mw["sm"]() as s:
        [out] = (await s.execute(select(MockupOutput).where(MockupOutput.run_id == run_id))).scalars().all()
    jpeg = mw["storage"].get(out.key)
    got = [_colour_in(jpeg, a["print_area"]) for a in photo["areas"]]
    for i, (c, g) in enumerate(zip(colours, got)):
        # Each area its own version: nearer its own colour than any other version's.
        nearest = min(range(11), key=lambda k: float(np.abs(np.array(colours[k]) - g).sum()))
        assert nearest == i, (i, c, g)


# --- presets ------------------------------------------------------------------------------------------


async def test_presets_with_suffixes_make_a_new_templates_subgroups_and_pair_files(mw) -> None:  # noqa: F811
    for body in ({"name": "White design", "use_on": "dark", "suffix": "-white"},
                 {"name": "Black design", "use_on": "light", "suffix": "-black"}):
        assert (await mw["a"].post("/api/mockup-subgroup-presets", json=body)).status_code == 201
    assert (await mw["a"].post("/api/mockup-subgroup-presets", json={"name": "white DESIGN"})).status_code == 409
    made = (await mw["a"].post("/api/mockup-templates", json={"name": "Comfort", "presets": True})).json()
    assert [(g["name"], g["suffix"], g["use_on"]) for g in made["subgroups"]] == [
        ("White design", "-white", "dark"), ("Black design", "-black", "light")]
    # Upload pairing reads the suffixes from the template's subgroups, as before.
    batch = await _new_batch(mw)
    slots = [{"name": g["name"], "suffix": g["suffix"], "use_on": g["use_on"]} for g in made["subgroups"]]
    for name in ("BR100-white.png", "BR100-black.png"):
        res = await mw["a"].post(f"/api/batches/{batch}/designs", data={"subgroups": json.dumps(slots)},
                                 files={"file": (name, samples.design("transparent"), "image/png")})
        assert res.status_code == 201, res.text
    designs = (await mw["a"].get(f"/api/batches/{batch}/designs")).json()
    assert len(designs) == 1 and designs[0]["parsed_sku"] == "BR100"


async def test_assign_subgroups_and_save_as_preset(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Plain")
    white = (await mw["a"].post("/api/mockup-subgroup-presets", json={"name": "White", "use_on": "dark", "suffix": "-w"})).json()
    black = (await mw["a"].post("/api/mockup-subgroup-presets", json={"name": "Black", "use_on": "light", "suffix": "-b"})).json()
    photo = await _prepared(mw, "a", t, "one", samples.blank((238, 238, 236), size=(700, 800)))
    done = (await mw["a"].post(f"/api/mockup-templates/{t}/presets", json={"preset_ids": [white["id"], black["id"]]})).json()
    # The untouched default subgroup became the first preset, its photos with it; no duplicates.
    assert [(g["name"], g["suffix"]) for g in done["subgroups"]] == [("White", "-w"), ("Black", "-b")]
    assert (await mw["a"].get(f"/api/mockup-photos/{photo['id']}")).json()["subgroup_id"] == done["subgroups"][0]["id"]
    again = (await mw["a"].post(f"/api/mockup-templates/{t}/presets", json={"preset_ids": [black["id"]]})).json()
    assert len(again["subgroups"]) == 2
    # A template's own subgroup kept for the account.
    other = await _template(mw, "a", "Other")
    tpl = (await mw["a"].post(f"/api/mockup-templates/{other}/subgroups", json={"name": "Heather", "use_on": "any", "suffix": "-h"})).json()
    saved = (await mw["a"].post(f"/api/mockup-subgroups/{tpl['subgroups'][1]['id']}/preset")).json()
    assert saved["name"] == "Heather" and saved["suffix"] == "-h"
    names = [p["name"] for p in (await mw["a"].get("/api/mockup-subgroup-presets")).json()]
    assert names == ["White", "Black", "Heather"]


# --- the file-name prefix ------------------------------------------------------------------------------


def test_a_prefix_keeps_only_safe_characters() -> None:
    assert M.clean_prefix(" CC ") == "CC"
    assert M.clean_prefix("Comfort Colors") == "Comfort-Colors"
    assert M.clean_prefix('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"
    assert M.clean_prefix("ŞİRKET") == "RKET"  # letters outside A-Z are left out (safe everywhere)
    assert M.clean_prefix("   ") is None and M.clean_prefix("") is None
    assert len(M.clean_prefix("x" * 50)) == M.PREFIX_MAX
    taken: set[str] = set()
    names = [M.prefixed_name("CC", "BR100", 1, taken), M.prefixed_name("CC", "BR100", 1, taken), M.prefixed_name("CC", "BR 1/00", 2, taken)]
    assert names == ["CC-BR100-01.jpg", "CC-BR100-01-2.jpg", "CC-BR-1-00-02.jpg"]
    assert all('/' not in n and '\\' not in n for n in names)


async def test_the_prefix_names_mockups_zips_and_listing_photos_without_duplicates(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Tees")
    res = await mw["a"].patch(f"/api/mockup-templates/{t}", json={"file_prefix": "C C/*"})
    assert res.status_code == 200 and res.json()["file_prefix"] == "C-C"
    for name, colour in (("white", (238, 238, 236)), ("sand", (220, 200, 160))):
        photo = await _prepared(mw, "a", t, name, samples.blank(colour, size=(700, 800)))
        a = photo["areas"][0]
        await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": [
            {"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default"}]})
    # Two designs with the same SKU: names stay unique.
    run_id = await _run(mw, t, [("BR100.png", samples.design("transparent")), ("BR100 copy.png", samples.design("white"))])
    async with mw["sm"]() as s:
        rows = (await s.execute(select(MockupOutput).where(MockupOutput.run_id == run_id).order_by(MockupOutput.position))).scalars().all()
    names = sorted(r.filename for r in rows)
    assert len(names) == 4 and len({n.lower() for n in names}) == 4
    assert all(n.startswith("C-C-") and n.endswith(".jpg") for n in names)
    assert {"C-C-BR100-01.jpg", "C-C-BR100-02.jpg"} <= set(names)
    made = await mw["a"].post(f"/api/mockup-runs/{run_id}/listings", json={})
    assert made.status_code == 200, made.text
    async with mw["sm"]() as s:
        photos = (await s.execute(select(Asset.original_filename).where(Asset.mockup_design_id.is_not(None)))).scalars().all()
    assert all(p.split("/")[-1].startswith("C-C-") for p in photos)
    # Clearing it brings back today's naming for the next run.
    assert (await mw["a"].patch(f"/api/mockup-templates/{t}", json={"file_prefix": ""})).json()["file_prefix"] is None


async def test_zip_entries_are_unique_with_a_prefix(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Zip")
    await mw["a"].patch(f"/api/mockup-templates/{t}", json={"file_prefix": "ZP"})
    photo = await _prepared(mw, "a", t, "only", samples.blank((238, 238, 236), size=(700, 800)))
    a = photo["areas"][0]
    await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": [
        {"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default"}]})
    run_id = await _run(mw, t, [("X1.png", samples.design("transparent")), ("X1 b.png", samples.design("white"))])
    res = await mw["a"].post(f"/api/mockup-runs/{run_id}/zip", json={"layout": "flat"})
    zid = uuid.UUID(res.json()["id"])
    async with mw["sm"]() as s:
        await work.build_zip(s, mw["storage"], zid)
    got = (await mw["a"].get(f"/api/mockup-zips/{zid}")).json()
    assert got["status"] == "ready", got
    data = (await mw["a"].get(f"/api/mockup-zips/{zid}/download")).content
    entries = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert len(entries) == len({e.lower() for e in entries}) == 2
    assert all(e.startswith("ZP-") for e in entries)


# --- another account ----------------------------------------------------------------------------------


async def test_another_account_cannot_see_or_change_presets_or_prefixes(mw) -> None:  # noqa: F811
    mine = (await mw["a"].post("/api/mockup-subgroup-presets", json={"name": "White", "suffix": "-w"})).json()
    t = await _template(mw, "a", "Mine")
    b = mw["b"]
    assert (await b.get("/api/mockup-subgroup-presets")).json() == []
    assert (await b.patch(f"/api/mockup-subgroup-presets/{mine['id']}", json={"name": "X"})).status_code == 404
    assert (await b.delete(f"/api/mockup-subgroup-presets/{mine['id']}")).status_code == 404
    assert (await b.post(f"/api/mockup-templates/{t}/presets", json={"preset_ids": [mine["id"]]})).status_code == 404
    theirs = await _template(mw, "b", "Theirs")
    assert (await b.post(f"/api/mockup-templates/{theirs}/presets", json={"preset_ids": [mine["id"]]})).status_code == 404
    assert (await b.patch(f"/api/mockup-templates/{t}", json={"file_prefix": "HACK"})).status_code == 404
    tpl = next(x for x in (await mw["a"].get("/api/mockup-templates")).json() if x["id"] == t)
    assert (await b.post(f"/api/mockup-subgroups/{tpl['subgroups'][0]['id']}/preset")).status_code == 404
    assert tpl["file_prefix"] is None
    async with mw["sm"]() as s:
        assert (await s.execute(select(MockupPhoto))).scalars().all() == []
