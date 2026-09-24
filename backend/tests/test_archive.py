"""ZIP uploads (docs/duzeltmeler-v6.md §F): structure kept, hostile archives refused."""

from __future__ import annotations

import io
import zipfile

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select

from app.core.config import set_settings_override
from app.db.models import Asset
from app.pipeline.archive import read_archive, safe_parts
from app.pipeline.uploads import UnsupportedUpload, UploadTooLarge
from tests.test_api import client  # noqa: F401  (the authenticated API client)

LIMITS = {"max_files": 50, "max_total_bytes": 50 * 1024 * 1024, "max_file_bytes": 5 * 1024 * 1024}


def _png(color: str = "red", size=(40, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _zip(entries: dict[str, bytes], *, raw_names: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            if raw_names:  # write the name exactly, bypassing zipfile's own cleanup
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, data)
            else:
                z.writestr(name, data)
    return buf.getvalue()


# --- reading --------------------------------------------------------------------------
def test_folders_become_groups_and_root_files_one_group() -> None:
    data = _zip({
        "BR5475/front.png": _png(),
        "BR5475/back.png": _png("blue"),
        "Nurse Tees/BR6001/a.png": _png(),
        "loose.png": _png(),
    })
    out = read_archive(data, **LIMITS)
    got = sorted((f.group_key or "", f.filename) for f in out.files)
    assert got == sorted([
        ("BR5475", "front.png"), ("BR5475", "back.png"),
        ("Nurse Tees/BR6001", "a.png"), ("", "loose.png"),
    ])


@pytest.mark.parametrize("name", ["../evil.png", "a/../../evil.png", "/etc/evil.png", "C:/evil.png", "..\\evil.png"])
def test_paths_that_leave_the_archive_are_refused(name: str) -> None:
    assert safe_parts(name) is None
    out = read_archive(_zip({name: _png(), "ok/fine.png": _png()}, raw_names=True), **LIMITS)
    assert out.unsafe == 1
    assert [f.filename for f in out.files] == ["fine.png"]


def test_a_nested_archive_is_counted_and_never_opened() -> None:
    inner = _zip({"hidden/design.png": _png()})
    out = read_archive(_zip({"more.zip": inner, "renamed.png": inner, "a/x.png": _png()}), **LIMITS)
    assert out.nested == 2
    assert [f.filename for f in out.files] == ["x.png"]


def test_the_type_comes_from_the_bytes_and_clutter_is_ignored() -> None:
    out = read_archive(
        _zip({
            "a/design.png": _png(),
            "a/notes.png": b"<html>not an image</html>",
            "a/readme.txt": b"hello",
            "a/photo.jpg": _png(),  # a PNG named .jpg is still an image
            "__MACOSX/a/._design.png": b"\x00\x05\x16\x07",
            "a/.DS_Store": b"\x00",
        }),
        **LIMITS,
    )
    assert sorted(f.filename for f in out.files) == ["design.png", "photo.jpg"]
    assert out.unsupported == 2  # notes.png and readme.txt; clutter is not counted


def test_not_a_zip_is_refused() -> None:
    with pytest.raises(UnsupportedUpload):
        read_archive(b"PK\x03\x04 but not really a zip", **LIMITS)
    with pytest.raises(UnsupportedUpload):
        read_archive(_png(), **LIMITS)


def test_too_many_files_is_refused() -> None:
    data = _zip({f"g/{i}.png": _png() for i in range(6)})
    with pytest.raises(UploadTooLarge, match="at most 5 files"):
        read_archive(data, max_files=5, max_total_bytes=10**8, max_file_bytes=10**7)


def test_a_zip_bomb_stops_at_the_limits() -> None:
    # 20 MB of zeros compresses to a few KB.
    bomb = _zip({"g/big.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * (20 * 1024 * 1024)})
    assert len(bomb) < 100 * 1024
    out = read_archive(bomb, max_files=10, max_total_bytes=10**9, max_file_bytes=1024 * 1024)
    assert out.too_large == ["big.png"] and out.files == []
    # And the total over every file is capped, counted from the bytes actually read.
    many = _zip({f"g/{i}.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * (3 * 1024 * 1024) for i in range(4)})
    with pytest.raises(UploadTooLarge, match="unpack"):
        read_archive(many, max_files=10, max_total_bytes=8 * 1024 * 1024, max_file_bytes=4 * 1024 * 1024)


def test_utf8_names_without_the_flag_are_recovered() -> None:
    # What Windows' own "Send to ZIP" writes: UTF-8 bytes, the UTF-8 flag unset.
    raw = "Hemşire Tişörtleri/ön.png".encode("utf-8")
    placeholder = b"X" * len(raw)  # ASCII, so zipfile leaves the flag unset
    data = _zip({placeholder.decode(): _png()}).replace(placeholder, raw)
    assert not zipfile.ZipFile(io.BytesIO(data)).infolist()[0].flag_bits & 0x800
    [f] = read_archive(data, **LIMITS).files
    assert (f.group_key, f.filename) == ("Hemşire Tişörtleri", "ön.png")


# --- through the API ------------------------------------------------------------------
async def _batch(client: AsyncClient) -> str:
    return (await client.post("/api/batches")).json()["id"]


async def _send(client: AsyncClient, batch_id: str, data: bytes, name: str = "designs.zip"):
    return await client.post(
        f"/api/batches/{batch_id}/archive", files={"file": (name, io.BytesIO(data), "application/zip")}
    )


async def test_a_zip_upload_keeps_the_folders_and_reports_what_it_skipped(client: AsyncClient) -> None:
    batch_id = await _batch(client)
    data = _zip({
        "BR5475/front.png": _png(),
        "BR5475/back.png": _png("blue"),
        "BR6001/a.png": _png("green"),
        "loose.png": _png(),
        "BR6001/notes.txt": b"hi",
        "inner.zip": _zip({"x.png": _png()}),
        "../escape.png": _png(),
    }, raw_names=True)
    resp = await _send(client, batch_id, data)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert sorted((a["group_key"] or "", a["original_filename"]) for a in body["assets"]) == sorted([
        ("BR5475", "front.png"), ("BR5475", "back.png"), ("BR6001", "a.png"), ("", "loose.png"),
    ])
    assert all(a["status"] == "processed" for a in body["assets"])
    assert (body["skipped_unsupported"], body["skipped_nested"], body["skipped_unsafe"]) == (1, 1, 1)
    # The folder is also the SKU source, as with a folder upload.
    assert {a["parsed_sku"] for a in body["assets"] if a["group_key"] == "BR5475"} == {"BR5475"}
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert len((await s.execute(select(Asset))).scalars().all()) == 4


async def test_several_zips_go_into_one_batch(client: AsyncClient) -> None:
    batch_id = await _batch(client)
    assert (await _send(client, batch_id, _zip({"A1/x.png": _png()}), "one.zip")).status_code == 201
    assert (await _send(client, batch_id, _zip({"B2/y.png": _png()}), "two.zip")).status_code == 201
    batch = (await client.get(f"/api/batches/{batch_id}")).json()
    assert sorted(a["group_key"] for a in batch["assets"]) == ["A1", "B2"]


async def test_an_oversized_zip_is_refused(client: AsyncClient, test_settings) -> None:
    data = _zip({"g/a.png": _png()})
    set_settings_override(test_settings.model_copy(update={"max_archive_bytes": len(data) - 1}))
    resp = await _send(client, await _batch(client), data)
    assert resp.status_code == 413


async def test_a_file_that_is_not_a_zip_is_refused(client: AsyncClient) -> None:
    resp = await _send(client, await _batch(client), _png(), "fake.zip")
    assert resp.status_code == 415
