"""Every attribute Etsy offers for a listing's category, as the seller edits it (customer request, 2026-11-05).

The list is Etsy's own (``getPropertiesByTaxonomyId``), read with the profile's reference listing and kept in its
payload within its 24-hour refresh (``category_properties``, :func:`compact`; a payload read before this falls back to
the older ``category_attributes`` lists). Only what Etsy allows is offered and accepted:

* **choice**: one of Etsy's values, or several up to ``max_values`` where Etsy takes several. Values that belong to a
  unit (sizes in US or EU) are filtered by the unit chosen.
* **number**: a number with one of Etsy's units (``scales``), where Etsy asks for a unit.

Properties with neither a value list nor a unit are free text on Etsy: they are not offered here (Shop Manager).

**Whose value wins**: the seller's (``generated_content.item_options[name]``: a list of values, or ``{"values",
"scale"}`` with a unit; ``[]`` is "not set", explicitly), else the writer's suggestion (``attributes["listing"]``),
else the profile's reference listing (the garment's own), else nothing. A rewrite writes only the suggestion, so the
seller's value is never overwritten. **Per shop** (``shop_overrides[shop]["attributes"]``): a value for that shop
beats the listing's own; "Keep as it is" clears the shop's. The section and Occasion/Holiday keys of ``item_options``
are the same storage the review card's Item Options always used.

**Category change**: a value the listing's category (in that shop, by its profile there) no longer offers is not
sent, and :func:`dropped` says which, so the card can show it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.pipeline import item_options as opts

CHOICE, NUMBER = "choice", "number"
#: The key of a shop's attributes inside its ``shop_overrides`` entry.
SHOP_KEY = "attributes"
#: Keys of ``item_options`` that are not attributes.
NOT_ATTRIBUTES = frozenset({opts.SECTIONS})

SELLER, SHOP, WRITER, PROFILE, NONE = "seller", "shop", "writer", "profile", "none"


@dataclass(frozen=True)
class Prop:
    name: str
    kind: str
    max_values: int = 1
    required: bool = False
    property_id: int | None = None
    #: Choice: Etsy's value names, in Etsy's order; each one's unit when it has one.
    values: list[str] = field(default_factory=list)
    value_scale: dict[str, str] = field(default_factory=dict)
    #: Etsy's units for this property (names), when it asks for one.
    scales: list[str] = field(default_factory=list)


def compact(taxonomy_properties: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """What the payload keeps of Etsy's property list: what the editor needs and nothing else."""
    out: list[dict[str, Any]] = []
    for prop in taxonomy_properties or []:
        name = str(prop.get("property_name") or prop.get("name") or "").strip()
        if not name or prop.get("supports_attributes") is False:
            continue
        scales = {int(s["scale_id"]): str(s.get("display_name") or s.get("name") or "").strip()
                  for s in prop.get("scales") or [] if s.get("scale_id") is not None}
        values = [{"name": str(v.get("name") or "").strip(), **({"scale": scales.get(int(v["scale_id"]), "")} if v.get("scale_id") in scales else {})}
                  for v in prop.get("possible_values") or [] if str(v.get("name") or "").strip()]
        if not values and not scales:
            continue  # free text on Etsy: set in Shop Manager
        multi = bool(prop.get("is_multivalued"))
        out.append({
            "id": prop.get("property_id"), "name": name, "kind": CHOICE if values else NUMBER,
            "max": (int(prop.get("max_values_allowed") or len(values) or 1) if multi else 1),
            "required": bool(prop.get("is_required")), "values": values, "scales": [s for s in scales.values() if s],
        })
    return out


def properties_of(payload: Mapping[str, Any] | None) -> list[Prop]:
    """The category's attributes from a profile's payload; the older lists (names and values only) when that is
    all it has."""
    payload = payload or {}
    stored = payload.get("category_properties")
    if isinstance(stored, list):
        return [Prop(name=str(p["name"]), kind=str(p.get("kind") or CHOICE), max_values=max(1, int(p.get("max") or 1)),
                     required=bool(p.get("required")), property_id=p.get("id"),
                     values=[str(v["name"]) for v in p.get("values") or []],
                     value_scale={str(v["name"]): str(v["scale"]) for v in p.get("values") or [] if v.get("scale")},
                     scales=[str(s) for s in p.get("scales") or []]) for p in stored if p.get("name")]
    out = []
    for name in (payload.get("category_attributes") or {}):
        options = opts.options_for(payload, str(name))
        if options is not None:
            out.append(Prop(name=options.name, kind=CHOICE, max_values=options.max_values, values=list(options.values)))
    return out


def find(props: Iterable[Prop], name: str) -> Prop | None:
    return next((p for p in props if p.name.strip().lower() == name.strip().lower()), None)


def parse(raw: Any) -> tuple[list[str], str | None]:
    """A stored value: (values, unit)."""
    if isinstance(raw, Mapping):
        values = raw.get("values")
        return ([str(v).strip() for v in values or [] if str(v).strip()] if isinstance(values, list) else
                ([str(values).strip()] if values not in (None, "") else [])), (str(raw["scale"]) if raw.get("scale") else None)
    if raw is None:
        return [], None
    if isinstance(raw, (list, tuple)):
        return [str(v).strip() for v in raw if str(v).strip()], None
    return [part.strip() for part in str(raw).split(",") if part.strip()], None


def stored(values: list[str], scale: str | None) -> Any:
    return {"values": values, "scale": scale} if scale else list(values)


