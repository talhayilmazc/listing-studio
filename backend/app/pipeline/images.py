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
# Etsy's uploadListingImage has no crop/zoom params, so the thumbnail is trimmed,
# padded and squared here before upload. Backend-agnostic Pillow implementation.
def prepare_thumbnail(
    data: bytes,
    *,
    padding_pct: int = 8,
    size: int = 2000,
    background: tuple[int, int, int] = (255, 255, 255),
) -> ProcessedImage:
    """Trim empty/transparent margins, pad, centre on a square, output size x size.

    Bounding box comes from the alpha channel on transparent images, or from the
    difference against the corner (background) colour on flat-background images.
    """
    from PIL import Image, ImageChops, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageProcessingError(str(exc)) from exc

    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    mask = None
    if has_alpha:
        content = img.convert("RGBA")
        bbox = content.getchannel("A").getbbox()
        if bbox:
            content = content.crop(bbox)
        mask = content.getchannel("A")
    else:
        content = img.convert("RGB")
        corner = content.getpixel((0, 0))
        diff = ImageChops.difference(content, Image.new("RGB", content.size, corner))
        bbox = diff.getbbox()
        if bbox:
            content = content.crop(bbox)

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
