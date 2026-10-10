"""Occasion, Holiday and Section for one listing: what the seller sees and sets on
the review card, as in Etsy's own listing editor (Item Options, shop section).

* **Occasion / Holiday** take only Etsy's own values for the listing's category
  (``getPropertiesByTaxonomyId``, kept in the profile's payload within its 24-hour
  refresh: ``category_attributes`` and ``category_attribute_limits``). A property
  that takes several values takes up to its limit; one that takes one, one. A
  value that is not on Etsy's list is refused, never written.
  What the listing gets: the seller's choice (``generated_content.item_options``,
  ``[]`` = cleared), else what the writer chose (``attributes["listing"]``).
* **Profile defaults** (``listing_profile.default_occasion`` / ``default_holiday``):
  "" means none; NULL means no default. The writer keeps its own choice only when
  the design clearly shows one (the vision read an occasion); otherwise the
  profile's default is used.
* **Section** is per shop (each shop has its own sections). The default is the
  shop's EXISTING section whose title shares the most words with the design's
  theme ("Nurse Collection" for a nurse design), said with why ("matches: nurse");
  failing that, the profile's rule (pipeline/sections.py). A section name is never
  invented: one the shop does not have is created only when the seller confirms.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.pipeline.sections import choose_section

OCCASION = "Occasion"
HOLIDAY = "Holiday"
PROPERTIES = (OCCASION, HOLIDAY)
#: The key of the per-shop section choices inside ``item_options``.
SECTIONS = "sections"


@dataclass(frozen=True)
class PropertyOptions:
    name: str
    values: list[str]
    max_values: int


def options_for(payload: Mapping[str, Any] | None, name: str) -> PropertyOptions | None:
    """Etsy's values for ``name`` in the profile's category, or None when the
    category has no such property (or it was not read yet)."""
    choices = (payload or {}).get("category_attributes") or {}
    limits = (payload or {}).get("category_attribute_limits") or {}
    for key, values in choices.items():
        if str(key).strip().lower() == name.lower() and values:
            limit = next((int(v) for k, v in limits.items() if str(k).strip().lower() == name.lower()), 1)
            return PropertyOptions(name=str(key), values=[str(v) for v in values], max_values=max(1, limit))
    return None


def attribute_limits(taxonomy_properties: Iterable[Mapping[str, Any]] | None) -> dict[str, int]:
    """How many values each property takes (Etsy: ``is_multivalued`` and
    ``max_values_allowed``); 1 unless Etsy says it takes several."""
    out: dict[str, int] = {}
    for prop in taxonomy_properties or []:
        name = str(prop.get("property_name") or prop.get("name") or "").strip()
        if not name:
            continue
        if prop.get("is_multivalued"):
            allowed = prop.get("max_values_allowed")
            out[name] = int(allowed) if allowed else len(prop.get("possible_values") or []) or 1
        else:
            out[name] = 1
    return out


def _split(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def chosen(content_attributes: Mapping[str, Any] | None, item_options: Mapping[str, Any] | None, name: str) -> tuple[list[str], str]:
    """What the listing gets for ``name`` and whose choice it is: "seller" (set on
    the card, possibly cleared), "writer" or "none"."""
    own = item_options or {}
    if name in own:
        return _split(own[name]), "seller"
    written = _split(((content_attributes or {}).get("listing") or {}).get(name))
    return written, ("writer" if written else "none")


def validate(values: Sequence[str] | None, options: PropertyOptions | None, name: str) -> list[str]:
    """``values`` as Etsy names them; refuses anything not on Etsy's list or more
    values than the property takes."""
    wanted = [v for v in (values or []) if str(v).strip()]
    if not wanted:
        return []
    if options is None:
        raise ValueError(f"{name} is not offered for this listing's category")
    canonical = {v.lower(): v for v in options.values}
    out: list[str] = []
    for value in wanted:
        match = canonical.get(str(value).strip().lower())
        if match is None:
            raise ValueError(f'"{value}" is not one of Etsy\'s {name} values for this category')
        if match not in out:
            out.append(match)
    if len(out) > options.max_values:
        raise ValueError(f"{name} takes {'one value' if options.max_values == 1 else f'up to {options.max_values} values'}")
    return out


def apply_profile_default(
    listing_attributes: dict[str, Any], name: str, default: str | None, design_shows: str, options: PropertyOptions | None
) -> dict[str, Any]:
    """The profile's default for ``name`` unless the design clearly shows another.

    ``default`` None: no default, the writer's choice stands. "": none, unless the
    design shows one. A default that is not on Etsy's list is not used."""
    if default is None:
        return listing_attributes
    if listing_attributes.get(name) and design_shows.strip():
        return listing_attributes  # the design shows it: the writer's choice stands
    out = {k: v for k, v in listing_attributes.items() if k != name}
    if default.strip():
        try:
            valid = validate([default], options, name)
        except ValueError:
            valid = []
        if valid:
            out[name] = valid[0]
    return out


