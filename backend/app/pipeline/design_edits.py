"""A design adjusted on one mockup, its look, and parts of it recoloured (seller feedback, Part B).

Every edit is a **render-time layer**: the uploaded design file is never changed. What is
stored is small and relative, so it travels:

* **The adjustment** (per design, photo and print area; ``mockup_adjustment``): where the
  design sits relative to its automatic fit in the area (pipeline/mockups.py ``place``):
  ``dx``/``dy`` move its centre (shares of the area's width/height), ``scale`` scales it,
  ``sx``/``sy`` stretch one side (proportions unlocked), ``angle`` turns it (degrees,
  clockwise), ``corners`` moves each corner on its own (perspective; shares of the area).
  No adjustment is the automatic fit ("Reset").
* **The look** (in the same row): ``opacity``, ``brightness`` and ``contrast``, in small
  ranges (``RANGES``), applied to the design's own pixels before it is printed.
* **Recolouring** (per design and subgroup; ``mockup_recolour``): regions chosen with a
  rectangle, a lasso, or a click with a tolerance (a magic wand), each with its colour. The
  selected pixels take that colour; their alpha, and so the anti-aliased edges, stay.

"Apply this change to…" (api/mockup_edits.py) gives one change to other designs or photos as
the **change** itself (``delta``), composed on top of each one's own adjustment
(``compose``), never as an absolute position.

The editor (frontend lib/designEdits.ts) computes the same points, look and selections with
the same arithmetic; both read tests/fixtures/design_edit_cases.json.
"""

from __future__ import annotations

import math
import re
from typing import Any

Point = tuple[float, float]

#: The fields of an adjustment and the look, their neutral values and their ranges.
NEUTRAL: dict[str, float] = {"dx": 0.0, "dy": 0.0, "scale": 1.0, "sx": 1.0, "sy": 1.0, "angle": 0.0,
                             "opacity": 1.0, "brightness": 0.0, "contrast": 0.0}
RANGES: dict[str, tuple[float, float]] = {
    "dx": (-1.5, 1.5), "dy": (-1.5, 1.5), "scale": (0.1, 4.0), "sx": (0.2, 5.0), "sy": (0.2, 5.0),
    "angle": (-180.0, 180.0), "opacity": (0.3, 1.0), "brightness": (-0.25, 0.25), "contrast": (-0.25, 0.25),
}
#: How far a free corner moves (shares of the area).
CORNER_RANGE = (-1.0, 1.0)
#: Pixels with alpha above this are part of the design (a wand never spreads into padding).
VISIBLE_ALPHA = 8
REGION_KINDS = ("rect", "lasso", "wand")
MAX_REGIONS = 40
MAX_LASSO_POINTS = 400
_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


class EditError(ValueError):
    """An adjustment or a region that cannot be used, with a reason a seller can read."""


