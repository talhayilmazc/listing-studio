"""The mockup engine: a seller's design placed on their own blank garment photo.

Deterministic image processing only, with libvips on our own server: no
generative model, no external service, no Etsy call. The design's pixels stay
the seller's design. What happens to them is geometric placement (fit,
perspective, the fabric's folds) and the garment's own light, shade and texture
blended over them, as a print on fabric would look: nothing is drawn or added.
Etsy asks for photos that show the item accurately.

**A template** is the blank photo plus what is derived from it once:

* the garment mask (the photo's own transparency, else the background, read
  from the photo's border colour and removed where it touches the edge);
* a default print area (a quadrilateral, chest-placed from the mask's bounds),
  which the seller corrects by dragging its four corners;
* at render time, the maps: shading (the garment's light and shadow relative to
  its average), a fabric texture (normalised, so heathered and plain fabrics show
  a similar amount through the ink) and a displacement map (the folds).

**A mockup** fits the design into the print area (aspect kept, top-aligned,
centred, at the template's size), warps it by the print area's perspective,
displaces it along the folds, and prints it: ink times the garment's shading
and texture, highlights kept with a screen, an underbase on dark garments so
light ink stays light, clipped to the garment.

**Same inputs, same pixels:** every statistic comes from exact integer
histograms (no floating-point sums whose order could change between threads)
and every other step is a per-pixel operation. The output is the image
pipeline's own: at most 2000 px, JPEG quality 85.
"""

from __future__ import annotations

import array
import math
import re
import threading
from dataclasses import dataclass, field
from typing import Any

from app.pipeline.images import ImageProcessingError, ProcessingSpec

#: Raised whenever the rendering changes, so old previews are not mistaken for new.
#: 2: the surface and creases bend the print; the occluder puts it behind hair and skin.
ENGINE_VERSION = 2

#: The image pipeline's own output (pipeline/images.py, ProcessingSpec).
OUTPUT = ProcessingSpec()
#: Defaults a template starts with; the seller changes them on the Templates page.
#: The design fills the print area: it touches the area on its limiting side (a design of
#: the area's proportions fills it corner to corner). The seller can make it smaller.
DEFAULT_SIZE = 1.0
#: A design's transparent padding is trimmed to its pixels with more alpha than this
#: (0..255; anti-aliased edges are kept), plus a safety edge of this many pixels.
TRIM_ALPHA = 8
TRIM_EDGE = 2
DEFAULT_FOLD = 0.5
SIZE_RANGE = (0.2, 1.0)
FOLD_RANGE = (0.0, 1.0)
#: How far the folds move the print at full strength, as a share of the photo's width.
FOLD_SHIFT = 0.012
#: How far the fabric's surface (its slopes, from the shading at three scales) moves
#: the print at full strength, and the most any point moves, as shares of the width.
SURFACE_SHIFT = 0.02
MAX_SHIFT = 0.02
#: How much of a crease line's shadow the ink lifts on a light print (0: none, 1: all).
#: Tuned on the seller's wrinkled photos: at full shadow a crease read as a black line
#: through a light design; the fabric still shows the line, softer.
CREASE_LIFT = 0.45
#: How strongly a crease pulls the print into its line.
CREASE_PULL = 0.012
#: How much fabric texture shows through the ink (as a share of its brightness).
TEXTURE_AMOUNT = 0.035
#: The brightness white ink reaches under even light (an underbase is not paper white).
WHITE_INK = 0.93
#: Garments darker than this (average brightness, 0..1) get a full underbase, as
#: DTG printers put one under anything but white and very light fabric; lighter
#: than ``UNDERBASE_FROM + UNDERBASE_RAMP`` none (the ink sits on the fabric).
UNDERBASE_FROM = 0.55
UNDERBASE_RAMP = 0.25
#: Added to brightness before light and shade are measured relative to the
#: garment's average, so a dark garment's sensor noise is not read as shading.
SHADE_FLOOR = 40.0
#: The least share of its bounding box a garment mask fills (a T-shirt fills ~65%).
MIN_FILL = 0.4

Point = tuple[float, float]


class MockupError(Exception):
    """A blank or a design that cannot be used, with a reason a seller can read."""


def _vips():  # noqa: ANN202
    import pyvips

    return pyvips


# --- loading ----------------------------------------------------------------------------------


def _load(data: bytes):  # noqa: ANN202 - pyvips.Image
    pyvips = _vips()
    try:
        image = pyvips.Image.new_from_buffer(data, "", access="random")
        image = image.autorot()
    except pyvips.Error as exc:  # type: ignore[attr-defined]
        raise ImageProcessingError(str(exc)) from exc
    if image.interpretation not in ("srgb", "b-w"):
        image = image.colourspace("srgb")
    if image.format != "uchar":
        image = image.cast("uchar") if image.format == "char" else image.colourspace("srgb")
    if image.bands in (1, 2):  # grey (+ alpha) -> sRGB (+ alpha)
        grey = image[0]
        rgb = grey.bandjoin([grey, grey])
        image = rgb.bandjoin(image[1]) if image.bands == 2 else rgb
        image = image.copy(interpretation="srgb")
    return image


def _fit_within(image, limit: int):  # noqa: ANN001, ANN202
    scale = min(limit / image.width, limit / image.height, 1.0)
    return image.resize(scale) if scale < 1.0 else image


def _uints(image) -> list[int]:  # noqa: ANN001 - a one-band uint image
    values = array.array("I")
    values.frombytes(image.cast("uint").write_to_memory())
    return list(values)


def _hist(image) -> list[int]:  # noqa: ANN001 - a one-band unsigned image
    """Exact counts per value (integers: the same whatever the thread order)."""
    return _uints(image.hist_find())


def _border_median(rgb) -> tuple[int, int, int]:  # noqa: ANN001
    """The median colour of the photo's outermost pixels: its background."""
    w, h = rgb.width, rgb.height
    strips = [rgb.crop(0, 0, w, 1), rgb.crop(0, h - 1, w, 1), rgb.crop(0, 0, 1, h), rgb.crop(w - 1, 0, 1, h)]
    out = []
    for band in range(3):
        counts = [0] * 256
        for strip in strips:
            for value, n in enumerate(_hist(strip[band])):
                counts[value] += n
        half, seen = sum(counts) / 2, 0
        for value, n in enumerate(counts):
            seen += n
            if seen >= half:
                out.append(value)
                break
    return out[0], out[1], out[2]


