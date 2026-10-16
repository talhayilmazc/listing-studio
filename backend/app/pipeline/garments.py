"""Garments in a blank photo, their landmarks and their print areas.

Runs once per template, in the worker, on our own CPU, with bundled models only (no
network, nothing sent anywhere, nothing drawn):

* the MediaPipe multiclass segmenter and pose landmarker (``mockup_vision.LiteRTRunner``):
  hair, skin, face, clothes; shoulders, hips, elbows and wrists of each person;
* IS-Net general-use (Apache-2.0) on onnxruntime: what in the photo is an object
  (garments, props) and what is background.

Each garment's visible region is grown from a patch of its own chest by a watershed on
the colour gradient (CIELAB, lightness weighted down so shading and folds do not split
it), against the background, skin, faces and anything far from its colour (props,
jeans, a neighbouring garment). Its landmarks (collar point, armpits, lower edge) come
from that region (and from the pose for a person); the print area comes from them by
``print_rule`` (the template standard) and is then kept on the region (``print_rule.fit``).
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.pipeline import print_rule

logger = logging.getLogger(__name__)

SALIENCY = "isnet-general-use.onnx"  # the name pipeline/fetch_models.py writes
#: Two distance-transform centres further apart than this many half-widths are two garments.
SPLIT_SEPARATION = 1.5
#: ... and the distance map between them falls below this share of the smaller centre.
SPLIT_VALLEY = 0.75
#: The print's top stays this many chest widths below the garment's own neckline edge.
COLLAR_MARGIN = 0.15
WORK = 1024  # analysis size (longest side)
HAIR, BODY, FACE, CLOTHES, OTHERS = 1, 2, 3, 4, 5

#: Calibrated on the seller's evaluation set (evals/mockup_eval.py ``calibrate``):
#: a worn garment's chest (armpit to armpit) and landmarks from the pose's shoulders.
WORN = {
    "chest_per_span": 1.6,        # first guess of the chest / shoulder-landmark distance
    "max_chest_per_span": 2.0,    # a body wider than this is arms or sleeves touching it
    "armpits_below_collar": 0.50, # armpits below the collar point, in chest widths
    "armpit_drop": 0.62,          # seed patch: armpits below the shoulder line, in spans
    "collar_rise": 0.30,          # seed patch: collar above the shoulder line, in spans
}
#: Hoodies (calibrated on the seller's hoodie photos): the print starts this far below the
#: collar (under the hood and drawstrings) and stops above the pocket.
HOODIE = {"below_hood": 0.14, "pocket_below_collar": 0.66}
#: A flat garment's collar point below the top of its region, in chest widths.
FLAT = {"collar_drop": {"folded": 0.37, "flat": 0.267},
        "armpit_below_collar": {"folded": 0.115, "flat": 0.336},
        "ribbing": {"folded": 0.279, "flat": 0.206},
        "sleeve_ratio_flat": 1.4,
        "neighbour_cap": 0.95}  # collar point below the region's neckline edge


@dataclass
class Found:
    """One garment: landmarks and print area in photo pixels (of the analysed image)."""

    corners: list[tuple[float, float]]
    confidence: float
    kind: str
    view: str
    layout: str
    neck: tuple[float, float]
    armpits: tuple[tuple[float, float], tuple[float, float]]
    bottom: tuple[float, float] | None
    region: np.ndarray  # bool (h, w)
    allowed: np.ndarray  # bool: the region plus what may lie in front of it
    notes: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)


@dataclass
class Detection:
    garments: list[Found]
    occluder: np.ndarray  # float 0..1 (h, w): in front of the garments
    garment_mask: np.ndarray  # float 0..1 (h, w): all garments
    persons: int
    details: dict[str, Any] = field(default_factory=dict)


# --- the saliency model ----------------------------------------------------------------------


class Saliency:
    """IS-Net general-use on onnxruntime, single-threaded memory arena off (~0.9 GB, ~1.2 s)."""

    def __init__(self, path: str | Path) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        options.intra_op_num_threads = 2
        self._session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        self._lock = threading.Lock()

    def __call__(self, rgb: np.ndarray) -> np.ndarray:
        from PIL import Image

        h, w = rgb.shape[:2]
        x = np.asarray(Image.fromarray(rgb).resize((1024, 1024), Image.BILINEAR), dtype=np.float32) / 255.0
        x = (x - 0.5).transpose(2, 0, 1)[None].astype(np.float32)
        with self._lock:
            y = self._session.run(None, {self._session.get_inputs()[0].name: x})[0][0, 0]
        y = (y - y.min()) / max(float(y.max() - y.min()), 1e-6)
        return np.asarray(Image.fromarray((y * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR), dtype=np.float32) / 255.0


_saliency: Any = None
_saliency_lock = threading.Lock()


def default_saliency() -> Saliency | None:
    """The bundled IS-Net, loaded once per process; None when it is not here."""
    global _saliency
    with _saliency_lock:
        if _saliency is None:
            from app.core.config import get_settings

            path = Path(get_settings().mockup_models_dir) / SALIENCY
            try:
                _saliency = Saliency(path) if path.exists() else False
            except Exception:
                logger.exception("the saliency model could not be loaded")
                _saliency = False
            if _saliency is False:
                logger.warning("saliency model not available: garments are found from colour only")
        return _saliency or None


# --- helpers -----------------------------------------------------------------------------------


def _lab(rgb: np.ndarray) -> np.ndarray:
    from skimage import color, filters

    return color.rgb2lab(filters.gaussian(rgb / 255.0, sigma=1.5, channel_axis=-1))


def _poly(points: list[tuple[float, float]], h: int, w: int) -> np.ndarray:
    from PIL import Image, ImageDraw

    im = Image.new("1", (w, h), 0)
    ImageDraw.Draw(im).polygon([(float(x), float(y)) for x, y in points], fill=1)
    return np.asarray(im, dtype=bool)


def _hull(mask: np.ndarray) -> np.ndarray:
    """The filled convex hull of a mask (scipy's hull, drawn with PIL: much faster than
    scikit-image's point-in-polygon test on a 1024 px image)."""
    from scipy.spatial import ConvexHull, QhullError

    ys, xs = np.nonzero(mask)
    if len(xs) < 3:
        return mask.copy()
    pts = np.stack([xs, ys], 1).astype(float)
    try:
        hull = pts[ConvexHull(pts).vertices]
    except QhullError:
        return mask.copy()
    return _poly([(float(x), float(y)) for x, y in hull], *mask.shape) | mask


def _kind_from_name(name: str) -> str | None:
    n = name.lower()
    if "hood" in n:
        return "hoodie"
    if "sweat" in n or "crew" in n:
        return "sweatshirt"
    if "tshirt" in n or "t-shirt" in n or "tee" in n:
        return "t-shirt"
    return None


def _regions(lab: np.ndarray, seeds: list[np.ndarray], other: np.ndarray, worn: bool, chests: list[float],
             fg: np.ndarray) -> list[np.ndarray]:
    """Each seed's region: a watershed on the colour gradient against ``other`` and
    anything far from every seed's colour."""
    from scipy import ndimage as ndi
    from skimage import filters, morphology, segmentation

    h, w = other.shape
    weight = np.array([0.1, 1.0, 1.0]) if worn else np.array([0.3, 1.0, 1.0])
    markers = np.zeros((h, w), np.int32)
    models = []
    for i, seed in enumerate(seeds):
        vals = lab[seed]
        med = np.median(vals, axis=0)
        spread = float(np.median(np.linalg.norm((vals - med) * weight, axis=1))) + 2.0
        models.append((med, spread))
        markers[seed] = i + 1
    floor = 25.0 if worn else 22.0
    dist = np.min([np.linalg.norm((lab - med) * weight, axis=-1) / max(spread * 5.0, floor) for med, spread in models], axis=0)
    markers[(other | (dist > 1.0)) & (markers == 0)] = len(seeds) + 1
    gradient = sum(filters.sobel(lab[..., c]) for c in range(3))
    labels = segmentation.watershed(gradient, markers)
    out = []
    for i in range(len(seeds)):
        region = morphology.binary_opening(labels == i + 1, morphology.disk(2))
        cc, _ = ndi.label(region)
        keep = np.unique(cc[(markers == i + 1) & region])
        region = np.isin(cc, keep[keep > 0])
        radius = max(2, round(0.035 * chests[i]))
        region = morphology.binary_closing(region, morphology.disk(radius)) & (fg > 0.3)
        filled = ndi.binary_fill_holes(region)
        _ENCLOSED[id(filled)] = filled & ~region  # objects lying on or held over the garment
        out.append(filled)
    return out


#: For each region (by id): what it encloses that is not garment (a mug held over the
#: chest, a bag lying on a tee): printed under, then put in front by the occluder.
_ENCLOSED: dict[int, np.ndarray] = {}


def _lower_edge(region: np.ndarray, f: print_rule.Frame, start: float) -> tuple[float, float] | None:
    """The garment's visible lower edge on its axis: going down from ``start`` (chest
    widths below the collar), the first depth where the region no longer covers the
    centre band (half its width), or narrows to under 60% of the chest (a knot)."""
    h, w = region.shape
    v = start * f.chest
    step = max(1.0, f.chest / 200)
    last_good = None
    while True:
        cx, cy = f.to_photo(0, v)
        if not (0 <= cx < w and 0 <= cy < h):
            break
        band = [f.to_photo(u * f.chest, v) for u in np.linspace(-0.12, 0.12, 9)]
        cover = np.mean([region[int(y), int(x)] if 0 <= x < w and 0 <= y < h else False for x, y in band])
        span = [f.to_photo(u * f.chest, v) for u in np.linspace(-0.3, 0.3, 13)]
        wide = np.mean([region[int(y), int(x)] if 0 <= x < w and 0 <= y < h else False for x, y in span])
        if cover < 0.5 or wide < 0.6:
            break
        last_good = (cx, cy)
        v += step
    if last_good is None:
        return None
    return last_good if v < 3.0 * f.chest else None


# --- people ------------------------------------------------------------------------------------


def is_person(rgb: np.ndarray, probs: np.ndarray, runner: Any) -> bool:
    """Whether a person wears the garment. The segmenter alone mistakes a hat for a face
    and wood or a rug for skin on a flat lay; what tells them apart on the seller's photos
    (evals/mockup_eval.py) is hair together with a face or real skin, or skin at the
    pose's elbows and wrists (arms)."""
    hair = float((probs[..., HAIR] > 0.5).mean())
    face = float((probs[..., FACE] > 0.5).mean())
    skin = float((probs[..., BODY] > 0.5).mean())
    if hair >= 0.01 and (face >= 0.002 or skin >= 0.035):
        return True
    h, w = probs.shape[:2]
    points, presence = runner.landmarks(rgb, (0, 0, w, h))
    if presence < 0.55:
        return False
    skin_map = probs[..., BODY] + probs[..., FACE]
    r = max(3, int(0.015 * max(h, w)))
    seen = []
    for i in (13, 14, 15, 16):
        x, y, v = points[i]
        if v > 0.5 and 0 <= x < w and 0 <= y < h:
            seen.append(float(skin_map[max(0, int(y) - r): int(y) + r, max(0, int(x) - r): int(x) + r].mean()))
    return bool(seen) and max(seen) >= 0.8


def _persons(probs: np.ndarray) -> list[dict[str, Any]]:
    """Face blobs (or hair blobs without a face: a back view), left to right."""
    from scipy import ndimage as ndi

    h, w = probs.shape[:2]
    face = probs[..., FACE] > 0.5
    hair = probs[..., HAIR] > 0.5
    found = []
    cc, n = ndi.label(face)
    for i in range(1, n + 1):
        ys, xs = np.where(cc == i)
        if len(xs) >= 0.0008 * h * w:
            found.append({"x": float(xs.mean()), "y": float(ys.min()), "face": True, "size": len(xs)})
    face_near = ndi.binary_dilation(face, iterations=max(3, round(0.03 * max(h, w))))
    cc, n = ndi.label(hair & ~face_near)
    for i in range(1, n + 1):
        ys, xs = np.where(cc == i)
        if len(xs) >= 0.01 * h * w and not found:
            found.append({"x": float(xs.mean()), "y": float(ys.min()), "face": False, "size": len(xs)})
    return sorted(found, key=lambda p: p["x"])


def _worn(rgb: np.ndarray, probs: np.ndarray, fg: np.ndarray, lab: np.ndarray, runner: Any,
          persons: list[dict[str, Any]], hint: str | None) -> list[Found]:
    h, w = probs.shape[:2]
    person_any = (probs[..., HAIR] + probs[..., BODY] + probs[..., FACE] + probs[..., CLOTHES]) > 0.5
    xs_all = [p["x"] for p in persons]
    poses = []
    for i, p in enumerate(persons):
        x0 = 0 if i == 0 else (xs_all[i - 1] + p["x"]) / 2
        x1 = w if i == len(persons) - 1 else (xs_all[i + 1] + p["x"]) / 2
        band = person_any.copy()
        band[:, : int(x0)] = False
        band[:, int(x1):] = False
        ys, xs = np.where(band)
        if len(xs) == 0:
            continue
        box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        points, presence = runner.landmarks(rgb, box)
        poses.append((p, points, presence, (x0, x1)))
    seeds, frames_, chests, meta = [], [], [], []
    for p, points, presence, (x0, x1) in poses:
        a, b = points[11], points[12]
        if min(a[2], b[2]) < 0.3:
            continue
        left, right = sorted([(a[0], a[1]), (b[0], b[1])])
        span = math.dist(left, right)
        if span < 0.06 * w:
            continue
        # Facing the camera: the person's left shoulder (11) is on the right of the photo.
        view = "front" if (p["face"] and a[0] > b[0]) else "back"
        mid = ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)
        chest = WORN["chest_per_span"] * span
        drop = WORN["armpit_drop"] * span
        armpits = ((mid[0] - chest / 2, mid[1] + drop), (mid[0] + chest / 2, mid[1] + drop))
        neck = _neck_from_skin(probs, mid, span) or (mid[0], mid[1] - WORN["collar_rise"] * span)
        f = print_rule.frame(neck, armpits, "on a person")
        patch = [f.to_photo(u * chest, v * chest) for u, v in ((-0.14, 0.3), (0.14, 0.3), (0.14, 0.6), (-0.14, 0.6))]
        seed = _poly(patch, h, w) & ~((probs[..., FACE] + probs[..., BODY] + probs[..., HAIR]) > 0.5)
        if seed.sum() < 20:
            seed = _poly(patch, h, w)
        kind = hint or _sleeve_kind(points, probs)
        seeds.append(seed)
        frames_.append(f)
        chests.append(chest)
        meta.append({"view": view, "kind": kind, "neck": neck, "armpits": armpits, "presence": presence,
                     "shoulders": (left, right),
                     "vis": float(min(a[2], b[2])), "points": points})
    if not seeds:
        return []
    other = (fg < 0.3) & (probs[..., CLOTHES] < 0.3) | ((probs[..., BODY] + probs[..., FACE]) > 0.6)
    regions = _regions(lab, seeds, other, True, chests, np.maximum(fg, (probs[..., CLOTHES] > 0.3).astype(np.float32)))
    hair_acc = (probs[..., HAIR] + probs[..., OTHERS] + probs[..., BODY]) > 0.5  # hair, arms, hands, props in front
    out = []

    for region, f, m in zip(regions, frames_, meta):
        hull = _hull(region) if region.any() else region
        hood = m["kind"] == "hoodie" or _has_hood(region, probs, m["shoulders"])
        kind = "hoodie" if hood else m["kind"]
        hair = probs[..., HAIR] > 0.5  # the chest is measured across hair over it, not arms beside it
        neck, armpits, how = _worn_landmarks(region | (hair & hull), probs, m["shoulders"], m["neck"])
        if hood and m["view"] == "back":
            # The hood hangs over the upper back: the back collar is at the shoulder line,
            # not at the hood's top edge.
            (sl, sr) = m["shoulders"]
            shoulder_y = (sl[1] + sr[1]) / 2
            dy = shoulder_y - neck[1]
            if dy > 0:
                neck = (neck[0], shoulder_y)
                armpits = ((armpits[0][0], armpits[0][1] + dy), (armpits[1][0], armpits[1][1] + dy))
        f2 = print_rule.frame(neck, armpits, "on a person")
        bottom = _lower_edge(region, f2, 0.35)
        top = None
        if hood:
            chest = math.dist(*armpits)
            top = f2.to_photo(0, HOODIE["below_hood"] * chest)
            if m["view"] == "front":  # the pocket: the print stays above it
                pocket = _pocket(lab, region, f2) or f2.to_photo(0, HOODIE["pocket_below_collar"] * chest)
                if bottom is None or f2.depth(pocket) < f2.depth(bottom):
                    bottom = pocket
        found = _finish(region, hair_acc, neck, armpits, bottom, kind, m["view"], "on a person", top=top)
        found.confidence = round(float(min(1.0, m["presence"] * m["vis"] * found.info.get("on_region", 1.0)
                                           * (1.0 if how == "region" else 0.85))), 3)
        found.info.update({"method": "pose", "shoulders": m["shoulders"], "chest_from": how})
        out.append(found)
    return out


