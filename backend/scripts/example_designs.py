"""The three example designs a new seller can try the app with (pipeline/examples.py).

Our own typographic designs, drawn here with Archivo (SIL Open Font License, the site's font):
plain words of our own, no brand, no character, no famous phrase. Run from the repository root:

    python backend/scripts/example_designs.py

It writes, for each design, into backend/app/examples/:
  <slug>.png          the design itself: ink on a transparent background, 1600 x 1600
  <slug>-light.jpg    the listing's first photo: the design on a light shirt-coloured square, 1200 x 1200
  <slug>-dark.jpg     its second photo: the same words in light ink on a dark square

Deterministic: the same input draws the same pixels. No model draws anything.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
FONT_BOLD = ROOT / "frontend" / "app" / "og" / "archivo-900.ttf"
FONT_TEXT = ROOT / "frontend" / "app" / "og" / "archivo-500.ttf"
OUT = ROOT / "backend" / "app" / "examples"

INK = (20, 22, 43)
TAG = (255, 210, 74)
PAPER_LIGHT = (243, 239, 230)
PAPER_DARK = (31, 35, 52)
INK_ON_DARK = (244, 240, 230)

#: slug, the big lines, the small line under them.
DESIGNS = [
    ("slow-mornings-club", ["SLOW", "MORNINGS"], "CLUB · EST. BEFORE NOON"),
    ("tomato-season", ["TOMATO", "SEASON"], "GROWN IN THE BACK GARDEN"),
    ("small-kind-things", ["SMALL", "KIND", "THINGS"], "DO ONE TODAY"),
]


def _fit(draw: ImageDraw.ImageDraw, text: str, font_path: Path, width: int, start: int) -> ImageFont.FreeTypeFont:
    size = start
    while size > 20:
        font = ImageFont.truetype(str(font_path), size)
        if draw.textlength(text, font=font) <= width:
            return font
        size -= 4
    return ImageFont.truetype(str(font_path), size)


def draw(lines: list[str], small: str, ink: tuple[int, int, int], side: int = 1600) -> Image.Image:
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    width = int(side * 0.78)
    fonts = [_fit(pen, line, FONT_BOLD, width, int(side * 0.26)) for line in lines]
    size = min(f.size for f in fonts)
    big = ImageFont.truetype(str(FONT_BOLD), size)
    small_font = _fit(pen, small, FONT_TEXT, int(width * 0.8), int(side * 0.05))
    line_h = int(size * 0.98)
    bar_h = int(side * 0.022)
    total = line_h * len(lines) + bar_h + int(side * 0.05) + small_font.size
    y = (side - total) // 2
    for line in lines:
        w = pen.textlength(line, font=big)
        pen.text(((side - w) / 2, y), line, font=big, fill=ink + (255,))
        y += line_h
    y += int(side * 0.02)
    bar_w = int(width * 0.34)
    pen.rounded_rectangle(((side - bar_w) // 2, y, (side + bar_w) // 2, y + bar_h), radius=bar_h // 2, fill=TAG + (255,))
    y += bar_h + int(side * 0.03)
    w = pen.textlength(small, font=small_font)
    pen.text(((side - w) / 2, y), small, font=small_font, fill=ink + (255,))
    return image


def photo(design: Image.Image, paper: tuple[int, int, int], side: int = 1200) -> Image.Image:
    canvas = Image.new("RGB", (side, side), paper)
    art = design.resize((int(side * 0.8), int(side * 0.8)), Image.LANCZOS)
    offset = (side - art.width) // 2
    canvas.paste(art, (offset, offset), art)
    return canvas


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for slug, lines, small in DESIGNS:
        design = draw(lines, small, INK)
        design.save(OUT / f"{slug}.png", optimize=True)
        photo(design, PAPER_LIGHT).save(OUT / f"{slug}-light.jpg", quality=86, optimize=True)
        photo(draw(lines, small, INK_ON_DARK), PAPER_DARK).save(OUT / f"{slug}-dark.jpg", quality=86, optimize=True)
        print(slug)


if __name__ == "__main__":
    main()
