"""The home page's demonstration pictures, made by the product itself from the synthetic demo account.

    docker compose exec -T api python - < backend/scripts/hero_assets.py
    docker compose cp api:/tmp/hero/. frontend/public/hero/

The design is the demo account's NW1042 drawing (scripts/demo_screens_seed.py, our own SVG); the
blanks are the demo's "Ten colours" tees (tests/mockup_samples.py, our own drawings); each mockup is
the mockup engine's real output (pipeline/mockups.py), the same call the worker makes. No seller's
work, no model download at run time, nothing leaves the machine. docs/design-v2.md.
"""
import io
import os
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import pyvips  # noqa: E402

from app.pipeline import mockups  # noqa: E402
from tests import mockup_samples as samples  # noqa: E402

# The demo seed defines ART (the designs) and SHOPS at import; it runs its own main() only from asyncio.run at the
# bottom, so read the two tables from its source instead of importing it.
source = open("/app/scripts/demo_screens_seed.py", encoding="utf-8").read()
namespace: dict = {}
exec(source[: source.index("import asyncio, random")], namespace)  # noqa: S102 - our own file: the G/ART/SHIRT tables
ART = namespace["ART"]
G = namespace["G"]

OUT = "/tmp/hero"
os.makedirs(OUT, exist_ok=True)

# NW1042, "Retro Camping Shirt, Mountain Sunset": its ink and accent, on a transparent ground.
_, ink, accent, _ = G["mountain"]
design_svg = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="62 80 76 64" width="760" height="640">'
    f"{ART['mountain'](ink, accent)}</svg>"
)
design_png = pyvips.Image.new_from_buffer(design_svg.encode(), "").write_to_buffer(".png")

# The tile sizes the page shows (CSS px), written at twice that.
TILE = (96, 112)
COLOURS = [("white", (238, 238, 236)), ("sand", (215, 200, 170)), ("sage", (150, 170, 140)), ("pink", (235, 180, 190)), ("mustard", (210, 165, 50))]


def webp(data: bytes, width: int, height: int, name: str, quality: int = 72) -> None:
    image = pyvips.Image.new_from_buffer(data, "")
    image = image.thumbnail_image(width, height=height, crop="centre")
    image.webpsave(os.path.join(OUT, name), Q=quality, strip=True)
    print(name, os.path.getsize(os.path.join(OUT, name)))


webp(design_png, 2 * 120, 2 * 101, "design.webp", quality=82)
design = mockups.load_design(design_png)
for name, rgb in COLOURS:
    blank = samples.blank(rgb, size=(900, 1050))
    prepared = mockups.prepare_blank(blank)
    maps = mockups.build_maps(prepared.image, prepared.mask, prepared.occluder, prepared.print_area)
    out = mockups.render(maps, design, mockups.Settings(prepared.print_area))
    # The whole photo: the blanks are already in the tile's proportions (900 x 1050 = 96 x 112).
    data = out
    webp(data, 2 * TILE[0], 2 * TILE[1], f"mockup-{name}.webp")
    if name == "white":
        # The same photo before printing, and the mockup larger: "on your own garment photos".
        webp(blank, 2 * 180, 2 * 210, "blank-white-large.webp")
        webp(data, 2 * 180, 2 * 210, "mockup-white-large.webp")
print("done", io.__name__)
