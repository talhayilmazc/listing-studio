"""Turn a seller's Photoshop mockup into a Listyro template (feature/psd-templates, step 1).

Runs in a child process with memory and CPU limits (``python -m app.pipeline.psd_convert <psd> <out> [choice]``,
started through ``psd_sandbox.run_child``): a hostile or broken file fails only its own conversion.

What it writes to ``<out>``:

* ``base.jpg``: every visible layer below the chosen smart object, composited at full resolution, then reduced to
  the stored size (at most ``TARGET`` px on the long side, like every template photo);
* ``overlay-N.png``: every visible layer above it, one file each, with its blend mode and opacity in the spec;
* ``mask.png``: the smart object's layer mask (and the layer it is clipped to), when it has one;
* ``content.png``: what the smart object holds now, at its canvas size (the preview and the golden comparison);
* ``spec.json``: the placement (canvas, corners, mesh, blend, opacity), the overlays, the smart objects a seller
  may choose from, and plain notes on anything drawn differently from Photoshop.

The PSD itself is not changed. Nothing is read from disk but the PSD (a linked smart object is never followed).
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.pipeline import psd_read, psd_template

#: The stored size: the long side of a template photo (``mockups``: outputs are at most 2000 px).
TARGET = 2000
#: Limits a PSD must meet (feature/psd-templates step 3); the API checks the file size before.
MAX_SIDE = 30_000
MAX_LAYERS = 2_000
#: Smart objects named like this are where a design goes, before the largest one.
DESIGN_NAME = re.compile(r"design|place|smart\s*object|your\s*(art|image|logo)|replace|insert", re.I)
#: Blend modes as psd-tools names them, mapped to the renderer's.
_BLENDS = {"normal": "normal", "pass_through": "normal", "multiply": "multiply", "screen": "screen",
           "overlay": "overlay", "soft_light": "soft_light", "hard_light": "hard_light",
           "linear_burn": "linear_burn", "linear_dodge": "linear_dodge", "darken": "darken", "lighten": "lighten",
           "color_burn": "color_burn", "color_dodge": "color_dodge", "difference": "difference"}

TOO_LARGE = ("This file is larger than 30,000 pixels on a side. Save a smaller copy in Photoshop "
             "(Image > Image Size) and upload that.")
TOO_MANY = ("This file has more than 2,000 layers. Merge or delete the layers the mockup does not need, "
            "then upload it again.")
NO_SMART_OBJECT = ("This file has no smart object to put a design in. In Photoshop, right-click the layer where "
                   "the design goes, choose Convert to Smart Object, save, and upload it again.")
NOT_EMBEDDED = ("The smart object in this file is linked to another file, so its content is not inside the PSD. "
                "In Photoshop, right-click it, choose Embed Linked, save, and upload it again.")
NOT_RGB = "This file is not in RGB colour. In Photoshop choose Image > Mode > RGB Color, save, and upload it again."


class Refused(Exception):
    """The file cannot become a template; the message tells the seller what to do."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.plain = message


@dataclass
class Unit:
    """A layer to draw: its pixels come from psd-tools when drawn (one at a time, to bound memory)."""

    layer: Any
    blend: str
    opacity: float
    clip_base: Any = None  # the layer this one is clipped to


@dataclass
class Notes:
    lines: list[str] = field(default_factory=list)

    def add(self, line: str) -> None:
        if line not in self.lines:
            self.lines.append(line)


def _blend(layer: Any, notes: Notes) -> str:
    name = getattr(layer.blend_mode, "name", str(layer.blend_mode)).lower()
    if name in _BLENDS:
        return _BLENDS[name]
    notes.add(f"This template uses the {name.replace('_', ' ')} blend mode, which we draw as normal. "
              "Colours there can look a little different from Photoshop.")
    return "normal"


