"""Downloading mockups (feature/mockups-visible, A2; pipeline/mockup_download.py, api/mockup_downloads.py).

One image, a selection or a ZIP; JPG, PNG or WebP; original, 3000, 2000 or 1080 px; quality 92 or 85; three
ways to name files; folders or flat; the seller's choice remembered. Always the pixels a draft sends to Etsy,
with the sRGB profile, 300 DPI and no other metadata. Another account's mockups are 404.
"""

from __future__ import annotations

import io
import uuid
import zipfile

import numpy as np
from PIL import Image
from sqlalchemy import select

from app.db.models import Asset, MockupOutput, MockupZip
from app.pipeline import mockup_download as dl
from app.pipeline.images import cover_image
from app.core.config import get_settings
from app.workers import mockups as work
from tests import mockup_samples as samples
from tests.test_mockups import _designs, _new_batch, _new_template, _photo, _run, mw, world  # noqa: F401  (fixtures)


async def _two_designs(mw) -> tuple[uuid.UUID, dict]:  # noqa: F811
    tee = await _new_template(mw, "Comfort Colors")
    await _photo(mw, tee["id"], "White", (238, 238, 236))
    await _photo(mw, tee["id"], "Navy blue", (30, 40, 80))
    batch = await _new_batch(mw)
    await _designs(mw, batch, ["BR100.png", "Sun & Moon art.png"])
    return batch, await _run(mw, batch, [tee["id"]])


def _pixels(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))


