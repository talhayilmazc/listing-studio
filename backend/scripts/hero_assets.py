"""The home page's shirt pictures: real blank-shirt photos with the demo account's designs on them,
made by our own mockup engine (pipeline/mockups.py), the same calls the worker makes for a seller.

    # 1. the photos (docs/marketing-assets.md lists each one: page, photographer, licence)
    docker compose cp <folder with the seven JPEGs> worker:/tmp/hero-photos
    # 2. the mockups
    docker compose exec -T worker python - < backend/scripts/hero_assets.py
    docker compose cp worker:/tmp/hero/. <scratch folder>
    node frontend/scripts/hero-images.mjs <scratch folder>   (also writes frontend/lib/marketing/heroImages.ts)

The designs are the demo account's drawings (scripts/demo_screens_seed.py, our own SVG). Each photo
goes through prepare_blank (the models in the worker image find the garment and its print area, as
for a seller's template); where the found area is not where a seller would put it, AREAS below holds
the corrected corners, which is what a seller does in the editor before "Looks right". Nothing leaves
the machine; no model is downloaded at run time.

Each picture is written at 1x and 2x (lossless PNG) at the size the page shows it (CSS px);
frontend/scripts/hero-images.mjs turns them into the AVIF and WebP files the page loads.
"""
import json
import os
import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")

import pyvips  # noqa: E402

from app.pipeline import mockups  # noqa: E402

source = open("/app/scripts/demo_screens_seed.py", encoding="utf-8").read()
namespace: dict = {}
exec(source[: source.index("import asyncio, random")], namespace)  # noqa: S102 - our own file: the ART table
ART = namespace["ART"]

PHOTOS = "/tmp/hero-photos"
OUT = "/tmp/hero"
os.makedirs(OUT, exist_ok=True)

# Ink for light and dark shirts (the seed's own palette).
LIGHT_SHIRT = ("#2F2E2C", "#E8707E")
DARK_SHIRT = ("#F2EEE3", "#F2D27A")
DARK = {"black", "navy"}
# On the pink shirt the seed's coral would vanish: light ink, as the reference draws it.
INK = {"pink": ("#FFF4E8", "#F2D27A")}

# A seller's correction of the found print area (normalised TL, TR, BR, BL), by photo. Empty: the
# engine's own area is used as found.
AREAS: dict[str, list[list[float]]] = {
    "orange": [[0.33, 0.42], [0.65, 0.42], [0.65, 0.585], [0.33, 0.585]],
    "pink": [[0.38, 0.39], [0.66, 0.39], [0.66, 0.54], [0.38, 0.54]],
    "white-rack": [[0.38, 0.535], [0.60, 0.535], [0.60, 0.74], [0.38, 0.74]],
}

# Where each picture is used, its frame (CSS px at 1x) and which design goes on which photo.
USES = {
    # The hero window's eight tiles.
    "tile": ((128, 132), [
        ("white-flat", "mountain"), ("pink", "heart"), ("navy", "books"), ("orange", "cup"),
        ("black", "paw"), ("mint", "leaf"), ("white-rack", "cup"), ("black", "mountain"),
    ]),
    # Step 2: one design in every colour.
    "colour": ((96, 104), [
        ("white-flat", "mountain"), ("navy", "mountain"), ("black", "mountain"),
        ("pink", "mountain"), ("mint", "mountain"), ("orange", "mountain"),
    ]),
    # The three shop cards: the same design, a different cover in each shop. In one column a card is as wide
    # as the page, so these also come at 4x (1200 px).
    "shop": ((300, 276), [("white-flat", "mountain"), ("navy", "mountain"), ("mint", "mountain")]),
}


def design_png(name: str, photo: str) -> bytes:
    ink, accent = INK.get(photo) or (DARK_SHIRT if photo in DARK else LIGHT_SHIRT)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="60 76 80 68" width="800" height="680">'
           f"{ART[name](ink, accent)}</svg>")
    return pyvips.Image.new_from_buffer(svg.encode(), "").write_to_buffer(".png")


def garment_box(mask_png: bytes, pad: float = 0.06) -> tuple[int, int, int, int]:
    mask = pyvips.Image.new_from_buffer(mask_png, "")
    cols, rows = (mask > 128).project()
    width, height = mask.width, mask.height
    xs = [i for i in range(width) if cols(i, 0)[0] > 0]
    ys = [i for i in range(height) if rows(0, i)[0] > 0]
    x0, x1, y0, y1 = xs[0], xs[-1], ys[0], ys[-1]
    px, py = (x1 - x0) * pad, (y1 - y0) * pad
    return int(max(0, x0 - px)), int(max(0, y0 - py)), int(min(width, x1 + px)), int(min(height, y1 + py))


def frame(image, box: tuple[int, int, int, int], ratio: float):  # noqa: ANN001, ANN201
    """The garment, centred, in a frame of the given width/height, inside the photo."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w / h < ratio:
        w = h * ratio
    else:
        h = w / ratio
    w, h = min(w, image.width), min(h, image.height)
    if w / h > ratio:
        w = h * ratio
    else:
        h = w / ratio
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left = int(min(max(0, cx - w / 2), image.width - w))
    top = int(min(max(0, cy - h / 2), image.height - h))
    return image.crop(left, top, int(w), int(h))


prepared: dict[str, mockups.PreparedBlank] = {}
maps: dict[str, mockups.Maps] = {}
report = {}
for name in sorted({photo for _, (_, pairs) in USES.items() for photo, _ in pairs}):
    with open(os.path.join(PHOTOS, name + ".jpg"), "rb") as handle:
        blank = mockups.prepare_blank(handle.read(), name=name)
    area = AREAS.get(name) or blank.print_area
    prepared[name] = blank
    maps[name] = mockups.build_maps(blank.image, blank.mask, blank.occluder, area)
    report[name] = {"found": blank.print_area, "used": area, "confidence": round(blank.confidence, 2),
                    "method": blank.method, "note": blank.note}

sizes = {}
for use, ((width, height), pairs) in USES.items():
    for photo, art in pairs:
        blank = prepared[photo]
        area = AREAS.get(photo) or blank.print_area
        design = mockups.load_design(design_png(art, photo))
        out = pyvips.Image.new_from_buffer(mockups.render(maps[photo], design, mockups.Settings(area)), "")
        framed = frame(out, garment_box(blank.mask), width / height)
        for scale in ((1, 2, 4) if use == "shop" else (1, 2)):
            img = framed.thumbnail_image(width * scale, height=height * scale, crop="centre")
            base = f"{use}-{photo}-{art}{'' if scale == 1 else f'-{scale}x'}"
            # Lossless here; frontend/scripts/hero-images.mjs writes the AVIF and WebP the page loads.
            img.pngsave(os.path.join(OUT, base + ".png"), compression=6, strip=True)
            sizes[base] = os.path.getsize(os.path.join(OUT, base + ".png"))
        # A full picture with the print area drawn, to check where the design went.
        framed.thumbnail_image(480).jpegsave(os.path.join(OUT, f"check-{use}-{photo}-{art}.jpg"), Q=80)

print(json.dumps(report, indent=1))
for base, size in sorted(sizes.items()):
    print(f"{base}: png {size}")