def _worn_landmarks(region: np.ndarray, probs: np.ndarray, shoulders, neck_guess):  # noqa: ANN001, ANN202
    """A worn garment's landmarks from its own region and the pose:

    * the axis: midway between the shoulder landmarks, corrected to the middle of the
      garment's body where the body is measured;
    * the collar point: the top edge of the garment region on the axis (the neckline:
      skin, hair or a necklace lie above it), else the neck skin;
    * the armpits: WORN["armpits_below_collar"] chest widths below the collar, the chest
      being the body's width there, between 1.05 and 2.0 shoulder spans (sleeves and
      arms that touch the body are cut off at the shoulder span ratio)."""
    h, w = region.shape
    (lx, ly), (rx, ry) = shoulders
    span = math.dist((lx, ly), (rx, ry))
    mx, my = (lx + rx) / 2, (ly + ry) / 2
    x = int(round(mx))
    col = region[:, max(0, x - 2): x + 3].any(axis=1) if 0 <= x < w else np.zeros(h, bool)
    top = None
    for y in range(int(max(0, my - 0.7 * span)), int(min(h, my + 0.6 * span))):
        if col[y] and col[y: y + max(3, int(0.05 * span))].all():
            top = y
            break
    neck_y = float(top) if top is not None else (_neck_from_skin(probs, (mx, my), span) or neck_guess)[1]

    def body_width(y: float) -> tuple[float, float]:
        yy = int(min(h - 1, max(0, y)))
        line = region[yy]
        if not line[min(w - 1, max(0, x))]:
            return 0.0, mx
        x0 = x
        while x0 > 0 and line[x0 - 1]:
            x0 -= 1
        x1 = x
        while x1 < w - 1 and line[x1 + 1]:
            x1 += 1
        return float(x1 - x0 + 1), (x0 + x1) / 2

    # the chest: the body's width at the armpit depth (iterated once: depth depends on it)
    chest = WORN["chest_per_span"] * span
    axis_x = mx
    for _ in range(2):
        arm_y = neck_y + WORN["armpits_below_collar"] * chest
        samples = [body_width(arm_y + d * span) for d in (0.1, 0.2, 0.3)]
        widths = [s_[0] for s_ in samples if s_[0] > 0]
        if widths:
            chest = float(np.median(widths))
            axis_x = float(np.median([s_[1] for s_ in samples if s_[0] > 0]))
        chest = min(max(chest, 1.05 * span), WORN["max_chest_per_span"] * span)
    if abs(axis_x - mx) > 0.25 * span:
        axis_x = mx
    arm_y = neck_y + WORN["armpits_below_collar"] * chest
    armpits = ((axis_x - chest / 2, arm_y), (axis_x + chest / 2, arm_y))
    return (axis_x, neck_y), armpits, "region" if top is not None else "skin"


