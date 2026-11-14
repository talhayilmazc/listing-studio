"""Placing a design on a template made from a seller's Photoshop mockup (feature/psd-templates, step 2).

A PSD template is what the converter (``pipeline/psd_convert.py``) kept of the file, at the stored size (at most
2000 px on the long side, like every template photo):

* the **base**: every layer below the chosen smart object, composited (``mockup_photo.image_key``);
* the **placement** of the smart object: its own canvas size, its four corners on the page, its mesh warp (when it
  really bends), its layer mask, its blend mode and opacity (``mockup_photo.psd``);
* the **overlays**: every layer above it, each with its blend mode and opacity, in order.

``render`` does what Photoshop does when a design replaces the smart object's content: the design is fitted into
the smart object's canvas ("contain", centred), the canvas is mapped onto the corners (a homography, through the
mesh when there is one), masked, blended onto the base, and the overlays go on top. No fabric shading is added:
the mockup looks exactly as the seller's Photoshop file would. Pure numpy and Pillow, no network, no model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: Blend modes we reproduce; any other is drawn as normal and the seller is told (the converter's notes).
BLENDS = ("normal", "multiply", "screen", "overlay", "soft_light", "hard_light", "linear_burn", "linear_dodge",
          "darken", "lighten", "color_burn", "color_dodge", "difference")


@dataclass
class Overlay:
    image: np.ndarray  # uint8 RGBA, stored scale
    left: int
    top: int
    blend: str = "normal"
    opacity: float = 1.0


@dataclass
class Spec:
    width: int
    height: int
    canvas: tuple[int, int]  # the smart object's own canvas, in its pixels
    quad: list[tuple[float, float]]  # TL, TR, BR, BL on the stored page
    mesh: list[tuple[float, float]] | None = None  # 16 points, as shares of the canvas, row by row
    blend: str = "normal"
    opacity: float = 1.0
    overlays: list[Overlay] = field(default_factory=list)


def _f(a: np.ndarray) -> np.ndarray:
    return a.astype(np.float32) / 255.0


def blend(mode: str, b: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Photoshop's separable blend of source ``s`` over backdrop ``b`` (both 0..1, same shape)."""
    if mode == "multiply":
        return b * s
    if mode == "screen":
        return 1 - (1 - b) * (1 - s)
    if mode == "overlay":
        return np.where(b <= 0.5, 2 * b * s, 1 - 2 * (1 - b) * (1 - s))
    if mode == "hard_light":
        return np.where(s <= 0.5, 2 * b * s, 1 - 2 * (1 - b) * (1 - s))
    if mode == "soft_light":
        d = np.where(b <= 0.25, ((16 * b - 12) * b + 4) * b, np.sqrt(b))
        return np.where(s <= 0.5, b - (1 - 2 * s) * b * (1 - b), b + (2 * s - 1) * (d - b))
    if mode == "linear_burn":
        return np.clip(b + s - 1, 0, 1)
    if mode == "linear_dodge":
        return np.clip(b + s, 0, 1)
    if mode == "darken":
        return np.minimum(b, s)
    if mode == "lighten":
        return np.maximum(b, s)
    if mode == "color_burn":
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(b >= 1, 1.0, np.where(s <= 0, 0.0, 1 - np.minimum(1, (1 - b) / s)))
    if mode == "color_dodge":
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(b <= 0, 0.0, np.where(s >= 1, 1.0, np.minimum(1, b / (1 - s))))
    if mode == "difference":
        return np.abs(b - s)
    return s


def compose(page: np.ndarray, rgba: np.ndarray, left: int, top: int, mode: str, opacity: float,
            band: int = 512) -> None:
    """Blend ``rgba`` (uint8, placed at ``left, top``) onto ``page`` (float32 RGB 0..1) in place, in row bands
    so a large layer never needs a full-size float copy."""
    h, w = page.shape[:2]
    x0, y0 = max(0, left), max(0, top)
    x1, y1 = min(w, left + rgba.shape[1]), min(h, top + rgba.shape[0])
    if x0 >= x1 or y0 >= y1 or opacity <= 0:
        return
    for ys in range(y0, y1, band):
        ye = min(y1, ys + band)
        src = rgba[ys - top:ye - top, x0 - left:x1 - left]
        alpha = _f(src[..., 3:4]) * opacity
        if not alpha.any():
            continue
        b = page[ys:ye, x0:x1]
        s = _f(src[..., :3])
        page[ys:ye, x0:x1] = b * (1 - alpha) + blend(mode, b, s) * alpha


