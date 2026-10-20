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

import re
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
NO_SCOPE = ("creating it needs Etsy's permission to change shop settings (\"{scope}\"), which this shop has not "
            "given Listyro; reconnect the shop on the Connect page to give it, or choose one of this shop's")

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
    #: How it was linked, as the seller reads it: "same name", "same terms", "your choice", "created".
    how: str | None = None
    #: The linked setting(s) as the seller recognises them in that shop.
    label: str | None = None
    #: The main shop's own setting it stands for ("create a copy of …").
    source_label: str | None = None

    @property
    def done(self) -> bool:
        return not self.needed or self.linked not in (None, [])


def _match(rows: list[dict[str, Any]], resource: str, same: Any) -> tuple[int | None, str | None]:
    hits = [r for r in rows if not r.get("is_deleted") and same(r)]
    if len(hits) == 1:
        return id_of(resource, hits[0]), None
    return None, AMBIGUOUS if hits else NOT_FOUND


#: Etsy's regions (createShopShippingProfile, createShopShippingProfileDestination:
#: ``destination_region`` enum ``eu``, ``non_eu``, ``none``). ``none`` is *not* a region: it
#: means "use destination_country_iso". A create call carrying ``destination_region=none``
#: without a country is refused with "You must provide either a destination_country_iso code
#: or a destination_region." (production, OPA -> PCS: Etsy lists "everywhere else" as country
#: "" + region "none", and that destination went first into the create call).
REGIONS = {"eu": "the EU", "non_eu": "Europe outside the EU"}
EVERYWHERE = "everywhere else"
#: A profile Etsy's create call cannot express: it needs a country or a region, and
#: "everywhere else" is neither (it can only be added afterwards, by naming neither).
EVERYWHERE_ONLY = ("Etsy's API cannot create a shipping profile whose only destination is \"everywhere else\" "
                   "(its create call needs a country, the EU or Europe outside the EU); create it in this shop's "
                   "Shop Manager, then press Refresh and pick it")
NO_DESTINATION = ("the main shop's shipping profile has no destination to copy; create it in this shop's Shop "
                  "Manager, then press Refresh and pick it")


def is_everywhere(d: dict[str, Any]) -> bool:
    """Etsy's "everywhere else" destination: no country and no region (region "none")."""
    return not (d.get("destination_country_iso") or None) and str(d.get("destination_region") or "").lower() not in REGIONS


def where_of(d: dict[str, Any]) -> str:
    """A destination as the seller knows it: "US", "the EU", "everywhere else"."""
    country = d.get("destination_country_iso") or None
    if country:
        return str(country)
    return REGIONS.get(str(d.get("destination_region") or "").lower(), EVERYWHERE)


def delivery(row: dict[str, Any]) -> dict[str, Any] | None:
    """The delivery time of a destination or upgrade in the one form Etsy takes.

    Etsy's schema: ``shipping_carrier_id`` *with* ``mail_class`` (Etsy works the days out), or
    ``min_delivery_days`` *with* ``max_delivery_days``. Its answers can hold both (days it
    worked out from the mail class) or a carrier without a mail class; sending those back
    as they came made Etsy refuse the copy. None: neither form is complete, and nothing
    is invented (an older profile may have none; if Etsy then refuses the copy, its
    reason names the delivery time and the seller is told so, ``create_failure``)."""
    carrier, mail = row.get("shipping_carrier_id"), row.get("mail_class") or None
    low, high = row.get("min_delivery_days"), row.get("max_delivery_days")
    if carrier not in (None, "", 0, "0") and mail:
        return {"shipping_carrier_id": int(carrier), "mail_class": str(mail)}
    if low not in (None, "") and high not in (None, ""):
        return {"min_delivery_days": int(low), "max_delivery_days": int(high)}
    if carrier not in (None, "") and mail:
        return {"shipping_carrier_id": int(carrier), "mail_class": str(mail)}
    return None