def _has_hood(region: np.ndarray, probs: np.ndarray, shoulders) -> bool:  # noqa: ANN001
    """A hood: the garment rises around the head, above the shoulder line, on both
    sides of the face (or over the head from behind)."""
    h, w = region.shape
    (lx, ly), (rx, ry) = shoulders
    span = math.dist((lx, ly), (rx, ry))
    mx, my = (lx + rx) / 2, (ly + ry) / 2
    x0, x1 = int(max(0, mx - 0.45 * span)), int(min(w, mx + 0.45 * span))
    y0, y1 = int(max(0, my - 0.55 * span)), int(max(0, my - 0.08 * span))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return False
    band = region[y0:y1, x0:x1] & ~((probs[y0:y1, x0:x1, FACE] + probs[y0:y1, x0:x1, BODY]) > 0.5)
    left, right = band[:, : band.shape[1] // 2].mean(), band[:, band.shape[1] // 2:].mean()
    return bool(min(left, right) > 0.3)


def _pocket(lab: np.ndarray, region: np.ndarray, f: print_rule.Frame) -> tuple[float, float] | None:
    """A hoodie's front pocket: the strongest horizontal edge (its top seam and shadow)
    on the garment's axis between 0.5 and 1.0 chest widths below the collar."""
    h, w = region.shape
    best, best_v = 0.0, None
    vs = np.linspace(0.5, 1.0, 60) * f.chest
    prev = None
    for v in vs:
        pts = [f.to_photo(u * f.chest, v) for u in np.linspace(-0.25, 0.25, 11)]
        vals = [lab[int(y), int(x), 0] for x, y in pts if 0 <= x < w and 0 <= y < h and region[int(y), int(x)]]
        if len(vals) < 6:
            prev = None
            continue
        cur = float(np.median(vals))
        if prev is not None and abs(cur - prev) > best:
            best, best_v = abs(cur - prev), v
        prev = cur
    if best_v is None or best < 4.0:
        return None
    return f.to_photo(0, best_v)


def _neck_from_skin(probs: np.ndarray, mid: tuple[float, float], span: float) -> tuple[float, float] | None:
    """The collar point: the lowest neck skin (or hair over it) on the centre line,
    between half a span above the shoulder line and a quarter below."""
    h, w = probs.shape[:2]
    x = int(round(mid[0]))
    half = max(1, int(0.03 * span))
    skin = (probs[..., BODY] + probs[..., FACE]) > 0.5
    y0, y1 = int(max(0, mid[1] - 0.6 * span)), int(min(h - 1, mid[1] + 0.25 * span))
    lowest = None
    for y in range(y0, y1):
        if skin[y, max(0, x - half): x + half + 1].mean() > 0.5:
            lowest = y
    return (mid[0], float(lowest + 0.02 * span)) if lowest is not None else None


def _sleeve_kind(points: np.ndarray, probs: np.ndarray) -> str:
    """A long sleeve (sweatshirt) when the forearms (elbow to wrist) are mostly clothes."""
    h, w = probs.shape[:2]
    clothes = probs[..., CLOTHES] > 0.5
    skin = probs[..., BODY] > 0.5
    votes = []
    for e, wr in ((13, 15), (14, 16)):
        if min(points[e][2], points[wr][2]) < 0.4:
            continue
        samples = [(points[e][0] + (points[wr][0] - points[e][0]) * t, points[e][1] + (points[wr][1] - points[e][1]) * t)
                   for t in np.linspace(0.2, 0.8, 9)]
        c = s = 0
        for x, y in samples:
            if 0 <= x < w and 0 <= y < h:
                c += clothes[int(y), int(x)]
                s += skin[int(y), int(x)]
        if c + s:
            votes.append(c / (c + s))
    return "sweatshirt" if votes and np.mean(votes) > 0.5 else "t-shirt"


# --- flat garments -----------------------------------------------------------------------------


def _flat(rgb: np.ndarray, probs: np.ndarray, fg: np.ndarray, lab: np.ndarray, hint: str | None) -> list[Found]:
    """Garments lying flat (single, folded or several): colour clusters of the
    foreground, their garment-sized parts, a region per part."""
    from scipy import ndimage as ndi
    from skimage import measure

    h, w = fg.shape
    obj = fg > 0.5
    if obj.sum() < 0.02 * h * w:
        return []
    parts = _colour_parts(lab, obj)
    seeds, chests, boxes, kept_parts = [], [], [], []
    for part in parts:
        dist = ndi.distance_transform_edt(part)
        r = float(dist.max())
        if r < 0.03 * max(h, w):
            continue
        seed = dist > 0.6 * r
        seeds.append(seed)
        kept_parts.append(part)
        props = measure.regionprops(part.astype(np.uint8))[0]
        chests.append(0.6 * props.minor_axis_length)
        boxes.append(props.bbox)
    if not seeds:
        return []
    grown = _regions(lab, seeds, fg < 0.3, False, chests, fg)
    # The colour part is the garment's base: a weak edge to the background (a cream
    # tee on white) must not cost it its sleeves; the watershed only adds to it.
    regions = []
    for part, g in zip(kept_parts, grown):
        raw = part | (g & ~_ENCLOSED.get(id(g), np.zeros_like(g)))
        filled = ndi.binary_fill_holes(raw)
        _ENCLOSED[id(filled)] = filled & ~raw
        regions.append(filled)
    hair_acc = (probs[..., HAIR] + probs[..., OTHERS]) > 0.5
    out = []
    texture = np.abs(lab[..., 0] - _smooth(lab[..., 0], 3.0))
    rejected = []
    largest = max((r.sum() for r in regions), default=0)
    sized = [r for r in regions if r.sum() >= max(0.015 * h * w, 0.25 * largest)]
    plain = [r for r in sized if float(np.median(texture[r])) <= 2.5]
    for region in sized:
        g = _flat_landmarks(region, hint)
        if g is None:
            continue
        # Folded or lying flat: several garments in a photo lie flat (sleeves often under
        # a neighbour); a single one is folded unless its sleeves stand out (calibrated
        # on the seller's photos: folded panels reach 1.3 x the body's width, sleeves 1.5+).
        layout = "flat" if (len(plain) > 1 or g["sleeve_ratio"] > FLAT["sleeve_ratio_flat"]) else "folded"
        if layout != g["layout"]:
            g = _flat_landmarks(region, hint, force=layout) or g
        # Only garment-shaped, plain regions: sleeves in the profile, or a near
        # rectangle (folded); a patterned skirt or a hat is not a print surface.
        busy = float(np.median(texture[region]))
        if busy > 2.5:  # a patterned region (a skirt, a rug) is not a print surface
            rejected.append((g, region, busy))
            continue
        found = _finish(region, hair_acc, g["neck"], g["armpits"], g["bottom"], g["kind"], "front", g["layout"])
        found.confidence = g["confidence"] * found.info.get("on_region", 1.0)
        found.info["method"] = "outline"
        found.notes.extend(g["notes"])
        found.info.update({"top_point": g["top"], "region_chest": g["chest"], "tilt": g["angle"],
                           "neckline": g["neckline"], "layout_found": g["layout"],
                           "sleeve_ratio": g["sleeve_ratio"], "fill": g["fill"], "aspect": g["aspect"]})
        out.append(found)
    out = _cap_by_neighbours(out, hair_acc)
    if not out and rejected:  # nothing garment-shaped: the best candidate, flagged
        g, region, busy = max(rejected, key=lambda t: t[1].sum())
        found = _finish(region, hair_acc, g["neck"], g["armpits"], g["bottom"], g["kind"], "front", g["layout"])
        found.confidence = min(0.4, g["confidence"])
        found.info["method"] = "outline"
        found.notes.append("unusual shape")
        out.append(found)
    # one garment per place: drop a region mostly inside a bigger one
    out.sort(key=lambda f: -f.region.sum())
    kept = []
    for f in out:
        if all((f.region & k.region).sum() < 0.5 * f.region.sum() for k in kept):
            kept.append(f)
    return kept


def _colour_parts(lab: np.ndarray, obj: np.ndarray) -> list[np.ndarray]:
    """The foreground split into colour clusters (k-means on chroma-weighted CIELAB,
    clusters closer than a just-visible difference merged) and their sizeable parts."""
    from scipy import ndimage as ndi
    from skimage import morphology

    h, w = obj.shape
    step = max(1, int(round(max(h, w) / 256)))
    small = lab[::step, ::step]
    mask = obj[::step, ::step]
    feats = small[mask] * np.array([0.15, 1.0, 1.0])  # heather and shading vary lightness, not hue
    if len(feats) < 50:
        return []
    rng = np.random.default_rng(0)
    k = min(6, max(1, len(feats) // 200))
    centres = feats[rng.choice(len(feats), k, replace=False)]
    for _ in range(15):
        d = np.linalg.norm(feats[:, None] - centres[None], axis=-1)
        lab_ = d.argmin(1)
        centres = np.array([feats[lab_ == j].mean(0) if (lab_ == j).any() else centres[j] for j in range(k)])
    # merge close clusters
    groups = list(range(k))
    for i in range(k):
        for j in range(i + 1, k):
            if np.linalg.norm(centres[i] - centres[j]) < 9:
                gi, gj = groups[i], groups[j]
                groups = [gi if g == gj else g for g in groups]
    full = np.linalg.norm(lab[..., None, :] * np.array([0.15, 1.0, 1.0]) - centres[None, None], axis=-1).argmin(-1)
    parts = []
    for g in sorted(set(groups)):
        members = [j for j in range(k) if groups[j] == g]
        m = np.isin(full, members) & obj
        m = morphology.binary_opening(m, morphology.disk(max(2, int(0.006 * max(h, w)))))
        cc, n = ndi.label(m)
        for i in range(1, n + 1):
            part = cc == i
            if part.sum() >= 0.02 * h * w:
                # Split on the filled shape (an object lying on a garment is a hole in its
                # colour, not a gap between two garments); keep the holes in each piece.
                filled = ndi.binary_fill_holes(part)
                parts.extend(piece & part for piece in _split_touching(filled))
    return parts


def _split_touching(part: np.ndarray) -> list[np.ndarray]:
    """Same-colour garments that touch: split at the valleys of the distance transform
    between their centres. Two centres closer than SPLIT_SEPARATION of the garment's
    half-width are the same garment (a tee's chest and lower body); further apart they
    are two (side by side, a chest width apart)."""
    from scipy import ndimage as ndi
    from skimage import segmentation
    from skimage.feature import peak_local_max

    dist = ndi.distance_transform_edt(part)
    r = float(dist.max())
    peaks = peak_local_max(dist, min_distance=max(3, int(0.8 * r)), threshold_abs=0.55 * r, labels=part.astype(int))
    if len(peaks) <= 1:
        return [part]
    def separate(a: tuple[int, int], b: tuple[int, int]) -> bool:
        # Two garments: far enough apart, and the distance map dips between them (the
        # neckline, the sleeves); inside one tee it stays high from chest to hem.
        low = min(dist[a[0], a[1]], dist[b[0], b[1]])
        if math.dist(a, b) <= SPLIT_SEPARATION * low:
            return False
        line = [dist[int(round(a[0] + (b[0] - a[0]) * t)), int(round(a[1] + (b[1] - a[1]) * t))] for t in np.linspace(0, 1, 40)]
        return min(line) < SPLIT_VALLEY * low or math.dist(a, b) > 2.6 * low

    kept: list[tuple[int, int]] = []
    for y, x in sorted(peaks.tolist(), key=lambda p: -dist[p[0], p[1]]):
        if all(separate((y, x), k) for k in kept):
            kept.append((y, x))
    if len(kept) <= 1:
        return [part]
    markers = np.zeros(part.shape, np.int32)
    for i, (y, x) in enumerate(kept, 1):
        markers[y, x] = i
    labels = segmentation.watershed(-dist, markers, mask=part)
    pieces = [labels == i for i in range(1, len(kept) + 1)]
    big = [p for p in pieces if p.sum() >= 0.35 * part.sum() / len(kept)]
    return big if len(big) > 1 else [part]


def _flat_landmarks(region: np.ndarray, hint: str | None, angle: float | None = None,
                    refine: bool = True, force: str | None = None) -> dict[str, Any] | None:
    """Collar point, armpits, lower edge and layout of a flat garment from its region:
    its orientation (minimum-area rectangle), the width profile down its axis (the
    sleeves end where the width drops to the body's), folded when it has no sleeves."""
    from skimage import measure

    h, w = region.shape
    props = measure.regionprops(region.astype(np.uint8))[0]
    if angle is None:
        angle = _orientation(region)
    t = math.radians(angle)
    across = np.array([math.cos(t), math.sin(t)])
    down = np.array([-across[1], across[0]])
    ys, xs = np.nonzero(region)
    pts = np.stack([xs, ys], 1).astype(float)
    centre = pts.mean(0)
    u = (pts - centre) @ across
    v = (pts - centre) @ down
    v0, v1 = v.min(), v.max()
    bins = np.linspace(v0, v1, 81)
    widths, lefts, rights = [], [], []
    for a, b in zip(bins[:-1], bins[1:]):
        sel = (v >= a) & (v < b)
        if sel.sum() < 3:
            widths.append(0.0); lefts.append(0.0); rights.append(0.0)
            continue
        uu = u[sel]
        lefts.append(float(np.percentile(uu, 2)))
        rights.append(float(np.percentile(uu, 98)))
        widths.append(rights[-1] - lefts[-1])
    widths = np.array(widths)
    if widths.max() <= 0:
        return None
    body = float(np.median(widths[40:64]))  # the body's width, below the chest
    upper = widths[:40]
    sleeve_rows = np.where(upper > 1.18 * body)[0]
    notes = []
    fill = _rect_fill(region)
    top_v = bins[0]
    lefts_a, rights_a = np.array(lefts), np.array(rights)

    def row_of(vv: float) -> int:
        return int(min(len(widths) - 1, max(0, round((vv - bins[0]) / (bins[1] - bins[0])))))

    if force == "folded":
        sleeve_rows = np.array([], dtype=int)
    if len(sleeve_rows):  # sleeves: the armpits are where the width drops back to the body's
        arm_i = int(sleeve_rows.max()) + 1
        layout = "flat"
        chest_rows = slice(arm_i + 1, min(len(widths), arm_i + 5))
        chest = float(np.median(widths[chest_rows])) if widths[chest_rows].size else body
        va = (bins[arm_i] + bins[arm_i + 1]) / 2
        collar_v = top_v + FLAT["collar_drop"][layout] * chest
    else:
        # No sleeves in the outline: folded when it is a rectangle; otherwise a garment
        # whose sleeves are hidden (under a neighbour): the armpits at the usual depth.
        layout = force or ("folded" if fill > 0.82 else "flat")
        if layout == "flat":
            notes.append("sleeves hidden")
        chest = body if layout == "flat" else float(np.median(widths[8:40]))
        collar_v = top_v + FLAT["collar_drop"][layout] * chest
        va = collar_v + FLAT["armpit_below_collar"][layout] * chest
        arm_i = row_of(va)
        chest = float(np.median(widths[arm_i: arm_i + 4])) or chest
    k = slice(arm_i + 1, min(len(widths), arm_i + 5))
    lu = float(np.median(lefts_a[k])) if widths[k].size else -chest / 2
    ru = float(np.median(rights_a[k])) if widths[k].size else chest / 2
    axis_u = (lu + ru) / 2
    to_photo = lambda uu, vv: tuple(centre + across * uu + down * vv)  # noqa: E731
    # The neckline: the top edge of the region on the axis lies below the shoulders' top
    # (the collar dips); when it is found it gives the collar point directly.
    near = np.abs(u - axis_u) < 0.03 * chest
    neckline_pt = None
    if near.sum() > 5:
        neckline = float(np.percentile(v[near], 1))
        dip = neckline - top_v
        neckline_pt = to_photo(axis_u, neckline) if 0.03 * chest < dip < 0.5 * chest else None
        if 0.03 * chest < dip < 0.5 * chest:
            collar_v = neckline + FLAT["ribbing"][layout] * chest
            if not len(sleeve_rows):
                va = collar_v + FLAT["armpit_below_collar"][layout] * chest
    armpits = (to_photo(axis_u - chest / 2, va), to_photo(axis_u + chest / 2, va))
    neck = to_photo(axis_u, collar_v)
    f = print_rule.frame(neck, armpits, layout)
    bottom = _lower_edge(region, f, 0.35)
    if refine and layout == "flat":  # the armpit line gives a better tilt than the outline
        (ax0, ay0), (ax1, ay1) = armpits
        tilt = math.degrees(math.atan2(ay1 - ay0, ax1 - ax0))
        tilt = 0.0 if abs(tilt) <= print_rule.UPRIGHT_DEGREES else tilt
        if angle != 0.0 and abs(tilt - angle) > 3 and abs(tilt) <= 35:
            again = _flat_landmarks(region, hint, tilt, refine=False, force=force)
            if again is not None and again["layout"] == "flat":
                return again
    kind = hint or "t-shirt"
    solidity = float(props.solidity)
    confidence = 0.55 + 0.25 * (layout == "flat") + 0.2 * min(1.0, max(0.0, (solidity - 0.6) / 0.3))
    return {"neck": neck, "armpits": armpits, "bottom": bottom, "layout": layout, "kind": kind,
            "confidence": round(min(1.0, confidence), 3), "notes": notes,
            "top": to_photo(axis_u, top_v), "chest": chest, "angle": angle, "fill": fill,
            "neckline": neckline_pt, "sleeve_ratio": float(upper.max() / body) if body else 0.0,
            "aspect": float((v1 - v0) / max(body, 1e-6))}


def _smooth(a: np.ndarray, sigma: float) -> np.ndarray:
    from scipy import ndimage as ndi

    return ndi.gaussian_filter(a, sigma)


def _rect_fill(region: np.ndarray) -> float:
    """How much of its minimum-area rectangle the region fills (1 = a rectangle)."""
    from scipy.spatial import ConvexHull

    ys, xs = np.nonzero(region)
    if len(xs) < 10:
        return 0.0
    pts = np.stack([xs, ys], 1).astype(float)
    hull = pts[ConvexHull(pts).vertices]
    best = math.inf
    for i in range(len(hull)):
        e = hull[(i + 1) % len(hull)] - hull[i]
        a = math.atan2(e[1], e[0])
        c, s_ = math.cos(a), math.sin(a)
        rot = hull @ np.array([[c, -s_], [s_, c]])
        best = min(best, (rot[:, 0].max() - rot[:, 0].min()) * (rot[:, 1].max() - rot[:, 1].min()))
    return float(region.sum() / best) if best else 0.0


def _orientation(region: np.ndarray, folded: bool | None = None) -> float:
    """The garment's tilt in degrees (within +-35); 0 within ``print_rule.UPRIGHT_DEGREES``.

    A garment is left-right symmetric about its axis: the tilt is the angle whose
    mirror image matches the garment best (on a 128 px copy). A folded garment (a
    near rectangle) uses its minimum-area rectangle instead, which is exact for it."""
    from PIL import Image
    from scipy.spatial import ConvexHull

    ys, xs = np.nonzero(region)
    if len(xs) < 10:
        return 0.0
    pts = np.stack([xs, ys], 1).astype(float)
    hull = pts[ConvexHull(pts).vertices]
    rect = (math.inf, 0.0)
    for i in range(len(hull)):
        e = hull[(i + 1) % len(hull)] - hull[i]
        ang = math.atan2(e[1], e[0])
        c, s_ = math.cos(ang), math.sin(ang)
        rot = hull @ np.array([[c, -s_], [s_, c]])
        area = (rot[:, 0].max() - rot[:, 0].min()) * (rot[:, 1].max() - rot[:, 1].min())
        if area < rect[0]:
            rect = (area, math.degrees(ang))
    fill = region.sum() / rect[0] if rect[0] else 0.0
    if folded or (folded is None and fill > 0.86):
        deg = ((rect[1] + 45) % 90) - 45
        return 0.0 if abs(deg) <= print_rule.UPRIGHT_DEGREES else deg
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    # The upper part (collar, shoulders, sleeves) is symmetric even when the hem is
    # knotted or folded under.
    y1 = y0 + max(2, int(0.6 * (y1 - y0)))
    region = region.copy()
    region[y1:] = False
    side = max(y1 - y0, x1 - x0)
    pad = np.zeros((side * 2, side * 2), np.uint8)
    oy, ox = side - (y1 - y0) // 2, side - (x1 - x0) // 2
    pad[oy:oy + y1 - y0, ox:ox + x1 - x0] = region[y0:y1, x0:x1]
    small = Image.fromarray(pad * 255).resize((128, 128), Image.BILINEAR)
    best = (-1.0, 0.0)
    for deg in range(-25, 26, 1):
        r = np.asarray(small.rotate(deg, resample=Image.BILINEAR), np.float32) / 255
        cols = r.sum(0)
        if cols.sum() == 0:
            continue
        cx = float((np.arange(128) * cols).sum() / cols.sum())
        shift = int(round(2 * cx - 127))
        mirrored = np.roll(r[:, ::-1], shift, axis=1)
        score = float((np.minimum(r, mirrored)).sum() / max(np.maximum(r, mirrored).sum(), 1e-6))
        if score > best[0]:
            best = (score, -deg)
    deg = float(best[1])
    if abs(deg) <= print_rule.UPRIGHT_DEGREES:
        return 0.0
    # A garment overlapped by a neighbour is not symmetric any more: rotate only when the
    # outline's own rectangle agrees (within 6 degrees) or the mirror match is clear.
    rect_deg = ((rect[1] + 45) % 90) - 45
    if abs(rect_deg - deg) <= 6.0 or best[0] >= 0.92:
        return deg
    return 0.0


def _cap_by_neighbours(found: list[Found], hair_acc: np.ndarray) -> list[Found]:
    """Several garments side by side: a garment's chest is at most FLAT["neighbour_cap"]
    of the distance to the nearest garment in the same row (its region can run into a
    neighbour of the same colour). Calibrated: the marked chest is 0.86 of that distance
    (median), under 0.98 in nine garments out of ten."""
    if len(found) < 2:
        return found
    out = []
    for g in found:
        chest = math.dist(*g.armpits)
        row = [o for o in found if o is not g and abs(o.neck[1] - g.neck[1]) < 0.5 * chest]
        if row:
            gap = min(abs(o.neck[0] - g.neck[0]) for o in row)
            cap = FLAT["neighbour_cap"] * gap
            if chest > cap:
                (ax, ay), (bx, by) = g.armpits
                cx, cy = (ax + bx) / 2, (ay + by) / 2
                k = cap / chest
                armpits = ((cx + (ax - cx) * k, cy + (ay - cy) * k), (cx + (bx - cx) * k, cy + (by - cy) * k))
                again = _finish(g.region, hair_acc, g.neck, armpits, g.bottom, g.kind, g.view, g.layout)
                again.confidence, again.notes = g.confidence, g.notes + ["chest capped by a neighbour"]
                again.info = {**g.info, **again.info}
                g = again
        out.append(g)
    return out


# --- the print area ----------------------------------------------------------------------------


def _safe_top(region: np.ndarray, neck, armpits, layout: str):  # noqa: ANN001, ANN202
    """The point the print must stay below: COLLAR_MARGIN chest widths under the
    garment's own top edge on its axis (the neckline), so a collar point estimated too
    high cannot put the print on the collar."""
    h, w = region.shape
    f = print_rule.frame(neck, armpits, layout)
    v = -0.6 * f.chest
    step = max(1.0, f.chest / 150)
    while v < 0.6 * f.chest:
        x, y = f.to_photo(0, v)
        if 0 <= x < w and 0 <= y < h and region[int(y), int(x)]:
            return f.to_photo(0, v + COLLAR_MARGIN * f.chest)
        v += step
    return None


def _finish(region: np.ndarray, hair_acc: np.ndarray, neck, armpits, bottom, kind: str, view: str,  # noqa: ANN001
            layout: str, top=None) -> Found:  # noqa: ANN001
    """The print area from the landmarks (``print_rule``), kept on the region."""
    from scipy import ndimage as ndi

    h, w = region.shape
    hull = _hull(region) if region.any() else region
    allowed = ndi.binary_fill_holes(region | (hair_acc & hull))
    safe = _safe_top(region, neck, armpits, layout)
    if top is not None:
        f = print_rule.frame(neck, armpits, layout)
        if safe is None or f.depth(top) > f.depth(safe):
            safe = top
    area = print_rule.print_area(neck, armpits, kind=kind, view=view, layout=layout, bottom=bottom, top=safe)

    xs = [x for x, _ in area.corners]
    ys = [y for _, y in area.corners]
    pad = 4
    x0, y0 = max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad)
    x1, y1 = min(w, int(max(xs)) + pad + 1), min(h, int(max(ys)) + pad + 1)
    window = allowed[y0:y1, x0:x1]
    wh, ww = window.shape

    def local(points: list[tuple[float, float]]) -> np.ndarray:
        return _poly([(x - x0, y - y0) for x, y in points], wh, ww)

    def outside(corners: list[tuple[float, float]]) -> tuple[float, str]:
        if ww < 2 or wh < 2:
            return 1.0, "bottom"
        m = local(corners)
        n = m.sum()
        if not n:
            return 0.0, "bottom"
        share = float((m & ~window).sum() / n)
        (ax, ay), (bx, by), _, (dx, dy) = corners
        thirds = {}
        for side, (ua, ub, va, vb) in {"left": (0, 1 / 3, 0, 1), "right": (2 / 3, 1, 0, 1), "bottom": (0, 1, 2 / 3, 1)}.items():
            pts = [(ax + (bx - ax) * uu + (dx - ax) * vv, ay + (by - ay) * uu + (dy - ay) * vv)
                   for uu, vv in ((ua, va), (ub, va), (ub, vb), (ua, vb))]
            thirds[side] = int((local(pts) & ~window).sum())
        return share, max(thirds, key=thirds.get)

    before, _ = outside(area.corners)
    corners, kept, after = print_rule.fit(area.corners, outside)
    info = dict(area.info)
    info.update({"outside_before": round(before, 3), "outside": round(after, 3), "kept": kept,
                 "on_region": round(1.0 - min(1.0, after * 5), 3)})
    return Found(corners, 0.0, kind, view, layout, neck, armpits, bottom, region, allowed, info=info)


# --- the whole photo ---------------------------------------------------------------------------


def detect(rgb: np.ndarray, runner: Any, saliency: Any, name: str = "") -> Detection:
    """Every garment in ``rgb`` (uint8, at most WORK px: the caller resizes)."""
    h, w = rgb.shape[:2]
    probs = runner.segment(rgb) if runner is not None else None
    if probs is None:
        probs = np.zeros((h, w, 6), np.float32)
        probs[..., 0] = 1.0
    fg = saliency(rgb) if saliency is not None else np.ones((h, w), np.float32)
    # The saliency model is usually the sharper; the segmenter's clothes class rescues a
    # garment it is unsure of (a tee lying on jeans under a hat).
    fg = np.maximum(fg, probs[..., CLOTHES])
    lab = _lab(rgb)
    hint = _kind_from_name(name)
    person_share = float(((probs[..., HAIR] + probs[..., BODY] + probs[..., FACE]) > 0.5).mean())
    persons = _persons(probs) if (runner is not None and is_person(rgb, probs, runner)) else []
    garments = _worn(rgb, probs, fg, lab, runner, persons, hint) if persons else []
    if not garments:
        garments = _flat(rgb, probs, fg, lab, hint)
    from scipy import ndimage as ndi

    union = np.zeros((h, w), bool)
    held = np.zeros((h, w), bool)
    for g in garments:
        union |= g.allowed  # the print may pass under hair, hands and held things
        enclosed = _ENCLOSED.get(id(g.region))
        if enclosed is not None and enclosed.any():
            cc, n = ndi.label(enclosed & (fg > 0.5))
            for i in range(1, n + 1):
                part = cc == i
                if part.sum() >= 0.002 * h * w:  # an object, not a speck in the fabric
                    held |= part
    _ENCLOSED.clear()
    occluder = np.clip(probs[..., HAIR] + probs[..., BODY] + probs[..., FACE] + probs[..., OTHERS], 0, 1)
    occluder = np.maximum(occluder, held.astype(np.float32))
    return Detection(garments, occluder, union.astype(np.float32), len(persons),
                     {"person_share": round(person_share, 4), "kind_hint": hint})