def _units(group: Any, notes: Notes, opacity: float = 1.0) -> list[Unit]:
    """Visible layers bottom to top, groups opened (their opacity carried down)."""
    from psd_tools.api.layers import AdjustmentLayer

    out: list[Unit] = []
    base = None
    for layer in group:
        if not layer.visible:
            continue
        if layer.is_group():
            mode = getattr(layer.blend_mode, "name", "").lower()
            if mode not in ("pass_through", "normal"):
                notes.add("A layer group uses its own blend mode; we draw its layers one by one. "
                          "Colours there can look a little different from Photoshop.")
            out += _units(layer, notes, opacity * layer.opacity / 255)
            continue
        if isinstance(layer, AdjustmentLayer):
            notes.add("This template has an adjustment layer (for example Curves or Hue/Saturation). We do not "
                      "apply it, so colours can look a little different from Photoshop.")
            continue
        if layer.has_effects() and any(getattr(e, "enabled", True) for e in layer.effects):
            names = sorted({type(e).__name__ for e in layer.effects if getattr(e, "enabled", True)})
            notes.add(f"A layer has a layer style ({', '.join(names)}). We show the layer without it.")
        fill = getattr(layer, "fill_opacity", 255) / 255
        unit = Unit(layer, _blend(layer, notes), opacity * layer.opacity / 255 * fill)
        if layer.clipping and base is not None:
            unit.clip_base = base
        else:
            base = layer
        out.append(unit)
    return out


def _pixels(layer: Any, notes: Notes) -> tuple[np.ndarray, int, int] | None:
    """A layer's own pixels as uint8 RGBA with its layer mask applied, and its left and top."""
    image = None
    try:
        image = layer.topil()
    except Exception:  # noqa: BLE001 - try drawing it instead
        image = None
    if image is None:
        try:
            image = layer.composite()
        except Exception:  # noqa: BLE001
            notes.add("A shape or fill layer could not be drawn and is left out.")
            return None
    if image is None:
        return None
    rgba = np.asarray(image.convert("RGBA")).copy()
    left, top = layer.left, layer.top
    if layer.has_mask() and not layer.mask.disabled:
        _apply_mask(rgba, left, top, layer.mask)
    return rgba, left, top