# --- Section -----------------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset({"the", "and", "a", "an", "of", "for", "with", "shirt", "shirts", "tee", "tees", "t", "collection",
                   "collections", "design", "designs", "shop", "new", "gift", "gifts", "sale", "all", "my", "our", "in"})


def _stem(word: str) -> str:
    for suffix in ("ies", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def _words(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


@dataclass(frozen=True)
class SectionSuggestion:
    title: str | None
    reason: str


def suggest_section(
    titles: Sequence[str], *, theme_words: Iterable[str], occasion: str = "", profile_name: str = ""
) -> SectionSuggestion:
    """The shop's existing section that best fits the design, and why; never a new name.

    Best: the title sharing the most words with the design's theme words (a tie is
    not a clear fit). Else the profile's rule: a Comfort Colors profile goes to a
    "Comfort Colors" section; the theme keyword rules, among existing titles only."""
    existing = [t for t in titles if t and t.strip()]
    if not existing:
        return SectionSuggestion(None, "this shop has no sections")
    design = set()
    for text in [*theme_words, occasion]:
        design |= _words(str(text))
    scored = sorted(((len(_words(t) & design), t) for t in existing), key=lambda x: -x[0])
    if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        title = scored[0][1]
        shared = sorted(_words(title) & design)
        return SectionSuggestion(title, "matches: " + ", ".join(shared))
    lowered = {t.lower(): t for t in existing}
    if "comfort colors" in profile_name.lower() and "comfort colors" in lowered:
        return SectionSuggestion(lowered["comfort colors"], "profile rule: Comfort Colors")
    decision = choose_section(theme=" ".join(str(w) for w in theme_words), occasion=occasion, existing_sections=existing)
    if decision.name and decision.exists:
        return SectionSuggestion(decision.name, "profile rule: theme")
    return SectionSuggestion(None, "no section fits clearly")


# --- What a draft gets ----------------------------------------------------------------------


def draft_attributes(content_attributes: Mapping[str, Any] | None, own: Mapping[str, Any] | None) -> dict[str, Any]:
    """The optional attributes a draft writes: the writer's, with the seller's
    Occasion / Holiday (several values as a list; cleared ones left out)."""
    out: dict[str, Any] = dict(((content_attributes or {}).get("listing") or {}))
    for name in PROPERTIES:
        values, source = chosen(content_attributes, own, name)
        if source != "seller":
            continue
        for key in [k for k in out if k.strip().lower() == name.lower()]:
            del out[key]
        if values:
            out[name] = values
    return out


def section_choice(own: Mapping[str, Any] | None, connection_id: Any) -> dict[str, Any] | None:
    """The seller's section for this shop (``explicit``), else the title they chose
    in another shop (used here when this shop has a section by that name), else None."""
    sections = (own or {}).get(SECTIONS) or {}
    mine = sections.get(str(connection_id))
    if mine is not None:
        return {"id": mine.get("id"), "title": mine.get("title"), "explicit": True}
    carried = next((s.get("title") for s in sections.values() if s and s.get("title")), None)
    return {"id": None, "title": carried, "explicit": False} if carried else None


def theme_words(content_attributes: Mapping[str, Any] | None) -> list[str]:
    vision = (content_attributes or {}).get("vision") or {}
    words = [str(t) for t in vision.get("themes") or [] if t]
    if vision.get("theme") and str(vision["theme"]) not in words:
        words.insert(0, str(vision["theme"]))
    return words
