"""Subgroups inside a template and designs with a version per subgroup (pipeline/subgroups.py,
api/mockups.py, workers/mockups.py).

A template ("Comfort Colors") split into "White" (Use on: light garments, suffix "-white") and
"Black" (Use on: dark garments, suffix "-black"): a design's two files pair by name into one
design, each subgroup's photos print its own file, a design with one file prints only on its
subgroup, and the design still makes one listing. Synthetic images only.
"""

from __future__ import annotations

import io
import json
import uuid
from types import SimpleNamespace

import numpy as np
from PIL import Image
from sqlalchemy import func, select

from app.db.models import AllowanceUse, Asset, MockupOutput
from app.pipeline import subgroups
from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_mockups import _new_batch, mw  # noqa: F401  (fixture)
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_print_geometry import _prepared, _template, _two_tees


def _png(colour: tuple[int, int, int], alpha: bool = True) -> bytes:
    im = Image.new("RGBA", (900, 900), (0, 0, 0, 0) if alpha else (255, 255, 255, 255))
    im.paste(Image.new("RGBA", (700, 700), (*colour, 255)), (100, 100))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


SLOTS = [{"name": "White", "suffix": "-white", "use_on": "light"},
         {"name": "Black", "suffix": "-black", "use_on": "dark"}]


# --- rules --------------------------------------------------------------------------------------------


def test_a_files_suffix_pairs_it_and_its_ink_suggests_a_subgroup() -> None:
    assert subgroups.split_name("BR100-white.png", SLOTS) == ("BR100", "White")
    assert subgroups.split_name("br100_BLACK.PNG", [SLOTS[0], {**SLOTS[1], "suffix": "black"}]) == ("br100", "Black")
    assert subgroups.split_name("BR100.png", SLOTS) == ("BR100", None)
    assert subgroups.ink_of(_png((20, 20, 30))) == "dark"
    assert subgroups.ink_of(_png((245, 245, 240))) == "light"
    assert subgroups.ink_of(_png((130, 130, 130))) == "mixed"
    # Light ink goes on dark garments, dark ink on light ones.
    assert subgroups.by_ink("light", SLOTS) == "Black"
    assert subgroups.by_ink("dark", SLOTS) == "White"
    assert subgroups.by_ink("mixed", SLOTS) is None


def test_an_area_follows_the_sellers_link_then_the_garments_tone() -> None:
    white = SimpleNamespace(id=uuid.uuid4(), name="White", use_on="light")
    black = SimpleNamespace(id=uuid.uuid4(), name="Black", use_on="dark")
    both = [white, black]
    assert subgroups.area_subgroup({"tone": "light"}, black, both) is white  # a white tee: dark ink
    assert subgroups.area_subgroup({"tone": "dark"}, white, both) is black
    assert subgroups.area_subgroup({"tone": "light", "subgroup_id": str(black.id)}, white, both) is black  # the seller's link
    assert subgroups.area_subgroup({"tone": "light"}, black, [black]) is black  # one subgroup: the photo's
    assert subgroups.area_subgroup({}, black, both) is black


def test_a_design_prints_the_file_for_each_subgroup_or_nothing() -> None:
    design = SimpleNamespace(subgroup="White")
    version = SimpleNamespace(subgroup="Black")
    assert subgroups.file_for(design, [version], "White") is design
    assert subgroups.file_for(design, [version], "black") is version
    assert subgroups.file_for(design, [], "Black") is None  # one version: only its own subgroup
    everywhere = SimpleNamespace(subgroup=None)
    assert subgroups.file_for(everywhere, [], "Anything") is everywhere


# --- the template's subgroups -------------------------------------------------------------------------


