"""``python -m app.cli psd-inspect <file> [<file> …]``: what a Photoshop mockup contains (feature/psd-templates,
step 0). READ ONLY: the file is opened and read, nothing is written, no database, no network, no Etsy call.

For each file: canvas and file size, the layer tree with blend modes, every smart object (name, embedded canvas
size, where it sits on the page as four corners, warp, masks, smart filters), the adjustment and fill layers,
and everything we cannot read. ``--summary`` adds one line per file in the form docs/psd-findings.md collects
(no file name, no layer names: those can identify a customer).

Parsing runs in a child process with a time and memory limit (``app.pipeline.psd_sandbox``), as uploads will:
a malformed file fails only its own line.
"""

from __future__ import annotations

import os
from collections import Counter

from app.pipeline.psd_read import Summary


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def _point(p: tuple[float, float]) -> str:
    return f"({p[0]:.0f}, {p[1]:.0f})"


def describe(summary: Summary, label: str) -> str:
    lines = [f"== {label}",
             f"canvas {summary.width} x {summary.height} px · {summary.version} · {summary.color_mode} "
             f"{summary.depth}-bit · file {_mb(summary.file_size)} · "
             f"{'has' if summary.has_composite else 'no'} saved composite"]
    lines.append(f"layers ({len(summary.layers)}):")
    for layer in summary.layers:
        flags = "" if layer.visible else " [hidden]"
        note = f" · {'; '.join(layer.notes)}" if layer.notes else ""
        lines.append(f"  {'  ' * layer.depth}{layer.name} — {layer.kind}, {layer.blend_mode}, "
                     f"opacity {layer.opacity}{flags}{note}")
    lines.append(f"smart objects ({len(summary.smart_objects)}):")
    for so in summary.smart_objects:
        size = f"{so.embedded_size[0]} x {so.embedded_size[1]}" if so.embedded_size else "unknown size"
        quad = " ".join(_point(p) for p in so.quad) if so.quad else "no transform"
        lines.append(f"  {so.path}: {so.kind} {so.filetype or '?'} {size}; {so.blend_mode}, opacity {so.opacity}"
                     f"{'' if so.visible else ' [hidden]'}")
        lines.append(f"    corners {quad} ({so.shape}, {so.area:,.0f} px²)")
        warp = so.warp + (f", {so.warp_points} mesh points" if so.warp_points else "")
        lines.append(f"    warp: {warp}")
        masks = [m for m, on in (("layer mask", so.has_mask), ("vector mask", so.has_vector_mask),
                                 ("clipped to the layer below", so.clipped)) if on]
        if so.clip_layers:
            masks.append(f"{so.clip_layers} layer(s) clipped onto it")
        lines.append(f"    masks: {', '.join(masks) or 'none'}")
        lines.append(f"    smart filters: {', '.join(so.smart_filters) or 'none'}")
        if so.effects:
            lines.append(f"    layer effects: {', '.join(so.effects)}")
    lines.append(f"adjustment layers: {'; '.join(summary.adjustments) or 'none'}")
    lines.append(f"fill layers: {'; '.join(summary.fills) or 'none'}")
    lines.append(f"cannot read: {'; '.join(summary.unreadable) or 'nothing'}")
    return "\n".join(lines) + "\n"


def anonymous_line(summary: Summary, n: int) -> str:
    """One line for docs/psd-findings.md: shapes and features only, no names."""
    blends = Counter(layer.blend_mode for layer in summary.layers)
    sos = summary.smart_objects
    parts = [
        f"sample {n}: {summary.width}x{summary.height} {summary.version} {summary.color_mode} {summary.depth}-bit, "
        f"{_mb(summary.file_size)}, {len(summary.layers)} layers",
        f"{len(sos)} smart object(s): " + ", ".join(
            f"{so.shape}/{so.warp}{'/mask' if so.has_mask or so.has_vector_mask else ''}"
            f"{'/clipped' if so.clipped else ''}{'/filters: ' + '+'.join(so.smart_filters) if so.smart_filters else ''}"
            for so in sos),
        "blend modes: " + ", ".join(f"{k} {v}" for k, v in sorted(blends.items())),
        f"adjustments {len(summary.adjustments)}, fills {len(summary.fills)}",
        f"cannot read: {len(summary.unreadable)}",
    ]
    return " · ".join(parts)


def run(paths: list[str], *, summary_lines: bool = False) -> tuple[str, bool]:
    from app.pipeline.psd_sandbox import ParseFailed, inspect_file

    out: list[str] = []
    short: list[str] = []
    fine = True
    for n, path in enumerate(paths, 1):
        label = os.path.basename(path)
        try:
            summary = inspect_file(path)
        except ParseFailed as exc:
            fine = False
            out.append(f"== {label}\ncould not be read: {exc}\n")
            short.append(f"sample {n}: could not be read ({exc})")
            continue
        out.append(describe(summary, label))
        short.append(anonymous_line(summary, n))
    if summary_lines:
        out.append("== summary (no names)\n" + "\n".join(short) + "\n")
    return "".join(out), fine