def _apply_mask(rgba: np.ndarray, left: int, top: int, mask: Any) -> None:
    h, w = rgba.shape[:2]
    full = np.full((h, w), mask.background_color, np.uint8)
    try:
        img = mask.topil()
    except Exception:  # noqa: BLE001
        img = None
    if img is not None:
        m = np.asarray(img.convert("L"))
        x0, y0 = mask.left - left, mask.top - top
        sx0, sy0 = max(0, -x0), max(0, -y0)
        dx0, dy0 = max(0, x0), max(0, y0)
        cw, ch = min(w - dx0, m.shape[1] - sx0), min(h - dy0, m.shape[0] - sy0)
        if cw > 0 and ch > 0:
            full[dy0:dy0 + ch, dx0:dx0 + cw] = m[sy0:sy0 + ch, sx0:sx0 + cw]
    rgba[..., 3] = (rgba[..., 3].astype(np.uint16) * full // 255).astype(np.uint8)


def _clip(rgba: np.ndarray, left: int, top: int, base: tuple[np.ndarray, int, int] | None) -> None:
    """Keep only what lies over the clip base's pixels."""
    if base is None:
        rgba[..., 3] = 0
        return
    b, bl, bt = base
    h, w = rgba.shape[:2]
    alpha = np.zeros((h, w), np.uint16)
    x0, y0 = bl - left, bt - top
    sx0, sy0, dx0, dy0 = max(0, -x0), max(0, -y0), max(0, x0), max(0, y0)
    cw, ch = min(w - dx0, b.shape[1] - sx0), min(h - dy0, b.shape[0] - sy0)
    if cw > 0 and ch > 0:
        alpha[dy0:dy0 + ch, dx0:dx0 + cw] = b[sy0:sy0 + ch, sx0:sx0 + cw, 3]
    rgba[..., 3] = (rgba[..., 3].astype(np.uint16) * alpha // 255).astype(np.uint8)


def compose_u8(page: np.ndarray, rgba: np.ndarray, left: int, top: int, mode: str, opacity: float,
               band: int = 256) -> None:
    """``psd_template.compose`` on a uint8 page, band by band (a full-size float copy of a 6000 px page is 430 MB)."""
    h, w = page.shape[:2]
    x0, y0 = max(0, left), max(0, top)
    x1, y1 = min(w, left + rgba.shape[1]), min(h, top + rgba.shape[0])
    for ys in range(y0, y1, band):
        ye = min(y1, ys + band)
        part = page[ys:ye, x0:x1].astype(np.float32) / 255.0
        psd_template.compose(part, rgba[ys - top:ye - top, x0 - left:x1 - left], 0, 0, mode, opacity, band=band)
        page[ys:ye, x0:x1] = np.clip(part * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _reduce(rgba: np.ndarray, left: int, top: int, scale: float) -> tuple[np.ndarray, int, int]:
    """A layer brought to the stored scale, on whole stored pixels (the box keeps it aligned)."""
    from PIL import Image

    if scale >= 1:
        return rgba, left, top
    h, w = rgba.shape[:2]
    sl, st = int(np.floor(left * scale)), int(np.floor(top * scale))
    sr, sb = int(np.ceil((left + w) * scale)), int(np.ceil((top + h) * scale))
    pad = int(np.ceil(1 / scale)) + 1  # rounding the box out to whole stored pixels can reach 1/scale beyond
    padded = np.zeros((h + 2 * pad, w + 2 * pad, 4), np.uint8)
    padded[pad:pad + h, pad:pad + w] = rgba
    box = (sl / scale - left + pad, st / scale - top + pad, sr / scale - left + pad, sb / scale - top + pad)
    out = Image.fromarray(padded, "RGBA").resize((max(1, sr - sl), max(1, sb - st)), Image.LANCZOS, box=box)
    return np.asarray(out), sl, st


def _content(so: Any, canvas: tuple[int, int], notes: Notes) -> np.ndarray | None:
    """What the smart object holds now, at its canvas size (Photoshop's own merged image when it has one)."""
    from PIL import Image

    try:
        data = so.data
        if so.is_psd():
            from psd_tools import PSDImage

            inner = PSDImage.open(io.BytesIO(data))
            image = inner.topil() or inner.composite()
        else:
            image = Image.open(io.BytesIO(data))
            image.load()
        image = image.convert("RGBA")
        if image.size != canvas:
            image = image.resize(canvas, Image.LANCZOS)
        return np.asarray(image)
    except Exception:  # noqa: BLE001 - only the golden comparison needs it (a vector placeholder needs aggdraw,
        return None    # which the app does not install: the seller's design replaces it, the preview uses a sample)


def choose(options: list[dict[str, Any]]) -> int:
    """The smart object a design goes in by default: one named like a design area, else the largest."""
    named = [o for o in options if DESIGN_NAME.search(o["name"] or "")]
    pool = named or options
    return max(pool, key=lambda o: o["area"])["index"]


def convert(path: str, out: str, choice: int | None = None) -> dict[str, Any]:
    from PIL import Image
    from psd_tools import PSDImage
    from psd_tools.constants import Tag

    psd = PSDImage.open(path)
    W, H = psd.width, psd.height
    if max(W, H) > MAX_SIDE:
        raise Refused(TOO_LARGE)
    if sum(1 for _ in psd.descendants()) > MAX_LAYERS:
        raise Refused(TOO_MANY)
    if getattr(psd.color_mode, "name", "") != "RGB":
        raise Refused(NOT_RGB)
    notes = Notes()
    if int(psd.depth) != 8:
        notes.add(f"This file is {psd.depth}-bit; we read it as 8-bit, as most mockups are.")

    units = _units(psd, notes)
    smart = [i for i, u in enumerate(units) if u.layer.kind == "smartobject"]
    if not smart:
        raise Refused(NO_SMART_OBJECT)
    options = []
    for i in smart:
        layer = units[i].layer
        config = None
        for key in (Tag.SMART_OBJECT_LAYER_DATA1, Tag.SMART_OBJECT_LAYER_DATA2):
            block = layer.tagged_blocks.get_data(key)
            if block is not None:
                config = block.data
                break
        quad = (psd_read._quad(psd_read._numbers(psd_read._get(config, "nonAffineTransform")))
                or psd_read._quad(psd_read._numbers(psd_read._get(config, "Trnf"))))
        try:
            embedded = layer.smart_object.kind == "data"
        except ValueError:
            embedded = False
        options.append({"index": len(options), "unit": i, "name": str(layer.name), "quad": quad,
                        "area": psd_read.quad_area(quad), "embedded": embedded, "config": config})
    usable = [o for o in options if o["quad"] and o["embedded"]]
    if not usable:
        raise Refused(NOT_EMBEDDED if any(o["quad"] for o in options) else NO_SMART_OBJECT)
    if choice is None or not any(o["index"] == choice and o in usable for o in options):
        choice = choose(usable)
    chosen = options[choice]
    so_unit = units[chosen["unit"]]
    so_layer = so_unit.layer
    config = chosen["config"]

    scale = min(1.0, TARGET / max(W, H))
    sw, sh = max(1, round(W * scale)), max(1, round(H * scale))

    # Below: composited at full resolution (uint8, band by band), then reduced once.
    page = np.full((H, W, 3), 255, np.uint8)
    last_base: dict[int, tuple[np.ndarray, int, int] | None] = {}
    for i, unit in enumerate(units[:chosen["unit"]]):
        px = _pixels(unit.layer, notes)
        if px is None:
            continue
        rgba, left, top = px
        if unit.clip_base is not None:
            _clip(rgba, left, top, last_base.get(id(unit.clip_base)))
        else:
            last_base[id(unit.layer)] = (rgba, left, top)
        compose_u8(page, rgba, left, top, unit.blend, unit.opacity)
        del rgba
    base = Image.fromarray(page, "RGB")
    del page
    if scale < 1:
        base = base.resize((sw, sh), Image.LANCZOS)
    base.save(os.path.join(out, "base.jpg"), "JPEG", quality=95, subsampling=0)
    del base

    # The smart object: its mask (and the layer it is clipped to), its canvas, its corners, its warp.
    mask_file = None
    so_mask = None
    if so_layer.has_mask() and not so_layer.mask.disabled:
        holder = np.zeros((H, W, 4), np.uint8)
        holder[..., 3] = 255
        _apply_mask(holder, 0, 0, so_layer.mask)
        so_mask = holder[..., 3].copy()
        del holder
    if so_unit.clip_base is not None:
        base_px = last_base.get(id(so_unit.clip_base))
        clip = np.zeros((H, W, 4), np.uint8)
        clip[..., 3] = 255
        _clip(clip, 0, 0, base_px)
        so_mask = clip[..., 3] if so_mask is None else (so_mask.astype(np.uint16) * clip[..., 3] // 255).astype(np.uint8)
        del clip
    if so_mask is not None:
        m = Image.fromarray(so_mask, "L")
        if scale < 1:
            m = m.resize((sw, sh), Image.LANCZOS)
        m.save(os.path.join(out, "mask.png"))
        mask_file = "mask.png"
        del so_mask

    so = so_layer.smart_object
    size = psd_read._get(config, "Sz  ")
    canvas = None
    try:
        canvas = (int(round(float(psd_read._get(size, "Wdth").value))), int(round(float(psd_read._get(size, "Hght").value))))
    except Exception:  # noqa: BLE001
        canvas = None
    content = _content(so, canvas or (1000, 1000), notes)
    if canvas is None:
        canvas = (content.shape[1], content.shape[0]) if content is not None else (1000, 1000)
    warp = psd_read._get(config, "warp")
    style = psd_read._name(psd_read._get(warp, "warpStyle")) if warp is not None else "warpNone"
    mesh = None
    points = psd_read.mesh_points(warp) if warp is not None else None
    if points and len(points) == 16:
        box = psd_read.warp_bounds(warp)
        if box:
            l, t, r, b = box
            mesh = [((x - l) / ((r - l) or 1), (y - t) / ((b - t) or 1)) for x, y in points]
            if not psd_template.bends(mesh):
                mesh = None
    elif points:
        notes.add("This template bends the design with a split warp (several mesh patches). We place the design "
                  "without that bend, so it can look slightly flatter.")
    if style not in ("warpNone", "warpCustom", ""):
        notes.add(f"This template bends the design with Photoshop's {psd_read.WARP_STYLES.get(style, style)} warp. "
                  "We place the design without it, so it can look slightly flatter.")
    for name in psd_read._filters(config):
        clean = name.rstrip(". ").strip() or "smart"
        notes.add(f"This template uses Photoshop's {clean} filter. We place the design without it, so it can "
                  "look slightly flatter.")
    if so_layer.has_effects() and any(getattr(e, "enabled", True) for e in so_layer.effects):
        notes.add("The design area has a layer style (for example a shadow). We place the design without it.")

    quad = [(x * scale, y * scale) for x, y in chosen["quad"]]
    if content is not None:
        Image.fromarray(content, "RGBA").save(os.path.join(out, "content.png"))

    # Above: one overlay each, at the stored scale.
    overlays = []
    for unit in units[chosen["unit"] + 1:]:
        px = _pixels(unit.layer, notes)
        if px is None:
            continue
        rgba, left, top = px
        if unit.clip_base is not None:
            base_px = last_base.get(id(unit.clip_base))
            if base_px is None and unit.clip_base is so_layer:
                base_px = _pixels(so_layer, notes)  # clipped onto the design area: kept to its shape as it is now
            _clip(rgba, left, top, base_px)
        else:
            last_base[id(unit.layer)] = (rgba, left, top)
        small, sl, st = _reduce(rgba, left, top, scale)
        if not small[..., 3].any():
            continue
        name = f"overlay-{len(overlays)}.png"
        Image.fromarray(small, "RGBA").save(os.path.join(out, name))
        overlays.append({"file": name, "left": sl, "top": st, "blend": unit.blend, "opacity": round(unit.opacity, 4)})

    spec = {
        "width": sw, "height": sh, "full": [W, H], "scale": scale,
        "placement": {"canvas": list(canvas), "quad": [list(p) for p in quad],
                      "mesh": [list(p) for p in mesh] if mesh else None,
                      "blend": so_unit.blend, "opacity": round(so_unit.opacity, 4), "mask": mask_file},
        "overlays": overlays,
        "content": "content.png" if content is not None else None,
        "choice": choice,
        "options": [{"index": o["index"], "name": o["name"][:120], "area": round(o["area"]),
                     "box": [round(v * scale) for v in _box(o["quad"])] if o["quad"] else None,
                     "usable": o in usable} for o in options],
        "notes": notes.lines,
    }
    with open(os.path.join(out, "spec.json"), "w", encoding="utf-8") as fh:
        json.dump(spec, fh)
    return spec


def _box(quad: list[tuple[float, float]]) -> list[float]:
    xs, ys = [p[0] for p in quad], [p[1] for p in quad]
    return [min(xs), min(ys), max(xs), max(ys)]


def _main(argv: list[str]) -> int:
    from app.pipeline.psd_sandbox import child_main, own_peak_mb

    path, out = argv[0], argv[1]
    choice = int(argv[2]) if len(argv) > 2 and argv[2] != "-" else None

    def work() -> None:
        spec = convert(path, out, choice)
        sys.stdout.write(json.dumps({"choice": spec["choice"], "peak_mb": own_peak_mb()}))

    return child_main(work)


if __name__ == "__main__":  # pragma: no cover - the child process
    raise SystemExit(_main(sys.argv[1:]))
