"""Large uploads (hotfix/upload-text).

Before: any photo above 60 megapixels (~7746 x 7746) was refused with "the image's dimensions are too
large to process", and files above 25 MB (30 MB for mockup photos); a 100 MP camera photo could not be
uploaded. Now: 50 MB per file, JPEG, PNG, WebP and HEIC; up to 250 MP, shrunk on load by libvips so a
large photo never sits in memory whole; only a real decompression bomb (above 250 MP) is refused, with a
plain message saying what to do.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from app.core.config import Settings
from app.pipeline.images import PyvipsBackend, ProcessingSpec
from app.pipeline.uploads import UploadTooLarge, admit_image, sniff_format
from tests.test_uploads import _png_header_only

pyvips = pytest.importorskip("pyvips")
NEW = Settings(_env_file=None)
LIMITS = {"max_bytes": NEW.max_upload_bytes, "max_pixels": NEW.max_image_pixels}


def _jpeg(width: int, height: int) -> bytes:
    return (pyvips.Image.black(width, height, bands=3) + [180, 90, 40]).cast("uchar").jpegsave_buffer(Q=80)


def test_the_new_limits() -> None:
    assert NEW.max_upload_bytes == 50 * 1024 * 1024
    assert NEW.max_mockup_upload_bytes == 50 * 1024 * 1024
    assert NEW.max_image_pixels == 250_000_000


def test_a_100_megapixel_photo_is_admitted_and_processed_small() -> None:
    data = _jpeg(10_000, 10_000)
    admitted = admit_image(data, **LIMITS)
    assert (admitted.width, admitted.height) == (10_000, 10_000)
    out = PyvipsBackend().process(data, ProcessingSpec())
    assert max(out.width, out.height) <= 2000


def test_pillows_own_ceiling_does_not_refuse_it() -> None:
    """Pillow warns above ~89 MP, and a warning was a refusal: 100-250 MP headers now pass."""
    admit_image(_png_header_only(12_000, 12_000), **LIMITS)  # 144 MP


def test_only_a_real_bomb_is_refused_with_what_to_do() -> None:
    with pytest.raises(UploadTooLarge) as exc:
        admit_image(_png_header_only(16_000, 16_000), **LIMITS)  # 256 MP
    message = str(exc.value)
    assert "256 megapixels" in message and "250 megapixels" in message
    assert "Save it smaller" in message and "too large to process" not in message
    with pytest.raises(UploadTooLarge):
        admit_image(_png_header_only(50_000, 50_000), **LIMITS)  # 2.5 gigapixels


def test_50_mb_is_the_file_limit() -> None:
    small = _jpeg(400, 400)
    with pytest.raises(UploadTooLarge) as exc:
        admit_image(small + b"\0" * (50 * 1024 * 1024), **LIMITS)
    assert "50 MB" in str(exc.value)


def test_heic_is_recognised_by_its_bytes() -> None:
    assert sniff_format(bytes(4) + b"ftypheic" + bytes(4) + b"mif1heic") == "HEIF"
    assert sniff_format(bytes(4) + b"ftypmif1" + bytes(4)) == "HEIF"
    assert sniff_format(bytes(4) + b"ftypisom" + bytes(4)) is None  # a video is not a photo


def test_heic_is_admitted_and_stored_as_jpeg() -> None:
    image = (pyvips.Image.black(1200, 900, bands=3) + [20, 120, 200]).cast("uchar")
    try:
        data = image.heifsave_buffer(Q=60, compression="hevc")
    except pyvips.Error:
        try:
            data = image.heifsave_buffer(Q=60, compression="av1")
        except pyvips.Error:
            pytest.skip("this libvips cannot write HEIF to make a sample")
    if sniff_format(data) != "HEIF":
        pytest.skip("sample is not a HEIC/HEIF brand")
    admitted = admit_image(data, **LIMITS)
    assert (admitted.format, admitted.width, admitted.height) == ("HEIF", 1200, 900)
    out = PyvipsBackend().process(data, ProcessingSpec())
    assert out.data[:3] == b"\xff\xd8\xff" and (out.width, out.height) == (1200, 900)


def test_memory_stays_low_on_a_100_megapixel_photo(tmp_path) -> None:
    """Shrink on load: processing a 100 MP JPEG never holds the 300 MB of decoded pixels."""
    path = tmp_path / "big.jpg"
    path.write_bytes(_jpeg(10_000, 10_000))
    code = (
        "import resource, sys, json\n"
        "from app.pipeline.images import PyvipsBackend, ProcessingSpec\n"
        "before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        f"out = PyvipsBackend().process(open({str(path)!r}, 'rb').read(), ProcessingSpec())\n"
        "after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "print(json.dumps({'grew_mb': (after - before) / 1024, 'side': max(out.width, out.height)}))\n"
    )
    result = json.loads(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout)
    assert result["side"] <= 2000
    assert result["grew_mb"] < 150, result  # a full decode alone is 300 MB
