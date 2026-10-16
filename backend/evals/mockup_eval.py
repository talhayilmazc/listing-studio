"""Mockup template detection, evaluated on the seller's own blank photos.

The photos are never copied into the repo, the image or the server: they are read
in place through a read-only mount, and everything this writes (decoded copies at
upload size, previews, contact sheets, scores) goes to an output folder outside
the repo. The marks (where the print should be on each photo) live in a JSON file
next to the photos.

Run in the dev container (psd-tools is a dev-only dependency, ``.[eval]``):

    R=$(pwd -W); MSYS_NO_PATHCONV=1 docker compose run --rm --no-deps -T \\
      -v "<photos folder>:/eval:ro" \\
      -v "<output folder outside the repo>:/out" \\
      api python -m evals.mockup_eval sheets

Commands: ``decode`` (each photo at upload size, cached in /out/decoded),
``previews`` (each photo with a grid, for marking), ``sheets`` (photo, mark,
detection and a sample design placed, 20 photos a sheet), ``score`` (IoU and
centre error against the marks, per garment type, view, layout and overall).

A mark is written as landmarks of each garment (normalised to the photo at
upload size), and the print area is derived from them by one rule, so every
photo is marked the same way:

* ``neck``: the bottom of the collar (front) or of the back collar, on the axis;
* ``armpits``: where each sleeve meets the body (left and right in the photo);
* optional ``top`` / ``bottom``: a point the print must stay below / above (a
  hoodie's drawstrings, its pocket, a knot, a fold-over at the hem);
* for a garment photographed at an angle, ``perspective: true`` and ``hem``: the
  bottom corners of the body (where they are, or would be if hidden).

The print area comes from those landmarks by the same rule the app uses
(``app/pipeline/print_rule.py``): centred on the garment's axis, its width, top
and aspect from the median of the seller's own PSD templates for that garment
type and layout, upright on worn and hanging garments within 6 degrees, and kept
above ``bottom`` by shrinking with the aspect kept. On a PSD the smart object's
own quad is the mark. See ``print_quad``.

``score --before`` scores the analysis as it was before the garment detector
(``prepare_blank(saliency=False)``); ``report [before]`` rewrites a report from
its saved scores.
"""

from __future__ import annotations

import io
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app.pipeline import mockups, print_rule

EVAL = Path(os.environ.get("MOCKUP_EVAL_DIR", "/eval"))
OUT = Path(os.environ.get("MOCKUP_EVAL_OUT", "/out"))
MARKS = Path(os.environ.get("MOCKUP_EVAL_MARKS", str(EVAL / "marks.json")))
IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".webp", ".psd")

#: The print in the body's unit square (u across the chest, v down from the armpits).
PRINT_WIDTH = 0.5
PRINT_ASPECT = 1.2
TOP_GAP = 0.13  # below the collar, in chest widths (about 2.5 in on a 20 in chest)


# --- photos -----------------------------------------------------------------------------------


def photos() -> list[Path]:
    return sorted(p for p in EVAL.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_TYPES)


def _psd_images(path: Path) -> tuple[Image.Image, list[list[float]] | None, tuple[int, int], str]:
    """A PSD's flattened composite, the first visible smart object's placement quad
    (TL, TR, BR, BL in pixels) and which composite was used.

    The composite Photoshop saved is used unless it shows something where the smart
    object is (a design, or the template's placeholder outline): then the PSD is
    composited with its smart objects hidden, every other layer as saved (hair or a
    sleeve drawn above the design stays)."""
    import numpy as np
    from psd_tools import PSDImage

    psd = PSDImage.open(path)
    saved = (psd.topil() if psd.has_preview() else psd.composite()).convert("RGB")
    smart = [layer for layer in psd.descendants() if layer.kind == "smartobject" and layer.visible]
    if not smart:
        return saved, None, (psd.width, psd.height), "saved composite"
    quad = None
    if smart[0].smart_object.transform_box:
        box = smart[0].smart_object.transform_box
        quad = [[box[0], box[1]], [box[2], box[3]], [box[4], box[5]], [box[6], box[7]]]
    hidden_ids = {id(layer) for layer in smart}
    hidden = psd.composite(layer_filter=lambda layer: layer.is_visible() and id(layer) not in hidden_ids).convert("RGB")
    x0, y0, x1, y1 = smart[0].bbox
    a = np.asarray(saved.crop((x0, y0, x1, y1)), dtype=np.int16)
    b = np.asarray(hidden.crop((x0, y0, x1, y1)), dtype=np.int16)
    changed = float((np.abs(a - b).max(axis=2) > 40).mean())
    if changed > 0.0005:
        return hidden, quad, (psd.width, psd.height), f"smart object hidden ({changed:.2%} of its area differed)"
    return saved, quad, (psd.width, psd.height), "saved composite"


@dataclass
class Photo:
    name: str
    jpeg: bytes  # as the app stores the upload: at most 2000 px, autorotated
    width: int
    height: int
    #: For a PSD: the author's smart-object quad, normalised (TL, TR, BR, BL).
    smart_quad: list[list[float]] | None = None
    #: For a PSD: "saved composite" or "smart objects hidden".
    source: str = "file"


def _upload(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=95)
    return mockups.resize_upload(buf.getvalue()).image


def decode(path: Path) -> Photo:
    """The photo as the app would have it after upload (cached in /out/decoded)."""
    cache = OUT / "decoded" / (path.name + ".jpg")
    meta = cache.with_suffix(".json")
    if cache.exists() and meta.exists():
        info = json.loads(meta.read_text())
        data = cache.read_bytes()
        return Photo(path.name, data, info["width"], info["height"], info.get("smart_quad"), info.get("source", "file"))
    cache.parent.mkdir(parents=True, exist_ok=True)
    smart_quad, source = None, "file"
    if path.suffix.lower() == ".psd":
        image, quad, (pw, ph), source = _psd_images(path)
        if quad:
            smart_quad = [[round(x / pw, 4), round(y / ph, 4)] for x, y in quad]
        data = _upload(image)
    else:
        data = mockups.resize_upload(path.read_bytes()).image
    with Image.open(io.BytesIO(data)) as im:
        width, height = im.size
    cache.write_bytes(data)
    meta.write_text(json.dumps({"width": width, "height": height, "smart_quad": smart_quad, "source": source}))
    return Photo(path.name, data, width, height, smart_quad, source)


# --- marks ------------------------------------------------------------------------------------


Point = tuple[float, float]


def _homography(quad: list[Point]):  # noqa: ANN202 - unit square -> quad
    return mockups._square_to_quad(list(quad))


def _inverse_point(m, x: float, y: float) -> Point:  # noqa: ANN001
    return mockups.apply(mockups._inverse(m), x, y)


#: The print in the garment's frame, measured from the template authors' smart objects
#: in the PSDs (``measure`` writes them to /out/psd-ratios.json; medians over unique
#: base photos): width and top offset in chest widths, aspect = height / width.
#: Keyed "<type> <folded|worn>": a folded garment's visible panel is narrower than its
#: chest, so its print is a larger share of it. Groups with fewer than MIN_PSDS
#: PSDs fall back as ``ratios_for`` says.
MIN_PSDS = 3
_STANDARD: dict[str, dict[str, float]] | None = None


def standard() -> dict[str, dict[str, float]]:
    global _STANDARD
    if _STANDARD is None:
        path = OUT / "psd-ratios.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        _STANDARD = data.get("standard") or {"t-shirt worn": {"width": 0.5, "top": TOP_GAP, "aspect": PRINT_ASPECT, "n": 0}}
    return _STANDARD


def _fold(layout: str | None) -> str:
    return "folded" if layout == "folded" else "worn"


def ratios_for(kind: str | None, view: str | None, layout: str | None = None) -> tuple[dict[str, float], str]:
    """The ratios for a garment, and which measured group they came from. No PSD shows
    a back or a hoodie: a back uses its type's front ratios (the authors' back prints
    are not in this set), a hoodie a worn sweatshirt's, anything else (a baby
    bodysuit) a t-shirt's; a group too small falls back to the same type's other
    layout, then to t-shirts."""
    table = standard()
    kind = kind if kind in ("t-shirt", "sweatshirt", "hoodie") else "t-shirt"
    fold = _fold(layout)
    if kind == "hoodie":
        kind, fold = "sweatshirt", "worn"
    other = "worn" if fold == "folded" else "folded"
    for key in (f"{kind} {fold}", f"{kind} {other}", f"t-shirt {fold}", "t-shirt worn"):
        if key in table and table[key].get("n", 0) >= MIN_PSDS or key == "t-shirt worn" and key in table:
            return table[key], key
    raise KeyError(kind)


UPRIGHT_LAYOUTS = print_rule.UPRIGHT_LAYOUTS
UPRIGHT_DEGREES = print_rule.UPRIGHT_DEGREES
EDGE_MARGIN = print_rule.EDGE_MARGIN
WORN_BOTTOM_MAX = print_rule.WORN_BOTTOM_MAX


def axis_angle(garment: dict[str, Any], width: int, height: int, entry: dict[str, Any] | None = None) -> float:
    """The print's rotation for a marked garment (``print_rule.axis_angle``)."""
    entry = entry or {}
    px = lambda p: (p[0] * width, p[1] * height)  # noqa: E731
    return print_rule.axis_angle(px(garment["neck"]), tuple(px(p) for p in garment["armpits"]),
                                 garment.get("layout", entry.get("layout")),
                                 px(garment["hem_centre"]) if garment.get("hem_centre") else None)


def _frame(garment: dict[str, Any], width: int, height: int, entry: dict[str, Any] | None = None):  # noqa: ANN202
    """The garment's frame in pixels (``print_rule.frame``): across, down, origin, chest."""
    entry = entry or {}
    px = lambda p: (p[0] * width, p[1] * height)  # noqa: E731
    f = print_rule.frame(px(garment["neck"]), tuple(px(p) for p in garment["armpits"]),
                         garment.get("layout", entry.get("layout")),
                         px(garment["hem_centre"]) if garment.get("hem_centre") else None)
    return f.across, f.down, f.origin, f.chest


def print_quad(garment: dict[str, Any], width: int, height: int,
               entry: dict[str, Any] | None = None) -> list[list[float]]:
    return print_area(garment, width, height, entry)[0]


