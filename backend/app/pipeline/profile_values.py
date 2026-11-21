"""A profile's settings as the seller's own values (feature/manual-profiles).

Until now every profile copied its settings from one of the seller's own Etsy listings (a reference listing):
a new, empty shop has none, and a value the reference gives could not be changed. Here a profile's settings
are a plain dictionary, the *values* of the profile form:

* a **manual** profile (``listing_profile.manual``) has no reference listing: ``own_values`` holds every value
  and :func:`to_payload` turns them into the same ``cached_payload`` the draft path has always read
  (pipeline/reference.py ``build_profile_payload``), so drafts are built exactly as before;
* a profile that **follows a reference listing** keeps reading it; ``own_values`` holds only the fields the
  seller changed ("your value"). :func:`apply` puts them over the payload every time it is read from Etsy, so a
  Refresh never overwrites them, and remembers the listing's own value (``payload["_listing"]``) for "Use the
  listing's value again".

The values are the seller's own settings, not Etsy content: they are kept until the profile is deleted. What is
read from Etsy for a manual profile (its category's attribute lists) is still Etsy content and goes after 24
hours like every profile's (workers/retention.py strips it).

:func:`problems` checks the values against Etsy's own limits (the request schema of createDraftListing and
updateListingInventory, and Etsy's documented limits) before anything is saved; each message is one plain
sentence about one field.
"""

from __future__ import annotations

import math
import re
from typing import Any

from app.pipeline import personalization as P
from app.pipeline.reference import MAX_MATERIAL_LENGTH, MAX_MATERIALS, PAYLOAD_VERSION, SIZE_UNITS, WEIGHT_UNITS, clean_materials

#: Etsy's enumerations (createDraftListing in Etsy's OpenAPI file, read 21 November 2026).
WHO_MADE = ("i_did", "someone_else", "collective")
WHEN_MADE = ("made_to_order", "2020_2026", "2010_2019", "2007_2009", "before_2007", "2000_2006", "1990s", "1980s",
             "1970s", "1960s", "1950s", "1940s", "1930s", "1920s", "1910s", "1900s", "1800s", "1700s", "before_1700")
WHO_WORDS = {"i_did": "I did", "someone_else": "Another company or person", "collective": "A member of my shop"}
WHEN_WORDS = {"made_to_order": "Made to order", "2020_2026": "2020 – 2026", "2010_2019": "2010 – 2019",
              "2007_2009": "2007 – 2009", "before_2007": "Before 2007", "2000_2006": "2000 – 2006"}

#: Etsy's limits. A listing's price is at least 0.20 and at most 50,000 of the shop's currency; its quantity 1 to
#: 999; a listing has at most two variation properties and 70 combinations; an option's name is 20 characters.
MIN_PRICE, MAX_PRICE = 0.20, 50_000.0
MAX_QUANTITY = 999
MAX_VARIATION_PROPERTIES = 2
MAX_VARIATION_ROWS = 70
MAX_OPTION_LENGTH = 20
MAX_PROPERTY_NAME = 45
MAX_TAGS = 13
MAX_TAG_LENGTH = 20
MAX_NAME = 120
MAX_PREFIX = 60
#: Etsy's two custom variation properties: a variation whose name the seller types ("Size", "Colour").
CUSTOM_PROPERTIES = (513, 514)

#: The fields of ``own_values`` (and of the form), by the form's groups.
PAYLOAD_FIELDS = ("taxonomy_id", "who_made", "when_made", "is_supply", "price", "quantity", "variations",
                  "shipping_profile_id", "return_policy_id", "readiness_state_id", "production_partner_ids",
                  "item_weight", "item_weight_unit", "item_length", "item_width", "item_height", "item_dimensions_unit",
                  "materials", "attributes", "description")
