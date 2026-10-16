"""Finding a template's print area and what is in front of the garment.

Runs once per template, in the worker (workers/mockups.py), never per mockup.
Two open-source image-analysis models run on our own CPU through LiteRT; they
are bundled into the image at build time (pipeline/fetch_models.py, Apache-2.0)
and nothing is downloaded or sent anywhere at runtime: no photo leaves the
server. They analyse the seller's blank photo only; they draw nothing, and the
design's pixels never go near them.

* **The multiclass segmenter** (MediaPipe, 256 x 256) labels every pixel as
  background, hair, body-skin, face-skin, clothes or others (accessories). On a
  photo with a person, "clothes" is the garment mask and hair, skin and
  accessories are the **occluder**: the design is printed behind them.
* **The pose landmarker** (MediaPipe BlazePose GHUM 3D, "full") gives the
  shoulders and hips; the chest print area follows their position, size,
  perspective and rotation.
* **A flat-lay or ghost-mannequin photo** has no person: the garment's own
  outline gives the collar, shoulder line and side seams, and the print area
  sits below the collar between the side seams.

Every result carries a **confidence**. Below ``CHECK_BELOW`` the template still
works, but the seller is asked to check the print area. Anything that fails (no
models, an unusual photo) falls back to the outline or the old default area and
says so; nothing crashes.

Inference runs single-threaded (the same photo gives the same result), and what
it finds is stored with the template, so every mockup made from it is the same.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

logger = logging.getLogger(__name__)

#: Below this, the template's card asks the seller to check the print area.
#: The bundled model files (the same names pipeline/fetch_models.py writes at build time;
#: that module downloads, so nothing at runtime imports it).
SEGMENTER = "selfie_multiclass_256x256.tflite"
LANDMARKER = "pose_landmarks_detector.tflite"
CHECK_BELOW = 0.6
#: A photo shows a person when hair, face or skin covers at least this share of it.
PERSON_SHARE = 0.02
#: The largest side the analysis works at (the masks are scaled up afterwards).
ANALYSIS_SIZE = 512
#: The pose is trusted from this confidence; below it the garment's outline is used.
POSE_MIN = 0.35

SEGMENTER_INPUT = 256
LANDMARKER_INPUT = 256
BACKGROUND, HAIR, BODY, FACE, CLOTHES, OTHERS = range(6)

NOTES = {
    "pose": "Placed on the garment from the person's shoulders and the garment's own outline.",
    "outline": "Placed from the garment's collar, shoulders and sides.",
    "garments": "Found {n} garments: each has its own print area.",
    "default": "The garment's shape could not be found, so the print area is a default. Check it.",
    "pose_unsure": "The person's pose was unclear, so the garment's outline was used. Check the print area.",
    "fallback": "The photo could not be analysed; the garment's outline was used instead. Check the print area.",
}

Area = list[list[float]]


class Runner(Protocol):
    """What the analysis needs from the models (tests give a stand-in)."""

    def segment(self, rgb: np.ndarray) -> np.ndarray:
        """(h, w, 3) uint8 -> (h, w, 6) class probabilities at the same size."""

    def landmarks(self, rgb: np.ndarray, box: tuple[int, int, int, int]) -> tuple[np.ndarray, float]:
        """The person in ``box`` -> ((33, 3) x, y, visibility in ``rgb`` pixels, presence)."""


def _sigmoid(x: Any) -> Any:
    return 1.0 / (1.0 + np.exp(-x))


class LiteRTRunner:
    """The bundled MediaPipe models on LiteRT, single-threaded (deterministic)."""

    def __init__(self, directory: str | Path) -> None:
        from ai_edge_litert.interpreter import Interpreter

        directory = Path(directory)
        self._segmenter = Interpreter(model_path=str(directory / SEGMENTER), num_threads=1)
        self._segmenter.allocate_tensors()
        self._landmarker = Interpreter(model_path=str(directory / LANDMARKER), num_threads=1)
        self._landmarker.allocate_tensors()
        self._lock = threading.Lock()

    def segment(self, rgb: np.ndarray) -> np.ndarray:
        from PIL import Image

        h, w = rgb.shape[:2]
        side = max(h, w)
        square = np.zeros((side, side, 3), dtype=np.uint8)
        top, left = (side - h) // 2, (side - w) // 2
        square[top:top + h, left:left + w] = rgb
        resized = np.asarray(Image.fromarray(square).resize((SEGMENTER_INPUT, SEGMENTER_INPUT), Image.BILINEAR), dtype=np.float32)
        tensor = (resized / 127.5 - 1.0)[None]  # the model's metadata: mean 127.5, std 127.5
        with self._lock:
            self._segmenter.set_tensor(self._segmenter.get_input_details()[0]["index"], tensor)
            self._segmenter.invoke()
            out = self._segmenter.get_tensor(self._segmenter.get_output_details()[0]["index"])[0].copy()
        # Back to the photo's own frame and size, one class at a time.
        scale = SEGMENTER_INPUT / side
        crop = (round(left * scale), round(top * scale), round((left + w) * scale), round((top + h) * scale))
        channels = []
        for c in range(out.shape[2]):
            plane = Image.fromarray(out[:, :, c].astype(np.float32), mode="F").crop(crop).resize((w, h), Image.BILINEAR)
            channels.append(np.asarray(plane, dtype=np.float32))
        probs = np.clip(np.stack(channels, axis=-1), 0.0, 1.0)
        return probs / np.maximum(probs.sum(axis=-1, keepdims=True), 1e-6)

    def landmarks(self, rgb: np.ndarray, box: tuple[int, int, int, int]) -> tuple[np.ndarray, float]:
        from PIL import Image

        x0, y0, x1, y1 = box
        side = max(x1 - x0, y1 - y0) * 1.25  # the model wants a margin around the body
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        left, top = cx - side / 2, cy - side / 2
        crop = Image.fromarray(rgb).crop((round(left), round(top), round(left + side), round(top + side)))
        tensor = (np.asarray(crop.resize((LANDMARKER_INPUT, LANDMARKER_INPUT), Image.BILINEAR), dtype=np.float32) / 255.0)[None]
        with self._lock:
            self._landmarker.set_tensor(self._landmarker.get_input_details()[0]["index"], tensor)
            self._landmarker.invoke()
            outputs = {tuple(d["shape"]): self._landmarker.get_tensor(d["index"]) for d in self._landmarker.get_output_details()}
        points = outputs[(1, 195)][0].reshape(39, 5)[:33]
        presence = float(_sigmoid(outputs[(1, 1)][0][0]))
        k = round(side) / LANDMARKER_INPUT
        out = np.stack([points[:, 0] * k + round(left), points[:, 1] * k + round(top), _sigmoid(points[:, 3])], axis=1)
        return out.astype(np.float64), presence


_runner: Any = None
_runner_lock = threading.Lock()


def default_runner() -> Runner | None:
    """The bundled models, loaded once per process; None when they are not here."""
    global _runner
    with _runner_lock:
        if _runner is None:
            from app.core.config import get_settings

            directory = Path(get_settings().mockup_models_dir)
            if not ((directory / SEGMENTER).exists() and (directory / LANDMARKER).exists()):
                logger.warning("mockup models not found in %s: templates use the garment's outline only", directory)
                _runner = False
            else:
                try:
                    _runner = LiteRTRunner(directory)
                except Exception:  # a broken runtime must not stop templates being made
                    logger.exception("mockup models could not be loaded: templates use the garment's outline only")
                    _runner = False
        return _runner or None


# --- geometry -----------------------------------------------------------------------------------


def _quad_ok(area: Area) -> bool:
    signs = []
    for i in range(4):
        (ax, ay), (bx, by), (cx, cy) = area[i], area[(i + 1) % 4], area[(i + 2) % 4]
        signs.append((bx - ax) * (cy - by) - (by - ay) * (cx - bx))
    return all(s > 0 for s in signs) and all(0.0 <= v <= 1.0 for c in area for v in c)


def _normalise(points: list[tuple[float, float]], w: int, h: int) -> Area:
    return [[round(min(1.0, max(0.0, x / w)), 4), round(min(1.0, max(0.0, y / h)), 4)] for x, y in points]


def _inside(mask: np.ndarray, area_px: list[tuple[float, float]]) -> float:
    """Share of a 5 x 5 grid of points of the quad that fall on the garment."""
    h, w = mask.shape
    (tl, tr, br, bl) = area_px
    hits = total = 0
    for v in np.linspace(0.1, 0.9, 5):
        for u in np.linspace(0.1, 0.9, 5):
            top = (tl[0] + (tr[0] - tl[0]) * u, tl[1] + (tr[1] - tl[1]) * u)
            bottom = (bl[0] + (br[0] - bl[0]) * u, bl[1] + (br[1] - bl[1]) * u)
            x, y = top[0] + (bottom[0] - top[0]) * v, top[1] + (bottom[1] - top[1]) * v
            total += 1
            if 0 <= int(x) < w and 0 <= int(y) < h and mask[int(y), int(x)]:
                hits += 1
    return hits / total


def pose_area(points: np.ndarray, presence: float, garment: np.ndarray) -> tuple[Area | None, float]:
    """The chest print area from the shoulders (11, 12) and hips (23, 24), and how sure."""
    h, w = garment.shape
    shoulders = sorted([points[11], points[12]], key=lambda p: p[0])
    hips = sorted([points[23], points[24]], key=lambda p: p[0])
    s_left, s_right = np.array(shoulders[0][:2]), np.array(shoulders[1][:2])
    vis_shoulders = float(min(shoulders[0][2], shoulders[1][2]))
    span = float(np.linalg.norm(s_right - s_left))
    if span < 0.08 * w or vis_shoulders < 0.3:
        return None, 0.0
    across = (s_right - s_left) / span
    down = np.array([-across[1], across[0]])
    if down[1] < 0:
        down = -down
    hips_seen = float(min(hips[0][2], hips[1][2]))
    h_left, h_right = np.array(hips[0][:2]), np.array(hips[1][:2])
    torso = float(np.dot(((h_left + h_right) / 2) - ((s_left + s_right) / 2), down))
    if hips_seen < 0.5 or not (0.6 * span < torso < 2.6 * span):
        # Hips out of the photo (a chest-up shot): the torso goes on as long as usual.
        h_left, h_right = s_left + down * 1.3 * span, s_right + down * 1.3 * span
        hips_seen = 0.0

    def at(u: float, v: float) -> tuple[float, float]:
        top = s_left + (s_right - s_left) * u
        bottom = h_left + (h_right - h_left) * u
        p = top + (bottom - top) * v
        return float(p[0]), float(p[1])

    quad = [at(0.22, 0.16), at(0.78, 0.16), at(0.78, 0.62), at(0.22, 0.62)]
    area = _normalise(quad, w, h)
    if not _quad_ok(area):
        return None, 0.0
    on_garment = _inside(garment >= 128, quad)
    confidence = presence * vis_shoulders * (1.0 if hips_seen else 0.85) * on_garment
    return area, round(float(min(1.0, max(0.0, confidence))), 3)


def outline_area(mask: np.ndarray) -> tuple[Area | None, float]:
    """The print area from a garment outline (flat lay, ghost mannequin): below the
    collar, between the side seams, along the shoulder line's angle."""
    h, w = mask.shape
    solid = mask >= 128
    rows = np.where(solid.any(axis=1))[0]
    cols = np.where(solid.any(axis=0))[0]
    if rows.size < 10 or cols.size < 10:
        return None, 0.0
    y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
    gh, gw = y1 - y0, x1 - x0
    if gh * gw > 0.97 * h * w:  # the whole photo: no outline to read
        return None, 0.0
    torso = [y for y in range(int(y0 + 0.55 * gh), int(y0 + 0.9 * gh)) if solid[y].any()]
    if len(torso) < 5:
        return None, 0.0
    lefts = np.array([np.argmax(solid[y]) for y in torso], dtype=np.float64)
    rights = np.array([w - 1 - np.argmax(solid[y][::-1]) for y in torso], dtype=np.float64)
    widths = rights - lefts
    tw, tc = float(np.median(widths)), float(np.median((lefts + rights) / 2))
    if tw < 0.1 * w:
        return None, 0.0
    stability = 1.0 - min(1.0, float(np.std(widths)) / tw * 4)
    symmetry = 1.0 - min(1.0, float(np.mean(np.abs((tc - lefts) - (rights - tc)))) / tw * 2)

    def first(x: float) -> int | None:
        column = solid[:, min(w - 1, max(0, int(x)))]
        hit = np.where(column)[0]
        return int(hit[0]) if hit.size else None

    collar = first(tc)
    shoulder_l, shoulder_r = first(tc - 0.3 * tw), first(tc + 0.3 * tw)
    if collar is None or shoulder_l is None or shoulder_r is None:
        return None, 0.0
    dip = collar - min(shoulder_l, shoulder_r)
    collar_found = dip > 0.01 * gh
    angle = math.atan2(shoulder_r - shoulder_l, 0.6 * tw)
    angle = max(-math.radians(15), min(math.radians(15), angle))
    top = max(collar, min(shoulder_l, shoulder_r)) + 0.07 * gh
    pw = 0.6 * tw
    ph = min(pw * 1.25, y0 + 0.92 * gh - top)
    if ph < 0.1 * gh:
        return None, 0.0
    cx, cy = tc, top + ph / 2
    cos, sin = math.cos(angle), math.sin(angle)
    corners = [(-pw / 2, -ph / 2), (pw / 2, -ph / 2), (pw / 2, ph / 2), (-pw / 2, ph / 2)]
    quad = [(cx + x * cos - y * sin, cy + x * sin + y * cos) for x, y in corners]
    area = _normalise(quad, w, h)
    if not _quad_ok(area):
        return None, 0.0
    confidence = 0.35 + (0.25 if collar_found else 0.0) + 0.2 * stability + 0.2 * symmetry
    confidence *= _inside(solid, quad)
    return area, round(float(min(1.0, max(0.0, confidence))), 3)