def print_area(garment: dict[str, Any], width: int, height: int,
               entry: dict[str, Any] | None = None) -> tuple[list[list[float]], dict[str, Any]]:
    """The answer key's area for a marked garment: the app's own rule
    (``app.pipeline.print_rule.print_area``) applied to the marked landmarks."""
    if garment.get("perspective"):
        return _perspective_quad(garment, width, height), {"rule": "perspective"}
    entry = entry or {}
    px = lambda p: (p[0] * width, p[1] * height)  # noqa: E731
    area = print_rule.print_area(
        px(garment["neck"]), tuple(px(p) for p in garment["armpits"]),
        kind=garment.get("type", entry.get("type")), view=garment.get("view", entry.get("view")),
        layout=garment.get("layout", entry.get("layout")),
        top=px(garment["top"]) if garment.get("top") else None,
        bottom=px(garment["bottom"]) if garment.get("bottom") else None,
        hem_centre=px(garment["hem_centre"]) if garment.get("hem_centre") else None,
        table=standard())
    corners = [[round(x / width, 4), round(y / height, 4)] for x, y in area.corners]
    return corners, area.info


def smart_ratios(garment: dict[str, Any], quad, width: int, height: int,  # noqa: ANN001
                 entry: dict[str, Any] | None = None) -> dict[str, float]:
    """A PSD smart object (normalised quad) in its garment's frame: width and top
    offset in chest widths, aspect, and its centre's offset from the axis."""
    ex, ey, origin, chest = _frame(garment, width, height, entry)
    pts = [(x * width, y * height) for x, y in quad]
    across = lambda p: (p[0] - origin[0]) * ex[0] + (p[1] - origin[1]) * ex[1]  # noqa: E731
    down = lambda p: (p[0] - origin[0]) * ey[0] + (p[1] - origin[1]) * ey[1]  # noqa: E731
    tl, tr, br, bl = pts
    w = ((across(tr) - across(tl)) + (across(br) - across(bl))) / 2
    h = ((down(bl) - down(tl)) + (down(br) - down(tr))) / 2
    return {
        "width": w / chest, "top": (down(tl) + down(tr)) / 2 / chest, "aspect": h / w,
        "offset": sum(across(p) for p in pts) / 4 / chest,
    }


def _perspective_quad(garment: dict[str, Any], width: int, height: int) -> list[list[float]]:
    """For a garment photographed at an angle: the same print in the body's own unit
    square (armpits to hem), centred on the collar's u."""
    px = lambda p: (p[0] * width, p[1] * height)  # noqa: E731
    left_arm, right_arm = (px(p) for p in garment["armpits"])
    left_hem, right_hem = (px(p) for p in garment["hem"])
    m = _homography([left_arm, right_arm, right_hem, left_hem])
    u_neck, v_neck = _inverse_point(m, *px(garment["neck"]))
    chest = (math.dist(left_arm, right_arm) + math.dist(left_hem, right_hem)) / 2
    length = (math.dist(left_arm, left_hem) + math.dist(right_arm, right_hem)) / 2
    per_chest = chest / max(length, 1.0)  # v units per chest width
    v_top = v_neck + TOP_GAP * per_chest
    if garment.get("top"):
        v_top = max(v_top, _inverse_point(m, *px(garment["top"]))[1])
    v_bottom = v_top + PRINT_WIDTH * PRINT_ASPECT * per_chest
    if garment.get("bottom"):
        v_bottom = min(v_bottom, _inverse_point(m, *px(garment["bottom"]))[1])
    u0, u1 = u_neck - PRINT_WIDTH / 2, u_neck + PRINT_WIDTH / 2
    corners = [mockups.apply(m, u, v) for u, v in ((u0, v_top), (u1, v_top), (u1, v_bottom), (u0, v_bottom))]
    return [[round(x / width, 4), round(y / height, 4)] for x, y in corners]


def load_marks() -> dict[str, Any]:
    """{file name: entry}; an entry ``{"same_as": name}`` reuses another file's marks."""
    data = json.loads(MARKS.read_text(encoding="utf-8")) if MARKS.exists() else {}
    photos_ = data.get("photos", data)
    for name, entry in list(photos_.items()):
        if "same_as" in entry:
            photos_[name] = {**photos_[entry["same_as"]], **entry}
    # A JPG and a PSD of the same photo share the PSD's smart object.
    for name, entry in photos_.items():
        base = entry.get("same_as")
        if base and base.lower().endswith(".psd") and not name.lower().endswith(".psd"):
            entry["psd_twin"] = base
        if name.lower().endswith(".psd") and base and not base.lower().endswith(".psd"):
            photos_[base]["psd_twin"] = name
    return photos_


def inside_frame(quad) -> float:  # noqa: ANN001
    """The share of a (normalised) area that lies inside the photo."""
    full = _mask([(0.25 + x * 0.5, 0.25 + y * 0.5) for x, y in quad], 400)
    inside = _mask([(0.25 + max(0, min(1, x)) * 0.5, 0.25 + max(0, min(1, y)) * 0.5) for x, y in quad], 400)
    return float(inside.sum() / full.sum()) if full.sum() else 0.0


def garment_groups(entry: dict[str, Any], garment: dict[str, Any]) -> dict[str, Any]:
    """How a marked garment is reported: its type (a baby bodysuit is its own group),
    view, layout, tone, busy background and low contrast (black on black, white on
    white: reported apart and left out of the target)."""
    low = entry.get("low_contrast_all") or garment.get("low_contrast", entry.get("low_contrast", False))
    return {
        "type": garment.get("type", entry.get("type")), "view": garment.get("view", entry.get("view")),
        "layout": garment.get("layout", entry.get("layout")), "tone": garment.get("tone", entry.get("tone")),
        "busy": entry.get("busy_background"), "low_contrast": bool(low),
    }


def align(moving: Photo, fixed: Photo) -> dict[str, float]:
    """How ``fixed`` (a PSD's composite) maps onto ``moving`` (a JPG of the same
    photo): pixel_moving = scale * pixel_fixed + (tx, ty), found by image matching
    (edge maps, a scale search, phase correlation for the offset), with the mean
    difference after alignment and how much of each image the other covers (crop)."""
    import numpy as np
    from PIL import ImageFilter

    def edges(photo: Photo, width: int) -> Any:
        im = Image.open(io.BytesIO(photo.jpeg)).convert("L")
        im = im.resize((width, max(1, round(width * im.height / im.width))), Image.BILINEAR)
        a = np.asarray(im.filter(ImageFilter.FIND_EDGES), dtype=np.float32)
        return (a - a.mean()) / (a.std() + 1e-6)

    work = 400  # working width of the moving image
    m = edges(moving, work)
    best = None
    for scale in np.linspace(0.8, 1.25, 46):
        fw = max(8, round(work * scale * fixed.width / moving.width))
        f = edges(fixed, fw)
        h, w = max(m.shape[0], f.shape[0]), max(m.shape[1], f.shape[1])
        A = np.zeros((h * 2, w * 2), np.float32)
        B = np.zeros_like(A)
        A[:m.shape[0], :m.shape[1]] = m
        B[:f.shape[0], :f.shape[1]] = f
        R = np.fft.fft2(A) * np.conj(np.fft.fft2(B))
        corr = np.real(np.fft.ifft2(R / (np.abs(R) + 1e-6)))
        y, x = np.unravel_index(np.argmax(corr), corr.shape)
        peak = float(corr[y, x])
        if y > A.shape[0] // 2:
            y -= A.shape[0]
        if x > A.shape[1] // 2:
            x -= A.shape[1]
        if best is None or peak > best[0]:
            best = (peak, scale, x, y)
    peak, scale, x, y = best
    k = moving.width / work  # working px -> moving px
    s_ = scale * fixed.width / fixed.width * (moving.width / fixed.width)
    result = {"scale": float(s_), "tx": float(x * k), "ty": float(y * k), "peak": round(peak, 4)}
    # Mean difference after alignment, and the share of each image the other covers.
    mv = np.asarray(Image.open(io.BytesIO(moving.jpeg)).convert("L"), dtype=np.float32)
    fx = Image.open(io.BytesIO(fixed.jpeg)).convert("L")
    warped = fx.transform(mv.shape[::-1], Image.AFFINE, (1 / s_, 0, -result["tx"] / s_, 0, 1 / s_, -result["ty"] / s_),
                          resample=Image.BILINEAR, fillcolor=0)
    covered = np.asarray(fx.point(lambda _: 255).transform(mv.shape[::-1], Image.AFFINE,
                         (1 / s_, 0, -result["tx"] / s_, 0, 1 / s_, -result["ty"] / s_), fillcolor=0)) > 0
    wv = np.asarray(warped, dtype=np.float32)
    result["mean_diff"] = round(float(np.abs(wv - mv)[covered].mean()), 2)
    result["moving_covered"] = round(float(covered.mean()), 4)
    return result


def psd_quad(name: str, entry: dict[str, Any], photo: Photo) -> list[list[float]] | None:
    """The template author's smart object for this photo: its own, or, for a JPG of
    the same photo as a PSD (``psd_twin``), the PSD's smart object mapped through the
    image alignment of the two (``align``; scale, offset and crop)."""
    if photo.smart_quad:
        return photo.smart_quad
    twin = entry.get("psd_twin")
    if not (twin and (EVAL / twin).exists()):
        return None
    psd = decode(EVAL / twin)
    if not psd.smart_quad:
        return None
    key = (photo.name, twin)
    if key not in _ALIGNED:
        _ALIGNED[key] = align(photo, psd)
    a = _ALIGNED[key]
    out = []
    for x, y in psd.smart_quad:
        px, py = a["scale"] * x * psd.width + a["tx"], a["scale"] * y * psd.height + a["ty"]
        out.append([round(px / photo.width, 4), round(py / photo.height, 4)])
    return out


_ALIGNED: dict[tuple[str, str], dict[str, float]] = {}


def apply_fit(quad, fit) -> list[list[float]]:  # noqa: ANN001
    """An area kept between shares ``fit = [a, b]`` of its top edge, aspect and top kept
    (the overflow check's shrink, ``mockup_regions.fit``)."""
    if not fit:
        return quad
    a, b = fit
    (x0, y0), (x1, y1), _, (x3, y3) = quad
    ux, uy, vx, vy = x1 - x0, y1 - y0, x3 - x0, y3 - y0
    k = b - a
    px, py = x0 + ux * a, y0 + uy * a
    pts = [(px, py), (px + ux * k, py + uy * k), (px + ux * k + vx * k, py + uy * k + vy * k), (px + vx * k, py + vy * k)]
    return [[round(x, 4), round(y, 4)] for x, y in pts]


