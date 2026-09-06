"""Image processing: resize, format conversion, and watermarking.

Primary backend is **pyvips** (fast, low memory); if libvips is unavailable the
processor transparently falls back to **Pillow** (per the stack in CLAUDE.md).
Both backends implement the same :class:`ImageBackend` contract, so the rest of
the pipeline is backend-agnostic.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Protocol

# Canonical output format -> (extension, mime type).
FORMAT_EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
FORMAT_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class ImageProcessingError(Exception):
    """Raised when input bytes cannot be decoded / processed as an image."""


@dataclass
class ProcessingSpec:
    """How to transform an image."""

    max_width: int = 2000
    max_height: int = 2000
    target_format: str = "JPEG"  # JPEG | PNG | WEBP
    quality: int = 85
    watermark_text: str | None = None
    watermark_opacity: float = 0.35

    def normalized_format(self) -> str:
        fmt = self.target_format.upper()
        if fmt == "JPG":
            fmt = "JPEG"
        if fmt not in FORMAT_EXT:
            raise ImageProcessingError(f"unsupported target_format: {self.target_format}")
        return fmt


@dataclass
class ProcessedImage:
    data: bytes
    width: int
    height: int
    format: str  # canonical: JPEG | PNG | WEBP

    @property
    def extension(self) -> str:
        return FORMAT_EXT[self.format]

    @property
    def mime_type(self) -> str:
        return FORMAT_MIME[self.format]


class ImageBackend(Protocol):
    name: str

    def process(self, data: bytes, spec: ProcessingSpec) -> ProcessedImage: ...


# --- Pillow backend ---------------------------------------------------------
class PillowBackend:
    name = "pillow"

    def process(self, data: bytes, spec: ProcessingSpec) -> ProcessedImage:
        from PIL import Image, UnidentifiedImageError

        fmt = spec.normalized_format()
        try:
            img = Image.open(io.BytesIO(data))
            img.load()  # force decode so corrupt input fails here
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise ImageProcessingError(str(exc)) from exc

        # Downscale only, preserving aspect ratio (thumbnail never upscales).
        img.thumbnail((spec.max_width, spec.max_height))

        if spec.watermark_text:
            img = self._watermark(img, spec)

        out = io.BytesIO()
        save_img = img
        if fmt in ("JPEG", "WEBP") and img.mode in ("RGBA", "LA", "P"):
            save_img = img.convert("RGB")
        elif fmt == "PNG" and img.mode == "P":
            save_img = img.convert("RGBA")

        save_kwargs: dict[str, object] = {}
        if fmt in ("JPEG", "WEBP"):
            save_kwargs["quality"] = spec.quality
        save_img.save(out, format=fmt, **save_kwargs)
        return ProcessedImage(out.getvalue(), save_img.width, save_img.height, fmt)

    def _watermark(self, img, spec: ProcessingSpec):
        from PIL import Image, ImageDraw, ImageFont

        base = img.convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = ImageFont.load_default()
        text = spec.watermark_text or ""
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        margin = max(8, base.width // 100)
        x = max(0, base.width - tw - margin)
        y = max(0, base.height - th - margin)
        alpha = int(255 * max(0.0, min(1.0, spec.watermark_opacity)))
        draw.text((x, y), text, fill=(255, 255, 255, alpha), font=font)
        return Image.alpha_composite(base, overlay)


# --- pyvips backend ---------------------------------------------------------
class PyvipsBackend:
    name = "pyvips"

    def process(self, data: bytes, spec: ProcessingSpec) -> ProcessedImage:
        import pyvips

        fmt = spec.normalized_format()
        try:
            img = pyvips.Image.new_from_buffer(data, "", access="sequential")
        except pyvips.Error as exc:  # type: ignore[attr-defined]
            raise ImageProcessingError(str(exc)) from exc

        scale = min(spec.max_width / img.width, spec.max_height / img.height, 1.0)
        if scale < 1.0:
            img = img.resize(scale)

        if spec.watermark_text:
            img = self._watermark(img, spec)

        suffix = FORMAT_EXT[fmt]
        if fmt in ("JPEG", "WEBP") and img.hasalpha():
            img = img.flatten(background=[255, 255, 255])

        kwargs: dict[str, object] = {}
        if fmt in ("JPEG", "WEBP"):
            kwargs["Q"] = spec.quality
        try:
            buf = img.write_to_buffer(suffix, **kwargs)
        except pyvips.Error as exc:  # type: ignore[attr-defined]
            raise ImageProcessingError(str(exc)) from exc
        return ProcessedImage(buf, img.width, img.height, fmt)

    def _watermark(self, img, spec: ProcessingSpec):
        import pyvips

        if not img.hasalpha():
            img = img.addalpha()
        if img.interpretation != "srgb":
            img = img.colourspace("srgb")

        mask = pyvips.Image.text(spec.watermark_text, dpi=120)  # 1-band alpha 0..255
        opacity = max(0.0, min(1.0, spec.watermark_opacity))
        alpha = (mask.cast("float") * opacity).cast("uchar")
        white = (pyvips.Image.black(mask.width, mask.height) + 255).cast("uchar")
        watermark = white.bandjoin([white, white, alpha]).copy(interpretation="srgb")

        margin = max(8, img.width // 100)
        x = max(0, img.width - watermark.width - margin)
        y = max(0, img.height - watermark.height - margin)
        return img.composite(watermark, "over", x=x, y=y)


def default_backend() -> ImageBackend:
    """Return pyvips if libvips is importable, otherwise Pillow."""
    try:
        import pyvips  # noqa: F401

        return PyvipsBackend()
    except Exception:  # ImportError, or OSError when libvips is missing
        return PillowBackend()


class ImageProcessor:
    def __init__(self, backend: ImageBackend | None = None) -> None:
        self._backend = backend or default_backend()

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def process(self, data: bytes, spec: ProcessingSpec) -> ProcessedImage:
        return self._backend.process(data, spec)


# --- Thumbnail preparation (rank=1 listing image) --------------------------
# Etsy's uploadListingImage has no crop/zoom params, so the square thumbnail is
# produced here before upload. Two modes:
#   crop (default): centre-crop the largest square of the source, so no background
#     is ever added (a portrait mockup would otherwise get white bars). Transparent
#     designs, and flat-background images whose content is smaller than the frame,
#     still get trimmed + padded so their margins are normalised.
#   pad: always trim to content and pad to a square (the older framed look), for a
#     shop that prefers it.
# Backend-agnostic Pillow implementation.
def _open(data: bytes):  # noqa: ANN202 - PIL.Image.Image
    """Decode bytes to a loaded PIL image, or raise :class:`ImageProcessingError`."""
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        return img
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageProcessingError(str(exc)) from exc


def _content_bbox(img):  # noqa: ANN001, ANN202
    """Locate the artwork within ``img``.

    Returns ``(source, bbox, has_alpha)``. Transparent designs are measured on
    their alpha channel; everything else by difference from the top-left pixel,
    which isolates the subject on a flat studio background. ``bbox`` is ``None``
    for a wholly uniform image.
    """
    from PIL import Image, ImageChops

    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    if has_alpha:
        source = img.convert("RGBA")
        return source, source.getchannel("A").getbbox(), True
    source = img.convert("RGB")
    corner = source.getpixel((0, 0))
    diff = ImageChops.difference(source, Image.new("RGB", source.size, corner))
    return source, diff.getbbox(), False


def prepare_thumbnail(
    data: bytes,
    *,
    padding_pct: int = 8,
    size: int = 2000,
    background: tuple[int, int, int] = (255, 255, 255),
    mode: str = "crop",
) -> ProcessedImage:
    """Return a ``size`` x ``size`` JPEG thumbnail (see the modes above)."""
    from PIL import Image

    img = _open(data)

    # Find the content bounding box and whether the content is smaller than the
    # frame (a transparent margin, or a uniform flat border to trim).
    source, bbox, has_alpha = _content_bbox(img)
    full = (0, 0, source.width, source.height)
    content_smaller = bbox is not None and bbox != full

    # crop mode + a full-bleed photographic mockup -> centre-crop, never pad.
    if mode != "pad" and not content_smaller:
        rgb = img.convert("RGB")
        square = min(rgb.width, rgb.height)
        left = (rgb.width - square) // 2
        top = (rgb.height - square) // 2
        canvas = rgb.crop((left, top, left + square, top + square))
    else:
        # Trim to content, then pad to a centred square.
        mask = None
        if has_alpha:
            content = source.crop(bbox) if bbox else source
            mask = content.getchannel("A")
        else:
            content = source.crop(bbox) if bbox else source
        side = max(content.width, content.height)
        pad = round(side * padding_pct / 100)
        canvas_side = side + 2 * pad
        canvas = Image.new("RGB", (canvas_side, canvas_side), background)
        offset = ((canvas_side - content.width) // 2, (canvas_side - content.height) // 2)
        if mask is not None:
            canvas.paste(content, offset, mask)
        else:
            canvas.paste(content, offset)

    canvas = canvas.resize((size, size), Image.LANCZOS)
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=90)
    return ProcessedImage(out.getvalue(), size, size, "JPEG")


# --- UI preview derivatives -------------------------------------------------
# The batch grid renders small tiles; serving the full processed JPEG for each
# one costs ~600KB a tile. `resize_preview` produces a small JPEG that the API
# caches on disk, so the work happens once per asset per size.
#
# With an aspect ratio it also crops, centring on the artwork found by
# `_content_bbox` rather than on the frame. Centring on the frame slices the
# design off a portrait mockup; centring on the content keeps it whole.
# Presentation only: nothing here touches what is uploaded to Etsy.
PREVIEW_WIDTHS: frozenset[int] = frozenset({112, 224, 448, 896})

# Tile shapes the grid actually uses, as width/height.
PREVIEW_ASPECTS: dict[str, float] = {"4:5": 4 / 5, "16:10": 16 / 10}


def _crop_box(
    size: tuple[int, int], bbox: tuple[int, int, int, int] | None, ratio: float
) -> tuple[int, int, int, int]:
    """The largest ``ratio`` window that contains ``bbox`` and fits inside ``size``.

    Centred on the content, then nudged back inside the image edges.

    When the window is too short to hold the content — a wide tile over a tall
    mockup — it anchors to the top of the content instead of its middle. On a
    photograph of a worn garment the detected content is the whole model, whose
    midpoint sits at the waist; the printed design sits high on the chest, so
    centring cuts through the artwork and keeping the top keeps it whole.
    """
    width, height = size
    left, top, right, bottom = bbox or (0, 0, width, height)
    box_w, box_h = max(1, right - left), max(1, bottom - top)

    # Smallest window of this ratio that still covers the content...
    crop_w = max(box_w, box_h * ratio)
    crop_h = crop_w / ratio
    # ...shrunk to fit the image, keeping the ratio exact.
    if crop_w > width:
        crop_w, crop_h = width, width / ratio
    if crop_h > height:
        crop_w, crop_h = height * ratio, height

    centre_x = (left + right) / 2
    x = min(max(0.0, centre_x - crop_w / 2), width - crop_w)

    if crop_h < box_h:
        y = float(top)  # keep the top of the content, not its midpoint
    else:
        y = (top + bottom) / 2 - crop_h / 2
    y = min(max(0.0, y), height - crop_h)

    return round(x), round(y), round(x + crop_w), round(y + crop_h)


def resize_preview(data: bytes, width: int, aspect: str | None = None) -> bytes:
    """Return ``data`` as a small JPEG.

    Without ``aspect`` the image is scaled to ``width``, preserving its own
    proportions. With one, it is first cropped to that ratio around the artwork.
    Never upscales beyond the source's own width.
    """
    from PIL import Image

    img = _open(data)
    source, bbox, has_alpha = _content_bbox(img)

    # Flatten onto white: a plain RGBA->RGB conversion renders transparent
    # areas black, which is not how these designs are meant to be seen.
    if has_alpha:
        flat = Image.new("RGB", source.size, (255, 255, 255))
        flat.paste(source, (0, 0), source.getchannel("A"))
        img = flat
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    if aspect is not None:
        ratio = PREVIEW_ASPECTS[aspect]
        img = img.crop(_crop_box(img.size, bbox, ratio))
        target_w = min(width, img.width)
        target_h = max(1, round(target_w / ratio))
    else:
        target_w = min(width, img.width)
        target_h = max(1, round(img.height * target_w / img.width))

    img = img.resize((target_w, target_h), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=82, optimize=True)
    return out.getvalue()
