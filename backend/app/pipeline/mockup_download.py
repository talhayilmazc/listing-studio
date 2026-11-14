"""Downloading finished mockups (feature/mockups-visible, A2): one image, a selection or a ZIP, in the seller's
format, size and file names.

**Always the adjusted mockup, the pixels Etsy gets.** A mockup's stored file is its render with every
adjustment the seller made (placement, size, rotation, look, recolouring: pipeline/design_edits.py), in the
template's photo order. When it is a listing's photo it is what the draft uploads as it is; when it is the
listing's first photo (the cover), the draft sends ``images.cover_image`` of it (the seller's crop, else the
automatic square), and so does the download (``etsy_bytes``). At "original, JPG, high" the file is that
JPEG itself: nothing is decoded or encoded again, only the metadata below is written into its header, so the
pixels are the same as Etsy's (``tests/test_mockup_download.py``). Every other choice is made from those pixels.

**Metadata:** sRGB with the sRGB ICC profile embedded, 300 DPI, and nothing else: no EXIF, no GPS, no camera
data from the seller's blank photos (the mockup engine writes none; this module writes only the profile and
the resolution; WebP has no resolution field of its own, so a minimal EXIF carries it, with nothing else).

**Sizes are never enlarged:** mockups are made at up to 2,000 px (mockups.OUTPUT); "3000 px" and "original"
both give a mockup at its own size, and the page says so.
"""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any

FORMATS = ("jpg", "png", "webp")
SIZES = ("original", "3000", "2000", "1080")
QUALITIES = (92, 85)
NAMINGS = ("full", "sku", "file")
LAYOUTS = ("folders", "flat")
DPI = 300
MIME = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