def check(prop: Prop | None, values: Iterable[str], scale: str | None, name: str) -> tuple[list[str], str | None]:
    """``values`` (and ``scale``) as Etsy names them, or ValueError saying why not. ``[]``: not set."""
    wanted = [str(v).strip() for v in values if str(v).strip()]
    if prop is None:
        if not wanted:
            return [], None
        raise ValueError(f"{name} is not offered for this listing's category")
    unit = None
    if scale:
        unit = next((s for s in prop.scales if s.lower() == scale.strip().lower()), None)
        if unit is None:
            raise ValueError(f'"{scale}" is not one of Etsy\'s units for {prop.name}')
    if not wanted:
        return [], None
    if prop.kind == NUMBER:
        if prop.scales and unit is None:
            raise ValueError(f"Choose a unit for {prop.name}")
        if len(wanted) != 1:
            raise ValueError(f"{prop.name} takes one number")
        try:
            number = float(wanted[0])
        except ValueError:
            raise ValueError(f"{prop.name} takes a number") from None
        if number <= 0:
            raise ValueError(f"{prop.name} takes a number above 0")
        return [wanted[0]], unit
    canonical = {v.lower(): v for v in prop.values}
    out: list[str] = []
    for value in wanted:
        match = canonical.get(value.lower())
        if match is None:
            raise ValueError(f'"{value}" is not one of Etsy\'s {prop.name} values for this category')
        if unit and prop.value_scale.get(match) not in (None, unit):
            raise ValueError(f'"{value}" is not a {unit} value of {prop.name}')
        if match not in out:
            out.append(match)
    if len(out) > prop.max_values:
        raise ValueError(f"{prop.name} takes {'one value' if prop.max_values == 1 else f'up to {prop.max_values} values'}")
    if unit is None and prop.value_scale:
        units = {prop.value_scale.get(v) for v in out} - {None}
        unit = units.pop() if len(units) == 1 else None
    return out, unit


def own(content: Any) -> dict[str, Any]:
    """The seller's values on the listing itself."""
    return {k: v for k, v in (content.item_options or {}).items() if k not in NOT_ATTRIBUTES}


def shop_own(content: Any, shop_id: Any) -> dict[str, Any]:
    from app.pipeline import shop_listing

    raw = shop_listing.entry(content, shop_id).get(SHOP_KEY) if shop_id is not None else None
    return dict(raw) if isinstance(raw, Mapping) else {}


def _by_name(mapping: Mapping[str, Any], name: str) -> tuple[bool, Any]:
    for key, value in mapping.items():
        if str(key).strip().lower() == name.strip().lower():
            return True, value
    return False, None


def reference_values(reference: Iterable[Mapping[str, Any]] | None, prop: Prop) -> tuple[list[str], str | None]:
    for attr in reference or []:
        if (prop.property_id is not None and attr.get("property_id") == prop.property_id) or \
                str(attr.get("property_name") or "").strip().lower() == prop.name.lower():
            return [str(v) for v in attr.get("values") or []], None
    return [], None


@dataclass
class Current:
    name: str
    values: list[str]
    scale: str | None
    source: str


def current(content: Any, prop: Prop, shop_id: Any = None, reference: Iterable[Mapping[str, Any]] | None = None) -> Current:
    """What this listing has for ``prop`` (in ``shop_id`` when given) and whose it is."""
    hit, raw = _by_name(shop_own(content, shop_id), prop.name)
    if hit:
        return Current(prop.name, *parse(raw), SHOP)
    hit, raw = _by_name(own(content), prop.name)
    if hit:
        return Current(prop.name, *parse(raw), SELLER)
    hit, raw = _by_name(((content.attributes or {}).get("listing") or {}), prop.name)
    if hit and parse(raw)[0]:
        return Current(prop.name, *parse(raw), WRITER)
    values, scale = reference_values(reference, prop)
    return Current(prop.name, values, scale, PROFILE if values else NONE)


def dropped(content: Any, props: list[Prop], shop_id: Any = None) -> list[tuple[str, list[str]]]:
    """The seller's values that this category does not offer: kept, never sent, and said."""
    out: list[tuple[str, list[str]]] = []
    for scope in (own(content), shop_own(content, shop_id)):
        for name, raw in scope.items():
            values, scale = parse(raw)
            if not values:
                continue
            try:
                check(find(props, name), values, scale, name)
            except ValueError:
                out.append((name, values))
    return out


def draft_set(content: Any, shop_id: Any = None, props: list[Prop] | None = None) -> tuple[dict[str, Any], set[str]]:
    """What a draft for ``shop_id`` sends: the writer's suggestions with the seller's values on top, the shop's on
    top of those (``[]`` leaves the attribute out), and the names the seller set (they beat the reference listing's
    values too). ``props``: the category of the profile the draft goes with; a seller's value it does not offer is
    left out (:func:`dropped` says so on the card). The category the draft goes to checks each again
    (attributes.resolve_optional_attributes)."""
    out: dict[str, Any] = dict(((content.attributes or {}).get("listing") or {}))
    seller: set[str] = set()
    for scope in (own(content), shop_own(content, shop_id)):
        for name, raw in scope.items():
            for key in [k for k in out if k.strip().lower() == name.strip().lower()]:
                del out[key]
            values, scale = parse(raw)
            if props and values:
                try:
                    check(find(props, name), values, scale, name)
                except ValueError:
                    continue  # its category does not offer it any more: shown as dropped, never sent
            seller.add(name)
            if values:
                out[name] = {"values": values, "scale": scale} if scale else values
    return out, seller


def set_value(mapping: dict[str, Any], prop_name: str, value: Any) -> dict[str, Any]:
    """``mapping`` with ``prop_name`` set (or, with None, removed), under one spelling."""
    out = {k: v for k, v in mapping.items() if str(k).strip().lower() != prop_name.strip().lower()}
    if value is not None:
        out[prop_name] = value
    return out
