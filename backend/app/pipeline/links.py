"""One profile across shops (v8 §C): what a profile needs in each shop, and how
each of those shop-specific settings is found there.

A profile is the account's. Its shared settings (category, price table,
variations, size charts, description, title prefix, personalization, who/when
made, auto-renew) come from its reference listing in its **main shop**
(``listing_profile.connection_id``). Every other shop the profile is used in
has a link (``profile_shop_link``) that holds only that shop's own ids:

* shipping profile    exact title match; else "Create it in <shop>" (a copy of
                      the main shop's, with its destinations and upgrades); a
                      calculated profile cannot be created through the API
* return policy       Etsy's return policies have no name: identical terms
                      (accepts returns, accepts exchanges, deadline); else create
* processing profile  identical terms (ready to ship / made to order, min and
                      max days); else create
* production partners exact partner name; never creatable through the API, so
                      the seller picks once
* section             not linked: drafts pick their section by the same rules
                      in every shop (publisher step 3), matching titles

Nothing is guessed: two candidates with the same name are "choose one", and
nothing is created in a shop without the seller's confirmation.

Pure functions over Etsy's own responses. No I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.pipeline.reference import _money_to_float

SHIPPING, RETURNS, READINESS, PARTNERS = "shipping_profile", "return_policy", "readiness_state", "production_partners"
RESOURCES: tuple[str, ...] = (SHIPPING, RETURNS, READINESS, PARTNERS)
#: The payload key each resource fills in a draft.
PAYLOAD_KEY: dict[str, str] = {
    SHIPPING: "shipping_profile_id",
    RETURNS: "return_policy_id",
    READINESS: "readiness_state_id",
    PARTNERS: "production_partner_ids",
}
LABEL: dict[str, str] = {
    SHIPPING: "Shipping profile",
    RETURNS: "Return policy",
    READINESS: "Processing profile",
    PARTNERS: "Production partners",
}
#: Which can be created through Etsy's API with our scopes (shops_w).
CREATABLE = frozenset({SHIPPING, RETURNS, READINESS})
#: The OAuth scope each create endpoint needs (createShopShippingProfile and its
#: destination/upgrade endpoints, createShopReturnPolicy,
#: createShopReadinessStateDefinition). Creation is offered only where the shop
#: granted it; the app never asks for a new scope to get it (every seller would
#: have to reconnect).
CREATE_SCOPE: dict[str, str] = {SHIPPING: "shops_w", RETURNS: "shops_w", READINESS: "shops_w"}
NO_SCOPE = ("creating it needs Etsy's \"{scope}\" permission, which this shop has not granted the app; "
            "choose one of this shop's")

# Why a resource is not linked yet, as the seller reads it.
NO_SOURCE = "the main shop's reference has none, so this shop needs none"
NOT_FOUND = "no match in this shop"
AMBIGUOUS = "several in this shop match; choose one"
NOT_CREATABLE = "Etsy's API cannot create this; choose one of this shop's"
CALCULATED = "the main shop's is a calculated shipping profile, which Etsy's API cannot create; choose one of this shop's"
CURRENCY = "this shop sells in {target}, the profile's prices are in {source}"


def results(resp: Any) -> list[dict[str, Any]]:
    if isinstance(resp, dict):
        return list(resp.get("results") or [])
    return list(resp or [])


def readiness_days(row: dict[str, Any]) -> tuple[int | None, int | None]:
    low = row.get("min_processing_days", row.get("min_processing_time"))
    high = row.get("max_processing_days", row.get("max_processing_time"))
    return (int(low) if low is not None else None, int(high) if high is not None else None)


def return_terms(row: dict[str, Any]) -> tuple[bool, bool, int | None]:
    deadline = row.get("return_deadline")
    return (bool(row.get("accepts_returns")), bool(row.get("accepts_exchanges")),
            int(deadline) if deadline is not None else None)


def readiness_terms(row: dict[str, Any]) -> tuple[str | None, int | None, int | None]:
    return (row.get("readiness_state"), *readiness_days(row))


def label_of(resource: str, row: dict[str, Any]) -> str:
    """How the seller recognises one of their own settings in a dropdown."""
    if resource == SHIPPING:
        return str(row.get("title") or f"Shipping profile {row.get('shipping_profile_id')}")
    if resource == RETURNS:
        returns, exchanges, deadline = return_terms(row)
        parts = ["returns" if returns else "no returns", "exchanges" if exchanges else "no exchanges"]
        return ", ".join(parts) + (f", {deadline} days" if deadline else "")
    if resource == READINESS:
        state, low, high = readiness_terms(row)
        kind = "Ready to ship" if state == "ready_to_ship" else "Made to order"
        return str(row.get("processing_days_display_label") or f"{kind}, {low}-{high} days")
    if resource == PARTNERS:
        return str(row.get("partner_name") or f"Partner {row.get('production_partner_id')}")
    return str(row)


def id_of(resource: str, row: dict[str, Any]) -> int | None:
    key = {SHIPPING: "shipping_profile_id", RETURNS: "return_policy_id", READINESS: "readiness_state_id",
           PARTNERS: "production_partner_id"}[resource]
    value = row.get(key)
    return int(value) if value is not None else None


@dataclass
class ShopSettings:
    """One shop's own lists, as Etsy returned them."""

    shipping: list[dict[str, Any]] = field(default_factory=list)
    returns: list[dict[str, Any]] = field(default_factory=list)
    readiness: list[dict[str, Any]] = field(default_factory=list)
    partners: list[dict[str, Any]] = field(default_factory=list)
    currency: str | None = None

    def rows(self, resource: str) -> list[dict[str, Any]]:
        return {SHIPPING: self.shipping, RETURNS: self.returns, READINESS: self.readiness,
                PARTNERS: self.partners}[resource]

    def find(self, resource: str, wanted: int | None) -> dict[str, Any] | None:
        return next((r for r in self.rows(resource) if wanted is not None and id_of(resource, r) == wanted), None)

    def options(self, resource: str) -> list[dict[str, Any]]:
        return [{"id": id_of(resource, r), "label": label_of(resource, r)}
                for r in self.rows(resource) if id_of(resource, r) is not None and not r.get("is_deleted")]