def _with_holes_filled(garment: np.ndarray, occluder: np.ndarray) -> np.ndarray:
    """The garment including what lies in front of it (hair, an arm): the holes the
    occluders leave in it are closed, so the print can go behind them (occlusion on)
    or over them (off). Only occluder pixels are added, nothing else."""
    from PIL import Image, ImageFilter

    size = max(3, (min(garment.shape) // 8) | 1)  # an odd filter about 12% of the photo
    closed = Image.fromarray(garment).filter(ImageFilter.MaxFilter(size)).filter(ImageFilter.MinFilter(size))
    return np.maximum(garment, np.minimum(np.asarray(closed), occluder)).astype(np.uint8)


# --- the analysis -------------------------------------------------------------------------------


def analyse_garments(rgb: np.ndarray, runner: Runner | None, saliency: Any, name: str = "") -> "Analysis | None":
    """Every garment in the photo (pipeline/garments.py): a print area, confidence, kind
    and view per garment, in reading order (rows top to bottom, then left to right).
    None when no garment was found (the caller falls back to the outline)."""
    from app.pipeline import garments as garments_

    found = garments_.detect(rgb, runner, saliency, name)
    if not found.garments:
        return None
    h, w = rgb.shape[:2]
    chests = sorted(math.dist(*g.armpits) for g in found.garments)
    row = 0.6 * chests[len(chests) // 2]
    # Reading order: rows (collars within ``row`` of the row's first), then left to right.
    rows: list[list[Any]] = []
    for g in sorted(found.garments, key=lambda g: g.neck[1]):
        if rows and g.neck[1] - rows[-1][0].neck[1] <= row:
            rows[-1].append(g)
        else:
            rows.append([g])
    ordered = [g for r in rows for g in sorted(r, key=lambda g: g.neck[0])]
    items = []
    for g in ordered:
        items.append({
            "print_area": [[round(x / w, 4), round(y / h, 4)] for x, y in g.corners],
            "confidence": round(float(g.confidence), 3), "kind": g.kind, "view": g.view, "layout": g.layout,
            "notes": list(g.notes),
        })
    first = items[0]
    confidence = min(i["confidence"] for i in items)
    method = "pose" if found.persons else "outline"
    n = len(items)
    note = NOTES["pose" if found.persons else "outline"] if n == 1 else NOTES["garments"].format(n=n)
    details = {**found.details, "garments": n, "persons": found.persons}
    garment = np.clip(found.garment_mask * 255.0, 0, 255).astype(np.uint8)
    occluder = np.clip(found.occluder * 255.0, 0, 255).astype(np.uint8) if found.persons or found.occluder.max() > 0.5 else None
    return Analysis(method, confidence, first["print_area"], garment, occluder, note, details, garments=items,
                    view=first["view"], kind=first["kind"])


@dataclass
class Analysis:
    #: "pose" (from the person), "outline" (from the garment's shape), "default" (neither).
    method: str
    confidence: float
    print_area: Area | None
    #: On a person photo, the garment from the segmentation (uint8, the analysis size).
    garment: np.ndarray | None = None
    #: Hair, skin and accessories in front of the garment (uint8 0..255, soft edges).
    occluder: np.ndarray | None = None
    note: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    #: Every garment found: print_area, confidence, kind, view, layout (reading order).
    garments: list[dict[str, Any]] = field(default_factory=list)
    view: str | None = None
    kind: str | None = None

    @property
    def needs_check(self) -> bool:
        return self.confidence < CHECK_BELOW


def analyse(rgb: np.ndarray, base_mask: np.ndarray, runner: Runner | None) -> Analysis:
    """What the photo shows, at the analysis size: ``rgb`` (h, w, 3) and the
    outline-based mask (h, w) the template had before. Never raises."""
    details: dict[str, Any] = {"models": runner is not None}
    if runner is not None:
        try:
            probs = runner.segment(rgb)
            classes = probs.argmax(axis=-1)
            person = float(np.isin(classes, (HAIR, BODY, FACE)).mean())
            details["person_share"] = round(person, 4)
            if person >= PERSON_SHARE:
                occluder = np.clip((probs[..., HAIR] + probs[..., BODY] + probs[..., FACE] + probs[..., OTHERS]) * 255.0, 0, 255).astype(np.uint8)
                garment = _with_holes_filled(np.clip(probs[..., CLOTHES] * 255.0, 0, 255).astype(np.uint8), occluder)
                ys, xs = np.where(classes != BACKGROUND)
                box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
                points, presence = runner.landmarks(rgb, box)
                details["presence"] = round(float(presence), 3)
                area, confidence = pose_area(points, presence, garment)
                if area is not None and confidence >= POSE_MIN:
                    return Analysis("pose", confidence, area, garment, occluder, NOTES["pose"], details)
                area, confidence = outline_area(garment)
                if area is not None:  # a person whose pose is unclear: always asked to check
                    confidence = min(round(confidence * 0.8, 3), CHECK_BELOW - 0.01)
                    return Analysis("outline", confidence, area, garment, occluder, NOTES["pose_unsure"], details)
                return Analysis("default", 0.0, None, garment, occluder, NOTES["default"], details)
        except Exception:  # an unusual photo or a model fault: the outline still works
            logger.exception("mockup template analysis failed; using the garment's outline")
            area, confidence = outline_area(base_mask)
            details["failed"] = True
            return Analysis("outline" if area else "default", min(confidence, CHECK_BELOW - 0.01) if area else 0.0,
                            area, None, None, NOTES["fallback"], details)
    area, confidence = outline_area(base_mask)
    if area is not None:
        return Analysis("outline", confidence, area, None, None, NOTES["outline"], details)
    return Analysis("default", 0.0, None, None, None, NOTES["default"], details)
