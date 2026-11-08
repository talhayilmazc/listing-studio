"""The logo files, from docs/design/logo-reference.dc.html: two stacked hang tags and the wordmark
"listyro" (Archivo 900, width 108%, tracking -0.048em), with the letters as outlines so no font is
needed to show them.

    pip install fonttools
    curl -L -o Archivo.ttf "https://github.com/google/fonts/raw/main/ofl/archivo/Archivo%5Bwdth%2Cwght%5D.ttf"
    python frontend/scripts/logo-assets.py Archivo.ttf frontend/public/brand frontend/lib/logoPaths.ts
    node frontend/scripts/logo-icons.mjs        # the PNG icons and favicon.ico, from these SVGs

Archivo is under the SIL Open Font License 1.1 (embedding outlines in a logo is allowed).
Writes: mark.svg, logo.svg (on light), logo-dark.svg (on dark), icon.svg (the app icon: the mark
on an ink square), and lib/logoPaths.ts (the same drawing for components/Logo.tsx and the share cards).
"""
import json
import os
import sys

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

INK = "#14162B"
TAG = "#FFD24A"
PAPER = "#FFFFFF"

# The reference's own drawing (viewBox 0 0 64 64).
BACK = "M28 4 H42 L54 16 V40 Q54 46 48 46 H28 Q22 46 22 40 V10 Q22 4 28 4 Z"
FRONT = "M18 16 H32 L44 28 V52 Q44 58 38 58 H18 Q12 58 12 52 V22 Q12 16 18 16 Z"


def mark(front: str, hole: str, back: str = TAG) -> str:
    return (f'<g transform="rotate(12 40 26)"><path d="{BACK}" fill="{back}"/></g>'
            f'<g transform="rotate(-8 30 36)"><path d="{FRONT}" fill="{front}"/>'
            f'<circle cx="33" cy="27.5" r="3.8" fill="{hole}"/></g>')


def wordmark(font_path: str, size: float = 104, tracking_em: float = -5 / 104) -> tuple[str, float, float]:
    """The word as one path at ``size`` px, the baseline at y = 0; its width and the line box's ascent
    (CSS line-height 1: the box is ``size`` tall, centred on the font's ascent + descent)."""
    font = instantiateVariableFont(TTFont(font_path), {"wght": 900, "wdth": 108})
    upm = font["head"].unitsPerEm
    scale = size / upm
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    hhea = font["hhea"]
    content = (hhea.ascent - hhea.descent) * scale  # descent is negative
    baseline = (size - content) / 2 + hhea.ascent * scale
    pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.1f}".rstrip("0").rstrip("."))
    x = 0.0
    word = "listyro"
    for i, ch in enumerate(word):
        name = cmap[ord(ch)]
        glyph = glyphs[name]
        glyph.draw(TransformPen(pen, (scale, 0, 0, -scale, x, 0)))
        x += glyph.width * scale
        if i < len(word) - 1:
            x += tracking_em * size
    return pen.getCommands(), x, baseline


def svg(width: float, height: float, body: str, label: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:g} {height:g}" '
            f'width="{width:g}" height="{height:g}" role="img" aria-label="{label}">{body}</svg>\n')


def main() -> None:
    font_path, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    path, text_width, baseline = wordmark(font_path)
    # The lockup as drawn: a 132 px mark, a 26 px gap, 104 px type centred on the mark.
    mark_size, gap, size = 132, 26, 104
    width = round(mark_size + gap + text_width, 1)
    text_top = (mark_size - size) / 2
    scale = mark_size / 64
    for name, front, hole, text in (("logo.svg", INK, PAPER, INK), ("logo-dark.svg", PAPER, INK, PAPER)):
        body = (f'<g transform="scale({scale:g})">{mark(front, hole)}</g>'
                f'<path transform="translate({mark_size + gap} {text_top + baseline:.2f})" d="{path}" fill="{text}"/>')
        with open(os.path.join(out, name), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(svg(width, mark_size, body, "Listyro"))
    with open(os.path.join(out, "mark.svg"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(svg(64, 64, mark(INK, PAPER), "Listyro"))
    # The app icon (reference: "App 128", "Favicon"): ink square, yellow back tag, white front tag.
    icon = (f'<rect width="64" height="64" rx="15" fill="{INK}"/>'
            f'<g transform="translate(6.4 6.4) scale(0.8)">{mark(PAPER, INK)}</g>')
    with open(os.path.join(out, "icon.svg"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(svg(64, 64, icon, "Listyro"))
    lines = [
        "// Written by scripts/logo-assets.py from docs/design/logo-reference.dc.html; do not edit by hand.",
        '// The two hang tags (viewBox 0 0 64 64) and the wordmark "listyro" as outlines (Archivo 900,',
        f"// width 108%, tracking -0.048em) at {size} px, baseline at y = 0.",
        f'export const TAG_BACK = "{BACK}";',
        f'export const TAG_FRONT = "{FRONT}";',
        f'export const WORDMARK = "{path}";',
        f"export const WORDMARK_SIZE = {size};",
        f"export const WORDMARK_WIDTH = {text_width:.2f};",
        f"export const WORDMARK_BASELINE = {baseline:.2f};",
    ]
    with open(sys.argv[3], "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    print("lockup", width, "x", mark_size, "word", round(text_width, 1), "baseline", round(baseline, 1))


if __name__ == "__main__":
    main()
