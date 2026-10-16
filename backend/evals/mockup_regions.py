"""Each marked garment's visible region, for checking that no print area runs off it.

Evaluation only (dev dependencies: onnxruntime, scikit-image, scipy; the BiRefNet
weights are read from MOCKUP_EVAL_MODELS and never bundled). Per photo:

1. Foreground: BiRefNet "general" (MIT), at 1024 x 1024; cached in /out/masks/fg.
2. Per garment: a watershed on the colour gradient (CIELAB), seeded with a patch of
   the garment's own chest (from its landmarks), against seeds for everything else:
   the background, and foreground whose colour is far from every garment's. This
   separates a garment from props, skirts, jeans and the garment next to it.
3. Allowed in front of a garment (occlusion handles them, they are not "off the
   garment"): hair and accessories (the bundled MediaPipe segmenter) and anything
   the garment's region encloses (a mug held against the chest). Hands, blankets,
   bags and other garments lying on it are not allowed.

``outside_share`` is the share of an area's pixels outside that allowance.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

MODELS = Path(os.environ.get("MOCKUP_EVAL_MODELS", "/models"))
BIREFNET = "BiRefNet-general-epoch_244.onnx"
WORK = 1000  # working size (longest side) for the regions

_session = None


def _birefnet():  # noqa: ANN202
    global _session
    if _session is None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.enable_mem_pattern = False
        options.enable_cpu_mem_arena = False
        options.intra_op_num_threads = 4
        _session = ort.InferenceSession(str(MODELS / BIREFNET), options, providers=["CPUExecutionProvider"])
    return _session


def foreground(photo: Any, cache: Path) -> np.ndarray:
    """BiRefNet foreground probability (0..1) at the photo's size, cached as PNG."""
    path = cache / f"{photo.name}.png"
    if path.exists():
        return np.asarray(Image.open(path), dtype=np.float32) / 255
    im = Image.open(io.BytesIO(photo.jpeg)).convert("RGB")
    x = np.asarray(im.resize((1024, 1024), Image.BILINEAR), dtype=np.float32) / 255
    x = ((x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]).transpose(2, 0, 1)[None].astype(np.float32)
    session = _birefnet()
    y = session.run(None, {session.get_inputs()[0].name: x})[-1][0, 0]
    if y.min() < 0 or y.max() > 1:
        y = 1 / (1 + np.exp(-y))
    out = Image.fromarray((np.clip(y, 0, 1) * 255).astype(np.uint8)).resize(im.size, Image.BILINEAR)
    cache.mkdir(parents=True, exist_ok=True)
    out.save(path)
    return np.asarray(out, dtype=np.float32) / 255


def _classes(rgb: np.ndarray) -> np.ndarray | None:
    """MediaPipe multiclass probabilities (h, w, 6): background, hair, body, face, clothes, others."""
    from app.pipeline import mockup_vision as vision

    runner = vision.default_runner()
    if runner is None:
        return None
    return runner.segment(rgb)


def _quad_mask(quad, w: int, h: int) -> np.ndarray:  # noqa: ANN001
    im = Image.new("1", (w, h), 0)
    ImageDraw.Draw(im).polygon([(x * w, y * h) for x, y in quad], fill=1)
    return np.asarray(im, dtype=bool)


