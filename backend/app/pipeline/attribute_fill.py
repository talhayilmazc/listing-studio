"""Fill every category attribute the design shows or the profile knows (Part C).

Etsy treats attributes like tags, so a listing should carry every one that is
true of it. The model chooses some from Etsy's lists while writing; this fills
the rest deterministically, only from what is known:

- **visible** in the design, per the vision analysis: its colours (primary and
  secondary colour), the holiday or occasion it was drawn for, its themes, its
  style or era;
- **known** from the profile's reference listing: the garment's own properties
  (sleeve length, neckline, ...), copied as the reference has them.

A value is written only when it is, after normalising case and punctuation, one
of the values Etsy lists for that property in this category. Nothing that is
neither visible nor in the profile is ever filled; a colour Etsy has no name for
is left empty rather than mapped to a neighbour.
"""

from __future__ import annotations

import re
from typing import Any

from app.pipeline.attributes import ResolvedAttribute

#: Common colour words for which Etsy's list uses another name. Only names that
#: mean the same hue; "teal" (blue or green?) is deliberately not here.
COLOUR_NAMES = {
    "grey": "gray", "navy": "blue", "navy blue": "blue", "sky blue": "blue", "royal blue": "blue",
    "cream": "beige", "ivory": "beige", "tan": "beige", "khaki": "beige", "maroon": "red",
    "burgundy": "red", "crimson": "red", "scarlet": "red", "golden": "gold", "violet": "purple",
    "lavender": "purple", "lilac": "purple", "mustard": "yellow", "olive": "green",
    "forest green": "green", "charcoal": "gray", "off white": "white", "off-white": "white",
}
#: Spellings of a holiday that Etsy names one way.
HOLIDAY_NAMES = {
    "4th of july": "independence day", "fourth of july": "independence day", "july 4th": "independence day",
    "july 4": "independence day", "xmas": "christmas", "valentines": "valentines day",
    "st patricks": "st patricks day", "saint patricks day": "st patricks day", "thanksgiving day": "thanksgiving",
    "mothers": "mothers day", "fathers": "fathers day", "new years": "new years", "new year": "new years",
}
#: Which attribute names each kind of fact fills (lowercase).
COLOUR_PRIMARY = ("primary color", "primary colour")
COLOUR_SECONDARY = ("secondary color", "secondary colour")
HOLIDAY = ("holiday", "celebration")
OCCASION = ("occasion",)
THEME = ("theme", "subject")
STYLE = ("style",)
#: The garment's own properties: never read from the artwork's analysis of the
#: design, only from the profile's reference listing or what the mockup shows.
GARMENT = ("sleeve length", "neckline", "clothing style", "fit", "material")


def norm(text: str) -> str:
    text = text.lower().replace("’", "").replace("'", "").replace("&", "and")
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _lookup(values: list[str], wanted: str) -> str | None:
    """The Etsy value that is ``wanted`` (normalised, singular/plural), if any."""
    key = norm(wanted)
    if not key:
        return None
    for value in values:
        v = norm(value)
        if v == key or v.rstrip("s") == key.rstrip("s"):
            return value
    return None


def _named(choices: dict[str, list[str]], names: tuple[str, ...]) -> list[str]:
    return [n for n in choices if n.strip().lower() in names]


def _colours(analysis: Any, values: list[str]) -> list[str]:
    out: list[str] = []
    for colour in getattr(analysis, "colors", None) or []:
        c = norm(str(colour))
        found = _lookup(values, COLOUR_NAMES.get(c, c))
        if found is None and c.split():  # "dusty pink" -> pink; "light blue" -> blue
            last = c.split()[-1]
            found = _lookup(values, COLOUR_NAMES.get(last, last))
        if found and found not in out:
            out.append(found)
    return out


def _holiday(analysis: Any, values: list[str]) -> str | None:
    for raw in (getattr(analysis, "occasion", ""), getattr(analysis, "season", "")):
        key = norm(str(raw or ""))
        if not key:
            continue
        found = _lookup(values, HOLIDAY_NAMES.get(key, key))
        if found:
            return found
        for value in values:  # "christmas morning" names Christmas
            if re.search(rf"\b{re.escape(norm(value))}\b", key):
                return value
    return None


def _first(values: list[str], candidates: list[str]) -> str | None:
    for c in candidates:
        found = _lookup(values, c)
        if found:
            return found
    return None


def fill_design_attributes(
    chosen: dict[str, str], choices: dict[str, list[str]], analysis: Any
) -> tuple[dict[str, str], dict[str, str]]:
    """``chosen`` plus every attribute the analysis shows, from Etsy's lists.

    Returns (attributes, where each came from: "model" or "vision"). An attribute
    the model already chose is kept; only empty ones are filled.
    """
    out = {k: v for k, v in chosen.items() if v}
    source = {k: "model" for k in out}

    def put(names: list[str], value: str | None) -> None:
        for name in names:
            if value and name not in out:
                out[name], source[name] = value, "vision"

    for name in _named(choices, COLOUR_PRIMARY):
        found = _colours(analysis, choices[name])
        put([name], found[0] if found else None)
    for name in _named(choices, COLOUR_SECONDARY):
        primary = next((out[n] for n in _named(choices, COLOUR_PRIMARY) if n in out), None)
        rest = [c for c in _colours(analysis, choices[name]) if c != primary]
        put([name], rest[0] if rest else None)
    for name in _named(choices, HOLIDAY):
        put([name], _holiday(analysis, choices[name]))
    for name in _named(choices, OCCASION):
        occasion = str(getattr(analysis, "occasion", "") or "")
        put([name], _lookup(choices[name], occasion) if occasion else None)
    themes = [*(getattr(analysis, "themes", None) or []), getattr(analysis, "theme", ""), getattr(analysis, "profession", "")]
    for name in _named(choices, THEME):
        put([name], _first(choices[name], [str(t) for t in themes if t]))
    for name in _named(choices, STYLE):
        style = norm(str(getattr(analysis, "style", "") or ""))
        found = next(
            (v for v in choices[name] if norm(v) and re.search(rf"\b{re.escape(norm(v))}\b", style)), None
        )
        put([name], found)
    return out, source


def garment_attributes(
    taxonomy_properties: list[dict[str, Any]],
    reference_attributes: list[dict[str, Any]] | None,
    vision: dict[str, Any] | None,
    taken: set[int],
) -> list[ResolvedAttribute]:
    """The garment's optional properties (sleeve length, neckline, ...): the
    profile's reference listing's own value, else the value the mockup shows when
    it is exactly one of Etsy's; never a guess."""
    ref = {a.get("property_id"): a for a in reference_attributes or []}
    vision = vision or {}
    out: list[ResolvedAttribute] = []
    for prop in taxonomy_properties or []:
        name = str(prop.get("property_name") or "").strip()
        pid = prop.get("property_id")
        if name.lower() not in GARMENT or prop.get("is_required") or pid is None or int(pid) in taken:
            continue
        have = ref.get(pid)
        if have and (have.get("value_ids") or have.get("values")):
            out.append(ResolvedAttribute(
                int(pid), name, [int(v) for v in have.get("value_ids") or []],
                [str(v) for v in have.get("values") or []], have.get("scale_id"),
            ))
            continue
        seen = str(vision.get(name.lower().replace(" ", "_"), "") or "").strip()
        names = [str(v.get("name", "")) for v in prop.get("possible_values") or []]
        match = _lookup(names, seen) if seen else None
        if match:
            value = next(v for v in prop["possible_values"] if str(v.get("name", "")) == match)
            out.append(ResolvedAttribute(int(pid), name, [int(value["value_id"])], [match]))
    return out