def fit_canvas(design: np.ndarray, canvas: tuple[int, int]) -> np.ndarray:
    """The design inside the smart object's canvas: as large as fits, centred, transparent around it (what
    Photoshop's placing does when the design is not the canvas's shape). Same shape: unchanged."""
    from PIL import Image

    cw, ch = canvas
    h, w = design.shape[:2]
    if (w, h) == (cw, ch):
        return design
    scale = min(cw / w, ch / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    im = Image.fromarray(design, "RGBA").resize((nw, nh), Image.LANCZOS)
    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    out.paste(im, ((cw - nw) // 2, (ch - nh) // 2))
    return np.asarray(out)


def homography(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> np.ndarray:
    """The 3 x 3 matrix taking the four ``src`` points to ``dst``."""
    rows, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y]); rhs.append(v)
    h = np.linalg.solve(np.array(rows, float), np.array(rhs, float))
    return np.append(h, 1.0).reshape(3, 3)


def _bern(t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Cubic Bernstein weights and their derivatives at ``t``: shape (4, n)."""
    mt = 1 - t
    w = np.stack([mt ** 3, 3 * t * mt ** 2, 3 * t ** 2 * mt, t ** 3])
    d = np.stack([-3 * mt ** 2, 3 * mt ** 2 - 6 * t * mt, 6 * t * mt - 3 * t ** 2, 3 * t ** 2])
    return w, d


def mesh_point(mesh: list[tuple[float, float]], u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Where the 4 x 4 bezier patch takes canvas share ``(u, v)``."""
    p = np.asarray(mesh, float).reshape(4, 4, 2)  # [row j][column i]
    bu, _ = _bern(u)
    bv, _ = _bern(v)
    x = np.einsum("in,jn,ji->n", bu, bv, p[..., 0])
    y = np.einsum("in,jn,ji->n", bu, bv, p[..., 1])
    return x, y


def _mesh_inverse(mesh: list[tuple[float, float]], s: np.ndarray, t: np.ndarray, steps: int = 12
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The canvas share ``(u, v)`` the patch takes to ``(s, t)``, by Newton's method; and where it converged."""
    p = np.asarray(mesh, float).reshape(4, 4, 2)
    u, v = s.copy(), t.copy()
    for _ in range(steps):
        bu, du = _bern(np.clip(u, 0, 1))
        bv, dv = _bern(np.clip(v, 0, 1))
        x = np.einsum("in,jn,ji->n", bu, bv, p[..., 0]) - s
        y = np.einsum("in,jn,ji->n", bu, bv, p[..., 1]) - t
        xu = np.einsum("in,jn,ji->n", du, bv, p[..., 0]); xv = np.einsum("in,jn,ji->n", bu, dv, p[..., 0])
        yu = np.einsum("in,jn,ji->n", du, bv, p[..., 1]); yv = np.einsum("in,jn,ji->n", bu, dv, p[..., 1])
        det = xu * yv - xv * yu
        det = np.where(np.abs(det) < 1e-12, 1e-12, det)
        u = u - (x * yv - y * xv) / det
        v = v - (y * xu - x * yu) / det
    bu, _ = _bern(np.clip(u, 0, 1))
    bv, _ = _bern(np.clip(v, 0, 1))
    err = np.hypot(np.einsum("in,jn,ji->n", bu, bv, p[..., 0]) - s, np.einsum("in,jn,ji->n", bu, bv, p[..., 1]) - t)
    ok = (err < 1e-3) & (u >= -1e-4) & (u <= 1 + 1e-4) & (v >= -1e-4) & (v <= 1 + 1e-4)
    return u, v, ok


def bends(mesh: list[tuple[float, float]] | None) -> bool:
    """Whether a mesh moves anything (an untouched custom warp is the regular grid)."""
    if not mesh or len(mesh) != 16:
        return False
    return any(abs(x - (k % 4) / 3) > 1e-3 or abs(y - (k // 4) / 3) > 1e-3 for k, (x, y) in enumerate(mesh))


def warp(canvas_rgba: np.ndarray, spec: Spec) -> tuple[np.ndarray, int, int]:
    """The smart object's canvas on the page: premultiplied float32 RGBA of the corners' bounding box, and its
    left and top. Sampled bilinearly; the canvas is first brought close to its size on the page so a large design
    is not aliased when it shrinks."""
    from PIL import Image
    from scipy.ndimage import map_coordinates

    quad = [(float(x), float(y)) for x, y in spec.quad]
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    left, top = max(0, int(np.floor(min(xs)))), max(0, int(np.floor(min(ys))))
    right, bottom = min(spec.width, int(np.ceil(max(xs))) + 1), min(spec.height, int(np.ceil(max(ys))) + 1)
    if right <= left or bottom <= top:
        return np.zeros((0, 0, 4), np.float32), 0, 0
    # Bring the canvas near its on-page size (never larger than it is).
    ch, cw = canvas_rgba.shape[:2]
    span = max(np.hypot(quad[1][0] - quad[0][0], quad[1][1] - quad[0][1]),
               np.hypot(quad[2][0] - quad[3][0], quad[2][1] - quad[3][1]))
    k = min(1.0, max(span / cw, 1e-3))
    if k < 0.98:
        canvas_rgba = np.asarray(Image.fromarray(canvas_rgba, "RGBA").resize(
            (max(2, round(cw * k)), max(2, round(ch * k))), Image.LANCZOS))
    ch, cw = canvas_rgba.shape[:2]
    pre = canvas_rgba.astype(np.float32)
    pre[..., :3] *= pre[..., 3:4] / 255.0

    gy, gx = np.mgrid[top:bottom, left:right].astype(np.float64)
    gx += 0.5
    gy += 0.5
    inv = np.linalg.inv(homography([(0, 0), (1, 0), (1, 1), (0, 1)], quad))
    den = inv[2, 0] * gx + inv[2, 1] * gy + inv[2, 2]
    s = ((inv[0, 0] * gx + inv[0, 1] * gy + inv[0, 2]) / den).ravel()
    t = ((inv[1, 0] * gx + inv[1, 1] * gy + inv[1, 2]) / den).ravel()
    if bends(spec.mesh):
        u, v, ok = _mesh_inverse(spec.mesh, s, t)  # type: ignore[arg-type]
    else:
        u, v = s, t
        ok = (u >= 0) & (u <= 1) & (v >= 0) & (v <= 1)
    cols = (u * cw - 0.5).reshape(gx.shape)
    rows = (v * ch - 0.5).reshape(gx.shape)
    out = np.empty(gx.shape + (4,), np.float32)
    for c in range(4):
        out[..., c] = map_coordinates(pre[..., c], [rows, cols], order=1, mode="constant", cval=0.0)
    out *= ok.reshape(gx.shape)[..., None]
    return out, left, top


def render(spec: Spec, base_rgb: np.ndarray, design_rgba: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    """The mockup, uint8 RGB at the stored size."""
    page = _f(base_rgb)
    canvas = fit_canvas(design_rgba, spec.canvas)
    placed, left, top = warp(canvas, spec)
    if placed.size:
        h, w = placed.shape[:2]
        alpha = placed[..., 3:4] / 255.0
        if mask is not None:
            alpha = alpha * _f(mask[top:top + h, left:left + w])[..., None]
        alpha = alpha * spec.opacity
        with np.errstate(divide="ignore", invalid="ignore"):
            colour = np.where(placed[..., 3:4] > 0, placed[..., :3] / np.maximum(placed[..., 3:4], 1e-6), 0.0)
        b = page[top:top + h, left:left + w]
        page[top:top + h, left:left + w] = b * (1 - alpha) + blend(spec.blend, b, np.clip(colour, 0, 1)) * alpha
    for o in spec.overlays:
        compose(page, o.image, o.left, o.top, o.blend, o.opacity)
    return np.clip(page * 255.0 + 0.5, 0, 255).astype(np.uint8)


def spec_from(data: dict[str, Any], overlays: list[np.ndarray]) -> Spec:
    """The stored spec (``mockup_photo.psd``) with its overlay images, in order."""
    so = data["placement"]
    return Spec(
        width=int(data["width"]), height=int(data["height"]), canvas=tuple(so["canvas"]),  # type: ignore[arg-type]
        quad=[tuple(p) for p in so["quad"]], mesh=[tuple(p) for p in so["mesh"]] if so.get("mesh") else None,
        blend=so.get("blend", "normal"), opacity=float(so.get("opacity", 1.0)),
        overlays=[Overlay(img, int(o["left"]), int(o["top"]), o.get("blend", "normal"), float(o.get("opacity", 1.0)))
                  for o, img in zip(data.get("overlays", []), overlays)],
    )


def load_dir(folder: str) -> tuple[Spec, np.ndarray, np.ndarray | None, np.ndarray | None, dict[str, Any]]:
    """What the converter wrote to ``folder``: the spec, the base, the mask and the current content."""
    import json
    import os

    from PIL import Image

    with open(os.path.join(folder, "spec.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    overlays = [np.asarray(Image.open(os.path.join(folder, o["file"])).convert("RGBA")) for o in data["overlays"]]
    base = np.asarray(Image.open(os.path.join(folder, "base.jpg")).convert("RGB"))
    mask_file = data["placement"].get("mask")
    mask = np.asarray(Image.open(os.path.join(folder, mask_file)).convert("L")) if mask_file else None
    content = (np.asarray(Image.open(os.path.join(folder, data["content"])).convert("RGBA"))
               if data.get("content") else None)
    return spec_from(data, overlays), base, mask, content, data