def _otsu(counts: list[int]) -> int:
    total = sum(counts)
    weighted = sum(i * n for i, n in enumerate(counts))
    best, best_t, w0, s0 = -1.0, 0, 0, 0
    for t, n in enumerate(counts):
        w0 += n
        s0 += t * n
        w1 = total - w0
        if w0 == 0 or w1 == 0:
            continue
        m0, m1 = s0 / w0, (weighted - s0) / w1
        between = w0 * w1 * (m0 - m1) ** 2
        if between > best:
            best, best_t = between, t
    return best_t


# --- templates --------------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedBlank:
    #: The blank as the mockups' background: sRGB JPEG, at most 2000 px.
    image: bytes
    #: The garment mask, one-band PNG (255 = garment), same size.
    mask: bytes
    width: int
    height: int
    #: "alpha" (the photo's own transparency), "background" (removed) or "none".
    mask_source: str
    #: The print area found for it, as normalised corners TL, TR, BR, BL.
    print_area: list[list[float]]
    #: Hair, skin and accessories in front of the garment (one-band PNG), or None.
    occluder: bytes | None = None
    #: How sure the print area is (0..1), how it was found and what the seller is told.
    confidence: float = 0.0
    method: str = "default"
    note: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    #: Every garment found (reading order): print_area, confidence, kind, view, layout.
    #: The first is ``print_area``. Empty when only the outline was used.
    garments: list[dict[str, Any]] = field(default_factory=list)
    #: "front" or "back" (the seller can change it); None when it is not known.
    view: str | None = None
    #: Where garments are, one-band PNG at the analysis size, or None (the earlier analysis).
    foreground: bytes | None = None
    #: The photo's layout as the analysis reads it, and whether it is sure enough to set it.
    layout: str = "single"
    layout_confident: bool = False


def _to_numpy(image):  # noqa: ANN001, ANN202 - pyvips uchar -> numpy (h, w[, bands])
    import numpy as np

    arr = np.frombuffer(image.write_to_memory(), dtype=np.uint8)
    return arr.reshape(image.height, image.width, image.bands) if image.bands > 1 else arr.reshape(image.height, image.width)


def _from_numpy(arr):  # noqa: ANN001, ANN202 - numpy uint8 (h, w) -> pyvips one band
    import numpy as np

    arr = np.ascontiguousarray(arr, dtype=np.uint8)
    return _vips().Image.new_from_memory(arr.tobytes(), arr.shape[1], arr.shape[0], 1, "uchar")


#: One template analysis at a time per process: it is CPU-bound (no faster side by side)
#: and its peak memory (~0.7 GB above the loaded models) must not add up when the
#: worker prepares several templates at once.
_ANALYSIS = threading.Lock()


def _analyse(rgb, mask, runner: Any, saliency: Any = "default", name: str = "", personal: Any = None):  # noqa: ANN001, ANN202
    """The template's analysis, one at a time per process (``_ANALYSIS``); never raises."""
    with _ANALYSIS:
        return _analyse_one(rgb, mask, runner, saliency, name, personal)


def _analyse_one(rgb, mask, runner: Any, saliency: Any = "default", name: str = "", personal: Any = None):  # noqa: ANN001, ANN202
    """The template's analysis at its working size; never raises. Every garment is found
    by pipeline/garments.py (bundled models); when that finds none or cannot run, the
    earlier analysis (pipeline/mockup_vision.py ``analyse``) is used."""
    import logging

    from app.pipeline import garments as garments_
    from app.pipeline import mockup_vision as vision

    log = logging.getLogger(__name__)
    earlier_only = saliency is False  # the earlier analysis alone (evaluation: "before")
    try:
        if earlier_only:
            saliency = None
        if runner == "default":
            runner = vision.default_runner()
        if saliency == "default":
            saliency = garments_.default_saliency() if runner is not None else None
    except Exception:
        log.exception("mockup analysis models could not be loaded")
        runner, saliency = None, None
    if not earlier_only and (runner is not None or saliency is not None):
        try:
            scale = min(1.0, garments_.WORK / max(rgb.width, rgb.height))
            small = rgb.resize(scale) if scale < 1.0 else rgb
            found = vision.analyse_garments(_to_numpy(small), runner, saliency, name, personal)
            if found is not None:
                return found
        except Exception:  # an unusual photo or a model fault: the earlier analysis still works
            log.exception("garment detection failed; using the earlier analysis")
    try:
        scale = min(1.0, vision.ANALYSIS_SIZE / max(rgb.width, rgb.height))
        small = rgb.resize(scale) if scale < 1.0 else rgb
        small_mask = mask.resize(small.width / mask.width, vscale=small.height / mask.height)
        h, w = min(small.height, small_mask.height), min(small.width, small_mask.width)
        return vision.analyse(_to_numpy(small.crop(0, 0, w, h)), _to_numpy(small_mask.crop(0, 0, w, h)), runner)
    except Exception:  # never stop a template being made
        log.exception("mockup template analysis could not run")
        return vision.Analysis("default", 0.0, None, note=vision.NOTES["fallback"], details={"failed": True})


def _full_size(small, width: int, height: int):  # noqa: ANN001, ANN202
    """A small uint8 numpy mask, scaled up smoothly to the template's size."""
    image = _from_numpy(small)
    return image.resize(width / image.width, vscale=height / image.height, kernel="linear").cast("uchar")


def prepare_blank(data: bytes, runner: Any = "default", saliency: Any = "default", name: str = "",
                  personal: dict[str, dict[str, float]] | None = None) -> PreparedBlank:
    """The template for a blank garment photo (once, in the worker): its outline,
    print area, and what is in front of the garment. ``runner``: the analysis
    models (pipeline/mockup_vision.py); "default" the bundled ones, None none."""
    pyvips = _vips()
    image = _fit_within(_load(data), max(OUTPUT.max_width, OUTPUT.max_height))
    rgb = image[0:3]
    mask = None
    source = "none"
    if image.bands == 4:
        alpha = image[3]
        counts = _hist(alpha)
        if sum(counts[:128]) > 0.02 * image.width * image.height:
            mask = (alpha >= 128).ifthenelse(255, 0).cast("uchar")
            source = "alpha"
            rgb = image.flatten(background=[255, 255, 255]).cast("uchar")
    if mask is None:
        mask = _background_mask(rgb)
        if mask is not None:
            source = "background"
    if mask is None:
        mask = (pyvips.Image.black(rgb.width, rgb.height) + 255).cast("uchar")
    analysis = _analyse(rgb, mask, runner, saliency, name, personal)
    occluder = None
    if analysis.garment is not None and source != "alpha":  # the photo's own transparency is exact
        mask, source = _full_size(analysis.garment, rgb.width, rgb.height), "segmentation"
    if analysis.occluder is not None:
        occ = _full_size(analysis.occluder, rgb.width, rgb.height).gaussblur(1.0).cast("uchar")
        occluder = occ.copy(interpretation="b-w").write_to_buffer(".png", compression=6, strip=True)
    # A soft edge, so the print does not stop on a jagged line.
    mask = mask.gaussblur(0.8).cast("uchar")
    jpeg = rgb.copy(interpretation="srgb").write_to_buffer(".jpg", Q=OUTPUT.quality, strip=True)
    png = mask.copy(interpretation="b-w").write_to_buffer(".png", compression=6, strip=True)
    area = analysis.print_area or default_print_area(mask)
    foreground = None
    if analysis.foreground is not None:
        foreground = _from_numpy(analysis.foreground).copy(interpretation="b-w").write_to_buffer(
            ".png", compression=6, strip=True)
    return PreparedBlank(
        jpeg, png, rgb.width, rgb.height, source, area, occluder=occluder, confidence=analysis.confidence,
        method=analysis.method, note=analysis.note, details=dict(analysis.details),
        garments=list(analysis.garments), view=analysis.view, foreground=foreground,
        layout=analysis.layout, layout_confident=analysis.layout_confident,
    )


