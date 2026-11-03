"""The first run's sample: a design, a blank shirt and a listing, drawn and written here, for a new seller to walk through.

Nothing here belongs to anyone and nothing is stored: the pictures are made by the real mockup engine, in memory, from
shapes drawn below (``mockup_sample``), and kept only in this process's small cache. No model is called, no allowance is
used, no row is written and no job is queued, so the path works before a shop is connected and costs a seller nothing.

The listing's text is written in advance, and the page says so. It follows the rules the app writes by
(``tests/test_first_run.py`` puts it through them), so what a seller sees is what their own listing should look like.
**The sample never goes to a shop:** a made-up design has no place in a real shop, and a listing nobody counted would be
a way around the allowance. The seller's first real draft is their own design, through the usual approval.
"""

from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

from app.pipeline import mockup_sample, mockups

#: In every image URL: a change to the drawing or the engine's output gets a new one.
VERSION = "1"

SIZE = (1000, 1200)
BACKGROUND = (232, 229, 224)
#: (name, colour, the design's ink: a light one on dark cloth)
COLOURS = {
    "white": ((240, 240, 238), False),
    "sand": ((214, 197, 170), False),
    "black": ((42, 42, 44), True),
    "navy": ((34, 48, 78), True),
}
#: The chest of the shirt drawn below: top left, top right, bottom right, bottom left, as shares of the picture.
AREA = [[0.36, 0.26], [0.64, 0.26], [0.64, 0.60], [0.36, 0.60]]

IMAGES = ("design", "blank", *(f"mockup-{c}" for c in COLOURS))

TEXT = {
    "title": "Sunset Mountain Graphic Tee, Retro Cotton Hiking Style",
    "tags": [
        "outdoor lover top", "camping outfit", "national park top", "trail walker", "adventure clothing", "road trip wear", "nature lover",
        "summit view top", "vintage sun print", "camp counselor", "weekend trek top", "forest walk", "unisex crew neck",
    ],
    "description": (
        "A retro sunset over two mountains, printed across the chest of a soft cotton tee. Made to order, so each one is printed "
        "when you buy it.\n\nSoft combed cotton with a classic unisex fit. Machine wash cold, inside out, and tumble dry low. "
        "Choose your colour and size from the menus."
    ),
    "materials": ["Cotton"],
    "price": "24.00",
    "currency": "USD",
    "attributes": [
        {"name": "Primary color", "value": "White"},
        {"name": "Sleeve length", "value": "Short sleeve"},
        {"name": "Neckline", "value": "Crew neck"},
        {"name": "Theme", "value": "Outdoor & nature"},
    ],
    "section": "Outdoor tees",
}


def _shirt(w: int, h: int) -> list[tuple[float, float]]:
    return [
        (0.36 * w, 0.10 * h), (0.43 * w, 0.13 * h), (0.50 * w, 0.15 * h), (0.57 * w, 0.13 * h), (0.64 * w, 0.10 * h),
        (0.86 * w, 0.20 * h), (0.80 * w, 0.36 * h), (0.71 * w, 0.32 * h), (0.71 * w, 0.92 * h),
        (0.29 * w, 0.92 * h), (0.29 * w, 0.32 * h), (0.20 * w, 0.36 * h), (0.14 * w, 0.20 * h),
    ]


def _shape() -> Image.Image:
    shape = Image.new("L", SIZE, 0)
    ImageDraw.Draw(shape).polygon(_shirt(*SIZE), fill=255)
    return shape


@lru_cache(maxsize=len(COLOURS))
def blank(colour: str) -> bytes:
    """A flat-lay shirt with soft folds, on a plain background."""
    w, h = SIZE
    folds = Image.new("L", SIZE, 128)
    draw = ImageDraw.Draw(folds)
    for x, value in ((0.38, 96), (0.47, 158), (0.58, 96), (0.66, 158)):
        draw.line([(x * w, 0.25 * h), ((x + 0.06) * w, 0.9 * h)], fill=value, width=w // 30)
    folds = folds.filter(ImageFilter.GaussianBlur(w / 70))
    garment = Image.merge("RGB", [folds.point(lambda v, c=c: max(0, min(255, round(c * v / 128)))) for c in COLOURS[colour][0]])
    out = Image.new("RGB", SIZE, BACKGROUND)
    out.paste(garment, (0, 0), _shape())
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=92, subsampling=0)
    return buf.getvalue()


def _mask() -> bytes:
    buf = io.BytesIO()
    _shape().save(buf, format="PNG")
    return buf.getvalue()


@lru_cache(maxsize=len(COLOURS))
def mockup(colour: str) -> bytes:
    """The sample design on the blank, by the engine every mockup goes through."""
    maps = mockups.build_maps(blank(colour), _mask(), None, AREA)
    design = mockups.load_design(mockup_sample.sample(light_ink=COLOURS[colour][1]))
    return mockups.render(maps, design, mockups.Settings(AREA), quality=85)


def image(name: str) -> tuple[bytes, str]:
    """One of :data:`IMAGES`, and its media type."""
    if name == "design":
        return mockup_sample.sample(light_ink=False), "image/png"
    if name == "blank":
        return blank("white"), "image/jpeg"
    return mockup(name.removeprefix("mockup-")), "image/jpeg"

