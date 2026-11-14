"""Read what a Photoshop mockup (PSD/PSB) contains, without drawing anything (feature/psd-templates, step 0).

``read(path)`` opens the file with psd-tools and returns a plain summary: the canvas, the layer tree with
blend modes, every smart object (its embedded canvas, where it sits on the page, its warp, mask and smart
filters), the adjustment and fill layers, and everything we could not read. The inspector
(``ops/psd_inspect.py``, ``python -m app.cli psd-inspect``) prints it; the converter will decide from it.

PSDs are untrusted input. This module never reads a linked (external) smart object from disk: psd-tools would
resolve its path; we only look at data embedded in the file and report a linked one as "linked, not embedded".
Nothing is written anywhere; the file is only read.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from typing import Any

#: The smart-object descriptor keys that carry a warp, and Photoshop's names for the standard styles.
WARP_STYLES = {
    "warpNone": "none", "warpCustom": "custom mesh", "warpArc": "arc", "warpArcLower": "arc lower",
    "warpArcUpper": "arc upper", "warpArch": "arch", "warpBulge": "bulge", "warpShellLower": "shell lower",
    "warpShellUpper": "shell upper", "warpFlag": "flag", "warpWave": "wave", "warpFish": "fish",
    "warpRise": "rise", "warpFisheye": "fisheye", "warpInflate": "inflate", "warpSqueeze": "squeeze",
    "warpTwist": "twist", "warpCylinder": "cylinder", "warpCone": "cone",
}


@dataclass
class SmartObject:
    name: str
    path: str  # where it sits in the tree ("Group/Layer")
    kind: str  # "embedded" / "linked" / "unknown"
    filetype: str | None
    embedded_size: tuple[int, int] | None  # the smart object's own canvas, in its pixels
    quad: list[tuple[float, float]] | None  # top-left, top-right, bottom-right, bottom-left on the page
    shape: str  # "axis-aligned" / "rotated or scaled" / "perspective" / "unknown"
    warp: str  # "none" / a standard style / "custom mesh" / "custom mesh (split)" / "unknown"
    warp_points: int | None  # mesh points of a custom warp (16 = one 4 x 4 bezier patch)
    blend_mode: str
    opacity: int  # 0..255
    visible: bool
    has_mask: bool
    has_vector_mask: bool
    clipped: bool  # clipped to the layer below it
    clip_layers: int  # layers clipped onto it
    smart_filters: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    area: float = 0.0  # the quad's area on the page, in pixels


@dataclass
class LayerLine:
    depth: int
    name: str
    kind: str
    blend_mode: str
    opacity: int
    visible: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class Summary:
    file_size: int
    version: str  # "PSD" / "PSB"
    width: int
    height: int
    color_mode: str
    depth: int
    has_composite: bool
    layers: list[LayerLine] = field(default_factory=list)
    smart_objects: list[SmartObject] = field(default_factory=list)
    adjustments: list[str] = field(default_factory=list)
    fills: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def _name(value: Any) -> str:
    """A descriptor value as plain text (enumerations, strings, bytes)."""
    for attr in ("enum", "value"):
        inner = getattr(value, attr, None)
        if inner is not None and inner is not value:
            return _name(inner)
    if isinstance(value, bytes):
        return value.decode("latin-1").strip("\x00 ")
    return str(value).strip("\x00 ")


def _get(desc: Any, key: str) -> Any:
    if desc is None:
        return None
    try:
        return desc.get(key.encode("latin-1"))
    except Exception:  # noqa: BLE001 - an odd descriptor is "not there"
        return None


def _numbers(value: Any) -> list[float]:
    out = []
    for item in value or []:
        try:
            out.append(float(getattr(item, "value", item)))
        except (TypeError, ValueError):
            pass
    return out


def _quad(numbers: list[float]) -> list[tuple[float, float]] | None:
    if len(numbers) != 8:
        return None
    return [(numbers[i], numbers[i + 1]) for i in range(0, 8, 2)]


def quad_shape(quad: list[tuple[float, float]] | None) -> str:
    """Axis-aligned rectangle, a parallelogram (rotated/scaled/skewed), or a true perspective quad."""
    if not quad:
        return "unknown"
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = quad
    tol = 0.5
    if abs(y0 - y1) < tol and abs(y3 - y2) < tol and abs(x0 - x3) < tol and abs(x1 - x2) < tol:
        return "axis-aligned"
    # A parallelogram: opposite sides equal as vectors.
    if abs((x1 - x0) - (x2 - x3)) < tol and abs((y1 - y0) - (y2 - y3)) < tol:
        return "rotated or scaled"
    return "perspective"


def quad_area(quad: list[tuple[float, float]] | None) -> float:
    if not quad:
        return 0.0
    s = 0.0
    for i in range(4):
        (xa, ya), (xb, yb) = quad[i], quad[(i + 1) % 4]
        s += xa * yb - xb * ya
    return abs(s) / 2


def _warp(warp: Any) -> tuple[str, int | None]:
    """The warp's style and, for a custom mesh, how many mesh points it has (16 = one 4 x 4 bezier patch;
    more = a split warp, several patches)."""
    if warp is None:
        return "none", None
    style = _name(_get(warp, "warpStyle"))
    label = WARP_STYLES.get(style, style or "unknown")
    custom = _get(warp, "customEnvelopeWarp")
    if custom is None:
        return label, None
    points = mesh_points(warp) or []
    count = len(points) or None
    split = _get(custom, "quiltSliceX") is not None or _get(custom, "quiltSliceY") is not None
    if split or (count or 16) > 16:
        return "custom mesh (split)", count
    # Photoshop keeps a custom warp the designer opened and never moved: a regular grid over its bounds.
    return ("custom mesh, unchanged" if mesh_bend(warp) == 0 else "custom mesh"), count


def warp_bounds(warp: Any) -> tuple[float, float, float, float] | None:
    """The warp's bounds (left, top, right, bottom) on the smart object's canvas."""
    bounds = _get(warp, "bounds")
    try:
        return tuple(float(getattr(_get(bounds, k), "value", _get(bounds, k)))  # type: ignore[return-value]
                     for k in ("Left", "Top ", "Rght", "Btom"))
    except (TypeError, ValueError):
        return None


def mesh_bend(warp: Any) -> float | None:
    """How far a 4 x 4 mesh is from the regular grid over its bounds, as a share of the bounds (0 = no bend)."""
    points, box = mesh_points(warp), warp_bounds(warp)
    if not points or len(points) != 16 or box is None:
        return None
    left, top, right, bottom = box
    w, h = (right - left) or 1.0, (bottom - top) or 1.0
    worst = 0.0
    for k, (x, y) in enumerate(points):
        gx, gy = left + w * (k % 4) / 3, top + h * (k // 4) / 3
        worst = max(worst, abs(x - gx) / w, abs(y - gy) / h)
    return 0.0 if worst < 0.002 else round(worst, 3)


def mesh_points(warp: Any) -> list[tuple[float, float]] | None:
    """A custom warp's mesh points on the smart object's own canvas, row by row. Photoshop stores them as an
    object array with one list of x values (``Hrzn``) and one of y values (``Vrtc``)."""
    custom = _get(warp, "customEnvelopeWarp")
    points = _get(custom, "meshPoints")
    if points is None:
        return None
    xs, ys = _get(points, "Hrzn"), _get(points, "Vrtc")
    if xs is not None and ys is not None:
        xv = [float(v) for v in getattr(xs, "values", xs)]
        yv = [float(v) for v in getattr(ys, "values", ys)]
        return list(zip(xv, yv)) if len(xv) == len(yv) else None
    out = []
    try:
        for item in points:  # older files: a list of points, each with its own Hrzn / Vrtc
            out.append((float(getattr(_get(item, "Hrzn"), "value", 0.0)), float(getattr(_get(item, "Vrtc"), "value", 0.0))))
    except TypeError:
        return None
    return out or None


def _filters(config: Any) -> list[str]:
    fx = _get(config, "filterFX")
    if fx is None:
        return []
    names = []
    for item in _get(fx, "filterFXList") or []:
        name = _name(_get(item, "Nm  ")) or _name(getattr(_get(item, "Fltr"), "classID", "")) or "unnamed filter"
        names.append(name)
    return names


def _embedded_size(layer: Any, so: Any, config: Any, notes: list[str]) -> tuple[int, int] | None:
    size = _get(config, "Sz  ")
    w, h = _get(size, "Wdth"), _get(size, "Hght")
    if w is not None and h is not None:
        try:
            return int(round(float(getattr(w, "value", w)))), int(round(float(getattr(h, "value", h))))
        except (TypeError, ValueError):
            pass
    try:
        if so.kind != "data":
            return None
        data = so.data
        if so.is_psd():
            from psd_tools import PSDImage

            inner = PSDImage.open(io.BytesIO(data))
            return inner.width, inner.height
        from PIL import Image

        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception as exc:  # noqa: BLE001
        notes.append(f"embedded file could not be opened ({type(exc).__name__})")
        return None


def _layer_path(layer: Any) -> str:
    names = []
    node = layer
    while node is not None and getattr(node, "kind", "") != "psdimage":
        names.append(str(node.name))
        node = getattr(node, "parent", None)
    return "/".join(reversed(names))


def _effects(layer: Any) -> list[str]:
    try:
        if not layer.has_effects():
            return []
        return [type(e).__name__ for e in layer.effects if getattr(e, "enabled", True)]
    except Exception:  # noqa: BLE001
        return ["unreadable effects"]


def read(path: str | os.PathLike[str], *, max_alloc_bytes: int | None = None) -> Summary:
    """Read the PSD at ``path``. Raises only when the file is not a PSD at all; anything else is a note."""
    from psd_tools import PSDImage
    from psd_tools.api.adjustments import FillLayer
    from psd_tools.api.layers import AdjustmentLayer

    size = os.path.getsize(path)
    psd = PSDImage.open(path)
    if max_alloc_bytes is not None:
        psd.max_alloc_bytes = max_alloc_bytes
    summary = Summary(
        file_size=size,
        version="PSB" if psd.version == 2 else "PSD",
        width=psd.width,
        height=psd.height,
        color_mode=getattr(psd.color_mode, "name", str(psd.color_mode)),
        depth=int(psd.depth),
        has_composite=bool(psd.has_preview()),
    )
    if summary.color_mode != "RGB":
        summary.unreadable.append(f"colour mode {summary.color_mode} (only RGB is placed as is)")
    if summary.depth != 8:
        summary.unreadable.append(f"{summary.depth} bits per channel (read as 8)")

    def walk(group: Any, depth: int) -> None:
        for layer in group:
            notes: list[str] = []
            try:
                kind = layer.kind
                blend = getattr(layer.blend_mode, "name", str(layer.blend_mode)).lower()
                if layer.clipping:
                    notes.append("clipped to the layer below")
                if layer.has_mask():
                    notes.append("layer mask")
                if layer.has_vector_mask():
                    notes.append("vector mask")
                effects = _effects(layer)
                if effects:
                    notes.append("effects: " + ", ".join(effects))
                if isinstance(layer, AdjustmentLayer):
                    summary.adjustments.append(f"{_layer_path(layer)} ({kind}, {blend}, opacity {layer.opacity})")
                elif isinstance(layer, FillLayer):
                    summary.fills.append(f"{_layer_path(layer)} ({kind}, {blend}, opacity {layer.opacity})")
                if kind == "smartobject":
                    summary.smart_objects.append(_smart_object(layer, notes, summary))
                summary.layers.append(LayerLine(depth, str(layer.name), kind, blend, int(layer.opacity),
                                                bool(layer.visible), notes))
                if layer.is_group():
                    walk(layer, depth + 1)
            except Exception as exc:  # noqa: BLE001 - one odd layer is a note, not a failed read
                summary.unreadable.append(f"layer {getattr(layer, 'name', '?')!r}: {type(exc).__name__}: {exc}"[:200])

    walk(psd, 0)
    return summary


def _smart_object(layer: Any, notes: list[str], summary: Summary) -> SmartObject:
    from psd_tools.constants import Tag

    so = layer.smart_object
    config = None
    for key in (Tag.SMART_OBJECT_LAYER_DATA1, Tag.SMART_OBJECT_LAYER_DATA2):
        block = layer.tagged_blocks.get_data(key)
        if block is not None:
            config = block.data
            break
    try:
        kind = {"data": "embedded", "external": "linked", "alias": "linked"}.get(so.kind, so.kind)
    except ValueError:  # no embedded data and no link record: its content is simply not in the file
        kind = "linked"
    if kind != "embedded":
        summary.unreadable.append(f"smart object {layer.name!r} is linked, not embedded: its content is not in the file")
    # Its content is not in the file at all (no embedded data): we never look for it on disk.
    filetype = None
    try:
        filetype = so.detected_filetype
    except ValueError:
        pass
    if filetype and filetype not in ("8bps", "8bpb", "png", "jpg", "jpeg", "tiff", "tif", "gif", "bmp", "webp"):
        summary.unreadable.append(f"smart object {layer.name!r} holds a {filetype} file (vector or other), not pixels")
    quad = _quad(_numbers(_get(config, "nonAffineTransform"))) or _quad(_numbers(_get(config, "Trnf")))
    if quad is None:
        try:
            quad = _quad([float(v) for v in so.transform_box])
        except Exception:  # noqa: BLE001
            quad = None
    warp_label, points = _warp(_get(config, "warp"))
    return SmartObject(
        name=str(layer.name),
        path=_layer_path(layer),
        kind=kind,
        filetype=filetype,
        embedded_size=_embedded_size(layer, so, config, notes) if kind == "embedded" else None,
        quad=quad,
        shape=quad_shape(quad),
        warp=warp_label,
        warp_points=points,
        blend_mode=getattr(layer.blend_mode, "name", str(layer.blend_mode)).lower(),
        opacity=int(layer.opacity),
        visible=bool(layer.visible),
        has_mask=bool(layer.has_mask()),
        has_vector_mask=bool(layer.has_vector_mask()),
        clipped=bool(layer.clipping),
        clip_layers=len(layer.clip_layers or []),
        smart_filters=_filters(config),
        effects=_effects(layer),
        area=quad_area(quad),
    )
