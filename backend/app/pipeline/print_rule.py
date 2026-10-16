"""Where the print goes on a garment, from the garment's landmarks: one rule, used by the
template analysis (pipeline/garments.py) and by the evaluation's answer key
(evals/mockup_eval.py), so the two can only differ in the landmarks they start from.

A garment is described by its landmarks in photo pixels:

* ``neck``: the bottom of the collar ribbing on the garment's centre line;
* ``armpits``: where each sleeve seam meets the body (left and right in the photo);
* optional ``top`` / ``bottom``: points the print stays below (a hood, drawstrings) /
  above (its visible lower edge: hem, waistband, lap, a knot, an object or another
  garment lying on it);
* ``layout`` (worn / hanging / folded / flat), ``kind`` and ``view``.

The print is a rectangle in the garment's frame: upright for a worn or hanging garment
(unless its own axis leans more than UPRIGHT_DEGREES), along the garment for a flat one
that clearly lies at an angle. It is centred between the armpits, its top ``top`` chest
widths below the collar, ``width`` chest widths wide and ``aspect`` times as tall: the
template standard, measured from the smart objects of 29 sellers' PSD templates (medians
over unique photos; evals/mockup_eval.py ``measure``). Above a lower edge it stops
EDGE_MARGIN chest widths short and shrinks keeping its aspect, top and centre kept; a
worn tee's or sweatshirt's print ends within WORN_BOTTOM_MAX chest widths of the collar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

Point = tuple[float, float]

#: The template standard: width and top offset in chest widths (armpit to armpit),
#: aspect = height / width, and how many unique PSD templates each comes from.
#: A folded garment's visible panel is narrower than its chest, so its print is a larger
#: share of it.
STANDARD: dict[str, dict[str, float]] = {
    "t-shirt folded": {"width": 0.896, "top": 0.195, "aspect": 0.687, "n": 7},
    "t-shirt worn": {"width": 0.738, "top": 0.188, "aspect": 1.118, "n": 9},
    "sweatshirt folded": {"width": 0.815, "top": 0.083, "aspect": 0.921, "n": 4},
    "sweatshirt worn": {"width": 0.760, "top": 0.196, "aspect": 0.921, "n": 4},
}
MIN_TEMPLATES = 3
KINDS = ("t-shirt", "sweatshirt", "hoodie")
VIEWS = ("front", "back")
#: Layouts photographed upright: the print is not rotated (within UPRIGHT_DEGREES).
UPRIGHT_LAYOUTS = ("on a person", "on a hanger")
UPRIGHT_DEGREES = 6.0
EDGE_MARGIN = 0.03
WORN_BOTTOM_MAX = 1.1


def ratios_for(kind: str | None, view: str | None = None, layout: str | None = None,
               table: dict[str, dict[str, float]] | None = None) -> tuple[dict[str, float], str]:
    """The ratios for a garment and the group they come from. No template shows a back
    or a hoodie: a back uses its type's front ratios, a hoodie a worn sweatshirt's,
    anything else (a baby bodysuit) a t-shirt's; a group with too few templates falls
    back to the same type's other layout, then to t-shirts."""
    table = table or STANDARD
    kind = kind if kind in KINDS else "t-shirt"
    fold = "folded" if layout == "folded" else "worn"
    if kind == "hoodie":
        kind, fold = "sweatshirt", "worn"
    other = "worn" if fold == "folded" else "folded"
    for key in (f"{kind} {fold}", f"{kind} {other}", f"t-shirt {fold}", "t-shirt worn"):
        if key in table and (table[key].get("n", 0) >= MIN_TEMPLATES or key == "t-shirt worn"):
            return table[key], key
    raise KeyError(kind)


def axis_angle(neck: Point, armpits: tuple[Point, Point], layout: str | None,
               hem_centre: Point | None = None) -> float:
    """Degrees (clockwise from vertical) the print is rotated by: 0 within
    UPRIGHT_DEGREES; a worn or hanging garment is upright unless its own axis (collar
    centre to ``hem_centre``) leans more; a flat garment follows its armpit line."""
    if hem_centre is not None:
        angle = math.degrees(math.atan2(-(hem_centre[0] - neck[0]), hem_centre[1] - neck[1]))
    elif layout in UPRIGHT_LAYOUTS:
        angle = 0.0
    else:
        (lx, ly), (rx, ry) = armpits
        angle = math.degrees(math.atan2(ry - ly, rx - lx))
    return 0.0 if abs(angle) <= UPRIGHT_DEGREES else angle


