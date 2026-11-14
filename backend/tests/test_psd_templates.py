"""Photoshop mockups become templates (feature/psd-templates, steps 1-5).

Synthetic PSDs only (tests/psd_builder.py). The golden comparisons render each file's own smart-object content
through our pipeline and compare it with psd-tools' compositor (independent of our renderer) at the stored size.
A bent mesh's pixels are drawn by the builder with our own patch, so that case shows consistency, not Photoshop;
the real samples' numbers are in docs/psd-findings.md.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import numpy as np
import pytest
from fakeredis import FakeAsyncRedis
from PIL import Image, ImageDraw
from sqlalchemy import select

from app.api import psd_templates
from app.db.models import MockupPhoto, MockupTemplate, PsdUpload
from app.pipeline import psd_convert, psd_template
from app.pipeline.psd_sandbox import ParseFailed, run_child
from app.workers import psd as psd_work
from tests.psd_builder import Pixel, Smart, build
from tests.test_mockups import _designs, _new_batch, _run, mw  # noqa: F401  (fixture)
from tests.test_multi_shop import world  # noqa: F401  (fixture)

SIZE = (400, 400)


def _art(w: int = 300, h: int = 200) -> Image.Image:
    art = Image.new("RGBA", (w, h), (200, 30, 30, 255))
    d = ImageDraw.Draw(art)
    d.ellipse((w // 6, h // 4, w * 5 // 6, h * 3 // 4), fill=(20, 20, 200, 255))
    d.rectangle((0, 0, w - 1, h - 1), outline=(10, 120, 10, 255), width=4)
    return art


def _rect(x: float, y: float, w: float, h: float) -> list[tuple[float, float]]:
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def _cases() -> dict[str, list]:
    art = _art()
    shine = Image.new("RGBA", SIZE, (255, 255, 255, 0))
    ImageDraw.Draw(shine).rectangle((0, 0, 399, 150), fill=(255, 255, 255, 110))
    mask = Image.new("L", SIZE, 0)
    mask.paste(255, (120, 120, 270, 250))
    bend = [(100 * i, 200 * j / 3 + (25 if i in (1, 2) and j in (1, 2) else 0)) for j in range(4) for i in range(4)]
    return {
        "plain": [Smart(art, _rect(100, 100, 150, 100))],
        "rotated": [Smart(art, [(150, 80), (290, 130), (240, 270), (100, 220)])],
        "perspective": [Smart(art, [(100, 100), (300, 110), (290, 250), (110, 240)])],
        "mesh": [Smart(art, _rect(80, 80, 240, 180), mesh=bend)],
        "mask": [Smart(art, _rect(100, 100, 200, 140), mask=mask)],
        "blends": [Smart(art, _rect(100, 100, 200, 140), blend="MULTIPLY"),
                   Pixel(shine, name="Shine", blend="SCREEN", opacity=200)],
    }


def _convert(tmp_path: Path, name: str, layers: list, choice: int | None = None) -> tuple[str, dict]:
    psd = str(tmp_path / f"{name}.psd")
    build(psd, SIZE, layers)
    out = tmp_path / f"{name}-out"
    out.mkdir()
    spec = psd_convert.convert(psd, str(out), choice)
    return psd, spec


@pytest.mark.parametrize("name", ["plain", "rotated", "perspective", "mesh", "mask", "blends"])
def test_golden_the_files_own_content_renders_as_its_composite(tmp_path: Path, name: str) -> None:
    psd, _spec = _convert(tmp_path, name, _cases()[name])
    spec, base, mask, content, _data = psd_template.load_dir(str(tmp_path / f"{name}-out"))
    ours = psd_template.render(spec, base, content, mask).astype(np.int16)
    from psd_tools import PSDImage

    ref = np.asarray(PSDImage.open(psd).composite(force=True).convert("RGB")).astype(np.int16)
    diff = np.abs(ours - ref)
    # Pillow's perspective map and our sampler differ by a pixel along hard edges: the mean says "the same".
    assert diff.mean() < 2.5, (name, diff.mean())
    assert np.percentile(diff, 99) < 60, (name, np.percentile(diff, 99))


def test_several_smart_objects_default_to_the_design_one_and_can_be_chosen(tmp_path: Path) -> None:
    art = _art()
    layers = [Smart(art, _rect(30, 30, 60, 40), name="Label"), Smart(art, _rect(150, 150, 120, 80), name="Your design"),
              Smart(art, _rect(20, 250, 360, 140), name="Big background print")]
    _psd, spec = _convert(tmp_path, "three", layers)
    names = {o["index"]: o["name"] for o in spec["options"]}
    assert names[spec["choice"]] == "Your design"  # the name wins over the largest
    _psd, again = _convert(tmp_path, "three-b", layers, choice=2)
    assert again["choice"] == 2 and again["placement"]["quad"][0] == [20.0, 250.0]
    assert psd_convert.choose([{"index": 0, "name": "A", "area": 5}, {"index": 1, "name": "B", "area": 9}]) == 1


def test_unsupported_features_are_said_in_plain_words(tmp_path: Path) -> None:
    _psd, spec = _convert(tmp_path, "filter", [Smart(_art(), _rect(100, 100, 150, 100), filters=["Displace"])])
    assert "This template uses Photoshop's Displace filter. We place the design without it, so it can look slightly flatter." in spec["notes"]
    _psd, spec = _convert(tmp_path, "arc", [Smart(_art(), _rect(100, 100, 150, 100), style="warpArc")])
    assert any("arc warp" in n for n in spec["notes"])
    _psd, spec = _convert(tmp_path, "clean", [Smart(_art(), _rect(100, 100, 150, 100))])
    assert spec["notes"] == []


def test_refusals_say_what_to_do(tmp_path: Path, monkeypatch) -> None:
    psd = str(tmp_path / "none.psd")
    build(psd, SIZE, [Pixel(_art(), name="Just a photo")])
    with pytest.raises(psd_convert.Refused, match="no smart object"):
        psd_convert.convert(psd, str(tmp_path))
    psd = str(tmp_path / "linked.psd")
    build(psd, SIZE, [Smart(_art(), _rect(100, 100, 150, 100), linked=True)])
    with pytest.raises(psd_convert.Refused, match="Embed Linked"):
        psd_convert.convert(psd, str(tmp_path))
    plain = str(tmp_path / "plain.psd")
    build(plain, SIZE, [Smart(_art(), _rect(100, 100, 150, 100))])
    monkeypatch.setattr(psd_convert, "MAX_SIDE", 300)
    with pytest.raises(psd_convert.Refused, match="30,000 pixels"):
        psd_convert.convert(plain, str(tmp_path))
    monkeypatch.setattr(psd_convert, "MAX_SIDE", 30_000)
    monkeypatch.setattr(psd_convert, "MAX_LAYERS", 1)
    with pytest.raises(psd_convert.Refused, match="2,000 layers"):
        psd_convert.convert(plain, str(tmp_path))


def test_the_child_passes_refusals_through_and_a_damaged_file_fails_alone(tmp_path: Path) -> None:
    psd = str(tmp_path / "none.psd")
    build(psd, SIZE, [Pixel(_art(), name="Just a photo")])
    with pytest.raises(ParseFailed, match="no smart object"):
        run_child("app.pipeline.psd_convert", [psd, str(tmp_path)], memory_mb=1200, seconds=60)
    bad = tmp_path / "bad.psd"
    bad.write_bytes(b"8BPS" + b"\x00" * 200)
    with pytest.raises(ParseFailed, match="damaged"):
        run_child("app.pipeline.psd_convert", [str(bad), str(tmp_path)], memory_mb=1200, seconds=60)


def test_a_6000_px_template_converts_and_renders_in_little_memory(tmp_path: Path) -> None:
    """Templates are stored at 2000 px, so a design on a 6000 x 6000 PSD renders at that size. Each child reports
    its own peak (VmHWM): the conversion under its 1200 MB cap, the render well under 600 MB."""
    import json
    import subprocess
    import sys

    psd = str(tmp_path / "big.psd")
    build(psd, (6000, 6000), [Smart(_art(1200, 1200), _rect(1500, 1500, 3000, 3000))])
    out = tmp_path / "big-out"
    out.mkdir()
    said = json.loads(run_child("app.pipeline.psd_convert", [psd, str(out)], memory_mb=1200, seconds=170))
    assert 0 < said["peak_mb"] < 1200, said
    code = ("from app.pipeline import psd_template; from app.pipeline.psd_sandbox import own_peak_mb;"
            "from app.workers.psd import sample_design;"
            f"s, b, m, c, d = psd_template.load_dir({str(out)!r}); psd_template.render(s, b, sample_design(), m);"
            "print(own_peak_mb())")
    peak = int(subprocess.run([sys.executable, "-c", code], capture_output=True, check=True, cwd="/app").stdout)
    assert peak < 600, peak


# --- the upload, the worker, the template ----------------------------------------------------------------------


def _psd_bytes(tmp_path: Path, layers: list, name: str = "mockup") -> bytes:
    path = tmp_path / f"{name}.psd"
    build(str(path), SIZE, layers)
    return path.read_bytes()


async def _upload(mw, data: bytes, name: str = "My mockup.psd", *, chunk: int = 64 * 1024) -> dict:
    a = mw["a"]
    res = await a.post("/api/psd-uploads", json={"filename": name, "size": len(data), "rights_confirmed": True})
    assert res.status_code == 201, res.text
    up = res.json()
    for offset in range(0, len(data), chunk):
        res = await a.put(f"/api/psd-uploads/{up['id']}/chunk?offset={offset}", content=data[offset:offset + chunk])
        assert res.status_code == 200, res.text
    res = await a.post(f"/api/psd-uploads/{up['id']}/complete")
    assert res.status_code == 200 and res.json()["status"] == "queued", res.text
    return res.json()


def _ctx(mw) -> dict:
    return {"redis": FakeAsyncRedis(), "sessionmaker": mw["sm"], "storage": mw["storage"]}


async def test_a_psd_uploads_in_pieces_resumes_and_becomes_a_template(mw, tmp_path, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(psd_templates, "CHUNK_BYTES", 64 * 1024)
    data = _psd_bytes(tmp_path, [Smart(_art(), _rect(100, 100, 150, 100))])
    a = mw["a"]
    up = (await a.post("/api/psd-uploads", json={"filename": "Tee.psd", "size": len(data), "rights_confirmed": True})).json()
    chunk = 64 * 1024
    first = await a.put(f"/api/psd-uploads/{up['id']}/chunk?offset=0", content=data[:chunk])
    assert first.json()["received"] == chunk
    # The network drops: the same piece is sent again (no change), a later one leaves a gap (refused, says where).
    again = await a.put(f"/api/psd-uploads/{up['id']}/chunk?offset=0", content=data[:chunk])
    assert again.status_code == 200 and again.json()["received"] == chunk
    gap = await a.put(f"/api/psd-uploads/{up['id']}/chunk?offset={3 * chunk}", content=data[3 * chunk:4 * chunk])
    assert gap.status_code == 409 and gap.json()["detail"]["received"] == chunk
    early = await a.post(f"/api/psd-uploads/{up['id']}/complete")
    assert early.status_code == 409
    resumed = (await a.get(f"/api/psd-uploads/{up['id']}")).json()["received"]
    for offset in range(resumed, len(data), chunk):
        res = await a.put(f"/api/psd-uploads/{up['id']}/chunk?offset={offset}", content=data[offset:offset + chunk])
        assert res.status_code == 200
    big = await a.put(f"/api/psd-uploads/{up['id']}/chunk?offset=0", content=b"0" * (chunk + 1))
    assert big.status_code in (409, 413)
    done = (await a.post(f"/api/psd-uploads/{up['id']}/complete")).json()
    assert done["status"] == "queued"
    assert any(c[0] == "convert_psd" for c in mw["queue"].calls)

    assert await psd_work.convert_psd(_ctx(mw), up["id"]) == "ready"
    state = (await a.get(f"/api/psd-uploads/{up['id']}")).json()
    assert state["status"] == "ready" and state["preview_url"] and state["notes"] == [] and not state["can_choose"]
    preview = await a.get(state["preview_url"])
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/jpeg"
    async with mw["sm"]() as s:
        upload = await s.get(PsdUpload, uuid.UUID(up["id"]))
        assert upload.file_key is None  # one smart object: the PSD is deleted at once
        photo = await s.get(MockupPhoto, upload.photo_id)
        assert photo.psd and photo.confirmed_at is None and photo.status == "ready"
        assert (await s.get(MockupTemplate, upload.template_id)).name == "Tee"
    assert not list(Path(mw["storage"]._path(f"{upload.tenant_id}/psd/x")).parent.glob("*"))  # nothing left incoming

    used = (await a.post(f"/api/psd-uploads/{up['id']}/use")).json()
    assert used["status"] == "used"
    photo_out = (await a.get(f"/api/mockup-photos/{state['photo_id']}")).json()
    assert photo_out["confirmed"] and photo_out["psd"]["upload_id"] == up["id"]
    # Its placement is the smart object's: the area editor refuses it.
    area = {"print_area": photo_out["areas"][0]["print_area"], "detected_area": photo_out["areas"][0]["print_area"],
            "source": "seller"}
    locked = await a.put(f"/api/mockup-photos/{state['photo_id']}/areas", json={"areas": [area], "confirm": True})
    assert locked.status_code == 409 and "Photoshop" in locked.json()["detail"]

    # A mockup run places designs on it like any template (and nothing else changes).
    batch = await _new_batch(mw)
    await _designs(mw, batch, ["BR500.png"])
    run = await _run(mw, batch, [state["template_id"]])
    assert run["total"] == 1 and len(run["designs"][0]["outputs"]) == 1


async def test_several_smart_objects_can_be_chosen_while_the_psd_is_kept(mw, tmp_path) -> None:  # noqa: F811
    art = _art()
    data = _psd_bytes(tmp_path, [Smart(art, _rect(30, 30, 60, 40), name="Logo"),
                                 Smart(art, _rect(150, 150, 120, 80), name="Your design")])
    up = await _upload(mw, data)
    await psd_work.convert_psd(_ctx(mw), up["id"])
    state = (await mw["a"].get(f"/api/psd-uploads/{up['id']}")).json()
    assert state["can_choose"] and [o["name"] for o in state["options"]] == ["Logo", "Your design"]
    assert state["choice"] == 1
    chosen = (await mw["a"].post(f"/api/psd-uploads/{up['id']}/choice", json={"index": 0})).json()
    assert chosen["status"] == "queued"
    await psd_work.convert_psd(_ctx(mw), up["id"])
    state = (await mw["a"].get(f"/api/psd-uploads/{up['id']}")).json()
    assert state["choice"] == 0 and state["status"] == "ready"
    async with mw["sm"]() as s:
        assert len(list((await s.execute(select(MockupPhoto).where(MockupPhoto.template_id == uuid.UUID(state["template_id"])))).scalars())) == 1
    await mw["a"].post(f"/api/psd-uploads/{up['id']}/use")
    async with mw["sm"]() as s:
        assert (await s.get(PsdUpload, uuid.UUID(up["id"]))).file_key is None  # used: the PSD goes


async def test_limits_rights_and_failures_are_plain(mw, tmp_path) -> None:  # noqa: F811
    a = mw["a"]
    res = await a.post("/api/psd-uploads", json={"filename": "x.psd", "size": 10, "rights_confirmed": False})
    assert res.status_code == 422 and "right to use this mockup" in res.json()["detail"]
    res = await a.post("/api/psd-uploads", json={"filename": "x.png", "size": 10, "rights_confirmed": True})
    assert res.status_code == 422 and ".psd" in res.json()["detail"]
    res = await a.post("/api/psd-uploads", json={"filename": "x.psd", "size": 500 * 1024 * 1024 + 1, "rights_confirmed": True})
    assert res.status_code == 413 and "500 MB" in res.json()["detail"]
    # Not a Photoshop file at all: said at once, nothing queued.
    up = (await a.post("/api/psd-uploads", json={"filename": "x.psd", "size": 8, "rights_confirmed": True})).json()
    await a.put(f"/api/psd-uploads/{up['id']}/chunk?offset=0", content=b"\x89PNG1234")
    done = (await a.post(f"/api/psd-uploads/{up['id']}/complete")).json()
    assert done["status"] == "failed" and "not a Photoshop file" in done["error"]
    # A PSD with no smart object fails in the worker with what to do; the file is kept a day for a retry.
    up = await _upload(mw, _psd_bytes(tmp_path, [Pixel(_art(), name="Photo")], "flat"))
    assert await psd_work.convert_psd(_ctx(mw), up["id"]) == "failed"
    state = (await a.get(f"/api/psd-uploads/{up['id']}")).json()
    assert state["status"] == "failed" and "Convert to Smart Object" in state["error"]
    async with mw["sm"]() as s:
        assert (await s.get(PsdUpload, uuid.UUID(up["id"]))).file_key is not None
    # Cancelled: the file goes.
    assert (await a.delete(f"/api/psd-uploads/{up['id']}")).status_code == 204


async def test_one_conversion_at_a_time(mw, tmp_path) -> None:  # noqa: F811
    up = await _upload(mw, _psd_bytes(tmp_path, [Smart(_art(), _rect(100, 100, 150, 100))]))
    ctx = _ctx(mw)
    queued: list = []

    async def enqueue(function, *args, **options):  # noqa: ANN001, ANN002, ANN003
        queued.append((function, args, options.get("_defer_by")))

    ctx["enqueue"] = enqueue
    await ctx["redis"].set(psd_work.LOCK, "another")
    # Another is converting: this one waits by queuing itself again (a wait is never a failed try).
    assert await psd_work.convert_psd(ctx, up["id"]) == "waiting"
    assert queued == [("convert_psd", (up["id"],), 5.0)]
    await ctx["redis"].delete(psd_work.LOCK)
    assert await psd_work.convert_psd(ctx, up["id"]) == "ready"


async def test_old_psd_files_are_deleted_after_a_day(mw, tmp_path) -> None:  # noqa: F811
    from datetime import datetime, timedelta, timezone

    up = await _upload(mw, _psd_bytes(tmp_path, [Pixel(_art(), name="Photo")], "flat2"))
    async with mw["sm"]() as s:
        row = await s.get(PsdUpload, uuid.UUID(up["id"]))
        row.status = "failed"
        key = row.file_key
        await s.commit()
        assert mw["storage"]._path(key).exists()
        assert await psd_work.expire_psd_files(s, mw["storage"], datetime.now(timezone.utc) + timedelta(hours=25)) == 1
    assert not mw["storage"]._path(key).exists()
