"""Light and dark variants of one design (file-name suffixes) and the colour group of each
template photo.

A design file named with a variant suffix ("CDM290-white.png", "CDM290_dark.png", "CDM290
blk.png") is that variant of the design with the same base name (and SKU): several variants
are one design, one listing group, with every mockup of every variant. A file without a
suffix is printed on every photo, as before.

Each template photo is light or dark, read from the garment's own lightness (CIELAB L*,
median over the middle of its print area); heathers and mid tones go by the same number, and
a photo close to the line is marked uncertain for the seller to choose. The seller can change
it with one click.

What a suffix means is the account's setting: by default it names the shirt colour ("-white"
goes on light shirts); flipped, it names the ink colour ("-white" ink goes on dark shirts).
Which file a photo gets (``pick``): the variant for its group; else the design's plain file;
else, when the seller said so, the other variant ("use it on the other group too"); else none:
that photo gets no mockup of the design, and the upload page says so before anything is made.

Pure: no database, no storage (the API and the worker pass the rows in).
"""

from __future__ import annotations

import re
from pathlib import PurePath
from typing import Any

LIGHT, DARK = "light", "dark"
#: The suffixes, case-insensitive, after "-", "_" or a space at the end of the name.
TOKENS: dict[str, str] = {
    "white": LIGHT, "light": LIGHT, "lt": LIGHT, "wht": LIGHT,
    "dark": DARK, "black": DARK, "blk": DARK, "dk": DARK,
}
_SUFFIX = re.compile(r"^(?P<base>.*?\S)[\s_-]+(?P<token>" + "|".join(TOKENS) + r")$", re.IGNORECASE)

#: What a suffix names: the shirt colour (default) or the ink colour.
SHIRT, INK = "shirt", "ink"
MEANINGS = (SHIRT, INK)

#: A photo's garment lightness (CIELAB L*, 0..100): at or above LIGHT_AT light, at or below
#: DARK_AT dark; between them (heathers, mid greys) it goes by SPLIT and is marked uncertain.
LIGHT_AT = 62.0
DARK_AT = 48.0
SPLIT = 55.0


def parse(filename: str) -> tuple[str, str | None]:
    """(base name, "light" | "dark" | None) from a design's file name. Only a whole token at
    the end, after a separator, counts: "CDM290-white" is the light variant of "CDM290";
    "whitehouse", "CDM290white" and "dark-CDM290" are plain names."""
    stem = PurePath(filename).stem.strip()
    m = _SUFFIX.match(stem)
    if not m:
        return stem, None
    return m.group("base").strip(" -_") or stem, TOKENS[m.group("token").lower()]


def classify(lightness: float | None) -> tuple[str | None, bool]:
    """(colour group, uncertain) from a garment's lightness; (None, True) when unknown."""
    if lightness is None:
        return None, True
    if lightness >= LIGHT_AT:
        return LIGHT, False
    if lightness <= DARK_AT:
        return DARK, False
    return (LIGHT if lightness >= SPLIT else DARK), True


def other(colour: str | None) -> str | None:
    return {LIGHT: DARK, DARK: LIGHT}.get(colour or "")


def wanted(colour: str | None, meaning: str = SHIRT) -> str | None:
    """The variant a photo of this colour takes: the same name (the suffix names the shirt),
    or the opposite (the suffix names the ink: white ink goes on dark shirts)."""
    if colour not in (LIGHT, DARK):
        return None
    return colour if meaning != INK else other(colour)


def files_of(design: Any, versions: list[Any]) -> list[Any]:
    """The design's own file and its variant files (versions made for a seller-defined
    subgroup are not variants)."""
    return [design] + [v for v in versions if getattr(v, "subgroup", None) is None]


def has_variants(design: Any, versions: list[Any]) -> bool:
    return any(getattr(f, "variant", None) in (LIGHT, DARK) for f in files_of(design, versions))


def pick(design: Any, versions: list[Any], colour: str | None, meaning: str = SHIRT) -> Any | None:
    """The file a photo of ``colour`` prints for a design with variants (see the module)."""
    files = files_of(design, versions)
    want = wanted(colour, meaning)
    hit = next((f for f in files if want and getattr(f, "variant", None) == want), None)
    if hit is not None:
        return hit
    plain = next((f for f in files if getattr(f, "variant", None) is None), None)
    if plain is not None:
        return plain
    if getattr(design, "fill_other", False):
        return next((f for f in files if getattr(f, "variant", None) in (LIGHT, DARK)), None)
    return None


def photo_colour(photo: Any) -> str | None:
    """The photo's colour group as it stands (the seller's choice, else the measured one)."""
    return photo.colour if getattr(photo, "colour", None) in (LIGHT, DARK) else None


def area_colours(photo: Any, rows: list[dict], tones: list[str | None] | None = None) -> list[str | None]:
    """Each print area's colour group: the photo's (one garment, or the seller set it), else
    for a photo of several garments each garment's own tone (``tones``: measured from the photo
    for an area the analysis did not find), else the photo's."""
    colour = photo_colour(photo)
    if len(rows) <= 1 or getattr(photo, "colour_source", None) == "seller":
        return [colour] * len(rows)
    out = []
    for i, row in enumerate(rows):
        tone = row.get("tone") if row.get("tone") in (LIGHT, DARK) else (tones[i] if tones and i < len(tones) else None)
        out.append(tone if tone in (LIGHT, DARK) else colour)
    return out


def plan(designs: list[Any], versions: dict[Any, list[Any]], photos: list[Any], meaning: str = SHIRT) -> list[dict]:
    """Which design file goes on which photos, per design, before anything is made (the upload
    page): every file with its photos; the photos that get no mockup and why. ``photos``: rows
    with ``id``, ``name``, ``colour``, ``colour_source``."""
    out = []
    for d in designs:
        vs = versions.get(d.id, [])
        if not has_variants(d, vs):
            out.append({"design_id": d.id, "variants": False, "files": [], "missing": [], "uncertain": []})
            continue
        files: dict[Any, dict] = {}
        missing: list[Any] = []
        for p in photos:
            f = pick(d, vs, photo_colour(p), meaning)
            if f is None:
                missing.append(p)
                continue
            entry = files.setdefault(f.id, {"file_id": f.id, "filename": f.original_filename,
                                            "variant": getattr(f, "variant", None), "photos": []})
            entry["photos"].append(p.id)
        have = {getattr(f, "variant", None) for f in files_of(d, vs)}
        lacking = [c for c in (LIGHT, DARK) if c not in have]
        missing_colours = sorted({photo_colour(p) or "unknown" for p in missing})
        out.append({
            "design_id": d.id, "variants": True, "files": list(files.values()),
            "missing": [p.id for p in missing], "missing_colours": missing_colours,
            # The variant a photo of each missing colour would take, to name it plainly.
            "lacking": lacking,
            "uncertain": [p.id for p in photos if getattr(p, "colour_source", None) == "uncertain"],
            "fill_other": bool(getattr(d, "fill_other", False)),
        })
    return out
