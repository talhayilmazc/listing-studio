"""Synthetic blanks and designs for the mockup engine's tests and sample images.

Everything is drawn here from fixed numbers and a fixed seed: no real garment
photo, no real design, no seller's data.
"""

from __future__ import annotations

import io
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter


def _shirt_polygon(w: int, h: int) -> list[tuple[float, float]]:
    return [
        (0.36 * w, 0.10 * h), (0.43 * w, 0.13 * h), (0.50 * w, 0.15 * h), (0.57 * w, 0.13 * h), (0.64 * w, 0.10 * h),
        (0.86 * w, 0.20 * h), (0.80 * w, 0.36 * h), (0.71 * w, 0.32 * h), (0.71 * w, 0.92 * h),
        (0.29 * w, 0.92 * h), (0.29 * w, 0.32 * h), (0.20 * w, 0.36 * h), (0.14 * w, 0.20 * h),
    ]


def _noise(w: int, h: int, seed: int, cell: int) -> Image.Image:
    rng = random.Random(seed)
    sw, sh = max(1, w // cell), max(1, h // cell)
    small = Image.frombytes("L", (sw, sh), bytes(rng.randrange(256) for _ in range(sw * sh)))
    return small.resize((w, h), Image.BICUBIC)


def blank(
    colour: tuple[int, int, int] = (238, 238, 236),
    *,
    size: tuple[int, int] = (1200, 1400),
    background: tuple[int, int, int] = (200, 203, 207),
    heather: bool = False,
    transparent: bool = False,
    seed: int = 7,
) -> bytes:
    """A flat-lay T-shirt with folds and a fabric weave, on a plain background."""
    w, h = size
    shape = Image.new("L", size, 0)
    ImageDraw.Draw(shape).polygon(_shirt_polygon(w, h), fill=255)
    # Folds: soft diagonal bands of shadow and light.
    folds = Image.new("L", size, 128)
    draw = ImageDraw.Draw(folds)
    for i, (x, dark) in enumerate(((0.38, True), (0.47, False), (0.58, True), (0.66, False))):
        draw.line([(x * w, 0.25 * h), ((x + 0.06) * w, 0.9 * h)], fill=30 if dark else 230, width=max(6, w // 30))
    folds = folds.filter(ImageFilter.GaussianBlur(w / 70))
    weave = _noise(w, h, seed, 2 if not heather else 3)
    amount = 0.06 if not heather else 0.22
    # Brightness around 128: the folds and the weave, each a share of the colour.
    shade = ImageChops.add(
        folds.point(lambda v: round((v - 128) * 0.22 + 128)),
        weave.point(lambda v: round((v - 128) * amount + 128)),
        offset=-128,
    )
    garment = Image.merge("RGB", [shade.point(lambda v, c=c: max(0, min(255, round(c * v / 128)))) for c in colour])
    out = Image.new("RGBA" if transparent else "RGB", size, (*background, 0) if transparent else background)
    out.paste(garment, (0, 0), shape)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def design(kind: str = "transparent", size: tuple[int, int] = (900, 600)) -> bytes:
    """A made-up design: ``transparent`` (shapes on transparency), ``opaque`` (a
    JPEG-like rectangle), ``white`` (white shapes on transparency, for dark shirts)."""
    w, h = size
    if kind == "opaque":
        img = Image.new("RGB", size, (250, 230, 120))
    else:
        img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    ink = (255, 255, 255, 255) if kind == "white" else (210, 70, 40, 255)
    second = (255, 255, 255, 255) if kind == "white" else (40, 90, 160, 255)
    draw.ellipse([0.30 * w, 0.05 * h, 0.70 * w, 0.65 * h], fill=ink)
    draw.polygon([(0.05 * w, 0.95 * h), (0.40 * w, 0.40 * h), (0.75 * w, 0.95 * h)], fill=second)
    draw.polygon([(0.35 * w, 0.95 * h), (0.65 * w, 0.50 * h), (0.95 * w, 0.95 * h)], fill=ink)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