@dataclass
class Frame:
    across: Point  # unit vector across the chest (left to right in the photo)
    down: Point  # unit vector down the garment
    origin: Point  # on the axis (midway between the armpits), at the collar's height
    chest: float  # armpit to armpit, pixels
    angle: float

    def to_photo(self, u: float, v: float) -> Point:
        return (self.origin[0] + self.across[0] * u + self.down[0] * v,
                self.origin[1] + self.across[1] * u + self.down[1] * v)

    def depth(self, p: Point) -> float:
        """How far below the collar a point is, along the garment."""
        return (p[0] - self.origin[0]) * self.down[0] + (p[1] - self.origin[1]) * self.down[1]


def frame(neck: Point, armpits: tuple[Point, Point], layout: str | None, hem_centre: Point | None = None) -> Frame:
    left, right = armpits
    chest = max(math.dist(left, right), 1e-6)
    angle = axis_angle(neck, armpits, layout, hem_centre)
    t = math.radians(angle)
    across = (math.cos(t), math.sin(t))
    down = (-across[1], across[0])
    mid = ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)
    along = (neck[0] - mid[0]) * across[0] + (neck[1] - mid[1]) * across[1]
    origin = (neck[0] - across[0] * along, neck[1] - across[1] * along)
    return Frame(across, down, origin, chest, angle)


@dataclass
class Area:
    corners: list[Point]  # TL, TR, BR, BL in photo pixels
    info: dict[str, Any] = field(default_factory=dict)


def print_area(neck: Point, armpits: tuple[Point, Point], *, kind: str | None = None, view: str | None = None,
               layout: str | None = None, top: Point | None = None, bottom: Point | None = None,
               hem_centre: Point | None = None, table: dict[str, dict[str, float]] | None = None) -> Area:
    """The print area (photo pixels) from a garment's landmarks: see the module."""
    ratios, source = ratios_for(kind, view, layout, table)
    f = frame(neck, armpits, layout, hem_centre)
    info: dict[str, Any] = {"ratios": source, "angle": round(f.angle, 1), "chest": f.chest}
    y0 = ratios["top"] * f.chest
    if top is not None:
        y0 = max(y0, f.depth(top))
    w = ratios["width"] * f.chest
    tall = w * ratios["aspect"]
    if layout in UPRIGHT_LAYOUTS and kind in ("t-shirt", "sweatshirt") and y0 + tall > WORN_BOTTOM_MAX * f.chest:
        info["worn_cap"] = round((y0 + tall) / f.chest, 3)
        tall = WORN_BOTTOM_MAX * f.chest - y0
    if bottom is not None:
        room = f.depth(bottom) - EDGE_MARGIN * f.chest - y0
        if room < tall:
            scale = max(room, 0.05 * f.chest) / tall
            info["shrunk"] = round(scale, 3)
            tall *= scale
            w *= scale
    half = w / 2
    corners = [f.to_photo(u, v) for u, v in ((-half, y0), (half, y0), (half, y0 + tall), (-half, y0 + tall))]
    return Area(corners, info)


def keep_share(corners: list[Point], a: float, b: float) -> list[Point]:
    """The area kept between shares ``a`` and ``b`` of its top edge, aspect and top kept."""
    (x0, y0), (x1, y1), _, (x3, y3) = corners
    ux, uy, vx, vy = x1 - x0, y1 - y0, x3 - x0, y3 - y0
    k = b - a
    px, py = x0 + ux * a, y0 + uy * a
    return [(px, py), (px + ux * k, py + uy * k), (px + ux * k + vx * k, py + uy * k + vy * k), (px + vx * k, py + vy * k)]


def fit(corners: list[Point], outside, limit: float = 0.02, step: float = 0.01,  # noqa: ANN001
        min_share: float = 0.3) -> tuple[list[Point], tuple[float, float], float]:
    """Shrink an area until at most ``limit`` of it is off the garment, from the side
    where most of the outside is (left, right or the bottom), keeping its aspect and
    top. ``outside(corners) -> (share, side)`` measures it. Returns the corners, the kept
    share of the top edge (a, b) and the final outside share."""
    a, b = 0.0, 1.0
    current = list(corners)
    share, side = outside(current)
    while share > limit and b - a > min_share:
        if side == "left":
            a += step
        elif side == "right":
            b -= step
        else:
            a += step / 2
            b -= step / 2
        current = keep_share(corners, a, b)
        share, side = outside(current)
    return current, (round(a, 3), round(b, 3)), share