async def test_a_template_starts_with_one_subgroup_and_others_are_the_sellers(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Comfort Colors")
    out = (await mw["a"].get("/api/mockup-templates")).json()
    tpl = next(x for x in out if x["id"] == t)
    assert [g["name"] for g in tpl["subgroups"]] == ["Comfort Colors"]
    photo = await _prepared(mw, "a", t, "white tee", samples.blank((238, 238, 236), size=(700, 800)))
    assert photo["subgroup_id"] == tpl["subgroups"][0]["id"]

    added = await mw["a"].post(f"/api/mockup-templates/{t}/subgroups",
                               json={"name": "Comfort Colors Black", "use_on": "dark", "suffix": "-black"})
    assert added.status_code == 201, added.text
    groups = added.json()["subgroups"]
    assert [(g["name"], g["use_on"], g["suffix"]) for g in groups][1] == ("Comfort Colors Black", "dark", "-black")
    first = groups[0]["id"]
    renamed = await mw["a"].patch(f"/api/mockup-subgroups/{first}", json={"name": "Comfort Colors White", "use_on": "light"})
    assert renamed.json()["subgroups"][0]["name"] == "Comfort Colors White"
    assert (await mw["a"].post(f"/api/mockup-templates/{t}/subgroups", json={"name": "comfort colors white"})).status_code == 409

    # The photo moves to the black subgroup; a subgroup of another template is refused.
    other = await _template(mw, "a", "Bella")
    other_group = next(x for x in (await mw["a"].get("/api/mockup-templates")).json() if x["id"] == other)["subgroups"][0]["id"]
    assert (await mw["a"].patch(f"/api/mockup-photos/{photo['id']}", json={"subgroup_id": other_group})).status_code == 422
    moved = await mw["a"].patch(f"/api/mockup-photos/{photo['id']}", json={"subgroup_id": groups[1]["id"]})
    assert moved.json()["subgroup_id"] == groups[1]["id"]

    # Deleting the subgroup moves its photos back to the first one; the last one stays.
    left = await mw["a"].delete(f"/api/mockup-subgroups/{groups[1]['id']}")
    assert [g["id"] for g in left.json()["subgroups"]] == [first]
    assert (await mw["a"].get(f"/api/mockup-photos/{photo['id']}")).json()["subgroup_id"] == first
    assert (await mw["a"].delete(f"/api/mockup-subgroups/{first}")).status_code == 409


async def test_another_account_cannot_see_or_change_subgroups(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Mine")
    group = (await mw["a"].post(f"/api/mockup-templates/{t}/subgroups", json={"name": "Dark"})).json()["subgroups"][1]["id"]
    assert (await mw["b"].post(f"/api/mockup-templates/{t}/subgroups", json={"name": "X"})).status_code == 404
    assert (await mw["b"].patch(f"/api/mockup-subgroups/{group}", json={"name": "X"})).status_code == 404
    assert (await mw["b"].delete(f"/api/mockup-subgroups/{group}")).status_code == 404
    batch = await _new_batch(mw)
    design = (await mw["a"].post(f"/api/batches/{batch}/designs", files={"file": ("A1.png", _png((20, 20, 20)), "image/png")})).json()
    assert (await mw["b"].patch(f"/api/mockup-designs/{design['id']}", json={"subgroup": "Dark"})).status_code == 404


# --- uploads ------------------------------------------------------------------------------------------


async def test_files_pair_by_name_and_suffix_and_unpaired_files_follow_their_ink(mw) -> None:  # noqa: F811
    batch = await _new_batch(mw)
    slots = {"subgroups": json.dumps(SLOTS)}

    async def send(name: str, png: bytes, **extra: str) -> dict:
        res = await mw["a"].post(f"/api/batches/{batch}/designs", data={**slots, **extra}, files={"file": (name, png, "image/png")})
        assert res.status_code == 201, res.text
        return res.json()

    white = await send("BR100-white.png", _png((20, 20, 30)))
    assert white["subgroup"] == "White" and white["parsed_sku"] == "BR100" and white["versions"] == []
    paired = await send("BR100-black.png", _png((245, 245, 240)))
    assert paired["id"] == white["id"]  # one design
    assert [(v["subgroup"], v["original_filename"]) for v in paired["versions"]] == [("Black", "BR100-black.png")]
    # No suffix: light ink suggests the dark-garment subgroup, dark ink the light-garment one.
    assert (await send("CR200.png", _png((240, 240, 235))))["subgroup"] == "Black"
    assert (await send("DR300.png", _png((25, 25, 25))))["subgroup"] == "White"
    # Dropped on a slot: that slot, whatever the ink says.
    assert (await send("ER400.png", _png((25, 25, 25)), subgroup="Black"))["subgroup"] == "Black"
    designs = (await mw["a"].get(f"/api/batches/{batch}/designs")).json()
    assert len(designs) == 4  # BR100 (two files), CR200, DR300, ER400

    # Dragged to the other slot; a slot the design already has a file for is refused.
    dr = next(d for d in designs if d["original_filename"] == "DR300.png")
    assert (await mw["a"].patch(f"/api/mockup-designs/{dr['id']}", json={"subgroup": "Black"})).json()["subgroup"] == "Black"
    assert (await mw["a"].patch(f"/api/mockup-designs/{white['id']}", json={"subgroup": "Black"})).status_code == 409
    sku_kept = (await mw["a"].patch(f"/api/mockup-designs/{dr['id']}", json={"subgroup": None})).json()
    assert sku_kept["subgroup"] is None and sku_kept["parsed_sku"] == dr["parsed_sku"]
    # One of a design's files moved to another slot; never onto a slot it already fills, nor by another account.
    version = paired["versions"][0]["id"]
    assert (await mw["a"].patch(f"/api/mockup-design-versions/{version}", json={"subgroup": "White"})).status_code == 409
    assert (await mw["b"].patch(f"/api/mockup-design-versions/{version}", json={"subgroup": "Other"})).status_code == 404
    moved = (await mw["a"].patch(f"/api/mockup-design-versions/{version}", json={"subgroup": "Heather"})).json()
    assert [v["subgroup"] for v in moved["versions"]] == ["Heather"]


# --- several garments: an area per subgroup ----------------------------------------------------------


async def test_each_area_links_to_a_subgroup_by_default_from_its_garment(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Two")
    photo = await _prepared(mw, "a", t, "two", _two_tees())
    assert photo["layout"] == "several" and all(a["tone"] in ("light", "dark") for a in photo["areas"])
    tpl = (await mw["a"].post(f"/api/mockup-templates/{t}/subgroups", json={"name": "Dark", "use_on": "dark"})).json()
    first, dark = (g["id"] for g in tpl["subgroups"])
    areas = photo["areas"]
    body = {"areas": [{"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default",
                       "role": a["role"], "subgroup_id": sid} for a, sid in zip(areas, (dark, first))]}
    saved = (await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json=body)).json()
    assert [a["subgroup_id"] for a in saved["areas"]] == [dark, first]
    assert [a["tone"] for a in saved["areas"]] == [a["tone"] for a in areas]  # kept for the default
    # A subgroup of another template is not linked.
    x_id = await _template(mw, "a", "X")
    other = next(x for x in (await mw["a"].get("/api/mockup-templates")).json() if x["id"] == x_id)
    body["areas"][0]["subgroup_id"] = other["subgroups"][0]["id"]
    saved = (await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json=body)).json()
    assert saved["areas"][0]["subgroup_id"] is None


# --- runs and listings --------------------------------------------------------------------------------


async def _confirmed(mw, t: str, name: str, colour, subgroup: str) -> dict:  # noqa: ANN001, F811
    photo = await _prepared(mw, "a", t, name, samples.blank(colour, size=(700, 800)))
    a = photo["areas"][0]
    res = await mw["a"].put(f"/api/mockup-photos/{photo['id']}/areas", json={"confirm": True, "areas": [
        {"print_area": a["print_area"], "detected_area": a["detected_area"], "source": "default"}]})
    assert res.status_code == 200, res.text
    assert (await mw["a"].patch(f"/api/mockup-photos/{photo['id']}", json={"subgroup_id": subgroup})).status_code == 200
    return {**photo, "area": a["print_area"]}


async def test_one_listing_per_design_each_subgroup_with_its_own_file(mw) -> None:  # noqa: F811
    t = await _template(mw, "a", "Comfort Colors")
    tpl = (await mw["a"].get("/api/mockup-templates")).json()[0]
    white = tpl["subgroups"][0]["id"]
    await mw["a"].patch(f"/api/mockup-subgroups/{white}", json={"name": "White", "use_on": "light", "suffix": "-white"})
    black = (await mw["a"].post(f"/api/mockup-templates/{t}/subgroups",
                                json={"name": "Black", "use_on": "dark", "suffix": "-black"})).json()["subgroups"][1]["id"]
    on_white = await _confirmed(mw, t, "white tee", (238, 238, 236), white)
    on_black = await _confirmed(mw, t, "black tee", (35, 35, 38), black)

    batch = await _new_batch(mw)
    slots = {"subgroups": json.dumps(SLOTS)}
    for name, png in (("BR100-white.png", _png((200, 20, 20))), ("BR100-black.png", _png((20, 40, 220))),
                      ("GR200-white.png", _png((20, 160, 50)))):
        res = await mw["a"].post(f"/api/batches/{batch}/designs", data=slots, files={"file": (name, png, "image/png")})
        assert res.status_code == 201, res.text
    designs = {d["parsed_sku"]: d for d in (await mw["a"].get(f"/api/batches/{batch}/designs")).json()}
    assert set(designs) == {"BR100", "GR200"}

    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [t]})
    assert res.status_code == 202, res.text
    run_id = uuid.UUID(res.json()["id"])
    async with mw["sm"]() as s:
        assert (await work.run(s, mw["storage"], run_id)).status == "succeeded"
        rows = (await s.execute(select(MockupOutput).where(MockupOutput.run_id == run_id))).scalars().all()

    def colour_at(row: MockupOutput, q) -> np.ndarray:  # noqa: ANN001
        img = np.asarray(Image.open(io.BytesIO(mw["storage"].get(row.key))).convert("RGB")).astype(int)
        h, w = img.shape[:2]
        return img[int((q[0][1] + 0.4 * (q[3][1] - q[0][1])) * h), int(sum(p[0] for p in q) / 4 * w)]

    by = {(str(r.design_id), str(r.photo_id)): r for r in rows}
    br, gr = designs["BR100"]["id"], designs["GR200"]["id"]
    red = colour_at(by[(br, on_white["id"])], on_white["area"])
    blue = colour_at(by[(br, on_black["id"])], on_black["area"])
    assert red[0] > red[2] + 60  # the white subgroup prints the "-white" file
    assert blue[2] > blue[0] + 60  # the black subgroup prints the "-black" file
    # A design with one file: only its own subgroup's mockups.
    assert (gr, on_white["id"]) in by and (gr, on_black["id"]) not in by
    out = (await mw["a"].get(f"/api/mockup-runs/{run_id}")).json()
    totals = {d["sku"]: (d["done"], d["total"]) for d in out["designs"]}
    assert totals == {"BR100": (2, 2), "GR200": (1, 1)}

    made = await mw["a"].post(f"/api/mockup-runs/{run_id}/listings", json={})
    assert made.status_code == 200, made.text
    assert made.json() == {"groups": 2, "photos": 3}  # one listing per design, mixed mockups
    async with mw["sm"]() as s:
        photos = (await s.execute(select(Asset.mockup_design_id, func.count()).where(Asset.batch_id == batch)
                                  .group_by(Asset.mockup_design_id))).all()
        assert {str(d): n for d, n in photos} == {br: 2, gr: 1}
        # Mockups and their listing groups are not written listings: the allowance is unchanged.
        assert (await s.execute(select(func.count()).select_from(AllowanceUse))).scalar_one() == 0