def _clip(name: str, value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise EditError(f"{name} must be a number.") from exc
    if not math.isfinite(v):
        raise EditError(f"{name} must be a number.")
    lo, hi = RANGES[name]
    return round(min(hi, max(lo, v)), 5)


def clean(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """An adjustment as it is stored: known fields only, each inside its range; None when it
    is the automatic fit with the neutral look (nothing to store)."""
    if not data:
        return None
    out: dict[str, Any] = {name: _clip(name, data.get(name, NEUTRAL[name])) for name in NEUTRAL}
    corners = data.get("corners")
    if corners:
        if not isinstance(corners, (list, tuple)) or len(corners) != 4:
            raise EditError("Free corners need four corners.")
        cleaned = []
        for c in corners:
            if not isinstance(c, (list, tuple)) or len(c) != 2:
                raise EditError("Each corner is an x and a y.")
            cleaned.append([round(min(CORNER_RANGE[1], max(CORNER_RANGE[0], float(v))), 5) for v in c])
        out["corners"] = cleaned if any(abs(v) > 1e-6 for c in cleaned for v in c) else None
    else:
        out["corners"] = None
    if all(abs(out[k] - NEUTRAL[k]) < 1e-9 for k in NEUTRAL) and out["corners"] is None:
        return None
    return out


def full(data: dict[str, Any] | None) -> dict[str, Any]:
    """Every field, neutral where unset."""
    return {**NEUTRAL, "corners": None, **(data or {})}


def delta(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    """The change from ``before`` to ``after``: added for moves, turns and the look, a factor
    for scales, added offsets for free corners."""
    b, a = full(before), full(after)
    out: dict[str, Any] = {}
    for k in ("dx", "dy", "angle", "opacity", "brightness", "contrast"):
        out[k] = float(a[k]) - float(b[k])
    for k in ("scale", "sx", "sy"):
        out[k] = float(a[k]) / float(b[k]) if float(b[k]) else 1.0
    bc, ac = b.get("corners") or [[0.0, 0.0]] * 4, a.get("corners") or [[0.0, 0.0]] * 4
    out["corners"] = [[ac[i][0] - bc[i][0], ac[i][1] - bc[i][1]] for i in range(4)]
    return out


def compose(base: dict[str, Any] | None, change: dict[str, Any]) -> dict[str, Any] | None:
    """``change`` (``delta``) on top of ``base``: each target keeps its own fit and adjustment
    and moves, scales and turns by the same amount."""
    b = full(base)
    out: dict[str, Any] = {}
    for k in ("dx", "dy", "angle", "opacity", "brightness", "contrast"):
        out[k] = float(b[k]) + float(change.get(k, 0.0))
    for k in ("scale", "sx", "sy"):
        out[k] = float(b[k]) * float(change.get(k, 1.0))
    bc = b.get("corners") or [[0.0, 0.0]] * 4
    dc = change.get("corners") or [[0.0, 0.0]] * 4
    out["corners"] = [[bc[i][0] + dc[i][0], bc[i][1] + dc[i][1]] for i in range(4)]
    if out["angle"] > 180:
        out["angle"] -= 360
    elif out["angle"] < -180:
        out["angle"] += 360
    return clean(out)


# --- where the design goes ----------------------------------------------------------------------


def _square_to_quad(q: list[Point]) -> list[list[float]]:
    from app.pipeline.mockups import _square_to_quad as sq

    return sq(q)


def fit_box(corners_px: list[Point], design_size: tuple[int, int], size: float) -> tuple[float, float, float, float, float, float]:
    """The automatic fit (pipeline/mockups.py ``place``): (u0, v0, du, dv) in the area's unit
    square, and the area's width and height in pixels."""
    tl, tr, br, bl = corners_px
    area_w = (math.dist(tl, tr) + math.dist(bl, br)) / 2
    area_h = (math.dist(tl, bl) + math.dist(tr, br)) / 2
    dw, dh = design_size
    scale = min(area_w / dw, area_h / dh) * size
    du, dv = dw * scale / area_w, dh * scale / area_h
    return (1 - du) / 2, 0.0, du, dv, area_w, area_h


def unit_points(corners_px: list[Point], design_size: tuple[int, int], size: float,
                adjust: dict[str, Any] | None) -> list[Point]:
    """The design's corners (TL, TR, BR, BL) in the area's unit square, after the adjustment."""
    u0, v0, du, dv, area_w, area_h = fit_box(corners_px, design_size, size)
    a = full(adjust)
    cu, cv = u0 + du / 2 + float(a["dx"]), v0 + dv / 2 + float(a["dy"])
    hw = du * area_w / 2 * float(a["scale"]) * float(a["sx"])
    hh = dv * area_h / 2 * float(a["scale"]) * float(a["sy"])
    t = math.radians(float(a["angle"]))
    cos, sin = math.cos(t), math.sin(t)
    out = []
    for sx_, sy_ in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y = sx_ * hw, sy_ * hh
        out.append((cu + (x * cos - y * sin) / area_w, cv + (x * sin + y * cos) / area_h))
    corners = a.get("corners")
    if corners:
        out = [(u + float(c[0]), v + float(c[1])) for (u, v), c in zip(out, corners)]
    return out


def photo_points(corners_px: list[Point], design_size: tuple[int, int], size: float,
                 adjust: dict[str, Any] | None) -> list[Point]:
    """The design's corners in photo pixels: the unit points through the area's perspective."""
    from app.pipeline.mockups import apply

    h = _square_to_quad(list(corners_px))
    return [apply(h, u, v) for u, v in unit_points(corners_px, design_size, size, adjust)]


# --- the look -----------------------------------------------------------------------------------


def look_rgba(rgba, adjust: dict[str, Any] | None):  # noqa: ANN001, ANN201 - numpy uint8 (h, w, 4)
    """The design's pixels with its look: ``(c - 0.5) * (1 + contrast) + 0.5 + brightness`` per
    colour channel (0..1, rounded back to 0..255), alpha times ``opacity``."""
    import numpy as np

    a = full(adjust)
    b, c, o = float(a["brightness"]), float(a["contrast"]), float(a["opacity"])
    if abs(b) < 1e-9 and abs(c) < 1e-9 and abs(o - 1) < 1e-9:
        return rgba
    out = rgba.copy()
    rgb = rgba[..., :3].astype(np.float64) / 255
    rgb = np.clip((rgb - 0.5) * (1 + c) + 0.5 + b, 0, 1)
    out[..., :3] = np.floor(rgb * 255 + 0.5).astype(np.uint8)
    out[..., 3] = np.floor(rgba[..., 3].astype(np.float64) * o + 0.5).astype(np.uint8)
    return out


# --- recolouring ----------------------------------------------------------------------------------


def clean_regions(regions: Any) -> list[dict[str, Any]]:
    """Regions as they are stored: kind, its points or seed (shares of the design file), a
    tolerance for a wand (0..100) and a colour "#rrggbb"."""
    if regions is None:
        return []
    if not isinstance(regions, list) or len(regions) > MAX_REGIONS:
        raise EditError(f"At most {MAX_REGIONS} recoloured regions per design.")
    out = []
    for r in regions:
        if not isinstance(r, dict) or r.get("kind") not in REGION_KINDS:
            raise EditError("A region is a rectangle, a lasso or a wand click.")
        colour = str(r.get("colour") or "")
        if not _COLOUR.match(colour):
            raise EditError("A region's colour is #rrggbb.")
        row: dict[str, Any] = {"kind": r["kind"], "colour": colour.lower()}
        if r["kind"] == "wand":
            seed = r.get("seed")
            if not isinstance(seed, (list, tuple)) or len(seed) != 2:
                raise EditError("A wand click needs a point.")
            row["seed"] = [round(min(1.0, max(0.0, float(v))), 6) for v in seed]
            row["tolerance"] = round(min(100.0, max(0.0, float(r.get("tolerance", 20)))), 3)
        else:
            pts = r.get("points")
            need = 2 if r["kind"] == "rect" else 3
            if not isinstance(pts, list) or not need <= len(pts) <= (2 if r["kind"] == "rect" else MAX_LASSO_POINTS):
                raise EditError("A rectangle needs two corners; a lasso at least three points.")
            row["points"] = [[round(min(1.0, max(0.0, float(x))), 6), round(min(1.0, max(0.0, float(y))), 6)] for x, y in pts]
        out.append(row)
    return out


def _rect_mask(points: list[list[float]], w: int, h: int):  # noqa: ANN202
    import numpy as np

    (x0, y0), (x1, y1) = points
    lx, hx, ly, hy = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
    cx = (np.arange(w) + 0.5) / w
    cy = (np.arange(h) + 0.5) / h
    return ((cy >= ly) & (cy <= hy))[:, None] & ((cx >= lx) & (cx <= hx))[None, :]


def _lasso_mask(points: list[list[float]], w: int, h: int):  # noqa: ANN202
    """Pixels whose centre is inside the polygon (even-odd), row by row."""
    import numpy as np

    mask = np.zeros((h, w), bool)
    n = len(points)
    for y in range(h):
        cy = (y + 0.5) / h
        xs = []
        for i in range(n):
            x1, y1 = points[i]
            x2, y2 = points[(i + 1) % n]
            if (y1 <= cy < y2) or (y2 <= cy < y1):
                xs.append(x1 + (cy - y1) * (x2 - x1) / (y2 - y1))
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            start = max(0, math.ceil(xs[k] * w - 0.5))
            end = min(w, math.ceil(xs[k + 1] * w - 0.5))
            if end > start:
                mask[y, start:end] = True
    return mask


def _wand_mask(rgba, seed: list[float], tolerance: float):  # noqa: ANN001, ANN202
    """The 4-connected pixels around the seed whose colour is within ``tolerance`` (0..100 of
    the channel range, each channel) of the seed's, among the design's visible pixels."""
    import numpy as np
    from scipy import ndimage

    h, w = rgba.shape[:2]
    x, y = min(w - 1, int(seed[0] * w)), min(h - 1, int(seed[1] * h))
    if rgba[y, x, 3] <= VISIBLE_ALPHA:
        return np.zeros((h, w), bool)
    limit = round(tolerance * 2.55)
    diff = np.abs(rgba[..., :3].astype(np.int16) - rgba[y, x, :3].astype(np.int16)).max(axis=-1)
    similar = (diff <= limit) & (rgba[..., 3] > VISIBLE_ALPHA)
    labels, _ = ndimage.label(similar, structure=[[0, 1, 0], [1, 1, 1], [0, 1, 0]])
    return labels == labels[y, x]


def region_mask(rgba, region: dict[str, Any]):  # noqa: ANN001, ANN201 - numpy bool (h, w)
    h, w = rgba.shape[:2]
    if region["kind"] == "rect":
        return _rect_mask(region["points"], w, h)
    if region["kind"] == "lasso":
        return _lasso_mask(region["points"], w, h)
    return _wand_mask(rgba, region["seed"], float(region.get("tolerance", 20)))


def recolour(rgba, regions: list[dict[str, Any]] | None):  # noqa: ANN001, ANN201 - numpy uint8 (h, w, 4)
    """The design's pixels with each region in its colour: RGB replaced, alpha kept (so the
    anti-aliased edges stay soft). Regions apply in order; a wand reads the pixels as the
    regions before it left them."""
    import numpy as np

    if not regions:
        return rgba
    out = rgba.copy()
    for region in regions:
        mask = region_mask(out, region) & (out[..., 3] > 0)
        colour = region["colour"]
        rgb = np.array([int(colour[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.uint8)
        out[mask, :3] = rgb
    return out
