"""The mockup generator (pipeline/mockups.py, workers/mockups.py, api/mockups.py).

* Geometry: the design lands inside the print area, its aspect kept, top-aligned.
* Determinism: the same template, design and settings give the same bytes.
* Transparency: a transparent design leaves the garment untouched around it.
* Dark garments: white ink stays light (an underbase), the garment around stays dark.
* No Etsy call and no model call, per mockup or anywhere in the feature.
* The allowance ("Listings generated") does not change.
* Another account's template, batch or run is 404; the rights confirmation is required.
* Each design's mockups become its listing group, in the seller's template order.
* Performance: 50 designs x 3 templates, timed.
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
from app.db.models import AiCall, Asset, AuditLog, MockupDesign, MockupTemplate, Tenant, UploadBatch, UploadBatchStatus
from app.etsy.api import EtsyApiClient
from app.pipeline import mockups
from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.storage import LocalStorage
from app.workers import mockups as worker
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
    assert prepared.mask_source == "background"
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
    prepared = mockups.prepare_blank(samples.blank((246, 246, 246), background=(250, 250, 250)))
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


async def _template(client, name: str, colour=(238, 238, 236)) -> dict:
    res = await client.post(
        "/api/mockup-templates", data={"name": name, "rights_confirmed": "true"},
        files={"file": (f"{name}.png", samples.blank(colour, size=(600, 700)), "image/png")},
    )
    assert res.status_code == 201, res.text
    return res.json()


async def test_a_blank_needs_the_rights_confirmation(mw) -> None:
    res = await mw["a"].post("/api/mockup-templates", data={"name": "Tee"},
                             files={"file": ("tee.png", samples.blank(size=(600, 700)), "image/png")})
    assert res.status_code == 422
    assert "This photo is my own, or I have the right to use it to show my products." in res.json()["detail"]
    created = await _template(mw["a"], "White tee")
    async with mw["sm"]() as s:
        template = await s.get(MockupTemplate, uuid.UUID(created["id"]))
        assert template.rights_confirmed_at is not None
        assert (await s.execute(select(AuditLog).where(AuditLog.action == "mockup_template.created"))).scalars().first()


async def test_another_accounts_template_batch_and_run_are_not_found(mw) -> None:
    template = await _template(mw["a"], "White tee")
    tid, batch = template["id"], mw["batch"]
    b = mw["b"]
    assert (await b.get(f"/api/mockup-templates/{tid}/image")).status_code == 404
    assert (await b.patch(f"/api/mockup-templates/{tid}", json={"size": 0.5})).status_code == 404
    assert (await b.post(f"/api/mockup-templates/{tid}/preview", json={})).status_code == 404
    assert (await b.delete(f"/api/mockup-templates/{tid}")).status_code == 404
    assert [t["id"] for t in (await b.get("/api/mockup-templates")).json()] == []
    assert (await b.post(f"/api/batches/{batch}/designs", files={"file": ("d.png", samples.design(), "image/png")})).status_code == 404
    # Bob cannot print on Alice's template, even in his own batch.
    async with mw["sm"]() as s:
        bobs = UploadBatch(tenant_id=mw["bob"], status=UploadBatchStatus.uploading, file_count=0)
        s.add(bobs)
        await s.commit()
        bobs_id = bobs.id
    assert (await b.post(f"/api/batches/{bobs_id}/mockups", json={"template_ids": [tid]})).status_code == 404
    run = await _start(mw, [tid])
    assert (await b.get(f"/api/mockup-runs/{run['id']}")).status_code == 404


async def _start(mw, template_ids: list[str], designs: int = 1, batch: uuid.UUID | None = None) -> dict:
    batch = batch or await _new_batch(mw)
    for i in range(designs):
        res = await mw["a"].post(f"/api/batches/{batch}/designs",
                                 files={"file": (f"BR{5200 + i}.png", samples.design(), "image/png")})
        assert res.status_code == 201, res.text
    res = await mw["a"].post(f"/api/batches/{batch}/mockups", json={"template_ids": template_ids})
    assert res.status_code == 202, res.text
    return res.json()


async def _new_batch(mw) -> uuid.UUID:
    res = await mw["a"].post("/api/batches", json={"connection_id": str(mw["a1"])})
    assert res.status_code == 201, res.text
    return uuid.UUID(res.json()["id"])


async def test_a_run_makes_each_designs_group_in_the_template_order_without_etsy_ai_or_allowance(mw, monkeypatch) -> None:
    guard = _NoCalls(monkeypatch)
    white = await _template(mw["a"], "White tee")
    black = await _template(mw["a"], "Black tee", (26, 26, 28))
    # The seller's order: black first, so its mockup is the cover.
    res = await mw["a"].put("/api/mockup-templates/order", json={"ids": [black["id"], white["id"]]})
    assert [t["name"] for t in res.json()] == ["Black tee", "White tee"]
    async with mw["sm"]() as s:
        tenant = await s.get(Tenant, mw["alice"])
        before = (await allowance.status(s, tenant)).used
    batch = await _new_batch(mw)
    run = await _start(mw, [white["id"], black["id"]], designs=2, batch=batch)
    assert (run["total"], run["templates"]) == (4, ["Black tee", "White tee"])
    assert mw["queue"].calls[-1] == ("generate_mockups", (run["id"],))

    async with mw["sm"]() as s:
        done = await worker.run(s, mw["storage"], uuid.UUID(run["id"]))
        assert (done.status, done.done, done.failed) == ("succeeded", 4, 0)
        assets = list((await s.execute(select(Asset).where(Asset.batch_id == batch).order_by(Asset.group_key, Asset.rank))).scalars())
        assert [(a.group_key, a.rank, a.original_filename) for a in assets] == [
            ("BR5200", 1, "BR5200/01-Black tee.jpg"), ("BR5200", 2, "BR5200/02-White tee.jpg"),
            ("BR5201", 1, "BR5201/01-Black tee.jpg"), ("BR5201", 2, "BR5201/02-White tee.jpg"),
        ]
        assert all(a.parsed_sku == a.group_key and a.status.value == "processed" and a.mockup_design_id for a in assets)
        assert (await s.get(UploadBatch, batch)).status.value == "ready"
        assert (await allowance.status(s, await s.get(Tenant, mw["alice"]))).used == before  # not counted
        assert (await s.execute(select(AiCall))).scalars().all() == []
    assert guard.calls == []
    # The groups are ordinary listing groups: the batch page lists them, cover first.
    groups = (await mw["a"].get(f"/api/batches/{batch}/groups")).json()
    assert [g["group_key"] for g in groups] == ["BR5200", "BR5201"]
    status = (await mw["a"].get(f"/api/mockup-runs/{run['id']}")).json()
    assert (status["status"], status["done"], status["total"]) == ("succeeded", 4, 4)


async def test_no_model_call_per_mockup_and_the_preview_makes_none_either(mw, monkeypatch) -> None:
    guard = _NoCalls(monkeypatch)
    template = await _template(mw["a"], "White tee")
    res = await mw["a"].post(f"/api/mockup-templates/{template['id']}/preview",
                             json={"print_area": AREA, "size": 0.8, "fold_strength": 0.2})
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(res.content)).size[0] <= 900
    # The same preview twice: the same bytes.
    again = await mw["a"].post(f"/api/mockup-templates/{template['id']}/preview",
                               json={"print_area": AREA, "size": 0.8, "fold_strength": 0.2})
    assert again.content == res.content
    assert guard.calls == []


async def test_settings_are_saved_checked_and_reset(mw) -> None:
    template = await _template(mw["a"], "White tee")
    tid = template["id"]
    res = await mw["a"].patch(f"/api/mockup-templates/{tid}", json={"print_area": AREA, "size": 0.7, "fold_strength": 0.2})
    assert res.status_code == 200
    body = res.json()
    assert (body["print_area"], body["print_area_source"], body["size"], body["fold_strength"]) == (AREA, "seller", 0.7, 0.2)
    assert body["version"] == template["version"] + 1
    crossed = [[0.1, 0.1], [0.9, 0.9], [0.9, 0.1], [0.1, 0.9]]
    assert (await mw["a"].patch(f"/api/mockup-templates/{tid}", json={"print_area": crossed})).status_code == 422
    assert (await mw["a"].patch(f"/api/mockup-templates/{tid}", json={"size": 3})).status_code == 422
    reset = (await mw["a"].patch(f"/api/mockup-templates/{tid}", json={"reset_print_area": True})).json()
    assert (reset["print_area"], reset["print_area_source"]) == (template["print_area"], "default")


async def test_deleting_a_template_is_recorded_and_keeps_mockups_already_made(mw) -> None:
    template = await _template(mw["a"], "White tee")
    run = await _start(mw, [template["id"]])
    async with mw["sm"]() as s:
        await worker.run(s, mw["storage"], uuid.UUID(run["id"]))
    assert (await mw["a"].delete(f"/api/mockup-templates/{template['id']}")).status_code == 204
    async with mw["sm"]() as s:
        assert (await s.get(MockupTemplate, uuid.UUID(template["id"]))) is None
        assert (await s.execute(select(AuditLog).where(AuditLog.action == "mockup_template.deleted"))).scalars().first()
        [asset] = (await s.execute(select(Asset).where(Asset.batch_id == uuid.UUID(run["batch_id"])))).scalars().all()
        assert asset.mockup_template_id is None and asset.processed_key  # the photo stays
    assert not mw["storage"].exists(f"{mw['alice']}/templates/{template['id']}/blank.jpg")


async def test_mockups_are_refused_once_listings_are_written(mw) -> None:
    template = await _template(mw["a"], "White tee")
    run = await _start(mw, [template["id"]])
    # The fixture's own batch has written listings.
    res = await mw["a"].post(f"/api/batches/{mw['batch']}/mockups", json={"template_ids": [template["id"]]})
    assert res.status_code == 409
    # And a second run while one is waiting is refused.
    res = await mw["a"].post(f"/api/batches/{run['batch_id']}/mockups", json={"template_ids": [template["id"]]})
    assert res.status_code == 409


async def test_a_second_run_replaces_the_first_runs_mockups(mw) -> None:
    template = await _template(mw["a"], "White tee")
    run = await _start(mw, [template["id"]])
    async with mw["sm"]() as s:
        await worker.run(s, mw["storage"], uuid.UUID(run["id"]))
    second = await mw["a"].post(f"/api/batches/{run['batch_id']}/mockups", json={"template_ids": [template["id"]]})
    assert second.status_code == 202, second.text
    async with mw["sm"]() as s:
        await worker.run(s, mw["storage"], uuid.UUID(second.json()["id"]))
        assets = (await s.execute(select(Asset).where(Asset.batch_id == uuid.UUID(run["batch_id"])))).scalars().all()
        assert len(assets) == 1


async def test_upload_retention_removes_a_design_with_its_groups_photos(mw) -> None:
    from datetime import datetime, timedelta, timezone

    from app.pipeline import upload_retention

    template = await _template(mw["a"], "White tee")
    run = await _start(mw, [template["id"]])
    batch = uuid.UUID(run["batch_id"])
    async with mw["sm"]() as s:
        await worker.run(s, mw["storage"], uuid.UUID(run["id"]))
        (await s.get(UploadBatch, batch)).created_at = datetime.now(timezone.utc) - timedelta(days=40)
        await s.commit()
        [design] = (await s.execute(select(MockupDesign).where(MockupDesign.batch_id == batch))).scalars().all()
        key = design.design_key
        assert mw["storage"].exists(key)
        await upload_retention.run(s, mw["storage"], now=datetime.now(timezone.utc))
        await s.refresh(design)
        assert design.files_removed_at is not None and design.design_key is None
    assert not mw["storage"].exists(key)


# --- performance ------------------------------------------------------------------------------


def test_fifty_designs_on_three_templates(capsys) -> None:
    """Times the engine alone at the pipeline's full size (2000 px), as a run does it."""
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
        print(f"\nmockups: {count} at {maps[0].width}x{maps[0].height}; templates {prepared - started:.2f}s; "
              f"mockups {finished - prepared:.1f}s; {per * 1000:.0f} ms per mockup")
    assert count == 150
    assert per < 2.0  # a generous ceiling; the figure is printed above