GROUPS = {
    "Basics": ("name", "taxonomy_id", "who_made", "when_made", "is_supply", "listing_style", "title_prefix"),
    "Price & variations": ("price", "quantity", "variations"),
    "Shipping & policies": ("shipping_profile_id", "return_policy_id", "readiness_state_id", "production_partner_ids",
                            "item_weight", "item_length"),
    "Details": ("materials", "attributes", "personalization"),
    "Text": ("description", "default_tags"),
}
LABELS = {
    "name": "Profile name", "taxonomy_id": "Category", "who_made": "Who made it", "when_made": "When it was made",
    "is_supply": "What it is", "price": "Price", "quantity": "Quantity", "variations": "Variations",
    "shipping_profile_id": "Shipping profile", "return_policy_id": "Return policy", "readiness_state_id": "Processing profile",
    "production_partner_ids": "Production partner", "item_weight": "Weight", "item_length": "Packed size",
    "materials": "Materials", "attributes": "Attributes", "description": "Description", "default_tags": "Default tags",
    "personalization": "Personalization", "listing_style": "Title style", "title_prefix": "Title prefix",
}
_TAG = re.compile(r"^[\w\s\-'™©®]+$", re.UNICODE)


def _number(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _text(value: Any) -> str:
    return str(value or "").strip()


# --- variations ---------------------------------------------------------------------------------


def clean_variations(raw: Any) -> dict[str, Any] | None:
    """The variation table as it is stored: ``{"properties": ["Size", "Colour"], "rows": [{"values": ["S", "Black"],
    "price": 19.99, "quantity": 5, "enabled": True}, ...]}``; None for a listing without variations."""
    if not isinstance(raw, dict):
        return None
    names = [_text(n) for n in raw.get("properties") or [] if _text(n)]
    if not names:
        return None
    rows = []
    for row in raw.get("rows") or []:
        values = [_text(v) for v in (row.get("values") or [])][: len(names)]
        price, quantity = _number(row.get("price")), _number(row.get("quantity"))
        rows.append({"values": values, "price": round(price, 2) if price is not None else None,
                     "quantity": int(quantity) if quantity is not None else None, "enabled": row.get("enabled", True) is not False})
    return {"properties": names, "rows": rows}


def variations_of(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """The variation table a payload's inventory holds (a reference listing's, or one made here)."""
    from app.pipeline.reference import _offering_price

    products = (payload or {}).get("inventory_products") or []
    names: list[str] = []
    rows = []
    for product in products:
        values = product.get("property_values") or []
        if not values:
            continue
        if not names:
            names = [_text(v.get("property_name")) or f"Option {i + 1}" for i, v in enumerate(values)]
        offering = (product.get("offerings") or [{}])[0]
        rows.append({"values": [_text((v.get("values") or [""])[0]) for v in values],
                     "price": _offering_price(offering), "quantity": offering.get("quantity"),
                     "enabled": bool(offering.get("is_enabled", True))})
    return {"properties": names, "rows": rows} if names and rows else None


def _same_table(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    """The same properties and the same options (prices and quantities may differ)."""
    key = lambda t: ([n.lower() for n in t["properties"]], sorted(tuple(v.lower() for v in r["values"]) for r in t["rows"]))  # noqa: E731
    return bool(a and b) and key(a) == key(b)


def _inventory(table: dict[str, Any], price: float | None, quantity: int | None) -> dict[str, Any]:
    """The inventory a variation table makes: Etsy's custom properties, one product a row."""
    ids = CUSTOM_PROPERTIES[: len(table["properties"])]
    products = []
    for row in table["rows"]:
        products.append({
            "property_values": [{"property_id": pid, "property_name": name, "value_ids": [], "values": [value]}
                                for pid, name, value in zip(ids, table["properties"], row["values"])],
            "offerings": [{"price": row["price"] if row.get("price") is not None else price,
                           "quantity": row["quantity"] if row.get("quantity") is not None else quantity,
                           "is_enabled": row.get("enabled", True) is not False}],
        })
    prices = {p["offerings"][0]["price"] for p in products}
    quantities = {p["offerings"][0]["quantity"] for p in products}
    return {"inventory_products": products, "price_on_property": list(ids) if len(prices) > 1 else [],
            "quantity_on_property": list(ids) if len(quantities) > 1 else [], "sku_on_property": [],
            "own_quantities": True}


def _patched(products: list[dict[str, Any]], table: dict[str, Any]) -> list[dict[str, Any]]:
    """A reference listing's own products with the seller's prices and quantities (same options: its ids stay)."""
    by_values = {tuple(v.lower() for v in r["values"]): r for r in table["rows"]}
    out = []
    for product in products:
        values = product.get("property_values") or []
        row = by_values.get(tuple(_text((v.get("values") or [""])[0]).lower() for v in values))
        if row is None:
            out.append(product)
            continue
        offerings = []
        for offering in product.get("offerings") or [{}]:
            o = dict(offering)
            if row.get("price") is not None:
                o["price"] = row["price"]
            if row.get("quantity") is not None:
                o["quantity"] = row["quantity"]
            o["is_enabled"] = row.get("enabled", True) is not False
            offerings.append(o)
        out.append({**product, "offerings": offerings})
    return out


# --- values <-> payload -------------------------------------------------------------------------


def from_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """The form's values for a payload (a reference listing's settings, or a manual profile's)."""
    p = payload or {}
    out: dict[str, Any] = {k: p.get(k) for k in PAYLOAD_FIELDS if k not in ("variations", "attributes", "materials",
                                                                             "production_partner_ids", "quantity")}
    out["quantity"] = p.get("quantity")
    out["variations"] = variations_of(p)
    out["materials"] = list(p.get("materials") or [])
    out["production_partner_ids"] = [int(i) for i in p.get("production_partner_ids") or []]
    named = dict(p.get("attribute_values") or {})
    for attr in p.get("attributes") or []:
        name = _text(attr.get("property_name"))
        if name and name not in named and attr.get("values"):
            named[name] = [str(v) for v in attr.get("values") or []]
    out["attributes"] = named
    return out


def to_payload(values: dict[str, Any], *, currency: str | None, keep: dict[str, Any] | None = None) -> dict[str, Any]:
    """A manual profile's ``cached_payload``: the shape the draft path reads (reference.build_profile_payload).
    ``keep``: the earlier payload, for what was read from Etsy about its category (while fresh)."""
    price, quantity = _number(values.get("price")), _number(values.get("quantity"))
    payload: dict[str, Any] = {
        "taxonomy_id": values.get("taxonomy_id"),
        "price": round(price, 2) if price is not None else None,
        "currency": currency,
        "quantity": int(quantity) if quantity is not None else None,
        "shipping_profile_id": values.get("shipping_profile_id"),
        "return_policy_id": values.get("return_policy_id"),
        "readiness_state_id": values.get("readiness_state_id"),
        "payload_version": PAYLOAD_VERSION,
        "production_partner_ids": [int(i) for i in values.get("production_partner_ids") or []],
        "who_made": values.get("who_made"),
        "when_made": values.get("when_made"),
        "materials": clean_materials(values.get("materials")),
        "is_supply": bool(values.get("is_supply")),
        "is_customizable": None, "is_personalizable": None, "should_auto_renew": values.get("should_auto_renew", True),
        "processing_min": None, "processing_max": None,
        **{k: values.get(k) for k in ("item_weight", "item_weight_unit", "item_length", "item_width", "item_height",
                                      "item_dimensions_unit")},
        "description": _text(values.get("description")),
        "inventory_products": [], "price_on_property": [], "quantity_on_property": [], "sku_on_property": [],
        "attributes": [],
        "attribute_values": {k: v for k, v in (values.get("attributes") or {}).items() if v not in (None, "", [])},
        "images": [],
        # Whether its shipping profile is calculated by Etsy (then a draft needs the weight and packed size).
        "shipping_profile_type": values.get("shipping_profile_type"),
        "manual": True,
    }
    table = clean_variations(values.get("variations"))
    if table and table["rows"]:
        payload.update(_inventory(table, payload["price"], payload["quantity"]))
        prices = [r["price"] for r in table["rows"] if r.get("price") is not None and r.get("enabled", True)]
        if prices:
            payload["price"] = min(prices)  # the listing's own price: its lowest option's, as Etsy shows it
    for key in ("category_attributes", "category_attribute_limits", "category_properties", "category_names",
                "category_read_at", "shop_id"):
        if keep and keep.get(key) is not None:
            payload[key] = keep[key]
    return payload


#: The payload keys a field's own value changes (the rest: the key of its own name).
_TOUCHES = {"variations": ("inventory_products", "price_on_property", "quantity_on_property", "sku_on_property",
                           "own_quantities", "price"),
            "attributes": ("attribute_values",)}


def base_of(payload: dict[str, Any] | None) -> dict[str, Any]:
    """The payload as it was read from the listing: the seller's own values taken off again."""
    out = dict(payload or {})
    raw = out.pop("_base", None) or {}
    out.pop("_listing", None)
    out.update(raw)
    return out


def apply(payload: dict[str, Any], own: dict[str, Any] | None) -> dict[str, Any]:
    """A reference listing's payload with the seller's own values over it ("your value"). The listing's own
    value of each is kept beside it (``_listing``: as the form shows it; ``_base``: as the payload held it), with
    the payload, so it goes when the payload does, for "Use the listing's value again"."""
    own = {k: v for k, v in (own or {}).items() if k in PAYLOAD_FIELDS}
    out = base_of(payload)
    if not own:
        return out
    listing = from_payload(out)
    kept: dict[str, Any] = {}
    base = {key: out.get(key) for field in own for key in _TOUCHES.get(field, (field,))}
    for field in sorted(own, key=lambda f: (f == "variations", f)):  # the variations last: they set the listing's price
        value = own[field]
        kept[field] = listing.get(field)
        if field == "variations":
            table = clean_variations(value)
            theirs = variations_of(out)
            if table is None:
                out.update({"inventory_products": [], "price_on_property": [], "quantity_on_property": [], "sku_on_property": []})
            elif _same_table(table, theirs):
                out["inventory_products"] = _patched(out.get("inventory_products") or [], table)
                out["own_quantities"] = True
                prices = {r["price"] for r in table["rows"]}
                if len(prices) > 1 and not out.get("price_on_property"):
                    out["price_on_property"] = [v.get("property_id") for v in
                                                (out["inventory_products"][0].get("property_values") or [])]
            else:
                out.update(_inventory(table, _number(out.get("price")), _number(out.get("quantity"))))
            if table and table["rows"]:
                prices_ = [r["price"] for r in table["rows"] if r.get("price") is not None and r.get("enabled", True)]
                if prices_:
                    out["price"] = min(prices_)
        elif field == "attributes":
            out["attribute_values"] = {k: v for k, v in (value or {}).items() if v not in (None, "", [])}
        elif field == "materials":
            out["materials"] = clean_materials(value)
        elif field == "production_partner_ids":
            out[field] = [int(i) for i in value or []]
        elif field == "price":
            n = _number(value)
            out["price"] = round(n, 2) if n is not None else out.get("price")
        elif field == "quantity":
            n = _number(value)
            out["quantity"] = int(n) if n is not None else None
        else:
            out[field] = value
    out["_listing"] = kept
    out["_base"] = base
    return out


def named_attributes(taxonomy_properties: list[dict[str, Any]] | None, reference: list[dict[str, Any]] | None,
                     named: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The profile's attributes as the draft writes them: the reference listing's own, with the values the seller
    set by name (a manual profile's, or "your value") looked up in the category the draft goes to. A name or value
    that category does not list is left out, never guessed."""
    out = [dict(a) for a in reference or []]
    if not named:
        return out
    by_name = {_text(p.get("property_name") or p.get("name")).lower(): p for p in taxonomy_properties or []}
    for name, wanted in named.items():
        prop = by_name.get(_text(name).lower())
        if prop is None or prop.get("property_id") is None:
            continue
        scale_name = None
        if isinstance(wanted, dict):
            scale_name, wanted = _text(wanted.get("scale")) or None, wanted.get("values") or []
        names = [_text(v) for v in (wanted if isinstance(wanted, (list, tuple)) else [wanted]) if _text(v)]
        if not names:
            continue
        scales = {_text(s.get("display_name") or s.get("name")).lower(): s.get("scale_id") for s in prop.get("scales") or []}
        scale_id = scales.get(scale_name.lower()) if scale_name else None
        possible = {_text(v.get("name")).lower(): v for v in prop.get("possible_values") or []}
        if possible:
            hits = [possible[n.lower()] for n in names if n.lower() in possible]
            if not hits:
                continue
            limit = int(prop.get("max_values_allowed") or len(hits)) if prop.get("is_multivalued") else 1
            hits = hits[: max(1, limit)]
            row = {"property_id": prop["property_id"], "property_name": prop.get("property_name") or name,
                   "value_ids": [int(h["value_id"]) for h in hits if h.get("value_id") is not None],
                   "values": [str(h.get("name")) for h in hits],
                   "scale_id": scale_id if scale_id is not None else next((h.get("scale_id") for h in hits if h.get("scale_id")), None)}
        elif scale_id is not None:
            row = {"property_id": prop["property_id"], "property_name": prop.get("property_name") or name,
                   "value_ids": [], "values": names[:1], "scale_id": scale_id}
        else:
            continue
        out = [a for a in out if a.get("property_id") != prop["property_id"]] + [row]
    return out


# --- validation ---------------------------------------------------------------------------------


def problems(values: dict[str, Any], *, options: dict[str, Any] | None = None, name: str | None = None,
             partial: bool = False, category_properties: list[dict[str, Any]] | None = None) -> dict[str, str]:
    """What Etsy would refuse, by field, each in one plain sentence; empty when the values can be saved.

    ``options``: the shop's own lists (``{"shipping": [{"id", "label", "type"?}], "returns": [...], "readiness":
    [...], "partners": [...]}``): an id must be one Etsy listed for the shop. ``partial``: only the fields given
    are checked (an edit of a profile that follows a reference listing). ``category_properties``: the category's
    attributes (listing_attributes.compact), when they are known."""
    out: dict[str, str] = {}
    has = lambda f: not partial or f in values  # noqa: E731
    if name is not None:
        if not _text(name):
            out["name"] = "Give the profile a name."
        elif len(_text(name)) > MAX_NAME:
            out["name"] = f"The name is {len(_text(name))} characters; keep it under {MAX_NAME}."
    if has("taxonomy_id"):
        taxonomy = _number(values.get("taxonomy_id"))
        if taxonomy is None or taxonomy < 1:
            out["taxonomy_id"] = "Choose a category: Etsy needs one on every listing."
    if has("who_made") and values.get("who_made") not in WHO_MADE:
        out["who_made"] = "Say who made it: Etsy needs this on every listing."
    if has("when_made") and values.get("when_made") not in WHEN_MADE:
        out["when_made"] = "Say when it was made: Etsy needs this on every listing."
    if has("is_supply") and not isinstance(values.get("is_supply"), bool):
        out["is_supply"] = "Say whether it is a finished product or a supply to make things with."
    table = clean_variations(values.get("variations")) if has("variations") else None
    if has("price"):
        price = _number(values.get("price"))
        priced_rows = bool(table and table["rows"] and all(r.get("price") is not None for r in table["rows"]))
        if price is None and not priced_rows:
            out["price"] = "Give a price: Etsy needs one on every listing."
        elif price is not None and not MIN_PRICE <= price <= MAX_PRICE:
            out["price"] = f"The price must be between {MIN_PRICE:.2f} and {MAX_PRICE:,.0f}: Etsy takes nothing outside that."
    if has("quantity"):
        quantity = _number(values.get("quantity"))
        if quantity is None or quantity != int(quantity) or not 1 <= quantity <= MAX_QUANTITY:
            out["quantity"] = f"The quantity must be a whole number from 1 to {MAX_QUANTITY}: Etsy's limit for a listing."
    if has("variations") and isinstance(values.get("variations"), dict) and values["variations"].get("properties"):
        raw_names = [_text(n) for n in values["variations"].get("properties") or []]
        why = None
        if any(not n for n in raw_names):
            why = "Name each variation (Size, Colour), or remove the empty one."
        elif len(raw_names) > MAX_VARIATION_PROPERTIES:
            why = f"Etsy takes at most {MAX_VARIATION_PROPERTIES} variations on a listing (Size and Colour, say)."
        elif len({n.lower() for n in raw_names}) != len(raw_names):
            why = "The two variations have the same name; name them differently."
        elif any(len(n) > MAX_PROPERTY_NAME for n in raw_names):
            why = f"A variation's name can be {MAX_PROPERTY_NAME} characters at most."
        elif not table or not table["rows"]:
            why = "Add at least one option to the variations, or remove them."
        elif len(table["rows"]) > MAX_VARIATION_ROWS:
            why = f"{len(table['rows'])} combinations: Etsy takes at most {MAX_VARIATION_ROWS} on a listing."
        else:
            seen: set[tuple[str, ...]] = set()
            for row in table["rows"]:
                key = tuple(v.lower() for v in row["values"])
                label = " / ".join(row["values"]) or "an option"
                if len(row["values"]) != len(raw_names) or any(not v for v in row["values"]):
                    why = "Every option needs a value for each variation."
                elif any(len(v) > MAX_OPTION_LENGTH for v in row["values"]):
                    why = f"\"{label}\": an option's name can be {MAX_OPTION_LENGTH} characters at most on Etsy."
                elif key in seen:
                    why = f"\"{label}\" is there twice; each combination once."
                elif row.get("price") is not None and not MIN_PRICE <= row["price"] <= MAX_PRICE:
                    why = f"\"{label}\": the price must be between {MIN_PRICE:.2f} and {MAX_PRICE:,.0f}."
                elif row.get("price") is None and _number(values.get("price")) is None:
                    why = f"\"{label}\" has no price, and the profile has none to fall back on."
                elif row.get("quantity") is not None and not 0 <= row["quantity"] <= MAX_QUANTITY:
                    why = f"\"{label}\": the quantity must be from 0 to {MAX_QUANTITY}."
                seen.add(key)
                if why:
                    break
            if not why and not any(r.get("enabled", True) for r in table["rows"]):
                why = "Every option is switched off; Etsy needs at least one that can be bought."
        if why:
            out["variations"] = why
    lists = options or {}
    for field, key, word, needed in (("shipping_profile_id", "shipping", "shipping profile", True),
                                     ("return_policy_id", "returns", "return policy", True),
                                     ("readiness_state_id", "readiness", "processing profile", True)):
        if not has(field):
            continue
        value = values.get(field)
        known = {int(o["id"]) for o in lists.get(key) or [] if o.get("id") is not None}
        if value in (None, ""):
            if needed:
                out[field] = f"Choose a {word}: Etsy needs one on a physical listing."
        elif options is not None and int(value) not in known:
            out[field] = f"That {word} is not one of this shop's on Etsy; choose one from the list."
    if has("production_partner_ids"):
        known = {int(o["id"]) for o in lists.get("partners") or [] if o.get("id") is not None}
        chosen = [int(i) for i in values.get("production_partner_ids") or []]
        if options is not None and any(i not in known for i in chosen):
            out["production_partner_ids"] = "That production partner is not one of this shop's on Etsy; choose from the list."
    if has("item_weight") or has("shipping_profile_id"):
        weight, unit = _number(values.get("item_weight")), values.get("item_weight_unit")
        sizes = [_number(values.get(k)) for k in ("item_length", "item_width", "item_height")]
        calculated = any(int(o["id"]) == int(values.get("shipping_profile_id") or 0) and o.get("type") == "calculated"
                         for o in lists.get("shipping") or [] if o.get("id") is not None)
        if weight is not None and (weight <= 0 or unit not in WEIGHT_UNITS):
            out["item_weight"] = "The weight needs a number above 0 and a unit (oz, lb, g or kg)."
        elif any(s is not None for s in sizes) and (any(s is None or s <= 0 for s in sizes)
                                                    or values.get("item_dimensions_unit") not in SIZE_UNITS):
            out["item_length"] = "The packed size needs length, width and height above 0 and a unit (in or cm)."
        elif calculated and (weight is None or any(s is None for s in sizes)):
            out["item_weight"] = ("This shipping profile is calculated by Etsy from the parcel: give the item's weight and "
                                  "packed size, or Etsy refuses the listing.")
    if has("materials"):
        raw = [_text(m) for m in values.get("materials") or [] if _text(m)]
        if len(raw) > MAX_MATERIALS:
            out["materials"] = f"{len(raw)} materials: Etsy takes {MAX_MATERIALS} at most."
        elif clean_materials(raw) != raw:
            bad = next((m for m in raw if m not in clean_materials([m])), None) or raw[0]
            out["materials"] = (f"\"{bad}\": a material is letters, numbers and spaces, {MAX_MATERIAL_LENGTH} characters "
                                "at most, each once.")
    if has("attributes") and category_properties is not None:
        from app.pipeline import listing_attributes as A

        props = A.properties_of({"category_properties": category_properties})
        for attr, raw in (values.get("attributes") or {}).items():
            chosen, scale = A.parse(raw)
            if not chosen:
                continue
            try:
                A.check(A.find(props, attr), chosen, scale, attr)
            except ValueError as exc:
                out["attributes"] = str(exc)
                break
    if has("description") and not _text(values.get("description")):
        out["description"] = "Write a description: Etsy needs one on every listing."
    return out


def text_problems(*, title_prefix: str | None = None, default_tags: list[str] | None = None,
                  personalization: dict[str, Any] | None = None) -> dict[str, str]:
    """The profile's own text settings, against Etsy's rules."""
    out: dict[str, str] = {}
    if title_prefix is not None and len(_text(title_prefix)) > MAX_PREFIX:
        out["title_prefix"] = f"The title prefix is {len(_text(title_prefix))} characters; keep it under {MAX_PREFIX}."
    if default_tags is not None:
        tags = [_text(t) for t in default_tags if _text(t)]
        if len(tags) > MAX_TAGS:
            out["default_tags"] = f"{len(tags)} tags: Etsy takes {MAX_TAGS} on a listing."
        elif len({t.lower() for t in tags}) != len(tags):
            out["default_tags"] = "A tag is there twice; each tag once."
        else:
            for tag in tags:
                if len(tag) > MAX_TAG_LENGTH:
                    out["default_tags"] = f"\"{tag}\" is {len(tag)} characters; a tag is {MAX_TAG_LENGTH} at most on Etsy."
                elif not _TAG.match(tag):
                    out["default_tags"] = f"\"{tag}\": a tag is letters, numbers, spaces, - and ' only on Etsy."
                if "default_tags" in out:
                    break
    if personalization and personalization.get("enabled"):
        found = P.etsy_problems(personalization)
        if found:
            out["personalization"] = found[0] if isinstance(found, list) else str(found)
    return out


def clean_tags(tags: list[str] | None) -> list[str]:
    seen: list[str] = []
    for tag in tags or []:
        t = _text(tag)
        if t and t.lower() not in {s.lower() for s in seen}:
            seen.append(t)
    return seen


# --- a profile's own values ---------------------------------------------------------------------


def own_fields(profile: Any) -> list[str]:
    """The fields that are the seller's own on this profile: all of them on a manual one."""
    if getattr(profile, "manual", False):
        return list(PAYLOAD_FIELDS)
    return sorted(k for k in (profile.own_values or {}) if k in PAYLOAD_FIELDS)


def rebuild(profile: Any, *, currency: str | None = None) -> None:
    """A manual profile's payload from its own values (kept: what was read about its category); a profile that
    follows a listing: its payload with the seller's values over it again."""
    from datetime import datetime, timezone

    if getattr(profile, "manual", False):
        keep = profile.cached_payload or {}
        profile.cached_payload = to_payload(profile.own_values or {}, currency=currency or keep.get("currency"), keep=keep)
        profile.updated_at = datetime.now(timezone.utc)
    elif profile.cached_payload:
        profile.cached_payload = apply(profile.cached_payload, profile.own_values)


def set_own(profile: Any, values: dict[str, Any]) -> list[str]:
    """Save the seller's values on a profile that follows a listing: only what differs from the listing's own
    becomes "your value". Returns the fields that are now the seller's."""
    current = dict(profile.own_values or {})
    listing = from_payload(base_of(profile.cached_payload))
    for field, value in values.items():
        if field not in PAYLOAD_FIELDS:
            continue
        if _equal(field, value, listing.get(field)):
            current.pop(field, None)
        else:
            current[field] = value
    profile.own_values = current or None
    return sorted(current)


def _equal(field: str, a: Any, b: Any) -> bool:
    if field == "variations":
        ta, tb = clean_variations(a), clean_variations(b)
        return ta == tb
    if field in ("price", "item_weight", "item_length", "item_width", "item_height"):
        na, nb = _number(a), _number(b)
        return na == nb or (na is not None and nb is not None and abs(na - nb) < 0.005)
    if field in ("materials", "production_partner_ids"):
        return list(a or []) == list(b or [])
    if field == "attributes":
        norm = lambda m: {str(k).lower(): v for k, v in (m or {}).items() if v not in (None, "", [])}  # noqa: E731
        return norm(a) == norm(b)
    if field == "description":
        return _text(a) == _text(b)
    return a == b
