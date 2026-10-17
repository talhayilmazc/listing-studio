"""Subgroups inside a template and design versions (api/mockups.py, workers/mockups.py).

A template ("Comfort Colors") can be split into subgroups ("Comfort Colors White", "Comfort
Colors Black"), each with "Use on" (any, dark garments, light garments) and a file-name suffix.
Photos belong to a subgroup; in several-garment photos each area can be linked to one, by
default the subgroup whose "Use on" matches the garment's brightness (a white tee takes the
light-garment subgroup and so its dark-ink design). A design has a file per subgroup, matched
by the subgroup's name (so one design's "White" version serves every template's "White"
subgroup); a design whose file has no subgroup prints everywhere, as before.
"""

from __future__ import annotations

import io
from pathlib import PurePath
from typing import Any

#: Ink: the mean lightness (0..255) of a design's opaque pixels above LIGHT_INK is light ink
#: (for dark garments), below DARK_INK dark ink (for light garments), between them mixed.
LIGHT_INK = 160
DARK_INK = 95
#: A garment's tone: its lightness (CIELAB L*, 0..100) above LIGHT_GARMENT is a light garment.
LIGHT_GARMENT = 60.0


def same(a: str | None, b: str | None) -> bool:
    return (a or "").strip().casefold() == (b or "").strip().casefold()


def ink_of(png: bytes) -> str:
    """"light", "dark" or "mixed": the colour of a design's opaque pixels."""
    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGBA")
    im.thumbnail((256, 256))
    a = np.asarray(im, dtype=np.float32)
    opaque = a[..., 3] > 128
    if not opaque.any():
        return "mixed"
    rgb = a[..., :3][opaque]
    light = float((0.299 * rgb[:, 0] + 0.587 * rgb[:, 1] + 0.114 * rgb[:, 2]).mean())
    return "light" if light > LIGHT_INK else "dark" if light < DARK_INK else "mixed"


def split_name(filename: str, subgroups: list[dict[str, Any]]) -> tuple[str, str | None]:
    """(base name, subgroup name) from a file name and the subgroups' suffixes ("-white"):
    the longest suffix the name's stem ends with wins; no suffix: (stem, None)."""
    stem = PurePath(filename).stem
    best: tuple[int, str | None, str] = (0, None, stem)
    for g in subgroups:
        suffix = (g.get("suffix") or "").strip()
        if suffix and stem.casefold().endswith(suffix.casefold()) and len(suffix) > best[0]:
            best = (len(suffix), g["name"], stem[: len(stem) - len(suffix)].rstrip(" -_") or stem)
    return best[2], best[1]


def by_ink(ink: str | None, subgroups: list[dict[str, Any]]) -> str | None:
    """The subgroup a file that does not pair goes to: light ink to the dark-garment
    subgroup, dark ink to the light-garment one; None when nothing matches."""
    wanted = {"light": "dark", "dark": "light"}.get(ink or "")
    return next((g["name"] for g in subgroups if g.get("use_on") == wanted), None)


def tone_of(lightness: float | None) -> str | None:
    if lightness is None:
        return None
    return "light" if lightness > LIGHT_GARMENT else "dark"


def area_subgroup(row: dict[str, Any], photo_subgroup: Any, subgroups: list[Any]) -> Any:
    """The subgroup an area prints for: the seller's link (``row["subgroup_id"]``), else (in a
    template with several subgroups) the one whose "Use on" matches the garment's tone, else
    the photo's subgroup. ``subgroups``: the template's (objects with ``id``, ``use_on``)."""
    ids = {str(g.id): g for g in subgroups}
    chosen = ids.get(str(row.get("subgroup_id") or ""))
    if chosen is not None:
        return chosen
    tone = row.get("tone")
    if len(subgroups) > 1 and tone in ("light", "dark"):
        match = next((g for g in subgroups if g.use_on == tone), None)
        if match is not None:
            return match
    return photo_subgroup


def file_for(design: Any, versions: list[Any], subgroup_name: str | None) -> Any | None:
    """The file (the design itself, or one of its versions) a subgroup's mockups print, or None
    when the design has nothing for that subgroup (a single-version design prints only its own
    subgroup's mockups). A design file without a subgroup prints for every subgroup."""
    for v in versions:
        if same(v.subgroup, subgroup_name):
            return v
    if design.subgroup is None or same(design.subgroup, subgroup_name):
        return design
    return None