def regions(photo: Any, entry: dict[str, Any], frame, cache: Path) -> dict[str, Any]:  # noqa: ANN001
    """The visible region and the allowance of every garment in ``entry``, at WORK size.

    ``frame(garment) -> (ex, ey, origin, chest)`` in photo pixels (the eval's frame)."""
    from scipy import ndimage as ndi
    from skimage import color, filters, morphology, segmentation

    full = Image.open(io.BytesIO(photo.jpeg)).convert("RGB")
    scale = WORK / max(full.size)
    w, h = round(full.width * scale), round(full.height * scale)
    rgb = np.asarray(full.resize((w, h), Image.BILINEAR))
    fg = np.asarray(Image.fromarray((foreground(photo, cache / "fg") * 255).astype(np.uint8)).resize((w, h)),
                    dtype=np.float32) / 255
    lab = color.rgb2lab(filters.gaussian(rgb / 255.0, sigma=1.5, channel_axis=-1))
    gradient = sum(filters.sobel(lab[..., c]) for c in range(3))

    garments = entry.get("garments", [])
    probs = _classes(rgb)
    worn = entry.get("layout") in ("on a person", "on a hanger")
    # Shading changes lightness far more than hue and chroma; on a worn garment (folds,
    # shadows, a lit side) lightness is almost ignored.
    weight = np.array([0.1, 1.0, 1.0]) if worn else np.array([0.3, 1.0, 1.0])
    markers = np.zeros((h, w), np.int32)
    models, chests = [], []
    for i, g in enumerate(garments):
        ex, ey, origin, chest = frame(g)
        c = chest * scale
        chests.append(c)
        ox, oy = origin[0] * scale, origin[1] * scale
        pts = []
        for su, sv in ((-0.14, 0.28), (0.14, 0.28), (0.14, 0.55), (-0.14, 0.55)):
            pts.append(((ox + (ex[0] * su + ey[0] * sv) * c) / w, (oy + (ex[1] * su + ey[1] * sv) * c) / h))
        seed = _quad_mask(pts, w, h) & (fg > 0.5)
        if g.get("bottom"):  # never seed below the garment's lower edge
            seed[int(g["bottom"][1] * h):, :] = False
        vals = lab[seed] if seed.any() else lab[_quad_mask(pts, w, h)]
        med = np.median(vals, axis=0)
        spread = float(np.median(np.linalg.norm((vals - med) * weight, axis=1))) + 2.0
        models.append((med, spread))
        markers[seed] = i + 1
    other = len(garments) + 1
    if garments:
        floor = 25.0 if worn else 22.0
        dist = np.min([np.linalg.norm((lab - med) * weight, axis=-1) / max(spread * 5.0, floor) for med, spread in models], axis=0)
        far = (fg < 0.35) | (dist > 1.0)
        if probs is not None:  # skin and face compete with the garment; hair is allowed in front of it
            far |= (probs[..., 2] + probs[..., 3]) > 0.6
        markers[far & (markers == 0)] = other
    labels = segmentation.watershed(gradient, markers)

    if probs is not None:
        hair_or_accessory = (probs[..., 1] + probs[..., 5]) > 0.5
    else:
        hair_or_accessory = np.zeros((h, w), bool)
    out = {"size": (w, h), "fg": fg, "regions": [], "allowed": []}
    for i in range(len(garments)):
        region = labels == i + 1
        region = morphology.binary_opening(region, morphology.disk(2))
        lab_cc, _ = ndi.label(region)
        keep = np.unique(lab_cc[(markers == i + 1) & region])
        region = np.isin(lab_cc, keep[keep > 0])
        radius = max(2, round(0.035 * chests[i]))  # shadow pockets and seams inside the garment
        region = morphology.binary_closing(region, morphology.disk(radius)) & (fg > 0.3)
        filled = ndi.binary_fill_holes(region)
        hull = morphology.convex_hull_image(filled) if filled.any() else filled
        allowed = ndi.binary_fill_holes(filled | (hair_or_accessory & hull))
        out["regions"].append(filled)
        out["allowed"].append(allowed)
    return out


def outside_share(quad, allowed: np.ndarray) -> float:  # noqa: ANN001
    h, w = allowed.shape
    area = _quad_mask(quad, w, h)
    n = area.sum()
    return float((area & ~allowed).sum() / n) if n else 0.0