async def test_a_designs_other_files_are_deleted_with_it(mw) -> None:  # noqa: F811
    from datetime import datetime, timedelta, timezone

    from app.db.models import MockupDesign, MockupDesignVersion
    from app.pipeline import upload_retention

    batch = await _new_batch(mw)
    slots = {"subgroups": json.dumps(SLOTS)}
    for name, png in (("BR100-white.png", _png((20, 20, 30))), ("BR100-black.png", _png((245, 245, 240)))):
        assert (await mw["a"].post(f"/api/batches/{batch}/designs", data=slots, files={"file": (name, png, "image/png")})).status_code == 201
    async with mw["sm"]() as s:
        [design] = (await s.execute(select(MockupDesign).where(MockupDesign.batch_id == batch))).scalars().all()
        [version] = (await s.execute(select(MockupDesignVersion).where(MockupDesignVersion.design_id == design.id))).scalars().all()
        keys = (design.design_key, version.design_key)
        assert all(mw["storage"].exists(k) for k in keys)
        # No mockups were ever made from it: it goes like any upload nobody worked on.
        await upload_retention.run(s, mw["storage"], now=datetime.now(timezone.utc) + timedelta(days=31))
        await s.refresh(design)
        await s.refresh(version)
        assert design.design_key is None and version.design_key is None
    assert not any(mw["storage"].exists(k) for k in keys)
