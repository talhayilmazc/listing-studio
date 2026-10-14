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


# --- people and harder garments ----------------------------------------------------------------
#
# A drawn person is not a photograph: the real models (pipeline/mockup_vision.py)
# rightly find no person in it. So each drawing comes with its own exact answer
# (the class of every pixel, the shoulder and hip points), which a stand-in model
# runner returns; that tests what the app does with the models' answer.

SKIN = (205, 160, 130)
HAIR_COLOUR = (60, 40, 28)


def person(kind: str = "hair", *, size: tuple[int, int] = (1200, 1500), colour: tuple[int, int, int] = (236, 236, 234)):
    """(PNG bytes, truth): a person in a T-shirt. ``kind``: "hair" (a strand of hair
    over the chest) or "arms" (arms crossed in front of the chest). ``truth`` has
    ``classes`` (an L image: 0 background, 1 hair, 2 body skin, 3 face, 4 clothes)
    and ``points`` ({11, 12, 23, 24}: (x, y) in pixels)."""
    w, h = size
    img = Image.new("RGB", size, (214, 210, 204))
    classes = Image.new("L", size, 0)
    d, c = ImageDraw.Draw(img), ImageDraw.Draw(classes)

    def both(shape: str, xy, fill, cls: int) -> None:
        getattr(d, shape)(xy, fill=fill)
        getattr(c, shape)(xy, fill=cls)

    both("rectangle", [w * 0.44, h * 0.15, w * 0.56, h * 0.27], SKIN, 2)  # neck
    both("ellipse", [w * 0.38, h * 0.02, w * 0.62, h * 0.21], SKIN, 3)  # face
    shirt = [(w * 0.22, h * 0.30), (w * 0.40, h * 0.24), (w * 0.50, h * 0.27), (w * 0.60, h * 0.24), (w * 0.78, h * 0.30),
             (w * 0.88, h * 0.50), (w * 0.77, h * 0.54), (w * 0.75, h * 1.0), (w * 0.25, h * 1.0), (w * 0.23, h * 0.54),
             (w * 0.12, h * 0.50)]
    both("polygon", shirt, colour, 4)
    # Folds on the shirt: soft shading inside its own outline only.
    folds = Image.new("L", size, 128)
    ImageDraw.Draw(folds).line([(w * 0.42, h * 0.40), (w * 0.47, h * 0.95)], fill=70, width=max(6, w // 45))
    ImageDraw.Draw(folds).line([(w * 0.60, h * 0.35), (w * 0.57, h * 0.95)], fill=190, width=max(6, w // 45))
    folds = folds.filter(ImageFilter.GaussianBlur(w / 60))
    garment = Image.new("L", size, 0)
    ImageDraw.Draw(garment).polygon(shirt, fill=255)
    shade = folds.point(lambda v: round((v - 128) * 0.25 + 128))
    shaded = Image.merge("RGB", [shade.point(lambda v, k=k: max(0, min(255, round(k * v / 128)))) for k in colour])
    img.paste(shaded, (0, 0), garment)
    if kind == "arms":
        left = [(w * 0.12, h * 0.50), (w * 0.23, h * 0.53), (w * 0.68, h * 0.60), (w * 0.66, h * 0.67), (w * 0.18, h * 0.62)]
        right = [(w * 0.88, h * 0.50), (w * 0.77, h * 0.53), (w * 0.32, h * 0.64), (w * 0.34, h * 0.71), (w * 0.82, h * 0.62)]
        both("polygon", left, SKIN, 2)
        both("polygon", right, SKIN, 2)
    else:
        both("polygon", [(w * 0.12, h * 0.50), (w * 0.22, h * 0.53), (w * 0.21, h * 0.86), (w * 0.14, h * 0.86)], SKIN, 2)
        both("polygon", [(w * 0.88, h * 0.50), (w * 0.78, h * 0.53), (w * 0.79, h * 0.86), (w * 0.86, h * 0.86)], SKIN, 2)
    both("ellipse", [w * 0.36, h * 0.0, w * 0.64, h * 0.11], HAIR_COLOUR, 1)
    if kind == "hair":
        # A strand of hair falling over the chest, its edge soft as hair is.
        strand = Image.new("L", size, 0)
        ImageDraw.Draw(strand).polygon([(w * 0.37, h * 0.05), (w * 0.43, h * 0.07), (w * 0.47, h * 0.47), (w * 0.41, h * 0.49),
                                        (w * 0.36, h * 0.25)], fill=255)
        strand = strand.filter(ImageFilter.GaussianBlur(2.5))
        img.paste(Image.new("RGB", size, HAIR_COLOUR), (0, 0), strand)
        classes.paste(1, (0, 0), strand.point(lambda v: 255 if v >= 128 else 0))
    for x0 in (0.44, 0.53):
        d.ellipse([w * x0, h * 0.09, w * (x0 + 0.03), h * 0.11], fill=(40, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    points = {12: (w * 0.30, h * 0.29), 11: (w * 0.70, h * 0.29), 24: (w * 0.33, h * 0.72), 23: (w * 0.67, h * 0.72)}
    return buf.getvalue(), {"classes": classes, "points": points}


def wrinkled(colour: tuple[int, int, int] = (238, 238, 236), *, size: tuple[int, int] = (1200, 1400), seed: int = 5) -> bytes:
    """A flat-lay tee crumpled all over: many soft folds and a few sharp creases."""
    w, h = size
    rng = random.Random(seed)
    shape = Image.new("L", size, 0)
    ImageDraw.Draw(shape).polygon(_shirt_polygon(w, h), fill=255)
    folds = Image.new("L", size, 128)
    draw = ImageDraw.Draw(folds)
    for _ in range(14):
        x0, y0 = rng.uniform(0.25, 0.75) * w, rng.uniform(0.2, 0.9) * h
        x1, y1 = x0 + rng.uniform(-0.25, 0.25) * w, y0 + rng.uniform(-0.2, 0.2) * h
        draw.line([(x0, y0), (x1, y1)], fill=rng.choice((50, 205)), width=max(6, w // 40))
    folds = folds.filter(ImageFilter.GaussianBlur(w / 70))
    creases = Image.new("L", size, 128)
    draw = ImageDraw.Draw(creases)
    for _ in range(5):
        x0, y0 = rng.uniform(0.38, 0.62) * w, rng.uniform(0.2, 0.3) * h
        draw.line([(x0, y0), (x0 + rng.uniform(-0.12, 0.12) * w, y0 + rng.uniform(0.08, 0.18) * h)], fill=20, width=max(3, w // 300))
    creases = creases.filter(ImageFilter.GaussianBlur(1.5))
    shade = ImageChops.add(folds.point(lambda v: round((v - 128) * 0.3 + 128)), creases.point(lambda v: round((v - 128) * 0.5 + 128)), offset=-128)
    garment = Image.merge("RGB", [shade.point(lambda v, c=c: max(0, min(255, round(c * v / 128)))) for c in colour])
    out = Image.new("RGB", size, (200, 203, 207))
    out.paste(garment, (0, 0), shape)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def folded(colour: tuple[int, int, int] = (238, 238, 236), *, size: tuple[int, int] = (1200, 1400), at: float = 0.31) -> bytes:
    """A flat-lay tee with a sharp fold across the chest at ``at`` (a share of the
    height): the fabric above the fold lies over the fabric below it."""
    w, h = size
    shape = Image.new("L", size, 0)
    ImageDraw.Draw(shape).polygon(_shirt_polygon(w, h), fill=255)
    shade = Image.new("L", size, 128)
    draw = ImageDraw.Draw(shade)
    y = at * h
    draw.rectangle([0, y - h * 0.012, w, y + h * 0.004], fill=200)  # the fold's lit edge
    draw.rectangle([0, y + h * 0.004, w, y + h * 0.012], fill=18)  # the deep line under it
    shade = shade.filter(ImageFilter.GaussianBlur(1.6))
    garment = Image.merge("RGB", [shade.point(lambda v, c=c: max(0, min(255, round(c * v / 128)))) for c in colour])
    out = Image.new("RGB", size, (200, 203, 207))
    out.paste(garment, (0, 0), shape)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


class TruthRunner:
    """A stand-in for the models that returns a drawing's own exact answer."""

    def __init__(self, truth: dict, presence: float = 0.98, visibility: float = 0.97) -> None:
        self.truth, self.presence, self.visibility = truth, presence, visibility
        self.calls: list[str] = []

    def segment(self, rgb):  # noqa: ANN001, ANN201
        import numpy as np

        self.calls.append("segment")
        h, w = rgb.shape[:2]
        cls = np.asarray(self.truth["classes"].resize((w, h), Image.NEAREST), dtype=np.int64)
        probs = np.zeros((h, w, 6), dtype=np.float32)
        for k in range(6):
            probs[..., k] = (cls == k).astype(np.float32)
        return probs

    def landmarks(self, rgb, box):  # noqa: ANN001, ANN201
        import numpy as np

        self.calls.append("landmarks")
        h, w = rgb.shape[:2]
        full_w, full_h = self.truth["classes"].size
        points = np.zeros((33, 3))
        for index, (x, y) in self.truth["points"].items():
            points[index] = (x * w / full_w, y * h / full_h, self.visibility)
        return points, self.presence