@dataclass
class Outcome:
    """What linking one resource in one shop came to."""

    resource: str
    linked: int | list[int] | None = None  # the id(s) in the target shop
    needed: bool = True  # False: the main shop's reference has none
    reason: str | None = None  # why it is not linked
    creatable: bool = False
    create: dict[str, Any] | None = None  # what would be created, when creatable
    requests: int = 0  # Etsy requests creating it costs
    options: list[dict[str, Any]] = field(default_factory=list)  # to choose from

    @property
    def done(self) -> bool:
        return not self.needed or self.linked not in (None, [])


def _match(rows: list[dict[str, Any]], resource: str, same: Any) -> tuple[int | None, str | None]:
    hits = [r for r in rows if not r.get("is_deleted") and same(r)]
    if len(hits) == 1:
        return id_of(resource, hits[0]), None
    return None, AMBIGUOUS if hits else NOT_FOUND


def shipping_copy(source: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """createShopShippingProfile's payload for a copy, plus the destinations and
    upgrades to add after it. The first destination goes in the create call."""
    destinations = [d for d in source.get("shipping_profile_destinations") or []]

    def dest(d: dict[str, Any]) -> dict[str, Any]:
        return {
            "primary_cost": _money_to_float(d.get("primary_cost")),
            "secondary_cost": _money_to_float(d.get("secondary_cost")),
            "destination_country_iso": d.get("destination_country_iso") or None,
            "destination_region": None if d.get("destination_country_iso") else (d.get("destination_region") or "none"),
            "shipping_carrier_id": d.get("shipping_carrier_id") or None,
            "mail_class": d.get("mail_class") or None,
            "min_delivery_days": d.get("min_delivery_days"),
            "max_delivery_days": d.get("max_delivery_days"),
        }

    first = dest(destinations[0]) if destinations else {"primary_cost": 0.0, "secondary_cost": 0.0, "destination_region": "none"}
    profile = {
        "title": source.get("title"),
        "origin_country_iso": source.get("origin_country_iso"),
        "origin_postal_code": source.get("origin_postal_code"),
        **first,
    }
    upgrades = [
        {
            "type": u.get("type", 0),
            "upgrade_name": u.get("upgrade_name"),
            "price": _money_to_float(u.get("price")),
            "secondary_price": _money_to_float(u.get("secondary_price")),
            "shipping_carrier_id": u.get("shipping_carrier_id") or None,
            "mail_class": u.get("mail_class") or None,
            "min_delivery_days": u.get("min_delivery_days"),
            "max_delivery_days": u.get("max_delivery_days"),
        }
        for u in source.get("shipping_profile_upgrades") or []
    ]
    return profile, [dest(d) for d in destinations[1:]], upgrades


def plan_resource(resource: str, wanted: Any, source: ShopSettings, target: ShopSettings) -> Outcome:
    """Link one shop-specific setting of the main shop's reference in ``target``."""
    if wanted in (None, "", []):
        return Outcome(resource, needed=False, reason=NO_SOURCE)
    options = target.options(resource)

    if resource == PARTNERS:
        ids: list[int] = []
        for partner_id in wanted:
            row = source.find(PARTNERS, int(partner_id))
            if row is None:
                return Outcome(resource, reason=NOT_FOUND, options=options)
            name = row.get("partner_name")
            found, why = _match(target.partners, PARTNERS, lambda r, name=name: name and r.get("partner_name") == name)
            if found is None:
                return Outcome(resource, reason=why if why == AMBIGUOUS else NOT_CREATABLE, options=options)
            ids.append(found)
        return Outcome(resource, linked=sorted(ids))

    row = source.find(resource, int(wanted))
    if row is None:  # the main shop's own list no longer has it
        return Outcome(resource, reason="the main shop no longer has it; refresh the profile", options=options)

    if resource == SHIPPING:
        found, why = _match(target.shipping, SHIPPING, lambda r: r.get("title") == row.get("title"))
    elif resource == RETURNS:
        found, why = _match(target.returns, RETURNS, lambda r: return_terms(r) == return_terms(row))
    else:
        found, why = _match(target.readiness, READINESS, lambda r: readiness_terms(r) == readiness_terms(row))
    if found is not None:
        return Outcome(resource, linked=found)
    if why == AMBIGUOUS:
        return Outcome(resource, reason=AMBIGUOUS, options=options)

    if resource == SHIPPING:
        if row.get("profile_type") == "calculated":
            return Outcome(resource, reason=CALCULATED, options=options)
        profile, destinations, upgrades = shipping_copy(row)
        return Outcome(resource, reason=NOT_FOUND, creatable=True, options=options,
                       create={"profile": profile, "destinations": destinations, "upgrades": upgrades},
                       requests=1 + len(destinations) + len(upgrades))
    if resource == RETURNS:
        returns, exchanges, deadline = return_terms(row)
        return Outcome(resource, reason=NOT_FOUND, creatable=True, options=options, requests=1,
                       create={"accepts_returns": returns, "accepts_exchanges": exchanges, "return_deadline": deadline})
    state, low, high = readiness_terms(row)
    return Outcome(resource, reason=NOT_FOUND, creatable=True, options=options, requests=1,
                   create={"readiness_state": state, "min_processing_time": low, "max_processing_time": high,
                           "processing_time_unit": "days"})


def plan_shop(payload: dict[str, Any], source: ShopSettings, target: ShopSettings,
              granted: set[str] | None = None) -> dict[str, Outcome]:
    """Every shop-specific setting of a profile, linked in ``target`` where it can be.

    ``granted``: the scopes the target shop granted (None = not known: assume the
    app's own). A create whose scope is not granted is not offered; the seller
    picks one of the shop's own instead.
    """
    out = {r: plan_resource(r, payload.get(PAYLOAD_KEY[r]), source, target) for r in RESOURCES}
    if granted is not None:
        for outcome in out.values():
            scope = CREATE_SCOPE.get(outcome.resource)
            if outcome.creatable and scope not in granted:
                outcome.creatable, outcome.create, outcome.requests = False, None, 0
                outcome.reason = NO_SCOPE.format(scope=scope)
    return out


def currency_problem(source_currency: str | None, target_currency: str | None) -> str | None:
    if source_currency and target_currency and source_currency != target_currency:
        return CURRENCY.format(source=source_currency, target=target_currency)
    return None


def link_values(link: Any) -> dict[str, Any]:
    """The ids a link holds, by payload key."""
    return {
        "shipping_profile_id": link.shipping_profile_id,
        "return_policy_id": link.return_policy_id,
        "readiness_state_id": link.readiness_state_id,
        "production_partner_ids": list(link.production_partner_ids or []),
    }


def missing(payload: dict[str, Any] | None, link: Any) -> dict[str, str]:
    """What a non-main shop's link still lacks, by resource, with the reason."""
    if not payload:
        return {}
    values = link_values(link)
    out: dict[str, str] = {}
    for resource in RESOURCES:
        wanted = payload.get(PAYLOAD_KEY[resource])
        if wanted in (None, "", []):
            continue
        if values[PAYLOAD_KEY[resource]] in (None, []):
            out[resource] = (link.notes or {}).get(resource) or NOT_FOUND
    return out


def shop_reference(payload: dict[str, Any], link: Any | None) -> dict[str, Any]:
    """The reference a draft in this shop is built from: the profile's shared
    settings with this shop's own ids. ``link`` None = the main shop (its ids are
    the reference's own)."""
    if link is None:
        return dict(payload)
    out = dict(payload)
    for key, value in link_values(link).items():
        if payload.get(key) in (None, "", []):
            continue  # not needed here either
        out[key] = value
    return out


# --- Same-named profiles of different shops: one profile, or "Link these" -----------------------

#: The reference fields that are the profile's shared settings (the rest are the
#: shop's own ids, image ids and links, or bookkeeping).
SHARED_PAYLOAD_KEYS: tuple[str, ...] = (
    "taxonomy_id", "price", "currency", "who_made", "when_made", "is_supply", "is_customizable",
    "should_auto_renew", "description", "price_on_property", "quantity_on_property", "sku_on_property",
    "personalization",
)


def _variations(products: list[dict[str, Any]]) -> list[Any]:
    """A variation table without the ids that belong to one listing."""
    out = []
    for product in products or []:
        values = sorted(
            (str(v.get("property_name") or v.get("property_id")), tuple(str(x) for x in v.get("values") or []))
            for v in product.get("property_values") or []
        )
        prices = sorted(
            (round(_money_to_float(o.get("price")), 2), bool(o.get("is_enabled", True)))
            for o in product.get("offerings") or []
        )
        out.append((values, prices))
    return sorted(out, key=repr)


def _attributes(rows: list[dict[str, Any]]) -> list[Any]:
    return sorted(
        (str(r.get("property_id")), tuple(sorted(str(v) for v in r.get("value_ids") or [])),
         tuple(sorted(str(v) for v in r.get("values") or [])))
        for r in rows or []
    )


def shared_signature(profile: Any, costs: dict[str, Any] | None) -> Any | None:
    """Everything a profile shares across shops, comparable; None when it cannot
    be known (the reference payload has lapsed). Size charts are compared by how
    many there are: their images are each shop's own."""
    payload = profile.cached_payload
    if not payload:
        return None
    return (
        profile.content_template, (profile.title_prefix or "").strip(), repr(profile.personalization),
        profile.listing_style, profile.title_min_length, profile.title_max_length,
        tuple(repr(payload.get(k)) for k in SHARED_PAYLOAD_KEYS),
        repr(_variations(payload.get("inventory_products") or [])),
        repr(_attributes(payload.get("attributes") or [])),
        len(profile.fixed_image_ids or []),
        repr(costs or None),
    )
