"""Resolve Etsy's required clothing attributes for a new listing (v4 §B).

Etsy's clothing categories require attributes (neckline, sleeve length, clothing
style, ...). Each required property is filled, in priority order:

1. from the **reference listing's** own attribute value (copied verbatim),
2. else from the **vision analysis** of the mockup (neckline / sleeve length /
   clothing style read from the image), mapped to the property's controlled value,
3. else it is reported **missing** — never a random/hardcoded default.

Pure functions only; the Etsy fetch/write happens in the publisher.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Etsy property name (lowercased) -> the vision analysis field that fills it.
_VISION_FIELD_BY_PROPERTY = {
    "neckline": "neckline",
    "sleeve length": "sleeve_length",
    "clothing style": "clothing_style",
    "style": "clothing_style",
}


@dataclass
class ResolvedAttribute:
    property_id: int
    property_name: str
    value_ids: list[int]
    values: list[str]
    scale_id: int | None = None


def _match_value(possible_values: list[dict[str, Any]] | None, wanted: str) -> tuple[int, str] | None:
    """Find a controlled value_id whose name matches ``wanted`` (exact, then contains)."""
    wanted_l = wanted.strip().lower()
    for value in possible_values or []:
        if str(value.get("name", "")).strip().lower() == wanted_l:
            return int(value["value_id"]), str(value["name"])
    for value in possible_values or []:
        name = str(value.get("name", "")).lower()
        if wanted_l and (wanted_l in name or name in wanted_l):
            return int(value["value_id"]), str(value["name"])
    return None


def resolve_required_attributes(
    taxonomy_properties: list[dict[str, Any]],
    reference_attributes: list[dict[str, Any]] | None,
    vision: dict[str, Any] | None,
) -> tuple[list[ResolvedAttribute], list[str]]:
    """Return (resolved attributes, names of the ones that couldn't be determined)."""
    ref_by_id = {a.get("property_id"): a for a in (reference_attributes or [])}
    vision = vision or {}
    resolved: list[ResolvedAttribute] = []
    missing: list[str] = []

    for prop in taxonomy_properties or []:
        if not prop.get("is_required"):
            continue
        pid = prop.get("property_id")
        pname = str(prop.get("property_name", ""))

        # 1) Reference listing's own value.
        ref = ref_by_id.get(pid)
        if ref and (ref.get("value_ids") or ref.get("values")):
            resolved.append(
                ResolvedAttribute(
                    property_id=int(pid),
                    property_name=pname,
                    value_ids=[int(v) for v in (ref.get("value_ids") or [])],
                    values=[str(v) for v in (ref.get("values") or [])],
                    scale_id=ref.get("scale_id"),
                )
            )
            continue

        # 2) Vision analysis of the mockup.
        field = _VISION_FIELD_BY_PROPERTY.get(pname.strip().lower())
        wanted = str(vision.get(field, "")).strip() if field else ""
        if wanted:
            possible = prop.get("possible_values")
            match = _match_value(possible, wanted)
            if match is not None:
                value_id, value_name = match
                resolved.append(
                    ResolvedAttribute(int(pid), pname, [value_id], [value_name])
                )
                continue
            if not possible:  # free-text property -> send the raw value
                resolved.append(ResolvedAttribute(int(pid), pname, [], [wanted]))
                continue

        # 3) Undetermined -> report; never guess.
        missing.append(pname)

    return resolved, missing


# What the listing's generated attribute values are called in Etsy's categories.
# A category offers only some of these; a property it does not offer is skipped.
_OPTIONAL_PROPERTY_NAMES: dict[str, tuple[str, ...]] = {
    "occasion": ("occasion",),
    "holiday": ("holiday", "celebration"),
    "style": ("style", "clothing style"),
    "primary_color": ("primary color", "primary colour"),
    "theme": ("theme", "subject", "graphic"),
    "recipient": ("recipient",),
}


def resolve_optional_attributes(
    taxonomy_properties: list[dict[str, Any]],
    proposed: dict[str, str] | None,
    already_set: set[int] | None = None,
) -> tuple[list[ResolvedAttribute], list[str]]:
    """The category's optional attributes that the design supports (search rules).

    ``proposed`` are the values written with the listing (occasion, holiday,
    style, primary colour, theme, recipient). Each is matched to a property the
    category actually offers and to one of that property's own values; Etsy
    matches searches on these. Nothing is guessed: a value the property does not
    list is left unset and returned in the second list, so the seller can be told.
    Required properties and anything in ``already_set`` are left alone.
    """
    proposed = {k: v.strip() for k, v in (proposed or {}).items() if v and v.strip()}
    taken = set(already_set or ())
    resolved: list[ResolvedAttribute] = []
    unmatched: list[str] = []
    for key, names in _OPTIONAL_PROPERTY_NAMES.items():
        wanted = proposed.get(key)
        if not wanted:
            continue
        prop = next(
            (
                p
                for name in names
                for p in taxonomy_properties or []
                if str(p.get("property_name", "")).strip().lower() == name
                and not p.get("is_required")
                and p.get("property_id") not in taken
            ),
            None,
        )
        if prop is None:
            continue  # this category has no such attribute
        pid = int(prop["property_id"])
        possible = prop.get("possible_values")
        if not possible:
            continue  # a free-text optional property is the seller's to fill
        match = _match_value(possible, wanted)
        if match is None:
            unmatched.append(f"{prop.get('property_name')}: {wanted}")
            continue
        taken.add(pid)
        resolved.append(ResolvedAttribute(pid, str(prop.get("property_name", "")), [match[0]], [match[1]]))
    return resolved, unmatched