def destination_target(d: dict[str, Any]) -> dict[str, Any]:
    """A country *or* a region (``eu``/``non_eu``), never both, never ``none``; for
    "everywhere else", neither: createShopShippingProfileDestination reads a destination
    with neither as "everywhere" (Etsy's spec). Only a destination with a country or a
    region can go in the create call (``create_order``)."""
    country = d.get("destination_country_iso") or None
    if country:
        return {"destination_country_iso": str(country).upper()}
    region = str(d.get("destination_region") or "").lower()
    return {"destination_region": region} if region in REGIONS else {}


def create_order(origin: str | None) -> Any:
    """The order destinations are sent in: the ship-from country first (it goes in the create
    call), then other countries, then the EU / Europe outside the EU, "everywhere else" last."""
    def key(d: dict[str, Any]) -> int:
        country = d.get("destination_country_iso") or None
        if country:
            return 0 if origin and str(country).upper() == str(origin).upper() else 1
        return 3 if is_everywhere(d) else 2
    return key


def copy_problem(source: dict[str, Any]) -> str | None:
    """Why Etsy's create call cannot take a copy of this shipping profile, if it cannot."""
    destinations = source.get("shipping_profile_destinations") or []
    if not destinations:
        return NO_DESTINATION
    if all(is_everywhere(d) for d in destinations):
        return EVERYWHERE_ONLY
    return None


