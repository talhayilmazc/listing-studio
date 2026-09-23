"""Upload admission (production-spec D): type from contents, size ceilings."""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select

from app.core.config import set_settings_override
from app.db.models import Asset
from app.pipeline.uploads import (
    UnsupportedUpload,
    UploadTooLarge,
    admit_image,
    sniff_format,
)

# The authenticated API client lives with the API tests; reuse it rather than
# building a second copy of that fixture.
from tests.test_api import client  # noqa: E402,F401

LIMITS = {"max_bytes": 25 * 1024 * 1024, "max_pixels": 60_000_000}


def _image(fmt: str, size=(60, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, fmt)
    return buf.getvalue()


def _png_header_only(width: int, height: int) -> bytes:
    """A syntactically valid PNG whose header claims ``width`` x ``height``.

    Tiny on disk, enormous once decoded — the shape of a decompression bomb.
    """
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00" * 16))
        + chunk(b"IEND", b"")
    )


# --- admission, unit level --------------------------------------------------
@pytest.mark.parametrize(
    "fmt,mime,ext",
    [
        ("PNG", "image/png", ".png"),
        ("JPEG", "image/jpeg", ".jpg"),
        ("WEBP", "image/webp", ".webp"),
        ("GIF", "image/gif", ".gif"),
        ("TIFF", "image/tiff", ".tif"),
    ],
)
def test_accepted_formats_are_recognised_from_their_bytes(fmt, mime, ext) -> None:
    admitted = admit_image(_image(fmt), **LIMITS)
    assert (admitted.format, admitted.mime, admitted.ext) == (fmt, mime, ext)
    assert (admitted.width, admitted.height) == (60, 40)


@pytest.mark.parametrize(
    "payload",
    [
        b"<html><script>alert(1)</script></html>",
        b"<?xml version='1.0'?><svg xmlns='http://www.w3.org/2000/svg'/>",
        b"%PDF-1.7\n",
        b"MZ\x90\x00 executable",
        b"PK\x03\x04 zip archive",
    ],
)
def test_non_images_are_refused(payload) -> None:
    assert sniff_format(payload) is None
    with pytest.raises(UnsupportedUpload):
        admit_image(payload, **LIMITS)


def test_empty_file_is_refused() -> None:
    with pytest.raises(UnsupportedUpload):
        admit_image(b"", **LIMITS)


def test_signature_without_a_real_image_is_refused() -> None:
    """Magic bytes alone are not enough; the parser must agree."""
    with pytest.raises(UnsupportedUpload):
        admit_image(b"\x89PNG\r\n\x1a\n" + b"garbage" * 20, **LIMITS)


def test_decompression_bomb_is_refused_from_the_header() -> None:
    bomb = _png_header_only(50_000, 50_000)  # 2.5 gigapixels
    assert len(bomb) < 200  # tiny on disk
    with pytest.raises(UploadTooLarge):
        admit_image(bomb, **LIMITS)


def test_pixel_ceiling_is_ours_not_just_pillows() -> None:
    """Below Pillow's own bomb threshold but above the configured ceiling."""
    with pytest.raises(UploadTooLarge):
        admit_image(_png_header_only(8_000, 8_000), max_bytes=10**9, max_pixels=60_000_000)


def test_byte_ceiling() -> None:
    data = _image("PNG", (400, 400))
    with pytest.raises(UploadTooLarge):
        admit_image(data, max_bytes=len(data) - 1, max_pixels=10**9)


# --- admission, through the API ---------------------------------------------
async def _batch(client: AsyncClient) -> str:
    return (await client.post("/api/batches")).json()["id"]


async def _upload(client: AsyncClient, batch_id: str, name: str, data: bytes):
    return await client.post(
        f"/api/batches/{batch_id}/assets",
        files={"file": (name, io.BytesIO(data), "image/png")},
    )


async def test_disguised_file_is_refused_and_never_stored(client: AsyncClient, tmp_path) -> None:
    batch_id = await _batch(client)
    before = {p for p in tmp_path.rglob("*") if p.is_file()}

    resp = await _upload(client, batch_id, "cute-design.png", b"<html><script>x</script></html>")

    assert resp.status_code == 415
    assert "not a supported image" in resp.json()["detail"]
    assert {p for p in tmp_path.rglob("*") if p.is_file()} == before  # nothing written
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.execute(select(Asset))).scalars().all() == []


async def test_type_comes_from_contents_not_the_name(client: AsyncClient, tmp_path) -> None:
    """A PNG named .jpg is stored and served as the PNG it is."""
    batch_id = await _batch(client)
    resp = await _upload(client, batch_id, "actually-a-png.jpg", _image("PNG"))
    assert resp.status_code == 201

    async with client.sm() as s:  # type: ignore[attr-defined]
        asset = (await s.execute(select(Asset))).scalars().one()
    assert asset.storage_key.endswith(".png")
    assert asset.byte_size == len(_image("PNG"))
    assert asset.original_filename == "actually-a-png.jpg"  # kept for display only


async def test_oversized_file_is_refused(client: AsyncClient, test_settings) -> None:
    data = _image("PNG", (300, 300))
    set_settings_override(test_settings.model_copy(update={"max_upload_bytes": len(data) - 1}))
    batch_id = await _batch(client)
    resp = await _upload(client, batch_id, "big.png", data)
    assert resp.status_code == 413


async def test_batch_ceiling_refuses_the_file_that_would_cross_it(
    client: AsyncClient, test_settings
) -> None:
    data = _image("PNG", (200, 200))
    set_settings_override(
        test_settings.model_copy(update={"max_batch_bytes": len(data) * 2 + len(data) // 2})
    )
    batch_id = await _batch(client)
    assert (await _upload(client, batch_id, "a.png", data)).status_code == 201
    assert (await _upload(client, batch_id, "b.png", data)).status_code == 201
    third = await _upload(client, batch_id, "c.png", data)
    assert third.status_code == 413
    assert "batch" in third.json()["detail"]

    # A fresh batch has its own allowance.
    other = await _batch(client)
    assert (await _upload(client, other, "d.png", data)).status_code == 201


async def test_bomb_is_refused_through_the_api(client: AsyncClient) -> None:
    batch_id = await _batch(client)
    resp = await _upload(client, batch_id, "tiny.png", _png_header_only(50_000, 50_000))
    assert resp.status_code == 413