@dataclass(frozen=True)
class Source:
    """An uploaded photo resized to the engine's size, transparency kept: what the
    worker prepares a template from, and what a design is printed from."""

    image: bytes
    width: int
    height: int
    has_alpha: bool


def resize_upload(data: bytes) -> Source:
    """Quick enough for the request: decode, resize to at most 2000 px, re-encode
    (PNG with transparency, else a high-quality JPEG). Nothing else."""
    image = _fit_within(_load(data), max(OUTPUT.max_width, OUTPUT.max_height))
    has_alpha = image.bands == 4
    image = image.copy(interpretation="srgb")
    if has_alpha:
        out = image.write_to_buffer(".png", compression=3, strip=True)
    else:
        out = image.write_to_buffer(".jpg", Q=95, strip=True)
    return Source(out, image.width, image.height, has_alpha)


#: Characters no file system (or ZIP reader) accepts in a name.
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_name(text: str, limit: int = 120) -> str:
    """``text`` as a file name part: any language, minus what file systems refuse."""
    cleaned = _UNSAFE.sub(" ", text).strip().strip(".")
    return re.sub(r"\s+", " ", cleaned)[:limit].strip() or "mockup"


def output_name(base: str, template_name: str, taken: set[str], photo_name: str | None = None) -> str:
    """"<SKU>_<template name>_<photo name>.jpg", made unique among ``taken`` (which it
    joins). The photo's part is left out when it is the template's own name (a template
    of one photo, as migration 0068 made them), so it is not said twice."""
    stem = f"{safe_name(base, 60)}_{safe_name(template_name, 80)}"
    if photo_name and safe_name(photo_name).lower() != safe_name(template_name).lower():
        stem = f"{stem}_{safe_name(photo_name, 80)}"
    name, n = f"{stem}.jpg", 1
    while name.lower() in taken:
        n += 1
        name = f"{stem} ({n}).jpg"
    taken.add(name.lower())
    return name


def _background_mask(rgb):  # noqa: ANN001, ANN202
    """The garment, from a photo on a plain background: pixels unlike the border's
    colour, minus the background regions that touch the photo's edge. None when
    nothing sensible separates (the garment would be under 5% or over 97%)."""
    bg = _border_median(rgb)
    diff = (rgb - list(bg)).abs()
    distance = diff[0].maxpair(diff[1]).maxpair(diff[2]).cast("uchar")
    threshold = max(18, min(80, _otsu(_hist(distance))))
    garment = (distance > threshold).ifthenelse(255, 0).cast("uchar").median(5)
    # Flood the background from outside: a one-pixel frame joins every edge region.
    framed = garment.embed(1, 1, garment.width + 2, garment.height + 2, extend="black").copy_memory()
    framed = framed.draw_flood(128, 0, 0, equal=True)
    outside = framed.crop(1, 1, garment.width, garment.height)
    mask = (outside == 128).ifthenelse(0, 255).cast("uchar")
    garment_pixels = _hist(mask)[255]
    if not 0.05 <= garment_pixels / (mask.width * mask.height) <= 0.97:
        return None
    # A garment fills much of its own bounding box; scattered pieces (only the folds'
    # shadows of a white garment on a white background) do not, and are not a mask.
    x0, y0, x1, y1 = _bounds(mask)
    if garment_pixels / max(1, (x1 - x0) * (y1 - y0)) < MIN_FILL:
        return None
    return mask


def _bounds(mask) -> tuple[int, int, int, int]:  # noqa: ANN001
    """The garment's bounding box (left, top, right, bottom) from row and column profiles."""
    solid = (mask >= 128).ifthenelse(1, 0).cast("uchar")
    columns, rows = solid.project()
    col, row = _uints(columns), _uints(rows)
    xs = [i for i, n in enumerate(col) if n > 0]
    ys = [i for i, n in enumerate(row) if n > 0]
    if not xs or not ys:
        return 0, 0, mask.width, mask.height
    return xs[0], ys[0], xs[-1] + 1, ys[-1] + 1


def default_print_area(mask) -> list[list[float]]:  # noqa: ANN001
    """A front-chest print area: 38% of the garment's width, from 21% of its height,
    in a 3:4 portrait (a common print size), inside the garment's bounds."""
    x0, y0, x1, y1 = _bounds(mask)
    w, h = x1 - x0, y1 - y0
    pw = 0.38 * w
    ph = min(pw * 4 / 3, 0.62 * h)
    cx, top = (x0 + x1) / 2, y0 + 0.21 * h
    left, right, bottom = cx - pw / 2, cx + pw / 2, top + ph
    W, H = mask.width, mask.height
    return [[round(left / W, 4), round(top / H, 4)], [round(right / W, 4), round(top / H, 4)],
            [round(right / W, 4), round(bottom / H, 4)], [round(left / W, 4), round(bottom / H, 4)]]