def shipping_copy(source: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """createShopShippingProfile's payload for a copy, plus the destinations and
    upgrades to add after it. The first destination goes in the create call, and it
    always names a country or a region (``create_order``); "everywhere else" is added
    afterwards, naming neither. A profile ``copy_problem`` refuses raises ValueError
    (``plan_resource`` never offers such a copy)."""
    problem = copy_problem(source)
    if problem:
        raise ValueError(problem)
    origin = source.get("origin_country_iso")
    destinations = sorted(source.get("shipping_profile_destinations") or [], key=create_order(origin))

    def dest(d: dict[str, Any]) -> dict[str, Any]:
        return {
            "primary_cost": _money_to_float(d.get("primary_cost")),
            "secondary_cost": _money_to_float(d.get("secondary_cost")),
            **destination_target(d),
            **(delivery(d) or {}),
            "label": where_of(d),
        }

    first = dest(destinations[0])
    first.pop("label")
    assert "destination_country_iso" in first or "destination_region" in first  # copy_problem guarantees it
    profile = {
        "title": source.get("title"),
        "origin_country_iso": origin,
        "origin_postal_code": source.get("origin_postal_code") or None,
        **first,
    }

    def kind(u: dict[str, Any]) -> int:
        try:
            return 1 if int(u.get("type") or 0) == 1 else 0
        except (TypeError, ValueError):
            return 1 if str(u.get("type")).lower() == "international" else 0

    upgrades = [
        {
            "type": kind(u),
            "upgrade_name": u.get("upgrade_name"),
            "price": _money_to_float(u.get("price")),
            "secondary_price": _money_to_float(u.get("secondary_price")),
            **(delivery(u) or {}),
            "label": f"the upgrade \"{u.get('upgrade_name') or 'upgrade'}\"",
        }
        for u in source.get("shipping_profile_upgrades") or []
    ]
    return profile, [dest(d) for d in destinations[1:]], upgrades


def send(row: dict[str, Any]) -> dict[str, Any]:
    """What goes to Etsy from a planned destination or upgrade (its label stays here)."""
    return {k: v for k, v in row.items() if k != "label"}


# --- When Etsy refuses a copy: its reason in plain words, and what fixes it ----------------------

#: Etsy's request fields, as the seller knows them on the main shop's setting.
FIELD_WORDS: dict[str, str] = {
    "origin_postal_code": "the ship-from postal code",
    "origin_country_iso": "the ship-from country",
    "destination_country_iso": "a destination country",
    "destination_region": "a destination region",
    "shipping_carrier_id": "the carrier",
    "mail_class": "the mail class",
    "min_delivery_days": "the delivery time",
    "max_delivery_days": "the delivery time",
    "primary_cost": "a shipping price",
    "secondary_cost": "the price for each additional item",
    "upgrade_name": "an upgrade's name",
    "secondary_price": "an upgrade's price for each additional item",
    "price": "an upgrade's price",
    "title": "the profile's name",
    "return_deadline": "the return deadline",
    "accepts_returns": "whether returns are accepted",
    "accepts_exchanges": "whether exchanges are accepted",
    "readiness_state": "ready to ship or made to order",
    "min_processing_time": "the processing time",
    "max_processing_time": "the processing time",
}
_FIELD = re.compile(r"\b(" + "|".join(sorted(FIELD_WORDS, key=len, reverse=True)) + r")\b")
_PERMISSION = re.compile(r"scope|permission|not allowed|forbidden|unauthori[sz]ed", re.IGNORECASE)
#: Etsy's refusal of a destination naming neither a country nor a region ("You must provide
#: either a destination_country_iso code or a destination_region."): the request's shape, not
#: anything on the seller's profile, so the seller is not asked to change a field.
_NO_TARGET = re.compile(r"destination_country_iso.*destination_region|destination_region.*destination_country_iso",
                        re.IGNORECASE)
#: Where each setting lives in Shop Manager.
WHERE = {SHIPPING: "Settings → Shipping settings", RETURNS: "Settings → Policies",
         READINESS: "Settings → Shipping settings → Processing profiles"}


def field_of(detail: str | None) -> str | None:
    """The request field Etsy's reason names, if it names one."""
    match = _FIELD.search(detail or "")
    return match.group(1) if match else None


def create_failure(
    exc: BaseException,
    resource: str,
    *,
    shop: str | None,
    main: str | None,
    source_label: str | None,
    made: str | None = None,
) -> dict[str, Any]:
    """What the seller reads when Etsy refused (or did not answer) a copy, and the one
    action that fixes it: ``reconnect`` (the shop's permission), ``fix`` (a field of the
    main shop's setting, named), ``finish`` (made, but a part was refused), ``refresh``
    (Etsy may have made it anyway), or ``manual`` (make it in Shop Manager, then Refresh).

    ``made``: the copy was created but this part of it ("the destination US") was refused.
    Etsy's own one-line reason is shown to the shop's owner only, through the log
    redaction (no token or address can reach it)."""
    from app.core.logsafety import redact
    from app.etsy.errors import EtsyRateLimited, EtsyServerError

    here = shop or "this shop"
    what = f"\"{source_label}\"" if source_label else f"the {LABEL[resource].lower()}"
    place = WHERE.get(resource, "Settings")
    status = getattr(exc, "status_code", None)
    detail = getattr(exc, "detail", None)
    said = f": \"{redact(detail)}\"" if detail else (f" (it answered {status}) without saying why" if status else "")
    then_pick = f"create it in {here}'s Shop Manager ({place}), then press Refresh and pick it"
    field = field_of(detail)
    if made:
        return {"action": "finish", "field": field, "status": status,
                "reason": f"{what} was created in {here}, but Etsy refused {made}{said}. Finish it in {here}'s Shop "
                          f"Manager ({place}), then press Refresh."}
    if isinstance(exc, EtsyRateLimited):
        return {"action": "refresh", "field": None, "status": 429,
                "reason": "Etsy said requests were coming too fast. Wait a few minutes, press Refresh, then confirm again."}
    if isinstance(exc, EtsyServerError) or status is None:
        return {"action": "refresh", "field": None, "status": status,
                "reason": f"Etsy did not answer properly{f' (it answered {status})' if status else ''}; it may have "
                          f"made the copy anyway. Press Refresh: if it is in {here} it is picked up, otherwise "
                          f"confirm the copy again."}
    if detail and _NO_TARGET.search(detail):
        return {"action": "manual", "field": None, "status": status,
                "reason": f"Etsy refused the copy of {what} because the copy did not name a destination country or "
                          f"region{said}. {then_pick[0].upper()}{then_pick[1:]}."}
    if status in (401, 403) or (detail and _PERMISSION.search(detail) and not field):
        return {"action": "reconnect", "field": None, "status": status,
                "reason": f"Etsy refused it{said}. {here} has not given Listyro permission to change its shop "
                          f"settings, or its connection has lapsed. Reconnect {here} on the Connect page, then "
                          f"confirm again; or {then_pick}."}
    if field:
        words = FIELD_WORDS[field]
        return {"action": "fix", "field": field, "status": status,
                "reason": f"Etsy refused the copy of {what} because of {words}{said}. Change {words} on {what} in "
                          f"{main or 'the main shop'}'s Shop Manager and press Refresh; or {then_pick}."}
    return {"action": "manual", "field": None, "status": status,
            "reason": f"Etsy refused the copy of {what}{said}. {then_pick[0].upper()}{then_pick[1:]}."}


def plan_resource(resource: str, wanted: Any, source: ShopSettings, target: ShopSettings) -> Outcome:
    """Link one shop-specific setting of the main shop's reference in ``target``."""
    if wanted in (None, "", []):
        return Outcome(resource, needed=False, reason=NO_SOURCE)
    options = target.options(resource)

    if resource == PARTNERS:
        ids: list[int] = []
        names: list[str] = []
        for partner_id in wanted:
            row = source.find(PARTNERS, int(partner_id))
            if row is None:
                return Outcome(resource, reason=NOT_FOUND, options=options)
            name = row.get("partner_name")
            names.append(label_of(PARTNERS, row))
            found, why = _match(target.partners, PARTNERS, lambda r, name=name: name and r.get("partner_name") == name)
            if found is None:
                return Outcome(resource, reason=why if why == AMBIGUOUS else NOT_CREATABLE, options=options,
                               source_label=", ".join(names))
            ids.append(found)
        return Outcome(resource, linked=sorted(ids), how="same name", options=options, source_label=", ".join(names),
                       label=", ".join(label_of(PARTNERS, target.find(PARTNERS, i) or {}) for i in sorted(ids)))

    row = source.find(resource, int(wanted))
    if row is None:  # the main shop's own list no longer has it
        return Outcome(resource, reason="the main shop no longer has it; refresh the profile", options=options)
    source_label = label_of(resource, row)

    if resource == SHIPPING:
        found, why = _match(target.shipping, SHIPPING, lambda r: r.get("title") == row.get("title"))
    elif resource == RETURNS:
        found, why = _match(target.returns, RETURNS, lambda r: return_terms(r) == return_terms(row))
    else:
        found, why = _match(target.readiness, READINESS, lambda r: readiness_terms(r) == readiness_terms(row))
    if found is not None:
        return Outcome(resource, linked=found, how="same name" if resource == SHIPPING else "same terms",
                       label=label_of(resource, target.find(resource, found) or {}), options=options,
                       source_label=source_label)
    if why == AMBIGUOUS:
        return Outcome(resource, reason=AMBIGUOUS, options=options, source_label=source_label)

    if resource == SHIPPING:
        if row.get("profile_type") == "calculated":
            return Outcome(resource, reason=CALCULATED, options=options, source_label=source_label)
        problem = copy_problem(row)
        if problem:  # Etsy's create call cannot take it: the seller picks one of this shop's
            return Outcome(resource, reason=problem, options=options, source_label=source_label)
        profile, destinations, upgrades = shipping_copy(row)
        return Outcome(resource, reason=NOT_FOUND, creatable=True, options=options, source_label=source_label,
                       create={"profile": profile, "destinations": destinations, "upgrades": upgrades},
                       requests=1 + len(destinations) + len(upgrades))
    if resource == RETURNS:
        returns, exchanges, deadline = return_terms(row)
        return Outcome(resource, reason=NOT_FOUND, creatable=True, options=options, requests=1, source_label=source_label,
                       create={"accepts_returns": returns, "accepts_exchanges": exchanges, "return_deadline": deadline})
    state, low, high = readiness_terms(row)
    return Outcome(resource, reason=NOT_FOUND, creatable=True, options=options, requests=1, source_label=source_label,
                   create={"readiness_state": state, "min_processing_time": low, "max_processing_time": high,
                           "processing_time_unit": "days"})


def plan_shop(payload: dict[str, Any], source: ShopSettings, target: ShopSettings,
              granted: set[str] | None = None) -> dict[str, Outcome]:
    """Every shop-specific setting of a profile, linked in ``target`` where it can be.

    ``granted``: the scopes the target shop granted (None = not known: assume the
    app's own). A create whose scope is not granted is not offered; the seller
    picks one of the shop's own instead.
    """
    out = {r: plan_resource(r, wanted(payload, r), source, target) for r in RESOURCES}
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


def offering_readiness(payload: dict[str, Any] | None) -> list[int]:
    """The processing profiles (readiness states) the reference's own offerings carry, in
    order, each once. Etsy keeps one on every offering of a physical listing's inventory;
    they are the main shop's ids, valid nowhere else."""
    seen: list[int] = []
    for product in (payload or {}).get("inventory_products") or []:
        for offering in product.get("offerings") or []:
            rid = offering.get("readiness_state_id")
            if rid not in (None, "") and int(rid) not in seen:
                seen.append(int(rid))
    return seen


def wanted(payload: dict[str, Any] | None, resource: str) -> Any:
    """The main shop's id a resource needs in another shop: the reference's own field;
    for the processing profile, the listing's, else the first one its offerings carry
    (a listing can keep it only on its offerings)."""
    value = (payload or {}).get(PAYLOAD_KEY[resource])
    if resource == READINESS and value in (None, ""):
        found = offering_readiness(payload)
        value = found[0] if found else None
    return value


def setup_message(resource: str, shop_name: str | None) -> str:
    """"Processing profile is not set up in BCT": what the seller is told, with the shop's name."""
    return f"{LABEL[resource]} is not set up in {shop_name or 'this shop'}"


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
        if wanted(payload, resource) in (None, "", []):
            continue
        if values[PAYLOAD_KEY[resource]] in (None, []):
            out[resource] = (link.notes or {}).get(resource) or NOT_FOUND
    return out


#: Marks a reference built for a shop other than the profile's main shop.
LINKED = "linked"


def shop_reference(payload: dict[str, Any], link: Any | None) -> dict[str, Any]:
    """The reference a draft in this shop is built from: the profile's shared
    settings with this shop's own ids. ``link`` None = the main shop (its ids are
    the reference's own).

    For any other shop, every shop-scoped id the main shop's reference carries is
    replaced by this shop's own or dropped, never sent: the shipping profile, return
    policy, processing profile and production partners (from the link); the
    processing profile on **every offering** of the inventory (Etsy keeps one per
    offering: the main shop's made a second shop's inventory update fail with
    "Could not find readiness_state_id ... associated with shop"); the inventory's
    own product and offering ids; and the main shop's image ids (size charts are
    copied into the shop instead, workers/publish.py). ``shop_missing`` lists what
    this shop still lacks: the draft is not started while anything is in it.
    """
    if link is None:
        return dict(payload)
    out = dict(payload)
    values = link_values(link)
    missing_here: list[str] = []
    for resource in RESOURCES:
        key = PAYLOAD_KEY[resource]
        if wanted(payload, resource) in (None, "", []):
            continue  # not needed here either
        out[key] = values[key]
        if values[key] in (None, "", []):
            missing_here.append(resource)
    readiness = values["readiness_state_id"]
    products = []
    for product in payload.get("inventory_products") or []:
        offerings = []
        for offering in product.get("offerings") or []:
            row = {k: v for k, v in offering.items() if k not in ("offering_id", "readiness_state_id")}
            if readiness is not None:
                row["readiness_state_id"] = readiness
            offerings.append(row)
        products.append({**{k: v for k, v in product.items() if k != "product_id"}, "offerings": offerings})
    out["inventory_products"] = products
    out.pop("images", None)
    out["shop_scope"] = LINKED
    out["shop_missing"] = missing_here
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