def _metadata_ok(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    assert image.info.get("icc_profile") == dl.srgb_icc()  # sRGB, embedded
    exif = image.getexif()
    if image.format == "WEBP":  # resolution only
        assert set(exif) <= {0x011A, 0x011B, 0x0128} and float(exif[0x011A]) == 300
    else:
        assert not exif and "gps" not in str(image.info).lower()
        assert tuple(round(float(v)) for v in image.info["dpi"]) == (300, 300)
    return image


async def test_the_options_are_remembered_and_unknown_values_are_the_defaults(mw) -> None:  # noqa: F811
    a = mw["a"]
    assert (await a.get("/api/mockup-download-options")).json() == {
        "format": "jpg", "size": "original", "quality": 92, "naming": "full", "layout": "folders"}
    saved = {"format": "webp", "size": "1080", "quality": 85, "naming": "sku", "layout": "flat"}
    assert (await a.put("/api/mockup-download-options", json=saved)).json() == saved
    assert (await a.get("/api/mockup-download-options")).json() == saved
    assert (await a.put("/api/mockup-download-options", json={**saved, "format": "gif"})).status_code == 422
    assert dl.Options.of({"format": "gif", "size": "999", "quality": 50}) == dl.Options()


async def test_one_mockup_in_each_format_size_and_quality(mw) -> None:  # noqa: F811
    _, run = await _two_designs(mw)
    output = run["designs"][0]["outputs"][0]
    url = f"/api/mockup-runs/{run['id']}/outputs/{output['id']}/download"
    a = mw["a"]
    stored = await a.get(f"/api/mockup-runs/{run['id']}/outputs/{output['id']}/image")
    side = max(Image.open(io.BytesIO(stored.content)).size)
    sizes: dict[str, int] = {}
    for fmt, mime, pil in (("jpg", "image/jpeg", "JPEG"), ("png", "image/png", "PNG"), ("webp", "image/webp", "WEBP")):
        for size in ("original", "3000", "2000", "1080"):
            res = await a.get(url, params={"format": fmt, "size": size})
            assert res.status_code == 200 and res.headers["content-type"] == mime, (fmt, size, res.text[:200])
            assert res.headers["content-disposition"].startswith("attachment;") and f'.{fmt}"' in res.headers["content-disposition"]
            image = _metadata_ok(res.content)
            assert image.format == pil
            # Smaller only, never enlarged: a mockup is made at up to 2,000 px.
            want = side if size in ("original", "3000") else min(side, int(size))
            assert max(image.size) == want, (fmt, size, image.size)
        sizes[fmt] = len((await a.get(url, params={"format": fmt, "quality": "85"})).content)
    high = len((await a.get(url, params={"format": "jpg", "size": "2000", "quality": "92"})).content)
    low = len((await a.get(url, params={"format": "jpg", "size": "2000", "quality": "85"})).content)
    assert low < high


def test_file_names_safe_unique_and_in_three_forms() -> None:
    items = [dl.Item(sku="BR100", design_file="BR100.png", template="Comfort Colors", colour="Navy blue", n=1),
             dl.Item(sku="BR100", design_file="BR100.png", template="Comfort Colors", colour="Comfort Colors", n=2),
             dl.Item(sku=None, design_file="folder/Sun & Moon art (final).png", template="Tee/Front", colour="Ash: grey", n=1),
             dl.Item(sku="BR100", design_file="BR100.png", template="Comfort Colors", colour="Navy blue", n=1)]
    full = dl.names(items, dl.Options())
    assert full == ["BR100_Comfort-Colors_Navy-blue_01.jpg", "BR100_Comfort-Colors_02.jpg",
                    "Sun-Moon-art-final_Tee-Front_Ash-grey_01.jpg", "BR100_Comfort-Colors_Navy-blue_01-2.jpg"]
    assert dl.names(items, dl.Options(naming="sku", format="png")) == [
        "BR100_01.png", "BR100_02.png", "Sun-Moon-art-final_01.png", "BR100_01-2.png"]
    assert dl.names(items[2:3], dl.Options(naming="file")) == ["Sun-Moon-art-final_01.jpg"]
    for name in full:
        assert all(c.isalnum() or c in "._-" for c in name), name


def _zip(mw, data: bytes) -> dict[str, bytes]:  # noqa: F811
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


async def _make_zip(mw, run: dict, body: dict) -> tuple[dict, dict[str, bytes]]:  # noqa: F811
    res = await mw["a"].post(f"/api/mockup-runs/{run['id']}/zip", json=body)
    assert res.status_code == 202, res.text
    async with mw["sm"]() as s:
        done = await work.build_zip(s, mw["storage"], uuid.UUID(res.json()["id"]))
        assert done.status == "ready", done.error
    out = (await mw["a"].get(f"/api/mockup-zips/{res.json()['id']}")).json()
    download = await mw["a"].get(f"/api/mockup-zips/{res.json()['id']}/download")
    assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
    return out, _zip(mw, download.content)


async def test_zips_of_a_run_a_design_and_a_selection_with_folders_or_flat(mw) -> None:  # noqa: F811
    _, run = await _two_designs(mw)
    first, second = run["designs"]
    out, files = await _make_zip(mw, run, {"layout": "folders"})
    assert (out["total"], out["done"], out["files"]) == (4, 4, 4)
    assert sorted(files) == sorted([
        "BR100/BR100_Comfort-Colors_White_01.jpg", "BR100/BR100_Comfort-Colors_Navy-blue_02.jpg",
        "Sun-Moon-art/Sun-Moon-art_Comfort-Colors_White_01.jpg", "Sun-Moon-art/Sun-Moon-art_Comfort-Colors_Navy-blue_02.jpg"])
    # One design's set, flat, PNG at 1080 px: the choice is remembered for the next download.
    out, files = await _make_zip(mw, run, {"design_ids": [first["design_id"]], "layout": "flat",
                                          "options": {"format": "png", "size": "1080", "naming": "sku"}})
    assert sorted(files) == ["BR100_01.png", "BR100_02.png"]
    assert all(max(Image.open(io.BytesIO(b)).size) <= 1080 and Image.open(io.BytesIO(b)).format == "PNG" for b in files.values())
    assert (await mw["a"].get("/api/mockup-download-options")).json()["format"] == "png"
    # Selected mockups only, across designs; their numbers stay the ones they have in their set.
    picked = [first["outputs"][1]["id"], second["outputs"][0]["id"]]
    out, files = await _make_zip(mw, run, {"output_ids": picked, "layout": "folders", "options": {"format": "jpg"}})
    assert sorted(files) == ["BR100/BR100_Comfort-Colors_Navy-blue_02.jpg", "Sun-Moon-art/Sun-Moon-art_Comfort-Colors_White_01.jpg"]
    # The Downloads tab lists them, newest first, with what they hold; kept 48 hours.
    listed = (await mw["a"].get("/api/mockup-zips")).json()
    assert [z["label"].split(" · ")[-1] for z in listed] == ["2 mockups", "1 design", "2 designs"]
    assert MockupZip.RETENTION_HOURS == 48


async def test_the_download_is_the_pixels_etsy_gets(mw) -> None:  # noqa: F811
    batch, run = await _two_designs(mw)
    design = run["designs"][0]
    # Used for listings, the navy one the cover with the seller's crop.
    navy = next(o for o in design["outputs"] if o["photo_name"] == "Navy blue")
    white = next(o for o in design["outputs"] if o["photo_name"] == "White")
    res = await mw["a"].post(f"/api/mockup-runs/{run['id']}/listings",
                             json={"design_ids": [design["design_id"]], "covers": {design["design_id"]: navy["photo_id"]}})
    assert res.status_code == 200, res.text
    async with mw["sm"]() as s:
        outputs = {o.id: o for o in (await s.execute(select(MockupOutput))).scalars()}
        cover = (await s.execute(select(Asset).where(Asset.processed_key == outputs[uuid.UUID(navy["id"])].key))).scalar_one()
        cover.cover_crop = {"x": 100, "y": 150, "size": 400, "width": 600, "height": 700}
        await s.commit()
        assert cover.rank == 1
    settings = get_settings()
    storage = mw["storage"]
    url = f"/api/mockup-runs/{run['id']}/outputs/{{}}/download"
    # Not the cover: the stored mockup, as the draft uploads it (workers/publish.py: storage.get(processed_key)).
    got = (await mw["a"].get(url.format(white["id"]), params={"format": "jpg", "size": "original", "quality": "92"})).content
    sent = storage.get(outputs[uuid.UUID(white["id"])].key)
    assert np.array_equal(_pixels(got), _pixels(sent)) and _pixels(got).shape == _pixels(sent).shape
    assert got[got.index(b"\xff\xda"):] == sent[sent.index(b"\xff\xda"):]  # the image data itself, untouched
    # The cover: the draft's cover image, the seller's crop.
    got = (await mw["a"].get(url.format(navy["id"]), params={"format": "jpg", "size": "original", "quality": "92"})).content
    etsy = cover_image(storage.get(outputs[uuid.UUID(navy["id"])].key), {"x": 100, "y": 150, "size": 400, "width": 600, "height": 700},
                       padding_pct=settings.thumbnail_padding_pct, size=settings.thumbnail_size, mode=settings.thumbnail_mode).data
    assert np.array_equal(_pixels(got), _pixels(etsy))
    # In the ZIP the listing's order: the cover first.
    _, files = await _make_zip(mw, run, {"design_ids": [design["design_id"]], "layout": "flat", "options": {"naming": "full", "format": "jpg"}})
    assert sorted(files) == ["BR100_Comfort-Colors_Navy-blue_01.jpg", "BR100_Comfort-Colors_White_02.jpg"]
    assert np.array_equal(_pixels(files["BR100_Comfort-Colors_Navy-blue_01.jpg"]), _pixels(etsy))


def test_metadata_is_written_without_touching_the_pixels() -> None:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "JPEG", quality=92,
                                                  exif=Image.Exif().tobytes(), comment=b"camera")
    original = buf.getvalue()
    out = dl.jpeg_with_metadata(original)
    image = _metadata_ok(out)
    assert image.size == (64, 48) and np.array_equal(_pixels(out), _pixels(original))
    assert b"camera" not in out and b"Exif" not in out


async def test_another_accounts_mockups_are_not_found(mw) -> None:  # noqa: F811
    _, run = await _two_designs(mw)
    output = run["designs"][0]["outputs"][0]["id"]
    b = mw["b"]
    assert (await b.get(f"/api/mockup-runs/{run['id']}/outputs/{output}/download")).status_code == 404
    assert (await b.post(f"/api/mockup-runs/{run['id']}/zip", json={"layout": "flat"})).status_code == 404
    assert (await b.get("/api/mockup-zips")).json() == []
    # A's own run with a mockup id that is not in it: not found either.
    assert (await mw["a"].post(f"/api/mockup-runs/{run['id']}/zip", json={"output_ids": [str(uuid.uuid4())]})).status_code == 404
    assert (await mw["a"].get(f"/api/mockup-runs/{run['id']}/outputs/{uuid.uuid4()}/download")).status_code == 404