def clean_print_area(corners: Any) -> list[list[float]]:
    """Four corners TL, TR, BR, BL, each inside the photo (0..1), not folded over."""
    if not isinstance(corners, (list, tuple)) or len(corners) != 4:
        raise MockupError("The print area needs four corners.")
    out: list[list[float]] = []
    for corner in corners:
        if not isinstance(corner, (list, tuple)) or len(corner) != 2:
            raise MockupError("Each corner is an x and a y.")
        x, y = (float(v) for v in corner)
        if not (math.isfinite(x) and math.isfinite(y)):
            raise MockupError("Each corner is an x and a y.")
        out.append([round(min(1.0, max(0.0, x)), 4), round(min(1.0, max(0.0, y)), 4)])
    # Convex and clockwise (TL, TR, BR, BL in image coordinates): no corner crosses another.
    signs = []
    for i in range(4):
        (ax, ay), (bx, by), (cx, cy) = out[i], out[(i + 1) % 4], out[(i + 2) % 4]
        signs.append((bx - ax) * (cy - by) - (by - ay) * (cx - bx))
    if not all(s > 0 for s in signs):
        raise MockupError("The corners cross: drag them so the print area is a four-sided shape.")
    return out


# --- geometry ---------------------------------------------------------------------------------


Matrix = list[list[float]]


