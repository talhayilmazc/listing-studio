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
def prepare_thumbnail(
    data: bytes,
    *,
    padding_pct: int = 8,
    size: int = 2000,
    background: tuple[int, int, int] = (255, 255, 255),
    mode: str = "crop",
) -> ProcessedImage:
    """Return a ``size`` x ``size`` JPEG thumbnail (see the modes above)."""
    from PIL import Image, ImageChops, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageProcessingError(str(exc)) from exc

    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    # Find the content bounding box and whether the content is smaller than the
    # frame (a transparent margin, or a uniform flat border to trim).
    if has_alpha:
        source = img.convert("RGBA")
        bbox = source.getchannel("A").getbbox()
    else:
        source = img.convert("RGB")
        corner = source.getpixel((0, 0))
        diff = ImageChops.difference(source, Image.new("RGB", source.size, corner))
        bbox = diff.getbbox()
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
# The batches list renders 56px thumbnail tiles; serving the full processed JPEG
# for each one costs ~600KB a tile. `resize_preview` produces a small JPEG that
# the API caches on disk, so the resize happens once per asset per width.
# Presentation only: nothing here touches what is uploaded to Etsy.
PREVIEW_WIDTHS: frozenset[int] = frozenset({112, 224, 448})


def resize_preview(data: bytes, width: int) -> bytes:
    """Return ``data`` as a JPEG scaled to ``width``, preserving aspect ratio.

    Never upscales: a source narrower than ``width`` is re-encoded at its own size.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageProcessingError(str(exc)) from exc

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    target = min(width, img.width)
    height = max(1, round(img.height * target / img.width))
    img = img.resize((target, height), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=82, optimize=True)
    return out.getvalue()
