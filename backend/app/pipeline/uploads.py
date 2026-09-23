"""Upload admission: what a file *is*, decided from its bytes (production-spec D).

The filename's extension is a claim made by the client and is never trusted. A
file is admitted only when its leading bytes carry the signature of one of the
accepted image formats *and* the image library, reading only the header, agrees
on the format and finds dimensions within the pixel ceiling. That second read
rejects polyglots and headers crafted to decode into gigabytes of pixels
("decompression bombs") before a single pixel is decoded.

Nothing here writes to storage: admission happens first, so a rejected file
never lands on disk.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass


class UploadRejected(Exception):
    """Base for refusals; ``status`` is the HTTP code the API should answer."""

    status = 400


class UnsupportedUpload(UploadRejected):
    status = 415


class UploadTooLarge(UploadRejected):
    status = 413


@dataclass(frozen=True)
class AdmittedImage:
    format: str  # canonical Pillow name: PNG, JPEG, WEBP, GIF, TIFF
    mime: str
    ext: str
    width: int
    height: int


_ACCEPTED: dict[str, tuple[str, str]] = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
    "GIF": ("image/gif", ".gif"),
    "TIFF": ("image/tiff", ".tif"),
}


def sniff_format(data: bytes) -> str | None:
    """Format from magic bytes alone, or ``None`` if it is not an accepted image."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "TIFF"
    return None


def admit_image(data: bytes, *, max_bytes: int, max_pixels: int) -> AdmittedImage:
    """Validate an upload, or raise :class:`UploadRejected` saying why.

    Messages are written for the uploader and never echo file contents.
    """
    if not data:
        raise UnsupportedUpload("the file is empty")
    if len(data) > max_bytes:
        raise UploadTooLarge(f"files are limited to {max_bytes // (1024 * 1024)} MB")

    claimed = sniff_format(data)
    if claimed is None:
        raise UnsupportedUpload("not a supported image (PNG, JPEG, WebP, GIF or TIFF)")

    from PIL import Image, UnidentifiedImageError

    try:
        with warnings.catch_warnings():
            # Pillow only *warns* between 1x and 2x its own ceiling; treat the
            # warning as the refusal it should be.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            # open() parses the header only; no pixel data is decoded here.
            with Image.open(io.BytesIO(data)) as img:
                detected = img.format
                width, height = img.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise UploadTooLarge("the image's dimensions are too large to process") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise UnsupportedUpload("the image could not be read") from exc

    # The signature and the parser must agree; a mismatch is a disguised file.
    if detected != claimed:
        raise UnsupportedUpload("the file's contents do not match an accepted image type")
    if width <= 0 or height <= 0:
        raise UnsupportedUpload("the image has no dimensions")
    if width * height > max_pixels:
        raise UploadTooLarge("the image's dimensions are too large to process")

    mime, ext = _ACCEPTED[claimed]
    return AdmittedImage(format=claimed, mime=mime, ext=ext, width=width, height=height)