def _square_to_quad(q: list[Point]) -> Matrix:
    """The homography taking the unit square (0,0),(1,0),(1,1),(0,1) to ``q``."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = q
    dx1, dx2, dx3 = x1 - x2, x3 - x2, x0 - x1 + x2 - x3
    dy1, dy2, dy3 = y1 - y2, y3 - y2, y0 - y1 + y2 - y3
    if abs(dx3) < 1e-12 and abs(dy3) < 1e-12:  # a parallelogram
        return [[x1 - x0, x3 - x0, x0], [y1 - y0, y3 - y0, y0], [0.0, 0.0, 1.0]]
    det = dx1 * dy2 - dx2 * dy1
    g = (dx3 * dy2 - dx2 * dy3) / det
    h = (dx1 * dy3 - dx3 * dy1) / det
    return [[x1 - x0 + g * x1, x3 - x0 + h * x3, x0], [y1 - y0 + g * y1, y3 - y0 + h * y3, y0], [g, h, 1.0]]


def _mul(a: Matrix, b: Matrix) -> Matrix:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _inverse(m: Matrix) -> Matrix:
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise MockupError("The print area is too small.")
    inv = [[e * i - f * h, c * h - b * i, b * f - c * e], [f * g - d * i, a * i - c * g, c * d - a * f],
           [d * h - e * g, b * g - a * h, a * e - b * d]]
    return [[v / det for v in row] for row in inv]


def apply(m: Matrix, x: float, y: float) -> Point:
    w = m[2][0] * x + m[2][1] * y + m[2][2]
    return (m[0][0] * x + m[0][1] * y + m[0][2]) / w, (m[1][0] * x + m[1][1] * y + m[1][2]) / w


@dataclass(frozen=True)
class Placement:
    """Where a design of ``design_size`` goes: the matrix from design pixels to photo pixels."""

    matrix: Matrix
    #: Within the print area (0..1 each): left, top, width, height of the design.
    box: tuple[float, float, float, float]
    #: The size the design is resized to before it is warped (no aliasing).
    scaled: tuple[int, int]


def place(corners_px: list[Point], design_size: tuple[int, int], size: float) -> Placement:
    """Fit the design into the print area: aspect kept, ``size`` of the area, top-aligned, centred."""
    (tl, tr, br, bl) = corners_px
    area_w = (math.dist(tl, tr) + math.dist(bl, br)) / 2
    area_h = (math.dist(tl, bl) + math.dist(tr, br)) / 2
    dw, dh = design_size
    if area_w < 4 or area_h < 4 or dw < 1 or dh < 1:
        raise MockupError("The print area is too small.")
    scale = min(area_w / dw, area_h / dh) * size
    du, dv = dw * scale / area_w, dh * scale / area_h
    u0, v0 = (1 - du) / 2, 0.0
    sw, sh = max(1, round(dw * scale)), max(1, round(dh * scale))
    # design pixel (in the resized design) -> (u, v) in the unit square -> photo pixel
    to_unit = [[du / sw, 0.0, u0], [0.0, dv / sh, v0], [0.0, 0.0, 1.0]]
    return Placement(_mul(_square_to_quad(list(corners_px)), to_unit), (u0, v0, du, dv), (sw, sh))


# --- the template's maps ----------------------------------------------------------------------


@dataclass
class Maps:
    """What every mockup on one template reads; built once per template per run.

    ``blank`` is the whole photo; the other maps cover only the window the print can
    reach (``left``, ``top``, and their own size), so a run of 20 templates does not
    hold 20 photos' worth of float maps.
    """

    blank: Any  # sRGB uchar
    mask: Any  # float 0..1
    colour_low: Any  # float 0..1, 3 bands: the garment's colour with its folds, no weave
    shade_low: Any  # float: brightness relative to the garment's average (1 = average)
    texture: Any  # float: the weave, normalised (about +-TEXTURE_AMOUNT)
    shift: Any  # float, 2 bands: pixels the print moves at full fold strength
    underbase: float  # 0 (light garment) .. 1 (dark garment)
    width: int
    height: int
    #: Where a deep crease folds the fabric over the print (float 0..1 at full strength).
    hide: Any = None
    #: Crease lines (float 0..1): ink partly lifts their shadow (CREASE_LIFT).
    crease: Any = None
    #: Hair, skin and accessories in front of the garment (float 0..1), or None.
    occluder: Any = None
    #: Where the maps' window starts in the photo.
    left: int = 0
    top: int = 0


def window(area: Any, width: int, height: int) -> tuple[int, int, int, int]:
    """The part of the photo a print in ``area`` can reach (left, top, width, height);
    ``area`` is one print area or a list of them (one per garment: their union).

    The design always lies inside the print area's quad; the folds move it by at
    most ``MAX_SHIFT`` of the width. No area: the whole photo.
    """
    if not area:
        return 0, 0, width, height
    areas = area if isinstance(area[0][0], (list, tuple)) else [area]
    xs, ys = [], []
    for quad in areas:
        if not quad:
            continue
        for x, y in clean_print_area(quad):
            xs.append(x * width)
            ys.append(y * height)
    if not xs:
        return 0, 0, width, height
    margin = math.ceil(MAX_SHIFT * width) + 4
    left, top = max(0, math.floor(min(xs)) - margin), max(0, math.floor(min(ys)) - margin)
    right, bottom = min(width, math.ceil(max(xs)) + margin), min(height, math.ceil(max(ys)) + margin)
    if right - left < 2 or bottom - top < 2:
        return 0, 0, width, height
    return left, top, right - left, bottom - top


def _masked_mean(values, mask_bool) -> float:  # noqa: ANN001 - uchar values 0..255
    counts = _hist(((values.cast("ushort") + 1) * mask_bool).cast("ushort"))
    counts = counts[1:257] + [0] * max(0, 256 - len(counts[1:257]))
    total = sum(counts)
    return sum(i * n for i, n in enumerate(counts)) / total if total else 128.0


def build_maps(blank_jpeg: bytes, mask_png: bytes, occluder_png: bytes | None = None, area: Any = None) -> Maps:
    """The maps for one template, kept only for the window a print in ``area`` can reach.

    libvips computes lazily, so only that window (and what its blurs read around it)
    is ever worked out; the pixels are the same as for the whole photo.
    """
    pyvips = _vips()
    blank = _load(blank_jpeg)[0:3]
    mask_u8 = pyvips.Image.new_from_buffer(mask_png, "", access="random")[0]
    if (mask_u8.width, mask_u8.height) != (blank.width, blank.height):
        mask_u8 = mask_u8.resize(blank.width / mask_u8.width, vscale=blank.height / mask_u8.height)
    mask = mask_u8.cast("float") / 255
    garment = (mask_u8 >= 128).ifthenelse(1, 0).cast("uchar")
    luma = (blank[0] * 0.299 + blank[1] * 0.587 + blank[2] * 0.114)
    mean = max(1.0, _masked_mean(luma.rint().cast("uchar"), garment))
    w = blank.width

    def normalised_blur(image, sigma: float):  # noqa: ANN001, ANN202 - only the garment's own pixels
        if sigma >= 8:  # a wide blur is smooth: work at a quarter of the size, then scale back
            k = 4
            small_mask = mask.resize(1 / k, kernel="linear")
            small = (image * mask).resize(1 / k, kernel="linear")
            out = small.gaussblur(sigma / k, precision="float") / (small_mask.gaussblur(sigma / k, precision="float") + 1e-4)
            return out.resize(image.width / out.width, vscale=image.height / out.height, kernel="linear")
        weight = mask.gaussblur(sigma, precision="float") + 1e-4
        return (image * mask).gaussblur(sigma, precision="float") / weight

    fine = normalised_blur(luma, max(1.5, w / 300))
    # Light and shade: the folds, not the weave; relative to the garment's average.
    low = normalised_blur(luma, max(2.0, w / 160))
    colour_low = normalised_blur(blank.cast("float"), max(2.0, w / 160)) / 255
    shade_low = ((low + SHADE_FLOOR) / (mean + SHADE_FLOOR)).maxpair(0.15).minpair(2.5)
    # The weave: what the light blur took out, scaled so its spread is the same on
    # every fabric (a heathered fabric's speckle does not swamp the print; a plain
    # one still shows some grain).
    high = luma - fine
    q = (high * 4 + 1024).rint().maxpair(1).minpair(2047).cast("ushort")
    counts = _hist((q * garment).cast("ushort"))[1:]
    total = sum(counts) or 1
    mu = sum((i + 1) * n for i, n in enumerate(counts)) / total
    var = sum(n * ((i + 1) - mu) ** 2 for i, n in enumerate(counts)) / total
    spread = max(math.sqrt(var) / 4, 0.5)
    texture = (high / spread * TEXTURE_AMOUNT).maxpair(-0.25).minpair(0.25)
    # The folds: the garment's broad light and shade; the print moves with them.
    folds = normalised_blur(luma, max(3.0, w / 120))
    d = ((folds - mean) / (mean + SHADE_FLOOR)).maxpair(-0.5).minpair(0.5) * (FOLD_SHIFT * w)
    # The surface: the shading at three scales read as a height field. The print
    # is pulled down its slopes into the folds (a dense, per-pixel warp).
    dx = pyvips.Image.new_from_array([[-0.5, 0.0, 0.5]])
    dy = pyvips.Image.new_from_array([[-0.5], [0.0], [0.5]])
    height = (normalised_blur(luma, max(1.5, w / 250)) * 0.25 + normalised_blur(luma, max(3.0, w / 100)) * 0.45
              + normalised_blur(luma, max(6.0, w / 40)) * 0.30)
    relief = ((height - mean) / (mean + SHADE_FLOOR)).maxpair(-0.6).minpair(0.6)
    reach = max(3.0, w / 100) * SURFACE_SHIFT * w
    slope_x = relief.conv(dx, precision="float") * reach
    slope_y = relief.conv(dy, precision="float") * reach
    # Creases: narrow lines darker than the fabric around them, measured against the
    # fabric's own texture (a heathered fabric's speckle is not a crease). The print
    # is drawn into each line, and where the line is deepest the fabric folds over it.
    smooth = normalised_blur(luma, max(1.5, w / 600))
    around = normalised_blur(luma, max(4.0, w / 90))
    valley = (around - smooth) / (mean + SHADE_FLOOR)
    threshold = 0.06 + 2.5 * spread / (mean + SHADE_FLOOR)
    crease = ((valley - threshold) * 5).maxpair(0).minpair(1).gaussblur(max(1.0, w / 1000), precision="float")
    drawn = crease.gaussblur(max(2.0, w / 300), precision="float")
    pull = CREASE_PULL * w * max(2.0, w / 300)
    pull_x = drawn.conv(dx, precision="float") * (-pull)
    pull_y = drawn.conv(dy, precision="float") * (-pull)
    limit = MAX_SHIFT * w
    shift_x = (d * 0.4 + slope_x + pull_x).maxpair(-limit).minpair(limit) * mask
    shift_y = (d * 0.4 + slope_y + pull_y).maxpair(-limit).minpair(limit) * mask
    shift = shift_x.bandjoin(shift_y)
    hide = ((crease - 0.55) / 0.35).maxpair(0).minpair(1)
    occluder = None
    if occluder_png:
        occ = pyvips.Image.new_from_buffer(occluder_png, "", access="random")[0]
        if (occ.width, occ.height) != (blank.width, blank.height):
            occ = occ.resize(blank.width / occ.width, vscale=blank.height / occ.height)
        occluder = (occ.cast("float") / 255).copy_memory()
    underbase = min(1.0, max(0.0, (UNDERBASE_FROM + UNDERBASE_RAMP - mean / 255) / UNDERBASE_RAMP))
    left, top, ww, wh = window(area, blank.width, blank.height)

    def kept(image):  # noqa: ANN001, ANN202
        return None if image is None else image.crop(left, top, ww, wh).copy_memory()

    return Maps(
        blank=blank.copy_memory(), mask=kept(mask), colour_low=kept(colour_low),
        shade_low=kept(shade_low), texture=kept(texture), shift=kept(shift),
        underbase=underbase, width=blank.width, height=blank.height, hide=kept(hide), occluder=kept(occluder),
        crease=kept(crease),
        left=left, top=top,
    )


# --- the design -------------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedDesign:
    #: PNG, alpha kept, at most the pipeline's size: what every mockup reads.
    image: bytes
    width: int
    height: int
    has_alpha: bool


#: A design smaller than this on its longest side is refused: printed across a chest it
#: would be visibly soft. Said to the seller with the design's own size.
MIN_DESIGN_SIDE = 500


class DesignTooSmall(MockupError):
    """The design is too small to print clearly (its size is in the message)."""


def prepare_design(data: bytes) -> PreparedDesign:
    """A design as it is stored for printing: resized only, its pixels and transparency kept."""
    loaded = _load(data)
    if max(loaded.width, loaded.height) < MIN_DESIGN_SIDE:
        raise DesignTooSmall(
            f"This design is {loaded.width} x {loaded.height} px: at least {MIN_DESIGN_SIDE} px on its longest side "
            "is needed to print it clearly."
        )
    image = _fit_within(loaded, max(OUTPUT.max_width, OUTPUT.max_height))
    has_alpha = image.bands == 4
    png = image.copy(interpretation="srgb").write_to_buffer(".png", compression=6, strip=True)
    load_design(png)  # refuses a design with no visible pixels
    return PreparedDesign(png, image.width, image.height, has_alpha)


def _with_alpha(data: bytes):  # noqa: ANN202 - pyvips.Image, sRGB + alpha
    image = _fit_within(_load(data), max(OUTPUT.max_width, OUTPUT.max_height))
    return image.bandjoin(255) if image.bands == 3 else image


def trim_box_of(image) -> tuple[int, int, int, int]:  # noqa: ANN001
    """The box (left, top, right, bottom) of a design's pixels with alpha above
    ``TRIM_ALPHA``, grown by ``TRIM_EDGE`` and kept inside the image: its transparent
    padding is outside it, none of its drawn pixels are."""
    solid = (image[3] > TRIM_ALPHA).ifthenelse(1, 0).cast("uchar")
    columns, rows = solid.project()
    col, row = _uints(columns), _uints(rows)
    xs = [i for i, n in enumerate(col) if n > 0]
    ys = [i for i, n in enumerate(row) if n > 0]
    if not xs or not ys:
        raise MockupError("The design has no visible pixels.")
    return (max(0, xs[0] - TRIM_EDGE), max(0, ys[0] - TRIM_EDGE),
            min(image.width, xs[-1] + 1 + TRIM_EDGE), min(image.height, ys[-1] + 1 + TRIM_EDGE))


def design_trim(data: bytes) -> dict[str, list[int]]:
    """What is stored with a design file: its trim box and the size it was measured
    on (the stored file itself is never changed)."""
    image = _with_alpha(data)
    return {"box": list(trim_box_of(image)), "size": [image.width, image.height]}


def load_design(data: bytes, trim: dict | None = None):  # noqa: ANN201 - pyvips.Image, sRGB + alpha, trimmed
    """The design with an alpha band, cropped to its stored trim box (``design_trim``;
    measured here when there is none): the transparent padding goes, every drawn pixel
    stays. Geometry only."""
    image = _with_alpha(data)
    box = None
    if trim and trim.get("box") and trim.get("size"):
        x0, y0, x1, y1 = trim["box"]
        tw, th = trim["size"]
        if (tw, th) != (image.width, image.height):  # measured on another size of the same file
            sx, sy = image.width / tw, image.height / th
            x0, y0, x1, y1 = int(x0 * sx), int(y0 * sy), math.ceil(x1 * sx), math.ceil(y1 * sy)
        box = (max(0, x0), max(0, y0), min(image.width, x1), min(image.height, y1))
    x0, y0, x1, y1 = box or trim_box_of(image)
    if x1 - x0 < 2 or y1 - y0 < 2:
        raise MockupError("The design has no visible pixels.")
    return image.crop(x0, y0, x1 - x0, y1 - y0).copy_memory()


# --- rendering --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    corners: list[list[float]]  # normalised TL, TR, BR, BL
    size: float = DEFAULT_SIZE
    fold: float = DEFAULT_FOLD
    #: Print behind the hair, skin and accessories the template found (when it found any).
    occlusion: bool = True


def render(maps: Maps, design, settings: Settings, *, quality: int = OUTPUT.quality) -> bytes:  # noqa: ANN001
    """One mockup, as JPEG bytes. Same maps, design and settings: same bytes."""
    result = _print_on(maps, maps.blank, design, settings)
    return result.copy(interpretation="srgb").write_to_buffer(".jpg", Q=quality, strip=True, optimize_coding=True)


def render_many(maps: Maps, placements: list[tuple[Any, list[list[float]]]], settings: Settings, *,
                quality: int = OUTPUT.quality) -> bytes:
    """One mockup with a design on each of several garments: ``placements`` pairs a design
    with a print area (the template's garments, in order), and optionally that area's
    "design goes behind hair and arms" switch. Same inputs: same bytes."""
    result = maps.blank
    for design, corners, *switch in placements:
        if design is None or not corners:
            continue
        behind = settings.occlusion and (switch[0] if switch else True)
        result = _print_on(maps, result, design, Settings(corners, settings.size, settings.fold, behind))
    return result.copy(interpretation="srgb").write_to_buffer(".jpg", Q=quality, strip=True, optimize_coding=True)


def _print_on(maps: Maps, base, design, settings: Settings):  # noqa: ANN001, ANN202
    """``base`` (the blank, or a mockup with other garments printed) with the design
    printed in ``settings.corners``."""
    pyvips = _vips()
    W, H = maps.width, maps.height
    corners = [(x * W, y * H) for x, y in clean_print_area(settings.corners)]
    size = min(SIZE_RANGE[1], max(SIZE_RANGE[0], float(settings.size)))
    fold = min(FOLD_RANGE[1], max(FOLD_RANGE[0], float(settings.fold)))
    placed = place(corners, (design.width, design.height), size)

    # The design at the size it will be printed, premultiplied (no dark fringes when warped).
    sw, sh = placed.scaled
    resized = design.resize(sw / design.width, vscale=sh / design.height, kernel="lanczos3")
    alpha = resized[3].cast("float") / 255
    premultiplied = (resized[0:3].cast("float") * alpha).bandjoin(alpha * 255)

    # The part of the photo the print can reach: the design's warped box, plus the fold shift.
    sw_, sh_ = resized.width, resized.height
    pts = [apply(placed.matrix, x, y) for x, y in ((0, 0), (sw_, 0), (sw_, sh_), (0, sh_))]
    margin = math.ceil(MAX_SHIFT * W * fold) + 3
    # Never past the maps' window (it already holds everything a print can reach).
    x0, y0 = maps.left, maps.top
    x1, y1 = x0 + maps.mask.width, y0 + maps.mask.height
    left = max(x0, math.floor(min(p[0] for p in pts)) - margin)
    top = max(y0, math.floor(min(p[1] for p in pts)) - margin)
    right = min(x1, math.ceil(max(p[0] for p in pts)) + margin)
    bottom = min(y1, math.ceil(max(p[1] for p in pts)) + margin)
    if right - left < 2 or bottom - top < 2:
        raise MockupError("The print area is outside the photo.")
    rw, rh = right - left, bottom - top
    mx, my = left - x0, top - y0  # the same region in the maps' window

    # Photo pixel (moved by the folds) -> design pixel, through the inverse homography.
    inverse = _inverse(placed.matrix)
    grid = pyvips.Image.xyz(rw, rh).cast("float")
    shift = maps.shift.crop(mx, my, rw, rh) * fold
    x = grid[0] + left + shift[0]
    y = grid[1] + top + shift[1]
    den = x * inverse[2][0] + y * inverse[2][1] + inverse[2][2]
    sx = (x * inverse[0][0] + y * inverse[0][1] + inverse[0][2]) / den
    sy = (x * inverse[1][0] + y * inverse[1][1] + inverse[1][2]) / den
    warped = premultiplied.mapim(sx.bandjoin(sy), interpolate=pyvips.Interpolate.new("bilinear"),
                                 extend="background", background=[0, 0, 0, 0])
    ink_alpha = (warped[3] / 255).maxpair(0).minpair(1)
    ink = warped[0:3] / (ink_alpha * 255 + 1e-6)  # unpremultiplied, 0..1
    ink = ink.maxpair(0).minpair(1)

    mask = maps.mask.crop(mx, my, rw, rh)
    a = ink_alpha * mask
    if maps.hide is not None and fold > 0:  # fabric folded over the print hides it
        a = a * (1 - maps.hide.crop(mx, my, rw, rh) * min(1.0, fold * 1.6))
    if settings.occlusion and maps.occluder is not None:  # behind hair, skin, straps
        a = a * (1 - maps.occluder.crop(mx, my, rw, rh))
    texture = maps.texture.crop(mx, my, rw, rh) + 1
    shade = maps.shade_low.crop(mx, my, rw, rh)
    # What the ink sits on: the fabric itself (light garments: the ink is tinted by
    # it, as DTG ink is), or a white underbase under the same light (dark garments).
    fabric = maps.colour_low.crop(mx, my, rw, rh) * texture
    lit_white = shade * texture * WHITE_INK
    u = maps.underbase
    ground = fabric * (1 - u) + lit_white.bandjoin([lit_white, lit_white]) * u
    printed = (ink * ground).maxpair(0).minpair(1)  # multiply: the shadows and the weave
    if maps.crease is not None:  # a crease line: ink lifts part of its shadow (light ink most)
        lift = maps.crease.crop(mx, my, rw, rh) * CREASE_LIFT
        light = (ink[0] + ink[1] + ink[2]) / 3
        target = (ink * shade.maxpair(0.75).minpair(1.2).bandjoin([shade.maxpair(0.75).minpair(1.2)] * 2)).maxpair(0).minpair(1)
        k = lift * light
        printed = printed * (1 - k) + target * k
    sheen = ((shade - 1).maxpair(0).minpair(1) * 0.5)
    printed = 1 - (1 - printed) * (1 - sheen)  # screen: the highlights survive the ink

    photo = base.crop(left, top, rw, rh).cast("float") / 255
    out = photo * (1 - a) + printed * a
    region = (out * 255).rint().maxpair(0).minpair(255).cast("uchar")
    return base.insert(region, left, top).copy_memory()


# --- what is in front of the garment: the analysis's map and the seller's brush -------------

#: The seller's "Keep in front" brush, one gray PNG at the photo's size: painted in front,
#: erased (not in front), or untouched (the analysis decides).
BRUSH_FRONT, BRUSH_BEHIND, BRUSH_UNTOUCHED = 255, 0, 128


def _gray(png: bytes, width: int, height: int, kernel: str = "linear"):  # noqa: ANN202 - pyvips one band
    image = _vips().Image.new_from_buffer(png, "", access="random")
    image = (image.colourspace("b-w") if image.bands >= 3 else image)[0]
    if (image.width, image.height) != (width, height):
        image = image.resize(width / image.width, vscale=height / image.height, kernel=kernel)
    return image.cast("uchar")


def brush_png(painted: bytes, width: int, height: int) -> bytes:
    """The seller's brush as it is stored: at the photo's size, each pixel one of the three
    values (the editor paints at its own screen size; scaling must not invent new ones)."""
    image = _gray(painted, width, height, kernel="nearest")
    front = (image > 192).ifthenelse(BRUSH_FRONT, (image < 64).ifthenelse(BRUSH_BEHIND, BRUSH_UNTOUCHED))
    return front.cast("uchar").copy(interpretation="b-w").write_to_buffer(".png", compression=6, strip=True)


def compose_occluder(auto_png: bytes | None, brush: bytes | None, width: int, height: int) -> bytes | None:
    """What every mockup and preview puts in front of the design: the analysis's map
    (``auto_png``, soft-edged), with the seller's brush over it: painted pixels in front,
    erased ones not, the rest as found. None when nothing is in front anywhere."""
    pyvips = _vips()
    base = _gray(auto_png, width, height) if auto_png else (pyvips.Image.black(width, height) + 0).cast("uchar")
    if brush:
        b = _gray(brush, width, height, kernel="nearest")
        base = (b > 192).ifthenelse(255, (b < 64).ifthenelse(0, base)).cast("uchar")
    if base.max() <= 0:
        return None
    return base.copy(interpretation="b-w").write_to_buffer(".png", compression=6, strip=True)


def front_layer(blank_jpeg: bytes, occluder_png: bytes, width: int | None = None) -> bytes:
    """The photo's own pixels that are in front of the garment, as a transparent PNG
    (alpha = the occluder): the editor draws it over its live design preview, so the
    preview hides the design behind hair and hands exactly as the mockup does."""
    blank = _load(blank_jpeg)[0:3]
    alpha = _gray(occluder_png, blank.width, blank.height)
    layer = blank.bandjoin(alpha)
    if width and width < blank.width:
        layer = layer.resize(width / blank.width, kernel="linear")
    return layer.copy(interpretation="srgb").write_to_buffer(".png", compression=3, strip=True)


def transfer_area(area: list[list[float]], source_found: list[list[float]], target_found: list[list[float]],
                  source_size: tuple[int, int], target_size: tuple[int, int]) -> list[list[float]]:
    """A print area set on one photo, placed on another by that photo's own garment: where
    the area sits relative to the garment found in the source (its suggestion), it is put
    relative to the garment found in the target. The seller's adjustment travels; the
    target's own detection decides where its garment is. Shares of each photo."""
    sw, sh = source_size
    tw, th = target_size
    to_px = lambda q, w, h: [(x * w, y * h) for x, y in clean_print_area(q)]  # noqa: E731
    from_unit = _inverse(_square_to_quad(to_px(source_found, sw, sh)))
    to_target = _square_to_quad(to_px(target_found, tw, th))
    out = []
    for x, y in to_px(area, sw, sh):
        u, v = apply(from_unit, x, y)
        px, py = apply(to_target, u, v)
        out.append([round(min(1.0, max(0.0, px / tw)), 4), round(min(1.0, max(0.0, py / th)), 4)])
    return clean_print_area(out)


def preview_scale(blank_jpeg: bytes, mask_png: bytes, occluder_png: bytes | None, width: int) -> tuple[bytes, bytes, bytes | None]:
    """The blank, its mask and its occluder at ``width`` (a fast preview); same aspect."""
    pyvips = _vips()
    blank = _load(blank_jpeg)[0:3]
    scale = min(1.0, width / blank.width)
    if scale >= 1.0:
        return blank_jpeg, mask_png, occluder_png

    def small(png: bytes) -> bytes:
        image = pyvips.Image.new_from_buffer(png, "", access="random")[0].resize(scale)
        return image.copy(interpretation="b-w").write_to_buffer(".png", compression=1, strip=True)

    return (blank.resize(scale).write_to_buffer(".jpg", Q=OUTPUT.quality, strip=True), small(mask_png),
            small(occluder_png) if occluder_png else None)


LAYOUTS = ("single", "front_back", "several")


def chosen_rows(candidates: list[dict], layout: str | bool) -> list[dict]:
    """A photo's suggested areas, by its layout, from every garment the analysis found:
    "several" every garment's; "front_back" the front garment's area and the back garment's
    back area (each with its role); "single" the main garment's alone (the seller adds more
    with "Add area"). Rows without a "main" mark (photos analysed before it) count the main
    one by size and nearness to the middle (``main_row``). (``True``/``False``: several/single,
    as the template switch said before photo layouts.)"""
    if layout is True:
        layout = "several"
    elif layout is False:
        layout = "single"
    if layout == "front_back":
        front = next((c for c in candidates if c.get("pair") == "front"), None)
        back = next((c for c in candidates if c.get("pair") == "back"), None)
        if front is not None and back is not None:
            b = dict(back)
            if b.get("back_area"):
                b["print_area"] = b["detected_area"] = b["back_area"]
            return [{**dict(front), "role": "front"}, {**b, "role": "back"}]
        layout = "several"  # no pair was found: every garment, the seller sets the roles
    role = lambda c: {**dict(c), "role": "back" if c.get("view") == "back" else "front"}  # noqa: E731
    if layout == "several" or len(candidates) <= 1:
        return [role(c) for c in candidates]
    marked = [c for c in candidates if c.get("main")]
    return [role(marked[0] if marked else candidates[main_row(candidates)])]


def main_row(rows: list[dict]) -> int:
    """For rows that carry no "main" mark: the largest print area nearest the middle."""
    best, score = 0, -1.0
    for i, row in enumerate(rows):
        q = row.get("detected_area") or row.get("print_area")
        if not q:
            continue
        area = abs(sum(q[k][0] * q[(k + 1) % 4][1] - q[(k + 1) % 4][0] * q[k][1] for k in range(4))) / 2
        cx, cy = sum(p[0] for p in q) / 4, sum(p[1] for p in q) / 4
        off = math.hypot(cx - 0.5, cy - 0.5) / math.hypot(0.5, 0.5)
        s = area * (1.0 - 0.6 * min(1.0, off))
        if s > score:
            best, score = i, s
    return best


def _rect_of(q: list[tuple[float, float]]) -> tuple[float, float, float, float, float]:
    """(cx, cy, width, height, angle in radians of the top edge) of a quad in pixels."""
    (ax, ay), (bx, by), _, _ = q
    w = (math.dist(q[0], q[1]) + math.dist(q[3], q[2])) / 2
    h = (math.dist(q[0], q[3]) + math.dist(q[1], q[2])) / 2
    return sum(p[0] for p in q) / 4, sum(p[1] for p in q) / 4, w, h, math.atan2(by - ay, bx - ax)


def _is_rect(q: list[tuple[float, float]], tolerance: float = 0.02) -> bool:
    w = math.dist(q[0], q[1])
    h = math.dist(q[0], q[3])
    if w <= 0 or h <= 0:
        return False
    ux, uy = (q[1][0] - q[0][0]) / w, (q[1][1] - q[0][1]) / w
    vx, vy = (q[3][0] - q[0][0]) / h, (q[3][1] - q[0][1]) / h
    return abs(ux * vx + uy * vy) < tolerance and abs(math.dist(q[3], q[2]) - w) < tolerance * w


def with_angle_of(placed: list[list[float]], source: list[list[float]], source_size: tuple[int, int],
                  target_size: tuple[int, int]) -> list[list[float]]:
    """``placed`` (shares of the target photo) turned to exactly the angle of ``source`` (shares
    of the source photo), its centre and size kept: "Apply this area to selected" copies the
    seller's angle as set. A source with free corners (perspective) is left as mapped."""
    sw, sh = source_size
    tw, th = target_size
    src = [(x * sw, y * sh) for x, y in source]
    if not _is_rect(src):
        return placed
    cx, cy, w, h, _ = _rect_of([(x * tw, y * th) for x, y in placed])
    angle = _rect_of(src)[4]
    ux, uy = math.cos(angle), math.sin(angle)
    vx, vy = -uy, ux
    out = [[round(min(1.0, max(0.0, (cx + su * ux * w / 2 + sv * vx * h / 2) / tw)), 4),
            round(min(1.0, max(0.0, (cy + su * uy * w / 2 + sv * vy * h / 2) / th)), 4)]
           for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    return clean_print_area(out)