def offending_side(quad, allowed: np.ndarray) -> str:  # noqa: ANN001
    """Where most of an area's outside pixels are: "left", "right" or "bottom" (thirds
    of the area in its own frame)."""
    h, w = allowed.shape
    tl, tr, br, bl = [np.array(p) for p in quad]

    def part(u0: float, u1: float, v0: float, v1: float) -> int:
        corners = [tl + (tr - tl) * u + (bl - tl) * v for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
        m = _quad_mask([tuple(c) for c in corners], w, h)
        return int((m & ~allowed).sum())

    counts = {"left": part(0, 1 / 3, 0, 1), "right": part(2 / 3, 1, 0, 1), "bottom": part(0, 1, 2 / 3, 1)}
    return max(counts, key=counts.get)


def fit(quad, allowed: np.ndarray, limit: float = 0.02, step: float = 0.01) -> tuple[list[list[float]], list[float], float]:  # noqa: ANN001
    """Shrink ``quad`` until at most ``limit`` of it is outside: from the offending side,
    keeping its aspect and its top edge (the top rule). Returns the quad, [a, b] (the
    kept share of the original top edge, from its left) and the final outside share."""
    tl, tr, br, bl = [np.array(p, dtype=float) for p in quad]
    a, b = 0.0, 1.0

    def make(a_: float, b_: float) -> list[list[float]]:
        u, v = tr - tl, bl - tl
        k = b_ - a_
        p0 = tl + u * a_
        return [list(p0), list(p0 + u * k), list(p0 + u * k + v * k), list(p0 + v * k)]

    current = make(a, b)
    share = outside_share(current, allowed)
    while share > limit and b - a > 0.3:
        side = offending_side(current, allowed)
        if side == "left":
            a += step
        elif side == "right":
            b -= step
        else:
            a += step / 2
            b -= step / 2
        current = make(a, b)
        share = outside_share(current, allowed)
    return [[round(x, 4), round(y, 4)] for x, y in current], [round(a, 3), round(b, 3)], share


def overlay(photo: Any, data: dict[str, Any], areas: list, before: list | None = None) -> Image.Image:  # noqa: ANN001
    """The photo dimmed outside the garments' allowances; each garment's visible region
    outlined in its own colour, what is allowed in front of it (hair, accessories,
    enclosed objects) hatched; the areas in green (before in grey)."""
    from skimage import segmentation

    w, h = data["size"]
    im = Image.open(io.BytesIO(photo.jpeg)).convert("RGB").resize((w, h))
    a = np.asarray(im, dtype=np.float32)
    allowed_any = np.zeros((h, w), bool)
    for allowed in data["allowed"]:
        allowed_any |= allowed
    out = np.where(allowed_any[..., None], a, a * 0.28)
    palette = [(255, 50, 50), (40, 120, 255), (255, 210, 0), (190, 70, 255), (0, 220, 220), (255, 130, 0)]
    yy, xx = np.mgrid[0:h, 0:w]
    hatch = ((xx + yy) // 4) % 4 == 0
    for i, (region, allowed) in enumerate(zip(data["regions"], data["allowed"])):
        colour = np.array(palette[i % len(palette)], dtype=np.float32)
        extra = allowed & ~region & hatch
        out[extra] = out[extra] * 0.4 + colour * 0.6
        edge = segmentation.find_boundaries(region, mode="inner")
        edge = edge | np.roll(edge, 1, 0) | np.roll(edge, 1, 1)
        out[edge] = colour
    im = Image.fromarray(out.astype(np.uint8))
    d = ImageDraw.Draw(im)
    for quad in before or []:
        pts = [(x * w, y * h) for x, y in quad]
        d.line(pts + [pts[0]], fill=(170, 170, 170), width=2)
    for quad in areas:
        pts = [(x * w, y * h) for x, y in quad]
        d.line(pts + [pts[0]], fill=(0, 255, 90), width=3)
    return im


if __name__ == "__main__":  # the foreground of every photo, alone in its process (BiRefNet needs ~7 GB)
    from evals import mockup_eval as ev

    for path in ev.photos():
        photo = ev.decode(path)
        if not (ev.OUT / "masks" / "fg" / f"{photo.name}.png").exists():
            foreground(photo, ev.OUT / "masks" / "fg")
            print(path.name, flush=True)