def marked_areas(entry: dict[str, Any], photo: Photo) -> list[list[list[float]]]:
    """The marks: the PSD smart object where the photo has one (the template
    author's own placement), else the area derived from each garment's landmarks
    (shrunk by the overflow check where it ran off the garment: ``fit``)."""
    quad = psd_quad(photo.name, entry, photo)
    if quad and len(entry.get("garments", [])) == 1:
        return [quad]
    return [apply_fit(print_quad(g, photo.width, photo.height, entry), g.get("fit")) for g in entry.get("garments", [])]


# --- drawing ----------------------------------------------------------------------------------


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _image(photo: Photo, longest: int) -> Image.Image:
    im = Image.open(io.BytesIO(photo.jpeg)).convert("RGB")
    im.thumbnail((longest, longest))
    return im


def gridded(photo: Photo, longest: int = 1000) -> Image.Image:
    """The photo with a 0.05 grid and 0.1 labels, for marking by eye."""
    im = _image(photo, longest)
    w, h = im.size
    draw = ImageDraw.Draw(im, "RGBA")
    font = _font(max(11, w // 70))
    for i in range(1, 20):
        t = i / 20
        major = i % 2 == 0
        colour = (255, 0, 160, 150) if major else (0, 200, 255, 90)
        draw.line([(t * w, 0), (t * w, h)], fill=colour, width=1)
        draw.line([(0, t * h), (w, t * h)], fill=colour, width=1)
        if major:
            label = f"{t:.1f}"[1:]
            draw.text((t * w + 2, 2), label, fill=(255, 0, 160, 255), font=font)
            draw.text((2, t * h + 2), label, fill=(255, 0, 160, 255), font=font)
    return im


def _quad(draw: ImageDraw.ImageDraw, quad, w: int, h: int, colour, width: int = 3) -> None:  # noqa: ANN001
    pts = [(x * w, y * h) for x, y in quad]
    draw.line(pts + [pts[0]], fill=colour, width=width)


def overlay(photo: Photo, entry: dict[str, Any] | None, detected=None, longest: int = 700) -> Image.Image:  # noqa: ANN001
    """The photo with the marks (green), their landmarks, the PSD author's smart object
    (blue, dashed look) and a detection (red)."""
    im = _image(photo, longest)
    w, h = im.size
    draw = ImageDraw.Draw(im, "RGBA")
    if photo.smart_quad:
        _quad(draw, photo.smart_quad, w, h, (40, 120, 255, 230), 2)
    for garment in (entry or {}).get("garments", []):
        for key, colour in (("neck", (255, 200, 0)), ("top", (255, 120, 0)), ("bottom", (255, 120, 0))):
            if garment.get(key):
                x, y = garment[key]
                draw.ellipse([x * w - 4, y * h - 4, x * w + 4, y * h + 4], fill=colour + (255,))
        for x, y in garment["armpits"] + garment.get("hem", []):
            draw.ellipse([x * w - 3, y * h - 3, x * w + 3, y * h + 3], outline=(255, 200, 0, 255), width=2)
        _quad(draw, print_quad(garment, photo.width, photo.height, entry), w, h, (0, 230, 90, 255), 3)
    if detected:
        _quad(draw, detected, w, h, (255, 40, 40, 255), 2)
    return im


# --- detection and scores ---------------------------------------------------------------------


@dataclass
class Detection:
    areas: list[list[list[float]]]  # one per garment found (today: one)
    method: str
    confidence: float
    note: str
    blank: Any = None  # the PreparedBlank, for rendering
    seconds: float = 0.0


def detect_garments(photo: Photo) -> Detection:
    """The garment detector (``app.pipeline.garments``) at its analysis size; areas
    normalised to the photo."""
    import time

    from app.pipeline import garments as garments_
    from app.pipeline import mockup_vision as vision

    im = Image.open(io.BytesIO(photo.jpeg)).convert("RGB")
    im.thumbnail((garments_.WORK, garments_.WORK))
    import numpy as np

    rgb = np.asarray(im)
    t = time.perf_counter()
    found = garments_.detect(rgb, vision.default_runner(), garments_.default_saliency(), photo.name)
    seconds = time.perf_counter() - t
    w, h = im.size
    areas = [[[round(x / w, 4), round(y / h, 4)] for x, y in g.corners] for g in found.garments]
    conf = min((g.confidence for g in found.garments), default=0.0)
    method = ",".join(sorted({g.info.get("method", "?") for g in found.garments})) or "none"
    d = Detection(areas or [[[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]], method, conf, "", None, seconds)
    d.found = found  # type: ignore[attr-defined]
    return d


def detect(photo: Photo) -> Detection:
    """Today's detection, exactly as the worker runs it (bundled models, if present)."""
    import time

    t = time.perf_counter()
    blank = mockups.prepare_blank(photo.jpeg, saliency=False)  # the earlier analysis (before this work)
    seconds = time.perf_counter() - t
    return Detection([blank.print_area], blank.method, blank.confidence, blank.note, blank, seconds)


def _mask(quad, size: int = 400) -> Any:  # noqa: ANN001 - normalised quad -> boolean array
    import numpy as np

    im = Image.new("1", (size, size), 0)
    ImageDraw.Draw(im).polygon([(x * size, y * size) for x, y in quad], fill=1)
    return np.asarray(im, dtype=bool)


def iou(a, b, aspect: float) -> float:  # noqa: ANN001
    """Overlap of two quads, measured in photo pixels (``aspect`` = height / width)."""
    sx, sy = (1.0, aspect) if aspect <= 1 else (1 / aspect, 1.0)
    ma, mb = _mask([(x * sx, y * sy) for x, y in a]), _mask([(x * sx, y * sy) for x, y in b])
    union = (ma | mb).sum()
    return float((ma & mb).sum() / union) if union else 0.0


def centre_error(mark, found, width: int, height: int) -> float:  # noqa: ANN001
    """Distance between the centres, as a share of the mark's width."""
    def centre(q):  # noqa: ANN001, ANN202
        return sum(p[0] for p in q) / 4 * width, sum(p[1] for p in q) / 4 * height

    mark_width = math.dist((mark[0][0] * width, mark[0][1] * height), (mark[1][0] * width, mark[1][1] * height))
    return math.dist(centre(mark), centre(found)) / max(mark_width, 1.0)


def score_photo(photo: Photo, entry: dict[str, Any], found: list) -> list[dict[str, Any]]:  # noqa: ANN001
    """Per marked garment: the best-overlapping detected area (each used once)."""
    aspect = photo.height / photo.width
    rows, used = [], set()
    marks_ = marked_areas(entry, photo)
    for index, garment in enumerate(entry.get("garments", [])):
        mark = marks_[index]
        best, best_iou = None, 0.0
        for j, area in enumerate(found):
            if j in used:
                continue
            value = iou(mark, area, aspect)
            if value > best_iou:
                best, best_iou = j, value
        if best is not None:
            used.add(best)
        geom = {}
        if best is not None:
            fq = found[best]
            mw = math.dist((mark[0][0] * photo.width, mark[0][1] * photo.height), (mark[1][0] * photo.width, mark[1][1] * photo.height))
            fw = math.dist((fq[0][0] * photo.width, fq[0][1] * photo.height), (fq[1][0] * photo.width, fq[1][1] * photo.height))
            mcx = sum(p[0] for p in mark) / 4 * photo.width
            fcx = sum(p[0] for p in fq) / 4 * photo.width
            mtop = (mark[0][1] + mark[1][1]) / 2 * photo.height
            ftop = (fq[0][1] + fq[1][1]) / 2 * photo.height
            mh = math.dist((mark[0][0] * photo.width, mark[0][1] * photo.height), (mark[3][0] * photo.width, mark[3][1] * photo.height))
            fh = math.dist((fq[0][0] * photo.width, fq[0][1] * photo.height), (fq[3][0] * photo.width, fq[3][1] * photo.height))
            geom = {"dx": round((fcx - mcx) / mw, 3), "dtop": round((ftop - mtop) / mw, 3),
                    "width_ratio": round(fw / mw, 3), "height_ratio": round(fh / max(mh, 1), 3)}
        rows.append({
            "garment": index, "label": garment.get("label", ""), "iou": round(best_iou, 3), **geom, "match": best,
            "centre_error": round(centre_error(mark, found[best], photo.width, photo.height), 3) if best is not None else None,
            **garment_groups(entry, garment), "same_as": entry.get("same_as"),
        })
    return rows


def summary(rows: list[dict[str, Any]], key: str | None) -> list[tuple[str, int, float, float, float]]:
    """(group, garments, share with IoU >= 0.8, median IoU, median centre error)."""
    import statistics

    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault("all" if key is None else str(row.get(key)), []).append(row)
    out = []
    for name, items in sorted(groups.items()):
        ious = [r["iou"] for r in items]
        errors = [r["centre_error"] for r in items if r["centre_error"] is not None]
        out.append((name, len(items), sum(v >= 0.8 for v in ious) / len(ious), statistics.median(ious),
                    statistics.median(errors) if errors else float("nan")))
    return out


def report(rows: list[dict[str, Any]]) -> None:
    for key in (None, "type", "view", "layout", "tone", "busy"):
        print(f"\n== by {key or 'overall'}: group, garments, IoU>=0.8, median IoU, median centre error (x mark width)")
        for name, n, share, med, err in summary(rows, key):
            print(f"   {name:22} {n:4d}  {share:6.1%}  {med:.2f}  {err:.2f}")


# --- contact sheets ---------------------------------------------------------------------------


PANEL = 320


def _panel(im: Image.Image, caption: str) -> Image.Image:
    im = im.copy()
    im.thumbnail((PANEL, PANEL))
    out = Image.new("RGB", (PANEL, PANEL + 34), (255, 255, 255))
    out.paste(im, ((PANEL - im.width) // 2, (PANEL - im.height) // 2))
    draw = ImageDraw.Draw(out)
    font = _font(11)
    for i, line in enumerate(caption.split("\n")[:2]):
        draw.text((3, PANEL + 3 + i * 14), line[:56], fill=(20, 20, 20), font=font)
    return out


def _placed(detection: Detection) -> Image.Image:
    """A sample design printed with today's detection, through the real engine."""
    from app.pipeline import mockup_sample

    blank = detection.blank
    maps = mockups.build_maps(blank.image, blank.mask, blank.occluder, blank.print_area)
    design = mockups.load_design(mockup_sample.sample(light_ink=maps.underbase >= 0.5))
    jpeg = mockups.render(maps, design, mockups.Settings(blank.print_area))
    return Image.open(io.BytesIO(jpeg)).convert("RGB")


def sheets(per_sheet: int = 20) -> None:
    marks = load_marks()
    target = OUT / "sheets"
    target.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    tiles: list[Image.Image] = []
    names = [p for p in photos() if p.name in marks]
    for number, path in enumerate(names, 1):
        photo = decode(path)
        entry = marks[path.name]
        found = detect(photo)
        scored = score_photo(photo, entry, found.areas)
        rows.extend({"photo": path.name, **r} for r in scored)
        best = max((r["iou"] for r in scored), default=0.0)
        kind = f"{entry.get('type')}, {entry.get('view')}, {entry.get('layout')}"
        garments = len(entry.get("garments", []))
        panels = [
            _panel(_image(photo, 600), f"{number}. {path.name}\n{kind}"),
            _panel(overlay(photo, entry), f"mark: {garments} area{'s' if garments != 1 else ''}"
                   + (", blue = PSD smart object" if photo.smart_quad else "") + "\ngreen = mark, dots = landmarks"),
            _panel(overlay(photo, None, found.areas[0]), f"today: {found.method}, confidence {found.confidence:.2f}\nbest IoU {best:.2f}"),
            _panel(_placed(found), "sample design on today's area\n"),
        ]
        tile = Image.new("RGB", (PANEL * 4 + 12, PANEL + 40), (235, 235, 235))
        for i, p in enumerate(panels):
            tile.paste(p, (i * (PANEL + 3), 3))
        tiles.append(tile)
        print(f"{number:3d} {path.name}: {found.method} {found.confidence:.2f} best IoU {best:.2f} ({found.seconds:.1f}s)", flush=True)
    for start in range(0, len(tiles), per_sheet):
        chunk = tiles[start:start + per_sheet]
        cols = 2
        tw, th = chunk[0].size
        sheet = Image.new("RGB", (cols * tw + 10, ((len(chunk) + cols - 1) // cols) * th + 10), (255, 255, 255))
        for i, tile in enumerate(chunk):
            sheet.paste(tile, (5 + (i % cols) * tw, 5 + (i // cols) * th))
        sheet.save(target / f"sheet-{start // per_sheet + 1}.jpg", quality=88)
    (OUT / "scores-before.json").write_text(json.dumps(rows, indent=1))
    report(rows)


def export(target: Path) -> None:
    """The marks as one self-contained JSON: the rule, every photo's description,
    landmarks, derived print areas and (for a PSD) the author's smart object and
    which composite was read."""
    marks = load_marks()
    raw = json.loads(MARKS.read_text(encoding="utf-8"))
    raw_photos = raw.get("photos", raw)
    out: dict[str, Any] = {
        "rule": {
            "standard": "measured from the 29 PSD smart objects (medians over unique base photos), per garment type and folded/worn: "
                        "width and top offset in chest widths (armpit to armpit), aspect = height / width",
            "ratios": standard(),
            "fallbacks": "back views use their type's front ratios; hoodies a worn sweatshirt's; baby bodysuits a t-shirt's",
            "axis": "centred between the armpits, on the line perpendicular to them; the top is measured below the collar point",
            "limits": "'top' keeps the print below a point (hood, drawstrings); 'bottom' above one (knot, fold, pocket, waistband, a garment lying over it)",
            "coordinates": "normalised to the photo as uploaded (at most 2000 px, EXIF-rotated); quads are TL, TR, BR, BL",
        },
        "skipped": [],
        "photos": {},
    }
    for path in photos():
        entry = marks.get(path.name)
        if entry is None:
            out["skipped"].append(path.name)
            continue
        photo = decode(path)
        record = dict(raw_photos[path.name])
        record["read_from"] = photo.source
        if photo.smart_quad:
            record["psd_smart_object_quad"] = photo.smart_quad
        record["print_areas"] = marked_areas(entry, photo)
        if psd_quad(path.name, entry, photo) and len(entry.get("garments", [])) == 1:
            record["mark_source"] = "PSD smart object" + (f" ({entry['psd_twin']})" if not photo.smart_quad else "")
        else:
            record["mark_source"] = "landmarks + template standard"
        out["photos"][path.name] = record
    target.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(out['photos'])} photos, {sum(len(r['print_areas']) for r in out['photos'].values())} print areas -> {target}")


def zoom(photo: Photo, garment: dict[str, Any], longest: int = 1100) -> Image.Image:
    """One garment's chest, enlarged, with a 0.01 grid in photo coordinates (labels
    every 0.05) and its landmarks as crosshairs: for placing landmarks precisely."""
    full = Image.open(io.BytesIO(photo.jpeg)).convert("RGB")
    w, h = full.size
    (lx, ly), (rx, ry) = garment["armpits"]
    nx, ny = garment["neck"]
    chest = math.dist((lx * w, ly * h), (rx * w, ry * h))
    x0 = max(0, min(lx * w, rx * w, nx * w) - 0.45 * chest)
    x1 = min(w, max(lx * w, rx * w, nx * w) + 0.45 * chest)
    y0 = max(0, ny * h - 0.4 * chest)
    y1 = min(h, max(ly, ry) * h + 0.9 * chest)
    crop = full.crop((int(x0), int(y0), int(x1), int(y1)))
    scale = longest / max(crop.size)
    crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))), Image.LANCZOS)
    draw = ImageDraw.Draw(crop, "RGBA")
    font = _font(14)
    to_px = lambda x, y: ((x * w - x0) * scale, (y * h - y0) * scale)  # noqa: E731
    for i in range(0, 101):
        t = i / 100
        major = i % 5 == 0
        for vertical in (True, False):
            if vertical:
                px, _ = to_px(t, 0)
                if 0 <= px <= crop.width:
                    draw.line([(px, 0), (px, crop.height)], fill=(255, 0, 160, 170) if major else (0, 220, 255, 70), width=1)
                    if major:
                        draw.text((px + 2, 2), f"{t:.2f}", fill=(255, 0, 160, 255), font=font)
            else:
                _, py = to_px(0, t)
                if 0 <= py <= crop.height:
                    draw.line([(0, py), (crop.width, py)], fill=(255, 0, 160, 170) if major else (0, 220, 255, 70), width=1)
                    if major:
                        draw.text((2, py + 2), f"{t:.2f}", fill=(255, 0, 160, 255), font=font)
    for label, (x, y) in (("N", garment["neck"]), ("L", garment["armpits"][0]), ("R", garment["armpits"][1])):
        px, py = to_px(x, y)
        draw.line([(px - 9, py), (px + 9, py)], fill=(255, 220, 0, 255), width=2)
        draw.line([(px, py - 9), (px, py + 9)], fill=(255, 220, 0, 255), width=2)
        draw.text((px + 6, py + 4), label, fill=(255, 220, 0, 255), font=font)
    return crop


def measure() -> dict[str, Any]:
    """The template authors' standard: every PSD smart object in its garment's frame;
    medians per type and per folded/worn over unique base photos (a recoloured copy
    of a base, or a PSD whose photo is also a JPG, counts once), and over all PSDs
    for comparison. Writes /out/psd-ratios.json, which ``print_quad`` reads."""
    import statistics

    marks = load_marks()
    rows = []
    for path in photos():
        if path.suffix.lower() != ".psd" or path.name not in marks:
            continue
        photo = decode(path)
        entry = marks[path.name]
        if not photo.smart_quad or not entry.get("garments"):
            continue
        r = smart_ratios(entry["garments"][0], photo.smart_quad, photo.width, photo.height, entry)
        rows.append({"psd": path.name, "base": entry.get("same_as") or path.name, "type": entry.get("type"),
                     "view": entry.get("view"), "layout": entry.get("layout"),
                     **{k: round(v, 3) for k, v in r.items()}})
    unique = list({r["base"]: r for r in rows}.values())

    def med(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {"n": len(items), **{k: round(statistics.median(r[k] for r in items), 3) for k in ("width", "top", "aspect", "offset")},
                "width_range": [min(r["width"] for r in items), max(r["width"] for r in items)]}

    table: dict[str, dict[str, Any]] = {}
    for scope, chosen in (("unique", unique), ("all files", rows)):
        for kind in ("t-shirt", "sweatshirt", "all"):
            for fold in ("folded", "worn", "any"):
                items = [r for r in chosen if kind in ("all", r["type"]) and fold in ("any", _fold(r["layout"]))]
                if items:
                    table[f"{scope}: {kind} {fold}"] = med(items)
    standard_ = {f"{kind} {fold}": table[f"unique: {kind} {fold}"] for kind in ("t-shirt", "sweatshirt")
                 for fold in ("folded", "worn") if f"unique: {kind} {fold}" in table}
    out = {"psds": rows, "medians": table, "standard": standard_}
    (OUT / "psd-ratios.json").write_text(json.dumps(out, indent=1))
    global _STANDARD
    _STANDARD = None
    for r in rows:
        print(f"  {r['psd']:34} {r['type']:10} {_fold(r['layout']):6} width {r['width']:.3f}  top {r['top']:+.3f}"
              f"  aspect {r['aspect']:.3f}  offset {r['offset']:+.3f}")
    for name, g in table.items():
        print(f"  {name:32} n={g['n']:2d}  width {g['width']:.3f} ({g['width_range'][0]:.2f}-{g['width_range'][1]:.2f})"
              f"  top {g['top']:+.3f}  aspect {g['aspect']:.3f}  offset {g['offset']:+.3f}")
    return out


def mark_panel(photo: Photo, entry: dict[str, Any], name: str, width: int = 900, old=None) -> Image.Image:  # noqa: ANN001
    """One photo for checking the marks: the green areas, every landmark drawn clearly
    (yellow collar point, cyan armpits, orange limits), the PSD author's smart object
    in blue, and the file name large underneath."""
    im = Image.open(io.BytesIO(photo.jpeg)).convert("RGB")
    im.thumbnail((width, width))
    w, h = im.size
    draw = ImageDraw.Draw(im, "RGBA")
    smart = psd_quad(photo.name, entry, photo)
    for quad in old or []:
        _quad(draw, quad, w, h, (120, 120, 120, 255), 2)
    if smart:
        _quad(draw, smart, w, h, (30, 110, 255, 255), 4)
    for garment, quad in zip(entry.get("garments", []), marked_areas(entry, photo)):
        pts = [(x * w, y * h) for x, y in quad]
        draw.polygon(pts, fill=(0, 230, 90, 50))
        _quad(draw, quad, w, h, (0, 200, 70, 255), 4)
        def dot(p, colour, r=7):  # noqa: ANN001, ANN202
            x, y = p[0] * w, p[1] * h
            draw.ellipse([x - r, y - r, x + r, y + r], fill=colour, outline=(0, 0, 0, 255), width=2)
        for key in ("top", "bottom"):
            if garment.get(key):
                dot(garment[key], (255, 140, 0, 255), 6)
        for p in garment["armpits"]:
            dot(p, (0, 220, 255, 255))
        dot(garment["neck"], (255, 225, 0, 255), 8)
    out = Image.new("RGB", (width, h + 56), (255, 255, 255))
    out.paste(im, ((width - w) // 2, 0))
    d = ImageDraw.Draw(out)
    d.text((8, h + 6), name, fill=(0, 0, 0), font=_font(26))
    garments = len(entry.get("garments", []))
    extra = f"{garments} area{'s' if garments != 1 else ''}" + ("   blue = PSD smart object (= the mark)" if smart else "") \
        + ("   grey = previous mark" if old else "")
    d.text((8, h + 34), extra, fill=(90, 90, 90), font=_font(16))
    return out


def check_sheets(per_sheet: int = 10) -> list[Path]:
    marks = load_marks()
    target = OUT / "check-sheets"
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob("*.jpg"):
        old.unlink()
    panels = []
    for number, path in enumerate([p for p in photos() if p.name in marks], 1):
        panels.append(mark_panel(decode(path), marks[path.name], f"{number}. {path.name}"))
    written = []
    for start in range(0, len(panels), per_sheet):
        chunk = panels[start:start + per_sheet]
        cell_w = max(p.width for p in chunk)
        rows = [chunk[i:i + 2] for i in range(0, len(chunk), 2)]
        heights = [max(p.height for p in row) for row in rows]
        sheet = Image.new("RGB", (2 * cell_w + 30, sum(heights) + 10 * (len(rows) + 1)), (225, 225, 225))
        y = 10
        for row, rh in zip(rows, heights):
            for i, panel in enumerate(row):
                sheet.paste(panel, (10 + i * (cell_w + 10), y))
            y += rh + 10
        out = target / f"marks-{start // per_sheet + 1:02d}.jpg"
        sheet.save(out, quality=88)
        written.append(out)
    return written


def moved(previous: Path, target: Path) -> None:
    """Every photo whose mark moved since the previous marks: the area centre and the
    landmarks, as a share of chest width; garments added or dropped are listed too."""
    old = json.loads(previous.read_text(encoding="utf-8"))
    old = old.get("photos", old)
    for name, entry in list(old.items()):
        if "same_as" in entry:
            old[name] = {**old[entry["same_as"]], **entry}
    new = load_marks()
    lines = ["How far each mark moved (share of that garment's chest width).",
             "area = the print area's centre, before (first marks, first rule) and after (new marks, template ratios);",
             "re-marking alone = the old landmarks through today's rule against the new ones: what the re-marking moved;",
             "collar / armpits = the landmarks themselves.", ""]
    for path in photos():
        if path.name not in new:
            continue
        photo = decode(path)
        a = old.get(path.name, {}).get("garments", [])
        b = new[path.name].get("garments", [])
        rows = []
        used = set()
        # Pair old and new garments closest-first (by collar distance in chest widths).
        pairs: dict[int, tuple[int, float]] = {}
        dists = sorted(
            (math.dist((og["neck"][0] * photo.width, og["neck"][1] * photo.height),
                       (g["neck"][0] * photo.width, g["neck"][1] * photo.height)) / _frame(g, photo.width, photo.height)[3], j, i)
            for j, g in enumerate(b) for i, og in enumerate(a))
        taken_old: set[int] = set()

        def key(g: dict[str, Any]) -> str:  # "row 3, olive, knotted" -> "row 3, olive"
            import re

            return ",".join(part.strip() for part in re.split(r"[,;(]", g.get("label", ""))[:2]).lower()

        for j, g in enumerate(b):  # the same garment by its label first
            for i, og in enumerate(a):
                if key(g) and key(g) == key(og) and i not in taken_old:
                    d = math.dist((og["neck"][0] * photo.width, og["neck"][1] * photo.height),
                                  (g["neck"][0] * photo.width, g["neck"][1] * photo.height)) / _frame(g, photo.width, photo.height)[3]
                    pairs[j] = (i, d)
                    taken_old.add(i)
                    break
        for d, j, i in dists:
            if j not in pairs and i not in taken_old and d <= 0.6:
                pairs[j] = (i, d)
                taken_old.add(i)
        for j, g in enumerate(b):
            ex, ey, origin, chest = _frame(g, photo.width, photo.height)
            q = print_quad(g, photo.width, photo.height, new[path.name])
            cx, cy = sum(p[0] for p in q) / 4 * photo.width, sum(p[1] for p in q) / 4 * photo.height
            best, best_d = pairs.get(j, (None, None))
            if best is None:
                rows.append(f"   garment {j + 1} ({g.get('label', 'single')}): new")
                continue
            used.add(best)
            og = a[best]
            oq = old[path.name]["print_areas"][best]  # as the previous marks file derived it
            ocx, ocy = sum(p[0] for p in oq) / 4 * photo.width, sum(p[1] for p in oq) / 4 * photo.height
            arm = max(math.dist((og["armpits"][k][0] * photo.width, og["armpits"][k][1] * photo.height),
                                (g["armpits"][k][0] * photo.width, g["armpits"][k][1] * photo.height)) for k in (0, 1)) / chest
            # The same old landmarks through today's rule: what the re-marking alone moved.
            lq = print_quad({k: v for k, v in og.items() if k != "hem"}, photo.width, photo.height, new[path.name])
            lcx, lcy = sum(p[0] for p in lq) / 4 * photo.width, sum(p[1] for p in lq) / 4 * photo.height
            rows.append(f"   garment {j + 1} ({g.get('label', 'single')}): area {math.dist((cx, cy), (ocx, ocy)) / chest:.2f}"
                        f"  (re-marking alone {math.dist((cx, cy), (lcx, lcy)) / chest:.2f})"
                        f"  collar {best_d:.2f}  armpits {arm:.2f}")
        for i, og in enumerate(a):
            if i not in used:
                rows.append(f"   dropped: {og.get('label', 'garment')}")
        if rows:
            lines.append(path.name)
            lines.extend(rows)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def changed(previous: Path) -> None:
    """Every photo whose mark changed against ``previous`` (an exported marks file):
    sheets of those photos only (new mark green, old mark grey, PSD smart object
    blue) and marks-moved-2.txt saying what changed and why."""
    old = json.loads(previous.read_text(encoding="utf-8"))["photos"]
    marks = load_marks()
    target = OUT / "changed-sheets"
    target.mkdir(parents=True, exist_ok=True)
    for f in target.glob("*.jpg"):
        f.unlink()
    lines = ["What changed in each mark since the previous marks, and why (moves in chest widths).", ""]
    counts: dict[str, int] = {}
    panels = []
    for number, path in enumerate([p for p in photos() if p.name in marks], 1):
        entry = marks[path.name]
        photo = decode(path)
        new_areas = marked_areas(entry, photo)
        old_areas = old.get(path.name, {}).get("print_areas", [])
        aspect = photo.height / photo.width
        smart = psd_quad(path.name, entry, photo)
        reasons_photo = []
        for j, (garment, quad) in enumerate(zip(entry.get("garments", []), new_areas)):
            prev = old_areas[j] if j < len(old_areas) else None
            same = prev is not None and iou(prev, quad, aspect) > 0.985
            if same:
                continue
            chest = _frame(garment, photo.width, photo.height, entry)[3]
            why = []
            if smart and len(new_areas) == 1:
                why.append("PSD smart object is the mark")
            else:
                _, info = print_area(garment, photo.width, photo.height, entry)
                if info.get("angle", 0) == 0 and prev and abs(_tilt(prev, photo)) > 0.5:
                    why.append(f"no rotation (was {_tilt(prev, photo):+.1f} deg; "
                               + ("worn/hanging, axis within 6 deg" if entry.get("layout") in UPRIGHT_LAYOUTS else "axis within 6 deg") + ")")
                if info.get("shrunk"):
                    why.append(f"stops above its lower edge ({garment.get('bottom_note', 'limit')}) and shrinks keeping the aspect (x{info['shrunk']:.2f})")
                if info.get("worn_cap"):
                    why.append(f"worn cap: bottom was {info['worn_cap']:.2f} chest widths below the collar, shortened to {WORN_BOTTOM_MAX}")
                if garment.get("v3_note"):
                    why.append(garment["v3_note"])
                if not why:
                    why.append("template ratios re-measured in the upright frame")
            for w_ in why:
                counts[w_.split(" (")[0]] = counts.get(w_.split(" (")[0], 0) + 1
            move = ""
            if prev:
                c = lambda q: (sum(p[0] for p in q) / 4 * photo.width, sum(p[1] for p in q) / 4 * photo.height)  # noqa: E731
                move = f"centre moved {math.dist(c(prev), c(quad)) / chest:.2f}, IoU with previous {iou(prev, quad, aspect):.2f}; "
            reasons_photo.append(f"   area {j + 1} ({garment.get('label', 'single')}): {move}" + "; ".join(why))
        if reasons_photo:
            lines.append(f"{number}. {path.name}")
            lines.extend(reasons_photo)
            panels.append(mark_panel(photo, entry, f"{number}. {path.name}", old=old_areas))
    lines += ["", "Totals (areas changed, by reason):"] + [f"   {v:3d}  {k}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
    (OUT / "marks-moved-2.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for start in range(0, len(panels), 10):
        chunk = panels[start:start + 10]
        cell_w = max(p.width for p in chunk)
        rows = [chunk[i:i + 2] for i in range(0, len(chunk), 2)]
        heights = [max(p.height for p in row) for row in rows]
        sheet = Image.new("RGB", (2 * cell_w + 30, sum(heights) + 10 * (len(rows) + 1)), (225, 225, 225))
        y = 10
        for row, rh in zip(rows, heights):
            for i, panel in enumerate(row):
                sheet.paste(panel, (10 + i * (cell_w + 10), y))
            y += rh + 10
        sheet.save(target / f"changed-{start // 10 + 1:02d}.jpg", quality=88)
    print(f"{len(panels)} photos changed")


def _tilt(quad, photo: Photo) -> float:  # noqa: ANN001
    (x0, y0), (x1, y1) = quad[0], quad[1]
    return math.degrees(math.atan2((y1 - y0) * photo.height, (x1 - x0) * photo.width))


def cap_report() -> None:
    """How many worn tee/sweatshirt areas a bottom cap would shorten, for several caps."""
    marks = load_marks()
    rows = []
    for path in photos():
        entry = marks.get(path.name)
        if not entry or entry.get("layout") not in UPRIGHT_LAYOUTS or path.name.lower().endswith(".psd"):
            continue
        photo = decode(path)
        if psd_quad(path.name, entry, photo):
            continue
        for g in entry.get("garments", []):
            if g.get("type", entry.get("type")) not in ("t-shirt", "sweatshirt"):
                continue
            ex, ey, origin, chest = _frame(g, photo.width, photo.height, entry)
            q = print_quad(g, photo.width, photo.height, entry)
            bottom = max((y * photo.height - origin[1]) for _, y in q) / chest
            rows.append((path.name, g.get("label", ""), round(bottom, 3)))
    for name, label, b in rows:
        print(f"   {name:28} {label[:30]:30} bottom {b:.2f} chest widths below the collar")
    for cap in (1.1, 1.0, 0.95, 0.9, 0.85):
        print(f"   cap {cap}: {sum(b > cap for *_, b in rows)} of {len(rows)} worn areas would be shortened")


def zoom_area(photo: Photo, entry: dict[str, Any], index: int, longest: int = 1000) -> Image.Image:
    """One garment's print area and what lies below it, with a 0.01 grid (labels every
    0.05) in photo coordinates: for placing the garment's lower edge."""
    full = Image.open(io.BytesIO(photo.jpeg)).convert("RGB")
    w, h = full.size
    garment = entry["garments"][index]
    quad = marked_areas(entry, photo)[index]
    chest = _frame(garment, w, h, entry)[3]
    xs, ys = [x * w for x, _ in quad], [y * h for _, y in quad]
    x0, x1 = max(0, min(xs) - 0.45 * chest), min(w, max(xs) + 0.45 * chest)
    y0, y1 = max(0, min(ys) - 0.35 * chest), min(h, max(ys) + 0.6 * chest)
    crop = full.crop((int(x0), int(y0), int(x1), int(y1)))
    scale = longest / max(crop.size)
    crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))), Image.LANCZOS)
    draw = ImageDraw.Draw(crop, "RGBA")
    font = _font(14)
    to_px = lambda x, y: ((x * w - x0) * scale, (y * h - y0) * scale)  # noqa: E731
    for i in range(0, 101):
        t = i / 100
        major = i % 5 == 0
        px_, _ = to_px(t, 0)
        _, py_ = to_px(0, t)
        colour = (255, 0, 160, 150) if major else (0, 220, 255, 55)
        if 0 <= px_ <= crop.width:
            draw.line([(px_, 0), (px_, crop.height)], fill=colour, width=1)
            if major:
                draw.text((px_ + 2, 2), f"{t:.2f}", fill=(255, 0, 160, 255), font=font)
        if 0 <= py_ <= crop.height:
            draw.line([(0, py_), (crop.width, py_)], fill=colour, width=1)
            if major:
                draw.text((2, py_ + 2), f"{t:.2f}", fill=(255, 0, 160, 255), font=font)
    pts = [to_px(x, y) for x, y in quad]
    draw.line(pts + [pts[0]], fill=(0, 230, 90, 255), width=3)
    if garment.get("bottom"):
        bx, by = to_px(*garment["bottom"])
        draw.ellipse([bx - 6, by - 6, bx + 6, by + 6], fill=(255, 140, 0, 255))
    return crop


def check_twins() -> None:
    """Align every JPG to the PSD of the same photo, map the smart object through it and
    save an overlay (JPG left half, PSD composite warped onto it right half, the mapped
    smart object on both) to /out/twins/; print the alignment."""
    marks = load_marks()
    target = OUT / "twins"
    target.mkdir(parents=True, exist_ok=True)
    for path in photos():
        entry = marks.get(path.name)
        if not entry or not entry.get("psd_twin") or path.suffix.lower() == ".psd":
            continue
        photo, psd = decode(path), decode(EVAL / entry["psd_twin"])
        quad = psd_quad(path.name, entry, photo)
        a = _ALIGNED[(path.name, entry["psd_twin"])]
        jpg = Image.open(io.BytesIO(photo.jpeg)).convert("RGB")
        warped = Image.open(io.BytesIO(psd.jpeg)).convert("RGB").transform(
            jpg.size, Image.AFFINE, (1 / a["scale"], 0, -a["tx"] / a["scale"], 0, 1 / a["scale"], -a["ty"] / a["scale"]))
        half = jpg.width // 2
        both = jpg.copy()
        both.paste(warped.crop((half, 0, jpg.width, jpg.height)), (half, 0))
        diff = Image.blend(jpg, warped, 0.5)
        out = Image.new("RGB", (jpg.width * 2, jpg.height), (255, 255, 255))
        out.paste(both, (0, 0))
        out.paste(diff, (jpg.width, 0))
        d = ImageDraw.Draw(out)
        for ox in (0, jpg.width):
            pts = [(ox + x * jpg.width, y * jpg.height) for x, y in quad]
            d.line(pts + [pts[0]], fill=(30, 110, 255), width=6)
        d.line([(half, 0), (half, jpg.height)], fill=(255, 0, 0), width=3)
        out.thumbnail((1800, 1800))
        out.save(target / f"{path.stem}.jpg", quality=85)
        tilt = math.degrees(math.atan2((quad[1][1] - quad[0][1]) * photo.height, (quad[1][0] - quad[0][0]) * photo.width))
        print(f"{path.name} <- {entry['psd_twin']}: scale {a['scale']:.4f} offset ({a['tx']:+.1f}, {a['ty']:+.1f}) px, "
              f"peak {a['peak']}, mean diff {a['mean_diff']} (of 255), JPG covered {a['moving_covered']:.1%}; "
              f"smart object top edge {tilt:+.2f} deg")


def overflow(limit: float = 0.02, only: list[str] | None = None) -> None:
    """Every area against its garment's visible region (``mockup_regions``): the share
    of its pixels outside, before and after fitting. Writes /out/overflow-check.txt,
    /out/overflow-fits.json (the fits to store with the marks) and the masks used
    (/out/overflow-masks/)."""
    from evals import mockup_regions as regions_

    marks = load_marks()
    raw = json.loads(MARKS.read_text(encoding="utf-8"))
    raw = raw.get("photos", raw)
    target = OUT / "overflow-masks"
    target.mkdir(parents=True, exist_ok=True)
    lines = ["Share of each area outside its garment's visible region (limit 2%).",
             "before = the marks as they stood; after = fitted (shrunk from the offending side, aspect and top kept).",
             "PSD smart objects are never changed: flagged only.", ""]
    fits: dict[str, dict[str, Any]] = {}
    for number, path in enumerate([p for p in photos() if p.name in marks], 1):
        if only and path.name not in only:
            continue
        entry = marks[path.name]
        photo = decode(path)
        base = raw[path.name].get("same_as") or path.name
        if base != path.name and not path.name.lower().endswith(".psd") and not base.lower().endswith(".psd") \
                and base in fits:
            lines.append(f"{number}. {path.name}: same photo as {base}, same result")
            continue
        frame = lambda g: _frame(g, photo.width, photo.height, entry)  # noqa: E731
        data = regions_.regions(photo, entry, frame, OUT / "masks")
        is_psd = bool(psd_quad(path.name, entry, photo)) and len(entry.get("garments", [])) == 1
        before_areas = [print_quad(g, photo.width, photo.height, entry) for g in entry.get("garments", [])]
        if is_psd:
            before_areas = marked_areas(entry, photo)
        after_areas = []
        rows = []
        for i, (g, quad) in enumerate(zip(entry.get("garments", []), before_areas)):
            share = regions_.outside_share(quad, data["allowed"][i])
            review = g.get("overflow_review") or entry.get("overflow_review")
            if review and share > limit:  # looked at: the mask is wrong here, not the area
                after_areas.append(quad if not g.get("fit") else apply_fit(quad, g["fit"]))
                rows.append(f"   area {i + 1} ({g.get('label', 'single')}): {share:.1%}  not changed: {review}")
                continue
            if is_psd or share <= limit:
                after_areas.append(quad)
                flag = "  PSD smart object over the limit: flagged, not changed" if is_psd and share > limit else ""
                rows.append(f"   area {i + 1} ({g.get('label', 'single')}): {share:.1%}{flag}")
                continue
            side = regions_.offending_side(quad, data["allowed"][i])
            fitted, ab, after = regions_.fit(quad, data["allowed"][i], limit)
            after_areas.append(fitted)
            fits.setdefault(base, {})[str(i)] = ab
            note = "" if after <= limit else "  (stopped at 30% of the width: check the mask)"
            rows.append(f"   area {i + 1} ({g.get('label', 'single')}): {share:.1%} -> {after:.1%}, shrunk from the {side}"
                        f" (keeps {ab[1] - ab[0]:.0%} of its width){note}")
        fits.setdefault(base, fits.get(base, {}))
        lines.append(f"{number}. {path.name}" + ("  [PSD smart object]" if is_psd else ""))
        lines.extend(rows)
        regions_.overlay(photo, data, after_areas, before_areas).save(target / f"{path.stem}.jpg", quality=85)
        print(f"{number:3d} {path.name}", flush=True)
    (OUT / "overflow-check.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "overflow-fits.json").write_text(json.dumps({k: v for k, v in fits.items() if v}, indent=1), encoding="utf-8")


def collar_or_hood(found_area, entry: dict[str, Any], photo: Photo) -> bool:  # noqa: ANN001
    """Whether a detected area reaches the collar (above the marked collar point) or, on a
    hoodie, the hood (above the marked ``top`` point) of the marked garment it lies on
    (the one whose chest contains the area's centre)."""
    w, h = photo.width, photo.height
    top_y = min(found_area[0][1], found_area[1][1]) * h
    cx = sum(p[0] for p in found_area) / 4 * w
    cy = sum(p[1] for p in found_area) / 4 * h
    best = None
    for g in entry.get("garments", []):
        ex, ey, origin, chest = _frame(g, w, h, entry)
        neck_y = g["neck"][1] * h
        if abs(cx - origin[0]) <= chest / 2 and neck_y < cy < neck_y + 1.5 * chest:
            d = abs(cx - origin[0])
            if best is None or d < best[0]:
                best = (d, g)
    if best is None:
        return False
    g = best[1]
    limit = g["neck"][1] * h
    if g.get("top"):
        limit = max(limit, g["top"][1] * h)
    return bool(top_y < limit)


def score(only: list[str] | None = None, before: bool = False) -> None:
    """The garment detector against the confirmed marks: per area and per photo IoU and
    centre error, by garment type, view, layout and overall; unique photos (identical
    files, JPG/PSD pairs and recoloured copies counted once) and all files; black on
    black / white on white apart. Writes /out/scores-{after,before}.json, /out/report-{after,before}.txt
    and sheets (/out/score-sheets[-before]/: green = mark, red = detection)."""
    import statistics

    marks = load_marks()
    raw = json.loads(MARKS.read_text(encoding="utf-8"))
    raw = raw.get("photos", raw)
    rows: list[dict[str, Any]] = []
    target = OUT / ("score-sheets-before" if before else "score-sheets")
    target.mkdir(parents=True, exist_ok=True)
    panels = []
    seconds = []
    for number, path in enumerate([p for p in photos() if p.name in marks], 1):
        if only and path.name not in only:
            continue
        entry = marks[path.name]
        photo = decode(path)
        found = detect(photo) if before else detect_garments(photo)
        if before:
            found.found = type("F", (), {"garments": []})()  # type: ignore[attr-defined]
        seconds.append(found.seconds)
        scored = score_photo(photo, entry, found.areas)
        base = raw[path.name].get("same_as") or path.name
        touching = int(sum(bool(collar_or_hood(a, entry, photo)) for a in found.areas))
        confs = [g.confidence for g in found.found.garments] if found.found.garments else []
        for r in scored:
            conf = confs[r["match"]] if r.get("match") is not None and r["match"] < len(confs) else 0.0
            rows.append({"photo": path.name, "base": base, "found": len(found.areas), "marked": len(scored),
                         "collar_or_hood": touching, "confidence": round(float(conf), 3), **r})
        mean = statistics.mean([r["iou"] for r in scored]) if scored else 0.0
        panel = overlay(photo, entry, None, 520)
        pw, ph = panel.size
        import numpy as np

        tints = [(255, 60, 60), (60, 140, 255), (255, 200, 0), (190, 80, 255), (0, 210, 210), (255, 130, 0)]
        arr = np.asarray(panel, np.float32)
        for gi, g in enumerate(found.found.garments):
            m = np.asarray(Image.fromarray(g.region.astype(np.uint8) * 255).resize((pw, ph))) > 127
            edge = m ^ np.roll(m, 2, 0) | m ^ np.roll(m, 2, 1)
            arr[edge] = tints[gi % len(tints)]
        panel = Image.fromarray(arr.astype(np.uint8))
        d = ImageDraw.Draw(panel, "RGBA")
        for a in found.areas:
            _quad(d, a, pw, ph, (255, 40, 40, 255), 3)
        canvas = Image.new("RGB", (pw, ph + 40), (255, 255, 255))
        canvas.paste(panel, (0, 0))
        ImageDraw.Draw(canvas).text((4, ph + 4), f"{number}. {path.name}  IoU {mean:.2f}  found {len(found.areas)}/{len(scored)}"
                                    f"  {found.method} {found.confidence:.2f}", fill=(0, 0, 0), font=_font(14))
        panels.append(canvas)
        print(f"{number:3d} {path.name}: mean IoU {mean:.2f}, found {len(found.areas)} of {len(scored)}, "
              f"{found.method} {found.confidence:.2f}, {found.seconds:.1f}s", flush=True)
    for start in range(0, len(panels), 12):
        chunk = panels[start:start + 12]
        cw, chh = max(p.width for p in chunk), max(p.height for p in chunk)
        sheet = Image.new("RGB", (3 * cw + 20, ((len(chunk) + 2) // 3) * chh + 10), (230, 230, 230))
        for i, p in enumerate(chunk):
            sheet.paste(p, (5 + (i % 3) * cw, 5 + (i // 3) * chh))
        sheet.save(target / f"score-{start // 12 + 1:02d}.jpg", quality=85)
    tag = "before" if before else "after"
    (OUT / f"scores-{tag}.json").write_text(json.dumps(rows, indent=1))
    lines = [f"Detection time per template: median {statistics.median(seconds):.1f}s, max {max(seconds):.1f}s"] if seconds else []
    lines += table(rows)
    (OUT / f"report-{tag}.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def table(rows: list[dict[str, Any]]) -> list[str]:
    """The accuracy tables: unique photos first (each base photo once; areas weighed
    equally inside a photo), then all files; black-on-black / white-on-white apart."""
    import statistics

    def summarise(items: list[dict[str, Any]], key: str | None) -> list[str]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in items:
            groups.setdefault("all" if key is None else str(r.get(key)), []).append(r)
        out = []
        for name, its in sorted(groups.items()):
            ious = [r["iou"] for r in its]
            errs = [r["centre_error"] for r in its if r["centre_error"] is not None]
            out.append(f"   {name:22} areas {len(its):4d}  IoU>=0.8 {sum(v >= 0.8 for v in ious) / len(ious):6.1%}"
                       f"  median IoU {statistics.median(ious):.2f}  median centre error {statistics.median(errs) if errs else float('nan'):.2f}")
        return out

    def per_photo(items: list[dict[str, Any]]) -> list[str]:
        photos_: dict[str, list[float]] = {}
        for r in items:
            photos_.setdefault(r["photo"], []).append(r["iou"])
        means = [statistics.mean(v) for v in photos_.values()]
        allp = [min(v) for v in photos_.values()]
        return [f"   photos {len(means)}: mean IoU >= 0.8 on {sum(m >= 0.8 for m in means) / max(1, len(means)):.1%};"
                f" every area >= 0.8 on {sum(m >= 0.8 for m in allp) / max(1, len(allp)):.1%}"]

    unique = []
    for r in rows:
        if r["photo"] == r["base"] or r["base"] not in {x["photo"] for x in rows}:
            unique.append(r)
    target = [r for r in unique if not r.get("low_contrast")]
    low = [r for r in unique if r.get("low_contrast")]
    lines = ["== UNIQUE PHOTOS (target set: black-on-black and white-on-white left out)"]
    lines += per_photo(target)
    for key in (None, "type", "view", "layout", "busy", "tone"):
        lines.append(f"-- by {key or 'overall'}")
        lines += summarise(target, key)
    for key in ("dx", "dtop", "width_ratio", "height_ratio"):
        vals = [r[key] for r in target if key in r]
        if vals:
            q = statistics.quantiles(vals, n=4)
            lines.append(f"-- {key:13} median {statistics.median(vals):+.3f}  IQR {q[0]:+.3f}..{q[2]:+.3f}  (x mark width; ratios detected/marked)")
    for lay in ("on a person", "several garments", "folded", "flat-lay"):
        sub = [r for r in target if r.get("layout") == lay and "dtop" in r]
        if sub:
            lines.append(f"   {lay:17} dtop median {statistics.median([r['dtop'] for r in sub]):+.3f}  width ratio {statistics.median([r['width_ratio'] for r in sub]):.3f}"
                         f"  height ratio {statistics.median([r['height_ratio'] for r in sub]):.3f}  |dx| {statistics.median([abs(r['dx']) for r in sub]):.3f}")
    touching = {r["photo"] for r in target if r.get("collar_or_hood")}
    lines.append(f"-- areas reaching the collar or hood: photos {len(touching)}" + (f": {sorted(touching)}" if touching else ""))
    if low:
        lines.append("== BLACK ON BLACK / WHITE ON WHITE (reported apart)")
        lines += per_photo(low) + summarise(low, None)
    lines.append("== ALL FILES")
    lines += per_photo(rows) + summarise(rows, None)
    return lines


def calibrate() -> None:
    """The detector's constants from the confirmed marks (unique photos, black on black
    and white on white left out): for a person, the marked chest, armpit drop and collar
    rise against the pose's shoulder span; for a flat garment, the marked collar below
    the top of the detected region and the marked chest against the region's."""
    import statistics

    marks = load_marks()
    raw = json.loads(MARKS.read_text(encoding="utf-8"))
    raw = raw.get("photos", raw)
    worn: dict[str, list[float]] = {"chest_per_span": [], "armpit_drop": [], "collar_rise": [], "axis_offset": []}
    flat: dict[str, list[float]] = {}
    for path in photos():
        entry = marks.get(path.name)
        if not entry or raw[path.name].get("same_as") or entry.get("low_contrast_all"):
            continue
        photo = decode(path)
        found = detect_garments(photo).found
        im_w, im_h = (lambda im: im.size)(Image.open(io.BytesIO(photo.jpeg)).convert("RGB").resize(
            (round(photo.width * min(1, 1024 / max(photo.width, photo.height))), round(photo.height * min(1, 1024 / max(photo.width, photo.height))))))
        k = photo.width / im_w
        for g in found.garments:
            best, best_d = None, None
            for mg in entry.get("garments", []):
                ex, ey, origin, chest = _frame(mg, photo.width, photo.height, entry)
                nx, ny = mg["neck"][0] * photo.width, mg["neck"][1] * photo.height
                d = math.dist((nx, ny), (g.neck[0] * k, g.neck[1] * k)) / chest
                if best_d is None or d < best_d:
                    best, best_d = mg, d
            if best is None or best_d > 0.8:
                continue
            ex, ey, origin, chest = _frame(best, photo.width, photo.height, entry)
            (lx, ly), (rx, ry) = [(p[0] * photo.width, p[1] * photo.height) for p in best["armpits"]]
            nx, ny = best["neck"][0] * photo.width, best["neck"][1] * photo.height
            if g.info.get("method") == "pose":
                (sl, sr) = g.info["shoulders"]
                sl, sr = (sl[0] * k, sl[1] * k), (sr[0] * k, sr[1] * k)
                span = math.dist(sl, sr)
                mid = ((sl[0] + sr[0]) / 2, (sl[1] + sr[1]) / 2)
                worn["chest_per_span"].append(chest / span)
                worn["armpit_drop"].append(((ly + ry) / 2 - mid[1]) / span)
                worn["collar_rise"].append((mid[1] - ny) / span)
                worn["axis_offset"].append(((lx + rx) / 2 - mid[0]) / span)
                worn.setdefault("neck_below_region_top", []).append((ny - g.neck[1] * k) / chest)
                worn.setdefault("chest_found_ratio", []).append(math.dist(*[(p[0] * k, p[1] * k) for p in g.armpits]) / chest)
            else:
                layout = "folded" if entry.get("layout") == "folded" else "flat"
                top = g.info["top_point"]
                top = (top[0] * k, top[1] * k)
                drop = (ny - top[1]) * ey[1] + (nx - top[0]) * ey[0]
                flat.setdefault(f"collar_drop {layout}", []).append(drop / chest)
                flat.setdefault(f"chest_ratio {layout}", []).append(g.info["region_chest"] * k / chest)
                if g.info.get("neckline"):
                    nl = (g.info["neckline"][0] * k, g.info["neckline"][1] * k)
                    flat.setdefault(f"neck_below_neckline {layout}", []).append(((ny - nl[1]) * ey[1] + (nx - nl[0]) * ey[0]) / chest)
                flat.setdefault(f"found_as {g.info.get('layout_found')} (marked {layout})", []).append(1.0)
                print(f"   LAYOUT {layout:6} sleeve_ratio {g.info.get('sleeve_ratio', 0):.2f} fill {g.info.get('fill', 0):.2f} aspect {g.info.get('aspect', 0):.2f}  {path.name}")
    for name, vals in list(worn.items()) + list(flat.items()):
        if vals:
            q = statistics.quantiles(vals, n=4) if len(vals) > 3 else [min(vals), statistics.median(vals), max(vals)]
            print(f"   {name:22} n={len(vals):3d}  median {statistics.median(vals):+.3f}  IQR {q[0]:+.3f}..{q[-1]:+.3f}")


def occlusion() -> None:
    """The detected occluder against the PSDs' own ground truth: in a PSD, the pixel
    layers drawn above the design's smart object (hair, a sleeve, a cuff, a hand) are
    exactly what must sit in front of the print. Inside the smart object's quad: the
    share of those layers' pixels the detector puts in front of the design (recall) and
    the share of the rest it wrongly puts in front (false occlusion). Writes
    /out/occlusion-check.txt and overlays in /out/occlusion/."""
    import numpy as np
    from psd_tools import PSDImage

    target = OUT / "occlusion"
    target.mkdir(parents=True, exist_ok=True)
    lines = ["Detected occluder vs the PSD layers above the design's smart object (inside the smart object's quad).",
             "recall = share of the layers' pixels put in front of the print; false = share of other pixels put in front.", ""]
    totals = []
    for path in photos():
        if path.suffix.lower() != ".psd":
            continue
        psd = PSDImage.open(path)
        layers = list(psd.descendants())
        smart = [i for i, layer in enumerate(layers) if layer.kind == "smartobject" and layer.visible]
        if not smart:
            continue
        above = [layer for layer in layers[smart[0] + 1:] if layer.is_visible() and layer.kind == "pixel" and layer.bbox != (0, 0, 0, 0)]
        if not above:
            continue
        truth = np.zeros((psd.height, psd.width), np.float32)
        for layer in above:
            im = layer.topil()
            if im is None:
                continue
            alpha = np.asarray(im.convert("RGBA"))[..., 3].astype(np.float32) / 255
            x0, y0 = layer.left, layer.top
            h, w = alpha.shape
            ys, xs = slice(max(0, y0), min(psd.height, y0 + h)), slice(max(0, x0), min(psd.width, x0 + w))
            truth[ys, xs] = np.maximum(truth[ys, xs], alpha[ys.start - y0: ys.stop - y0, xs.start - x0: xs.stop - x0])
        photo = decode(path)
        found = detect_garments(photo).found
        occ = Image.fromarray((np.clip(found.occluder, 0, 1) * 255).astype(np.uint8)).resize((psd.width, psd.height))
        occ = np.asarray(occ, np.float32) / 255
        quad = [(x * psd.width, y * psd.height) for x, y in photo.smart_quad]
        inside = np.asarray(_poly_mask(quad, psd.width, psd.height))
        gt = (truth > 0.5) & inside
        rest = (truth <= 0.5) & inside
        if gt.sum() < 50:
            continue
        recall = float(((occ > 0.5) & gt).sum() / gt.sum())
        false = float(((occ > 0.5) & rest).sum() / max(1, rest.sum()))
        names = ", ".join(layer.name for layer in above)
        totals.append((recall, false))
        lines.append(f"{path.name:34} layers above: {names[:40]:40} truth {gt.sum() / inside.sum():5.1%} of the area  "
                     f"recall {recall:5.1%}  false {false:5.1%}")
        base = np.asarray(Image.open(io.BytesIO(photo.jpeg)).convert("RGB").resize((psd.width, psd.height)), np.float32)
        view = base * 0.6
        view[gt & (occ > 0.5)] = [0, 200, 80]
        view[gt & (occ <= 0.5)] = [255, 60, 60]
        view[rest & (occ > 0.5)] = [60, 120, 255]
        out = Image.fromarray(view.astype(np.uint8))
        out.thumbnail((900, 900))
        out.save(target / f"{path.stem}.jpg", quality=85)
    if totals:
        lines += ["", f"overall: recall {sum(r for r, _ in totals) / len(totals):.1%}, false {sum(f for _, f in totals) / len(totals):.1%} "
                      f"(mean over {len(totals)} PSDs; green caught, red missed, blue put in front wrongly)"]
    (OUT / "occlusion-check.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def _poly_mask(quad, w: int, h: int):  # noqa: ANN001, ANN202
    im = Image.new("1", (w, h), 0)
    ImageDraw.Draw(im).polygon(quad, fill=1)
    return im


def main(argv: list[str]) -> None:
    command = argv[0] if argv else "sheets"
    if command == "decode":
        for path in photos():
            p = decode(path)
            print(f"{path.name}: {p.width}x{p.height} {p.source}{' smart quad' if p.smart_quad else ''}", flush=True)
    elif command == "previews":
        target = OUT / "previews"
        target.mkdir(parents=True, exist_ok=True)
        for index, path in enumerate(photos(), 1):
            photo = decode(path)
            gridded(photo).save(target / f"{index:03d}.jpg", quality=88)
        (target / "index.json").write_text(json.dumps({f"{i:03d}": p.name for i, p in enumerate(photos(), 1)}, indent=1))
    elif command == "measure":
        measure()
    elif command == "check-sheets":
        for path in check_sheets():
            print(path)
    elif command == "moved":
        moved(Path(argv[1]), OUT / "marks-moved.txt")
    elif command == "changed":
        changed(Path(argv[1]))
    elif command == "caps":
        cap_report()
    elif command == "zoom-area":
        marks = load_marks()
        target = OUT / "zoom-area"
        target.mkdir(parents=True, exist_ok=True)
        for f in target.glob("*.jpg"):
            f.unlink()
        for path in photos():
            entry = marks.get(path.name)
            if not entry or (len(argv) > 1 and path.name not in argv[1:]):
                continue
            raw = json.loads(MARKS.read_text(encoding="utf-8"))
            if "same_as" in raw.get("photos", raw)[path.name]:
                continue
            photo = decode(path)
            if psd_quad(path.name, entry, photo) and len(entry.get("garments", [])) == 1:
                continue
            for i in range(len(entry.get("garments", []))):
                zoom_area(photo, entry, i).save(target / f"{path.stem}-g{i}.jpg", quality=86)
    elif command == "twins":
        check_twins()
    elif command == "overflow":
        names = argv[1:]
        if names and names[0].startswith("@"):  # a file of names, one a line
            names = [n for n in Path(names[0][1:]).read_text(encoding="utf-8").splitlines() if n.strip()]
        overflow(only=names or None)
    elif command == "score":
        names = argv[1:]
        if names and names[0].startswith("@"):
            names = [n for n in Path(names[0][1:]).read_text(encoding="utf-8").splitlines() if n.strip()]
        before = "--before" in names
        names = [n for n in names if n != "--before"]
        score(names or None, before)
    elif command == "report":
        tag = "before" if "before" in argv[1:] else "after"
        lines = table(json.loads((OUT / f"scores-{tag}.json").read_text()))
        (OUT / f"report-{tag}.txt").write_text("\n".join(lines) + "\n")
        print("\n".join(lines))
    elif command == "calibrate":
        calibrate()
    elif command == "occlusion":
        occlusion()
    elif command == "export":
        export(OUT / "marks.json")
    elif command == "zoom":
        marks = load_marks()
        target = OUT / "zoom"
        target.mkdir(parents=True, exist_ok=True)
        for path in photos():
            if path.name in marks and (len(argv) < 2 or path.name in argv[1:]) and "same_as" not in marks[path.name]:
                photo = decode(path)
                for i, garment in enumerate(marks[path.name].get("garments", [])):
                    zoom(photo, garment).save(target / f"{path.stem}-g{i}.jpg", quality=88)
    elif command == "sheets":
        sheets()
    elif command == "check":
        marks = load_marks()
        target = OUT / "check"
        target.mkdir(parents=True, exist_ok=True)
        for path in photos():
            if path.name in marks and (len(argv) < 2 or path.name in argv[1:]):
                overlay(decode(path), marks[path.name]).save(target / f"{path.stem}.jpg", quality=85)
    else:
        raise SystemExit(f"unknown command {command}")


if __name__ == "__main__":
    main(sys.argv[1:])
