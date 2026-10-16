"""The mockup generator (pipeline/mockups.py, workers/mockups.py, api/mockups.py).

* Geometry: the design lands inside the print area, its aspect kept, top-aligned.
* Determinism: the same template, design and settings give the same bytes.
* Transparency: a transparent design leaves the garment untouched around it.
* Dark garments: white ink stays light (an underbase), the garment around stays dark.
* No Etsy call and no model call, per mockup or anywhere in the feature.
* The allowance ("Listings generated") does not change.
* Another account's template, batch or run is 404; the rights confirmation is required.
* Uploads: up to 30 MB per photo, refused above with the limit; a template is
  prepared in the worker ("preparing", then "ready" or "failed" with why).
* Long names; colour sets and "apply to the set"; SKU naming, unique; per-design progress.
* "Use for listings" (a group per design, the chosen cover) and "Download" (a ZIP,
  private, gone after 24 hours), for the same run.
* Performance: 50 designs x 3 templates (engine) and 10 designs x 10 colours (worker), timed.
"""

from __future__ import annotations

import hashlib
import io
import time
import uuid

import httpx
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select

from app.core import allowance
from app.core.config import get_settings, set_settings_override
from app.db.models import (
    AiCall,
    Asset,
    AuditLog,
    MockupDesign,
    MockupOutput,
    MockupTemplate,
    MockupZip,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.etsy.api import EtsyApiClient
from app.pipeline import mockups
from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.storage import LocalStorage
from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_multi_shop import world  # noqa: F401  (fixture)

AREA = [[0.35, 0.25], [0.65, 0.25], [0.65, 0.60], [0.35, 0.60]]


def _pixels(jpeg: bytes) -> Image.Image:
    return Image.open(io.BytesIO(jpeg)).convert("RGB")


@pytest.fixture(scope="module")
def light() -> tuple[mockups.PreparedBlank, mockups.Maps]:
    prepared = mockups.prepare_blank(samples.blank())
    return prepared, mockups.build_maps(prepared.image, prepared.mask)


@pytest.fixture(scope="module")
def dark() -> tuple[mockups.PreparedBlank, mockups.Maps]:
    prepared = mockups.prepare_blank(samples.blank((26, 26, 28)))
    return prepared, mockups.build_maps(prepared.image, prepared.mask)


# --- templates --------------------------------------------------------------------------------


def test_the_garment_is_separated_from_a_plain_background(light) -> None:
    prepared, maps = light
    assert prepared.mask_source in ("background", "segmentation")  # the garment found
    mask = Image.open(io.BytesIO(prepared.mask)).convert("L")
    assert mask.getpixel((5, 5)) == 0  # background corner
    assert mask.getpixel((prepared.width // 2, prepared.height // 2)) == 255  # chest
    # The default print area is inside the garment, on the chest.
    (l, t), (r, _), (_, b), _ = prepared.print_area
    assert 0.3 < l < r < 0.7 and 0.15 < t < b < 0.75
    assert maps.underbase == 0.0  # a light garment: the ink sits on the fabric


def test_a_transparent_blank_uses_its_own_transparency() -> None:
    prepared = mockups.prepare_blank(samples.blank((30, 40, 80), transparent=True))
    assert prepared.mask_source == "alpha"
    assert mockups.build_maps(prepared.image, prepared.mask).underbase == 1.0  # navy: full underbase


def test_a_white_garment_on_a_white_background_is_all_fabric_rather_than_lost() -> None:
    """Only the folds' shadows would separate: not a garment. The whole photo counts
    as fabric (the seller is told), so the design is still printed."""
    prepared = mockups.prepare_blank(samples.blank((246, 246, 246), background=(250, 250, 250)), runner=None)
    assert prepared.mask_source == "none"
    maps = mockups.build_maps(prepared.image, prepared.mask)
    out = _pixels(mockups.render(maps, mockups.load_design(samples.design()), mockups.Settings(AREA, 1.0, 0.0)))
    r, g, b = out.getpixel((round(0.5 * maps.width), round(0.30 * maps.height)))
    assert r > 150 and g < 120  # the red ink is there


def test_the_print_area_must_be_a_four_sided_shape() -> None:
    assert mockups.clean_print_area([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])
    with pytest.raises(mockups.MockupError):
        mockups.clean_print_area([[0.1, 0.1], [0.9, 0.9], [0.9, 0.1], [0.1, 0.9]])  # crossed
    with pytest.raises(mockups.MockupError):
        mockups.clean_print_area([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]])
    # Corners outside the photo are brought back in.
    assert mockups.clean_print_area([[-1, -1], [2, -1], [2, 2], [-1, 2]]) == [[0, 0], [1, 0], [1, 1], [0, 1]]


# --- geometry ---------------------------------------------------------------------------------


@pytest.mark.parametrize("design_size", [(900, 600), (300, 900), (500, 500)])
def test_the_design_fits_the_print_area_with_its_aspect_kept(design_size: tuple[int, int]) -> None:
    corners = [(400.0, 300.0), (700.0, 320.0), (690.0, 760.0), (410.0, 740.0)]  # a slight perspective
    placed = mockups.place(corners, design_size, 0.9)
    sw, sh = placed.scaled
    assert abs(sw / sh - design_size[0] / design_size[1]) < 0.02  # aspect kept
    # Every corner of the design lands inside the print area.
    for x, y in ((0, 0), (sw, 0), (sw, sh), (0, sh)):
        px, py = mockups.apply(placed.matrix, x, y)
        assert _inside((px, py), corners), (design_size, px, py)
    u0, v0, du, dv = placed.box
    assert v0 == 0.0  # top-aligned
    assert abs(u0 - (1 - du) / 2) < 1e-9  # centred
    assert max(du, dv) == pytest.approx(0.9, abs=0.01)  # the size setting


def _inside(p: tuple[float, float], quad: list[tuple[float, float]]) -> bool:
    for i in range(4):
        (ax, ay), (bx, by) = quad[i], quad[(i + 1) % 4]
        if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) < -1e-6:
            return False
    return True


def test_the_print_lands_only_inside_the_print_area(light) -> None:
    prepared, maps = light
    design = mockups.load_design(samples.design("opaque"))
    out = _pixels(mockups.render(maps, design, mockups.Settings(AREA, 1.0, 0.0)))
    blank = Image.open(io.BytesIO(prepared.image)).convert("RGB")
    W, H = maps.width, maps.height
    changed = [(x, y) for y in range(0, H, 6) for x in range(0, W, 6)
               if sum(abs(a - b) for a, b in zip(out.getpixel((x, y)), blank.getpixel((x, y)))) > 24]
    assert changed, "the design was printed"
    margin = 4
    for x, y in changed:
        assert AREA[0][0] * W - margin <= x <= AREA[1][0] * W + margin
        assert AREA[0][1] * H - margin <= y <= AREA[2][1] * H + margin
    # Top-aligned: the print starts at the top of the area.
    assert min(y for _, y in changed) <= AREA[0][1] * H + 8


# --- determinism, transparency, dark garments -------------------------------------------------


def test_the_same_inputs_give_the_same_pixels(light) -> None:
    prepared, maps = light
    settings = mockups.Settings(prepared.print_area, 0.85, 0.7)
    first = mockups.render(maps, mockups.load_design(samples.design()), settings)
    again = mockups.render(mockups.build_maps(prepared.image, prepared.mask), mockups.load_design(samples.design()), settings)
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(again).hexdigest()
    # And the template itself: the same blank gives the same mask and area.
    assert mockups.prepare_blank(samples.blank()) == prepared


def test_a_transparent_design_leaves_the_garment_untouched_around_it(light) -> None:
    prepared, maps = light
    out = _pixels(mockups.render(maps, mockups.load_design(samples.design()), mockups.Settings(AREA, 1.0, 0.0)))
    blank = Image.open(io.BytesIO(prepared.image)).convert("RGB")
    # A transparent corner of the design's box: the garment shows through unchanged (JPEG aside).
    x, y = round(0.36 * maps.width), round(0.26 * maps.height)
    assert sum(abs(a - b) for a, b in zip(out.getpixel((x, y)), blank.getpixel((x, y)))) < 18
    # The design's red ink is there, a little tinted by the light fabric.
    r, g, b = out.getpixel((round(0.5 * maps.width), round(0.30 * maps.height)))
    assert r > 150 and g < 110 and b < 90


def test_white_ink_stays_light_on_a_dark_garment(dark) -> None:
    prepared, maps = dark
    assert maps.underbase == 1.0
    out = _pixels(mockups.render(maps, mockups.load_design(samples.design("white")), mockups.Settings(AREA, 1.0, 0.3)))
    ink = out.getpixel((round(0.5 * maps.width), round(0.30 * maps.height)))
    around = out.getpixel((round(0.5 * maps.width), round(0.75 * maps.height)))
    assert min(ink) > 190, ink  # an underbase: white stays white
    assert max(around) < 60, around  # the garment stays dark


def test_a_heathered_fabric_shows_its_grain_but_does_not_swamp_the_print() -> None:
    plain = mockups.prepare_blank(samples.blank((150, 152, 156)))
    heather = mockups.prepare_blank(samples.blank((150, 152, 156), heather=True))
    spread = []
    for prepared in (plain, heather):
        maps = mockups.build_maps(prepared.image, prepared.mask)
        out = _pixels(mockups.render(maps, mockups.load_design(samples.design("opaque")), mockups.Settings(AREA, 1.0, 0.0)))
        box = out.crop((round(0.45 * maps.width), round(0.28 * maps.height), round(0.55 * maps.width), round(0.33 * maps.height)))
        values = list(box.convert("L").tobytes())
        mean = sum(values) / len(values)
        spread.append((sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5)
    # The texture is normalised: heather's far stronger speckle shows through the ink
    # only somewhat more than plain fabric's grain.
    assert spread[1] < spread[0] * 2.5, spread


def test_the_output_is_the_image_pipelines_size_and_format(light) -> None:
    _, maps = light
    jpeg = mockups.render(maps, mockups.load_design(samples.design()), mockups.Settings(AREA))
    image = Image.open(io.BytesIO(jpeg))
    assert image.format == "JPEG" and max(image.size) <= 2000
    big = mockups.prepare_blank(samples.blank(size=(2400, 2800)))
    assert max(big.width, big.height) == 2000


# --- the API and the worker -------------------------------------------------------------------


class _NoCalls:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []

        def refuse(name):
            async def _refuse(*args, **kwargs):
                self.calls.append(name)
                raise AssertionError(f"{name} was called")

            return _refuse

        monkeypatch.setattr(EtsyApiClient, "_request", refuse("Etsy API"))
        monkeypatch.setattr(AnthropicLLMClient, "complete_json", refuse("model"))
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse("network"))


@pytest_asyncio.fixture()
async def mw(world, tmp_path):  # noqa: F811
    from app.api.deps import get_storage

    set_settings_override(get_settings().model_copy(update={"storage_dir": str(tmp_path)}))
    get_storage.cache_clear()  # one storage per process: this test's folder
    world["storage"] = LocalStorage(tmp_path)
    yield world
    get_storage.cache_clear()


async def _template(mw, name: str, colour=(238, 238, 236), *, set_id: str | None = None, ready: bool = True) -> dict:
    data = {"name": name, "rights_confirmed": "true", **({"set_id": set_id} if set_id else {})}
    res = await mw["a"].post("/api/mockup-templates", data=data,
                             files={"file": (f"{name}.png", samples.blank(colour, size=(600, 700)), "image/png")})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "preparing"  # saved at once; the outline is found in the worker
    assert ("prepare_mockup_template", (body["id"],)) in mw["queue"].calls
    if ready:
        async with mw["sm"]() as s:
            prepared = await work.prepare(s, mw["storage"], uuid.UUID(body["id"]))
            assert prepared.status == "ready", prepared.error
        body = next(t for t in (await mw["a"].get("/api/mockup-templates")).json() if t["id"] == body["id"])
    return body


async def _new_batch(mw) -> uuid.UUID:
    res = await mw["a"].post("/api/batches", json={"connection_id": str(mw["a1"])})
    assert res.status_code == 201, res.text
    return uuid.UUID(res.json()["id"])


async def _designs(mw, batch: uuid.UUID, names: list[str], kind: str = "transparent") -> list[dict]:
    out = []
    for name in names:
        res = await mw["a"].post(f"/api/batches/{batch}/designs", files={"file": (name, samples.design(kind), "image/png")})
        assert res.status_code == 201, res.text
        out.append(res.json())
    return out


async def _run(mw, batch: uuid.UUID, template_ids: list[str]) -> dict:
    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": template_ids})
    assert res.status_code == 202, res.text
    async with mw["sm"]() as s:
        done = await work.run(s, mw["storage"], uuid.UUID(res.json()["id"]))
        assert done.status == "succeeded", done.error
    return (await mw["a"].get(f"/api/mockup-runs/{res.json()['id']}")).json()


def _noise_png(megabytes: float) -> bytes:
    """A real PNG of about ``megabytes`` (noise does not compress)."""
    import random

    side = int((megabytes * 1024 * 1024 / 3) ** 0.5)
    img = Image.frombytes("RGB", (side, side), random.Random(3).randbytes(side * side * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=0)
    return buf.getvalue()


async def test_a_25_mb_photo_is_accepted_and_a_31_mb_one_is_refused_with_the_limit(mw) -> None:
    big = _noise_png(25)
    assert 24 * 1024 * 1024 < len(big) < 26 * 1024 * 1024
    res = await mw["a"].post("/api/mockup-templates", data={"name": "Big", "rights_confirmed": "true"},
                             files={"file": ("big.png", big, "image/png")})
    assert res.status_code == 201, res.text
    # Over 30 MB: refused before anything is read, saying the limit (the body is
    # drained first, so a proxy in front can pass the answer on).
    res = await mw["a"].post("/api/mockup-templates", data={"name": "Huge", "rights_confirmed": "true"},
                             files={"file": ("huge.png", b"\x89PNG" + b"0" * (31 * 1024 * 1024), "image/png")})
    assert res.status_code == 413
    assert res.json()["detail"] == "This file is too large: the limit is 30 MB per file."
    # Just over 30 MB but inside the request's envelope: refused by the file check, with its size.
    res = await mw["a"].post(f"/api/batches/{await _new_batch(mw)}/designs",
                             files={"file": ("d.png", b"\x89PNG" + b"0" * (30 * 1024 * 1024 + 200_000), "image/png")})
    assert res.status_code == 413
    assert res.json()["detail"].endswith("the limit is 30 MB per file.") and res.json()["detail"].startswith("This file is 30.2 MB")


async def test_a_blank_needs_the_rights_confirmation_and_long_names_are_kept(mw) -> None:
    res = await mw["a"].post("/api/mockup-templates", data={"name": "Tee"},
                             files={"file": ("tee.png", samples.blank(size=(600, 700)), "image/png")})
    assert res.status_code == 422
    assert "This photo is my own, or I have the right to use it to show my products." in res.json()["detail"]
    long = "Comfort Colors 1717 Garment-Dyed Heavyweight T-Shirt, Front, Flat Lay on Linen — Kırmızı / Красный " + "x" * 40
    created = await _template(mw, long)
    assert created["name"] == " ".join(long.split())[:120] and len(created["name"]) == 120
    renamed = await mw["a"].patch(f"/api/mockup-templates/{created['id']}", json={"name": "  Tişört,   ön yüz  "})
    assert renamed.json()["name"] == "Tişört, ön yüz"
    assert (await mw["a"].patch(f"/api/mockup-templates/{created['id']}", json={"name": "y" * 121})).status_code == 422
    async with mw["sm"]() as s:
        template = await s.get(MockupTemplate, uuid.UUID(created["id"]))
        assert template.rights_confirmed_at is not None
        assert (await s.execute(select(AuditLog).where(AuditLog.action == "mockup_template.created"))).scalars().first()


async def test_a_template_is_preparing_until_the_worker_is_done_and_says_why_it_failed(mw) -> None:
    pending = await _template(mw, "Pending", ready=False)
    assert pending["print_area"] is None
    assert (await mw["a"].get(f"/api/mockup-templates/{pending['id']}/image")).status_code == 409
    assert (await mw["a"].post(f"/api/mockup-templates/{pending['id']}/preview", json={})).status_code == 409
    batch = await _new_batch(mw)
    await _designs(mw, batch, ["BR1.png"])
    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": [pending["id"]]})
    assert res.status_code == 409 and "not ready" in res.json()["detail"]
    # A photo the worker cannot read: "failed", with a reason the seller can act on.
    async with mw["sm"]() as s:
        template = await s.get(MockupTemplate, uuid.UUID(pending["id"]))
        mw["storage"].put(template.source_key, b"not an image", "image/png")
        done = await work.prepare(s, mw["storage"], template.id)
        assert (done.status, done.error) == ("failed", work.PREPARE_FAILED)


async def test_a_colour_set_takes_one_templates_print_area_size_and_fold(mw) -> None:
    res = await mw["a"].post("/api/mockup-sets", json={"name": "Comfort Colors 1717, front"})
    assert res.status_code == 201
    set_id = res.json()["id"]
    colours = [("White", (238, 238, 236)), ("Black", (26, 26, 28)), ("Pepper", (90, 92, 94))]
    templates = [await _template(mw, name, colour, set_id=set_id) for name, colour in colours]
    pending = await _template(mw, "Ivory", (240, 234, 220), set_id=set_id, ready=False)
    first = templates[0]["id"]
    await mw["a"].patch(f"/api/mockup-templates/{first}", json={"print_area": AREA, "size": 0.75, "fold_strength": 0.3})
    res = await mw["a"].post(f"/api/mockup-sets/{set_id}/apply", json={"template_id": first})
    assert res.status_code == 200, res.text
    for t in res.json():
        assert (t["print_area"], t["size"], t["fold_strength"]) == (AREA, 0.75, 0.3), t["name"]
    # The one still preparing keeps the set's area when it is ready.
    async with mw["sm"]() as s:
        done = await work.prepare(s, mw["storage"], uuid.UUID(pending["id"]))
        assert (done.status, done.print_area) == ("ready", AREA)
    sets = (await mw["a"].get("/api/mockup-sets")).json()
    assert sets[0]["name"] == "Comfort Colors 1717, front" and len(sets[0]["template_ids"]) == 4
    # A template outside the set cannot be the source; one is taken out; the set is deleted, its templates stay.
    loose = await _template(mw, "Loose")
    assert (await mw["a"].post(f"/api/mockup-sets/{set_id}/apply", json={"template_id": loose["id"]})).status_code == 422
    out = await mw["a"].patch(f"/api/mockup-templates/{templates[2]['id']}", json={"set_id": None})
    assert out.json()["set_id"] is None
    assert (await mw["a"].delete(f"/api/mockup-sets/{set_id}")).status_code == 204
    assert all(t["set_id"] is None for t in (await mw["a"].get("/api/mockup-templates")).json())
    async with mw["sm"]() as s:
        assert (await s.execute(select(AuditLog).where(AuditLog.action == "mockup_set.deleted"))).scalars().first()


async def test_mockups_are_named_by_sku_and_template_uniquely(mw, monkeypatch) -> None:
    guard = _NoCalls(monkeypatch)
    white = await _template(mw, "White / Front")
    black = await _template(mw, "Black", (26, 26, 28))
    batch = await _new_batch(mw)
    designs = await _designs(mw, batch, ["BR5229.png", "BR5229 copy.png", "sunset.png"])
    assert [d["parsed_sku"] for d in designs] == ["BR5229", "BR5229", None]
    assert [d["group_key"] for d in designs] == ["BR5229", "BR5229 2", "sunset"]
    # The SKU is editable before the mockups are made (Etsy's SKU rules).
    assert (await mw["a"].patch(f"/api/mockup-designs/{designs[2]['id']}", json={"sku": "bad^sku"})).status_code == 422
    edited = await mw["a"].patch(f"/api/mockup-designs/{designs[2]['id']}", json={"sku": "SUN-01"})
    assert (edited.json()["parsed_sku"], edited.json()["group_key"]) == ("SUN-01", "SUN-01")
    run = await _run(mw, batch, [white["id"], black["id"]])
    names = sorted(o["filename"] for d in run["designs"] for o in d["outputs"])
    assert names == [
        "BR5229_Black (2).jpg", "BR5229_Black.jpg", "BR5229_White Front (2).jpg", "BR5229_White Front.jpg",
        "SUN-01_Black.jpg", "SUN-01_White Front.jpg",
    ]
    # Per-design progress, and nothing in the listings yet.
    assert all((d["done"], d["total"], d["in_listings"]) == (2, 2, False) for d in run["designs"])
    async with mw["sm"]() as s:
        assert (await s.execute(select(Asset).where(Asset.batch_id == batch))).scalars().all() == []
    assert guard.calls == []
    # Without a SKU, the design's file name.
    await mw["a"].patch(f"/api/mockup-designs/{designs[2]['id']}", json={"sku": None})
    run = await _run(mw, batch, [white["id"]])
    assert "sunset_White Front.jpg" in [o["filename"] for d in run["designs"] for o in d["outputs"]]


async def test_use_for_listings_makes_a_group_per_design_with_the_chosen_cover(mw, monkeypatch) -> None:
    guard = _NoCalls(monkeypatch)
    templates = [await _template(mw, n, c) for n, c in (("White", (238, 238, 236)), ("Black", (26, 26, 28)), ("Navy", (30, 40, 80)))]
    async with mw["sm"]() as s:
        before = (await allowance.status(s, await s.get(Tenant, mw["alice"]))).used
    batch = await _new_batch(mw)
    designs = await _designs(mw, batch, ["BR5200.png", "BR5201.png"])
    run = await _run(mw, batch, [t["id"] for t in templates])
    navy = templates[2]["id"]
    res = await mw["a"].post(f"/api/mockup-runs/{run['id']}/listings",
                             json={"design_ids": [designs[0]["id"]], "covers": {designs[0]["id"]: navy}})
    assert res.json() == {"groups": 1, "photos": 3}
    async with mw["sm"]() as s:
        assets = list((await s.execute(select(Asset).where(Asset.batch_id == batch).order_by(Asset.rank))).scalars())
        assert [(a.group_key, a.rank, a.original_filename) for a in assets] == [
            ("BR5200", 1, "BR5200/BR5200_Navy.jpg"), ("BR5200", 2, "BR5200/BR5200_White.jpg"), ("BR5200", 3, "BR5200/BR5200_Black.jpg"),
        ]
        assert (await s.get(UploadBatch, batch)).status.value == "ready"
    # All designs, the first template as cover where none is chosen; using one again changes it.
    res = await mw["a"].post(f"/api/mockup-runs/{run['id']}/listings", json={"design_ids": None, "covers": {}})
    assert res.json() == {"groups": 2, "photos": 6}
    groups = (await mw["a"].get(f"/api/batches/{batch}/groups")).json()
    assert [g["group_key"] for g in groups] == ["BR5200", "BR5201"]
    async with mw["sm"]() as s:
        covers = {a.group_key: a.original_filename for a in (await s.execute(
            select(Asset).where(Asset.batch_id == batch, Asset.rank == 1))).scalars()}
        assert covers == {"BR5200": "BR5200/BR5200_White.jpg", "BR5201": "BR5201/BR5201_White.jpg"}
        assert (await allowance.status(s, await s.get(Tenant, mw["alice"]))).used == before  # not counted
        assert (await s.execute(select(AiCall))).scalars().all() == []
    assert all(d["in_listings"] for d in (await mw["a"].get(f"/api/mockup-runs/{run['id']}")).json()["designs"])
    assert guard.calls == []
    # A cover that is not one of the run's templates is refused.
    other = await _template(mw, "Other")
    res = await mw["a"].post(f"/api/mockup-runs/{run['id']}/listings", json={"covers": {designs[0]["id"]: other["id"]}})
    assert res.status_code == 422


async def test_a_zip_holds_the_chosen_mockups_and_expires_after_24_hours(mw) -> None:
    import zipfile
    from datetime import datetime, timedelta, timezone

    white = await _template(mw, "White")
    black = await _template(mw, "Black", (26, 26, 28))
    batch = await _new_batch(mw)
    designs = await _designs(mw, batch, ["BR5300.png", "BR5301.png"])
    run = await _run(mw, batch, [white["id"], black["id"]])
    res = await mw["a"].post(f"/api/mockup-runs/{run['id']}/zip", json={"layout": "folders"})
    assert res.status_code == 202
    zip_id = res.json()["id"]
    assert ("build_mockup_zip", (zip_id,)) in mw["queue"].calls
    async with mw["sm"]() as s:
        done = await work.build_zip(s, mw["storage"], uuid.UUID(zip_id))
        assert done.status == "ready" and done.files == 4
        assert done.expires_at - done.ready_at == timedelta(hours=24)
    got = await mw["a"].get(f"/api/mockup-zips/{zip_id}/download")
    assert got.status_code == 200 and got.headers["content-type"] == "application/zip"
    assert sorted(zipfile.ZipFile(io.BytesIO(got.content)).namelist()) == [
        "BR5300/BR5300_Black.jpg", "BR5300/BR5300_White.jpg", "BR5301/BR5301_Black.jpg", "BR5301/BR5301_White.jpg",
    ]
    # Flat, one design.
    res = await mw["a"].post(f"/api/mockup-runs/{run['id']}/zip", json={"layout": "flat", "design_ids": [designs[1]["id"]]})
    async with mw["sm"]() as s:
        flat = await work.build_zip(s, mw["storage"], uuid.UUID(res.json()["id"]))
    got = await mw["a"].get(f"/api/mockup-zips/{flat.id}/download")
    assert sorted(zipfile.ZipFile(io.BytesIO(got.content)).namelist()) == ["BR5301_Black.jpg", "BR5301_White.jpg"]
    # The same run, used for listings too.
    assert (await mw["a"].post(f"/api/mockup-runs/{run['id']}/listings", json={})).json()["groups"] == 2
    # Another account cannot see or download it.
    assert (await mw["b"].get(f"/api/mockup-zips/{zip_id}")).status_code == 404
    assert (await mw["b"].get(f"/api/mockup-zips/{zip_id}/download")).status_code == 404
    # After 24 hours: deleted, and the link says so.
    async with mw["sm"]() as s:
        key = (await s.get(MockupZip, uuid.UUID(zip_id))).key
        assert await work.expire_zips(s, mw["storage"], now=datetime.now(timezone.utc) + timedelta(hours=25)) == 2
    assert not mw["storage"].exists(key)
    assert (await mw["a"].get(f"/api/mockup-zips/{zip_id}/download")).status_code == 410
    assert (await mw["a"].get(f"/api/mockup-zips/{zip_id}")).json()["status"] == "expired"


async def test_another_accounts_templates_sets_runs_mockups_and_zips_are_not_found(mw) -> None:
    template = await _template(mw, "White tee")
    tid = template["id"]
    set_id = (await mw["a"].post("/api/mockup-sets", json={"name": "Set"})).json()["id"]
    batch = await _new_batch(mw)
    designs = await _designs(mw, batch, ["BR9.png"])
    run = await _run(mw, batch, [tid])
    output = run["designs"][0]["outputs"][0]["id"]
    b = mw["b"]
    for res in (
        await b.get(f"/api/mockup-templates/{tid}/image"),
        await b.patch(f"/api/mockup-templates/{tid}", json={"size": 0.5}),
        await b.post(f"/api/mockup-templates/{tid}/preview", json={}),
        await b.delete(f"/api/mockup-templates/{tid}"),
        await b.patch(f"/api/mockup-sets/{set_id}", json={"name": "x"}),
        await b.post(f"/api/mockup-sets/{set_id}/apply", json={"template_id": tid}),
        await b.delete(f"/api/mockup-sets/{set_id}"),
        await b.post(f"/api/batches/{batch}/designs", files={"file": ("d.png", samples.design(), "image/png")}),
        await b.patch(f"/api/mockup-designs/{designs[0]['id']}", json={"sku": "X1"}),
        await b.get(f"/api/mockup-runs/{run['id']}"),
        await b.get(f"/api/mockup-runs/{run['id']}/outputs/{output}/image"),
        await b.post(f"/api/mockup-runs/{run['id']}/listings", json={}),
        await b.post(f"/api/mockup-runs/{run['id']}/zip", json={}),
    ):
        assert res.status_code == 404, res.text
    assert (await b.get("/api/mockup-templates")).json() == [] and (await b.get("/api/mockup-sets")).json() == []
    # Bob cannot print on Alice's template, even in his own batch.
    async with mw["sm"]() as s:
        bobs = UploadBatch(tenant_id=mw["bob"], status=UploadBatchStatus.uploading, file_count=0)
        s.add(bobs)
        await s.commit()
        bobs_id = bobs.id
    assert (await b.post(f"/api/batches/{bobs_id}/mockups", json={"template_ids": [tid]})).status_code == 404
    # A template upload naming Alice's set from Bob's account.
    res = await b.post("/api/mockup-templates", data={"name": "x", "rights_confirmed": "true", "set_id": set_id},
                       files={"file": ("x.png", samples.blank(size=(600, 700)), "image/png")})
    assert res.status_code == 404


async def test_no_model_call_in_the_preview_and_settings_are_checked(mw, monkeypatch) -> None:
    guard = _NoCalls(monkeypatch)
    template = await _template(mw, "White tee")
    tid = template["id"]
    res = await mw["a"].post(f"/api/mockup-templates/{tid}/preview", json={"print_area": AREA, "size": 0.8, "fold_strength": 0.2})
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    again = await mw["a"].post(f"/api/mockup-templates/{tid}/preview", json={"print_area": AREA, "size": 0.8, "fold_strength": 0.2})
    assert again.content == res.content
    assert guard.calls == []
    body = (await mw["a"].patch(f"/api/mockup-templates/{tid}", json={"print_area": AREA, "size": 0.7, "fold_strength": 0.2})).json()
    assert (body["print_area"], body["print_area_source"], body["size"], body["fold_strength"]) == (AREA, "seller", 0.7, 0.2)
    crossed = [[0.1, 0.1], [0.9, 0.9], [0.9, 0.1], [0.1, 0.9]]
    assert (await mw["a"].patch(f"/api/mockup-templates/{tid}", json={"print_area": crossed})).status_code == 422
    reset = (await mw["a"].patch(f"/api/mockup-templates/{tid}", json={"reset_print_area": True})).json()
    assert (reset["print_area"], reset["print_area_source"]) == (template["print_area"], "default")


async def test_deleting_a_template_is_recorded_and_keeps_mockups_already_made(mw) -> None:
    template = await _template(mw, "White tee")
    batch = await _new_batch(mw)
    await _designs(mw, batch, ["BR1.png"])
    run = await _run(mw, batch, [template["id"]])
    await mw["a"].post(f"/api/mockup-runs/{run['id']}/listings", json={})
    assert (await mw["a"].delete(f"/api/mockup-templates/{template['id']}")).status_code == 204
    async with mw["sm"]() as s:
        assert (await s.execute(select(AuditLog).where(AuditLog.action == "mockup_template.deleted"))).scalars().first()
        [asset] = (await s.execute(select(Asset).where(Asset.batch_id == batch))).scalars().all()
        assert asset.mockup_template_id is None and asset.processed_key and mw["storage"].exists(asset.processed_key)


async def test_mockups_are_refused_once_listings_are_written_and_a_second_run_replaces_the_first(mw) -> None:
    template = await _template(mw, "White tee")
    res = await mw["a"].post(f"/api/batches/{mw['batch']}/mockups", json={"template_ids": [template["id"]]})
    assert res.status_code == 409  # the fixture's own batch has written listings
    batch = await _new_batch(mw)
    await _designs(mw, batch, ["BR1.png"])
    first = await _run(mw, batch, [template["id"]])
    await mw["a"].post(f"/api/mockup-runs/{first['id']}/listings", json={})
    second = await _run(mw, batch, [template["id"]])
    async with mw["sm"]() as s:
        assert (await s.execute(select(Asset).where(Asset.batch_id == batch))).scalars().all() == []  # the old photos went
        outputs = (await s.execute(select(MockupOutput).where(MockupOutput.batch_id == batch))).scalars().all()
        assert [o.run_id for o in outputs] == [uuid.UUID(second["id"])]


async def test_upload_retention_removes_designs_and_unused_mockups_on_time(mw) -> None:
    from datetime import datetime, timedelta, timezone

    from app.pipeline import upload_retention

    template = await _template(mw, "White tee")
    batch = await _new_batch(mw)
    await _designs(mw, batch, ["BR7001.png"])
    run = await _run(mw, batch, [template["id"]])
    async with mw["sm"]() as s:
        [design] = (await s.execute(select(MockupDesign).where(MockupDesign.batch_id == batch))).scalars().all()
        [output] = (await s.execute(select(MockupOutput).where(MockupOutput.run_id == uuid.UUID(run["id"])))).scalars().all()
        keys = (design.design_key, output.key)
        await upload_retention.run(s, mw["storage"], now=datetime.now(timezone.utc))
        assert all(mw["storage"].exists(k) for k in keys)  # not yet past the period
        await upload_retention.run(s, mw["storage"], now=datetime.now(timezone.utc) + timedelta(days=31))
        await s.refresh(design)
        await s.refresh(output)
        assert design.design_key is None and output.key is None
    assert not any(mw["storage"].exists(k) for k in keys)


# --- performance ------------------------------------------------------------------------------


def test_fifty_designs_on_three_templates(capsys) -> None:
    """Times the engine alone at the pipeline's full size (2000 px)."""
    blanks = [samples.blank(c, size=(1700, 2000)) for c in ((238, 238, 236), (26, 26, 28), (150, 152, 156))]
    started = time.perf_counter()
    templates = [mockups.prepare_blank(b) for b in blanks]
    maps = [mockups.build_maps(t.image, t.mask) for t in templates]
    prepared = time.perf_counter()
    designs = [samples.design("white" if i % 3 == 1 else "transparent", size=(900 + i * 7, 700 + i * 5)) for i in range(50)]
    count = 0
    for data in designs:
        design = mockups.load_design(data)
        for template, m in zip(templates, maps):
            mockups.render(m, design, mockups.Settings(template.print_area))
            count += 1
    finished = time.perf_counter()
    per = (finished - prepared) / count
    with capsys.disabled():
        print(f"\nengine: {count} mockups at {maps[0].width}x{maps[0].height}; templates {prepared - started:.2f}s; "
              f"mockups {finished - prepared:.1f}s; {per * 1000:.0f} ms per mockup")
    assert count == 150 and per < 2.0


async def test_ten_designs_in_ten_colours_through_the_worker(mw, capsys) -> None:
    """The whole run as the worker does it (storage, database, progress): 100 mockups."""
    set_id = (await mw["a"].post("/api/mockup-sets", json={"name": "Ten colours"})).json()["id"]
    colours = [(238, 238, 236), (26, 26, 28), (150, 152, 156), (30, 40, 80), (200, 60, 60),
               (60, 120, 70), (240, 210, 80), (120, 80, 160), (250, 190, 200), (90, 60, 40)]
    ids = []
    for i, colour in enumerate(colours):
        res = await mw["a"].post("/api/mockup-templates", data={"name": f"Colour {i + 1}", "rights_confirmed": "true", "set_id": set_id},
                                 files={"file": (f"c{i}.png", samples.blank(colour, size=(1700, 2000)), "image/png")})
        ids.append(res.json()["id"])
    async with mw["sm"]() as s:
        for tid in ids:
            assert (await work.prepare(s, mw["storage"], uuid.UUID(tid))).status == "ready"
    batch = await _new_batch(mw)
    await _designs(mw, batch, [f"BR{6000 + i}.png" for i in range(10)])
    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": ids})
    started = time.perf_counter()
    async with mw["sm"]() as s:
        done = await work.run(s, mw["storage"], uuid.UUID(res.json()["id"]))
    elapsed = time.perf_counter() - started
    assert (done.status, done.done, done.total) == ("succeeded", 100, 100)
    run = (await mw["a"].get(f"/api/mockup-runs/{done.id}")).json()
    assert [d["done"] for d in run["designs"]] == [10] * 10
    with capsys.disabled():
        print(f"\nworker: 10 designs x 10 colours = 100 mockups at 1700x2000 in {elapsed:.1f}s ({elapsed * 10:.0f} ms per mockup)")
    assert elapsed < 120