@dataclass(frozen=True)
class Options:
    """What a seller downloads: kept per account (``tenant.mockup_download``)."""

    format: str = "jpg"
    size: str = "original"
    quality: int = 92
    naming: str = "full"
    layout: str = "folders"

    @classmethod
    def of(cls, raw: Any) -> Options:
        """From stored or sent values; anything unknown is the default (never an error)."""
        raw = raw if isinstance(raw, dict) else {}
        base = cls()
        size = str(raw.get("size", base.size))
        try:
            quality = int(raw.get("quality", base.quality))
        except (TypeError, ValueError):
            quality = base.quality
        return cls(
            format=raw.get("format") if raw.get("format") in FORMATS else base.format,
            size=size if size in SIZES else base.size,
            quality=quality if quality in QUALITIES else base.quality,
            naming=raw.get("naming") if raw.get("naming") in NAMINGS else base.naming,
            layout=raw.get("layout") if raw.get("layout") in LAYOUTS else base.layout,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def passes_through(self) -> bool:
        """The stored JPEG as it is (only its metadata written): Etsy's pixels, byte for byte in the image data."""
        return self.format == "jpg" and self.size in ("original", "3000") and self.quality == 92


@lru_cache(maxsize=1)
def srgb_icc() -> bytes:
    from PIL import ImageCms

    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


# --- the file itself ---------------------------------------------------------------------------------------


def _jfif(dpi: int) -> bytes:
    body = b"JFIF\x00" + bytes([1, 1, 1]) + dpi.to_bytes(2, "big") + dpi.to_bytes(2, "big") + b"\x00\x00"
    return b"\xff\xe0" + (len(body) + 2).to_bytes(2, "big") + body


def _icc_segments(icc: bytes) -> list[bytes]:
    """The profile as APP2 ICC_PROFILE segments (65,519 bytes of profile each)."""
    chunk = 65519
    parts = [icc[i:i + chunk] for i in range(0, len(icc), chunk)] or [b""]
    out = []
    for n, part in enumerate(parts, 1):
        body = b"ICC_PROFILE\x00" + bytes([n, len(parts)]) + part
        out.append(b"\xff\xe2" + (len(body) + 2).to_bytes(2, "big") + body)
    return out


def jpeg_with_metadata(data: bytes, icc: bytes | None = None, dpi: int = DPI) -> bytes:
    """The JPEG with every APPn and comment segment taken out (EXIF, GPS, XMP, an old profile) and a JFIF
    header at ``dpi`` and the sRGB profile put in. The image data is not touched: the pixels stay the same."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    kept: list[bytes] = []
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            raise ValueError("a broken JPEG")
        marker = data[i + 1]
        if marker == 0xDA:  # start of scan: the rest is image data
            kept.append(data[i:])
            break
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            kept.append(data[i:i + 2])
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        segment = data[i:i + 2 + length]
        if not (0xE0 <= marker <= 0xEF or marker == 0xFE):  # APP0-15 and COM go; tables and frames stay
            kept.append(segment)
        i += 2 + length
    return b"".join([b"\xff\xd8", _jfif(dpi), *_icc_segments(icc if icc is not None else srgb_icc()), *kept])


def _exif_with_resolution(dpi: int) -> bytes:
    from PIL import Image

    exif = Image.Exif()
    exif[0x011A] = (dpi, 1)  # XResolution
    exif[0x011B] = (dpi, 1)  # YResolution
    exif[0x0128] = 2  # inches
    return exif.tobytes()


def target_side(size: str) -> int | None:
    return None if size == "original" else int(size)


def convert(data: bytes, options: Options) -> bytes:
    """The download of one mockup from its Etsy bytes (a JPEG), in ``options``. CPU work: run it on the image
    threads (core/image_work.py)."""
    if options.passes_through:
        return jpeg_with_metadata(data)
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    image.load()
    image = image.convert("RGB")  # the pixels are sRGB already (the engine's output); no profile to apply
    side = target_side(options.size)
    if side is not None and max(image.size) > side:  # smaller only: a mockup is never enlarged
        scale = side / max(image.size)
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)
    out = io.BytesIO()
    icc = srgb_icc()
    if options.format == "jpg":
        image.save(out, "JPEG", quality=options.quality, subsampling=0, optimize=True, icc_profile=icc, dpi=(DPI, DPI))
        return jpeg_with_metadata(out.getvalue(), icc)  # Pillow's own header, made the same as the pass-through's
    if options.format == "png":
        image.save(out, "PNG", optimize=False, compress_level=6, icc_profile=icc, dpi=(DPI, DPI))
    else:
        image.save(out, "WEBP", quality=options.quality, method=4, icc_profile=icc, exif=_exif_with_resolution(DPI))
    return out.getvalue()


def etsy_bytes(stored: bytes, *, cover: bool, crop: dict | None) -> bytes:
    """What a draft uploads for this mockup: the stored file, or for a listing's cover the cover image made from
    it exactly as the draft makes it (workers/publish.py: the seller's crop, else the automatic square)."""
    if not cover:
        return stored
    from app.core.config import get_settings
    from app.pipeline.images import cover_image

    settings = get_settings()
    return cover_image(stored, crop, padding_pct=settings.thumbnail_padding_pct, size=settings.thumbnail_size,
                       mode=settings.thumbnail_mode).data


# --- names ------------------------------------------------------------------------------------------------


_UNSAFE = re.compile(r"[^\w.-]+", re.UNICODE)


def part(text: str | None, limit: int = 60) -> str:
    """A safe file-name part: letters (any language), digits, "-", "_" and "."; spaces and everything else "-"."""
    cleaned = _UNSAFE.sub("-", (text or "").strip()).strip("-._")
    return re.sub(r"-{2,}", "-", cleaned)[:limit].strip("-._")


@dataclass(frozen=True)
class Item:
    """One mockup to download: what its name is made of. ``n``: its number among its design's mockups."""

    sku: str | None
    design_file: str
    template: str
    colour: str | None
    n: int
    #: The template's file-name prefix ("CC"), when the seller set one: it goes first in every name.
    prefix: str | None = None


def stem(item: Item, naming: str) -> str:
    base = _stem(item, naming)
    return f"{part(item.prefix, 20)}-{base}" if part(item.prefix, 20) else base


def _stem(item: Item, naming: str) -> str:
    sku = part(item.sku) or part(PurePosixPath(item.design_file.replace("\\", "/")).stem) or "design"
    number = f"{item.n:02d}"
    if naming == "sku":
        return f"{sku}_{number}"
    if naming == "file":
        return f"{part(PurePosixPath(item.design_file.replace(chr(92), '/')).stem) or sku}_{number}"
    pieces = [sku, part(item.template)]
    if item.colour and part(item.colour).lower() != part(item.template).lower():
        pieces.append(part(item.colour))
    return "_".join(p for p in pieces if p) + f"_{number}"


def names(items: list[Item], options: Options) -> list[str]:
    """Each item's file name, in order: "{SKU}_{template}_{colour}_{n}.jpg" (or "{SKU}_{n}", or the design's own
    file name and n), safe characters only, never two the same (a "-2" is added; case does not count)."""
    taken: set[str] = set()
    out = []
    for item in items:
        base = stem(item, options.naming) or "mockup"
        name, n = f"{base}.{options.format}", 1
        while name.lower() in taken:
            n += 1
            name = f"{base}-{n}.{options.format}"
        taken.add(name.lower())
        out.append(name)
    return out


def folder(item: Item, taken: dict[str, str], key: str) -> str:
    """The ZIP folder of a design (one per SKU, else per design file), the same for all its mockups."""
    if key not in taken:
        base = part(item.sku) or part(PurePosixPath(item.design_file.replace("\\", "/")).stem) or "design"
        name, n = base, 1
        while name.lower() in {v.lower() for v in taken.values()}:
            n += 1
            name = f"{base}-{n}"
        taken[key] = name
    return taken[key]
