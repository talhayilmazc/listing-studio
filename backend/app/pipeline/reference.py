"""Build a listing profile from one of the seller's own existing listings.

Section B: instead of inventing category/price/variations/description, the app
copies them from a reference listing the seller chooses. :func:`build_profile_payload`
turns the Etsy ``getListing`` + ``getListingInventory`` + ``getListingImages``
responses into the compact ``cached_payload`` stored on ``listing_profile``; the
publisher then reuses those fields verbatim so the draft matches the seller's own
proven listing. Only the authenticated seller's own shop is ever read.
"""

from __future__ import annotations

import html
import math
from collections import Counter
from typing import Any


def decode_etsy_text(text: str | None) -> str:
    """Decode HTML entities in Etsy-returned text (titles/descriptions come escaped,
    e.g. ``I&#39;m`` -> ``I'm``). Safe on ``None``."""
    return html.unescape(text or "")


def _money_to_float(money: Any) -> float:
    """Etsy money is ``{amount, divisor, currency_code}``; reduce to a float."""
    if isinstance(money, dict) and "amount" in money and money.get("divisor"):
        return round(float(money["amount"]) / float(money["divisor"]), 2)
    try:
        return float(money)
    except (TypeError, ValueError):
        return 0.0


# Bumped when the payload gains something a publish depends on. v2: production
# partners are read from Etsy's real field (docs/duzeltmeler-v5.md §D); a v1
# payload says "no partners" even when the reference has them.
PAYLOAD_VERSION = 2


def production_partner_ids(listing: dict[str, Any]) -> list[int]:
    """The production partner ids of an Etsy listing.

    Etsy *returns* them as ``production_partners``, a list of objects
    (``{"production_partner_id", "partner_name", "location"}``), and *accepts*
    them on createDraftListing as ``production_partner_ids``. Reading the request
    field name from a response always gave an empty list, so no draft ever got
    the reference's partners.
    """
    ids = [
        int(p["production_partner_id"])
        for p in listing.get("production_partners") or []
        if isinstance(p, dict) and p.get("production_partner_id") is not None
    ]
    if not ids:
        ids = [int(x) for x in listing.get("production_partner_ids") or []]
    return sorted(ids)


def build_profile_payload(
    listing: dict[str, Any],
    inventory: dict[str, Any],
    images: dict[str, Any],
    listing_properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract the reusable fields from a reference listing into ``cached_payload``."""
    price = listing.get("price")
    image_rows = images.get("results", images) if isinstance(images, dict) else images
    return {
        "taxonomy_id": listing.get("taxonomy_id"),
        "price": _money_to_float(price) if price is not None else None,
        "currency": (price or {}).get("currency_code") if isinstance(price, dict) else None,
        "shipping_profile_id": listing.get("shipping_profile_id"),
        "return_policy_id": listing.get("return_policy_id"),
        # Etsy's processing/readiness profile; mandatory for physical listings and
        # the modern replacement for raw processing_min/max (v4 §A).
        "readiness_state_id": listing.get("readiness_state_id"),
        "payload_version": PAYLOAD_VERSION,
        "production_partner_ids": production_partner_ids(listing),
        "who_made": listing.get("who_made"),
        "when_made": listing.get("when_made"),
        "is_supply": listing.get("is_supply"),
        "is_customizable": listing.get("is_customizable"),
        "is_personalizable": listing.get("is_personalizable"),
        "should_auto_renew": listing.get("should_auto_renew"),
        "processing_min": listing.get("processing_min"),
        "processing_max": listing.get("processing_max"),
        "description": decode_etsy_text(listing.get("description")),
        # Reference variation structure, reused (with the new SKU) at publish time.
        "inventory_products": list((inventory or {}).get("products") or []),
        # Which properties price/quantity/sku vary on — Etsy needs these declared at
        # the top level of updateListingInventory or it rejects size-varying prices.
        "price_on_property": list((inventory or {}).get("price_on_property") or []),
        "quantity_on_property": list((inventory or {}).get("quantity_on_property") or []),
        "sku_on_property": list((inventory or {}).get("sku_on_property") or []),
        # Category attributes (neckline, sleeve length, ...) copied from the reference
        # so required clothing properties can be re-applied to new drafts (v4 §B).
        "attributes": list((listing_properties or {}).get("results") or []),
        # Image ids/urls so the UI can offer them as fixed images (B3).
        # `url` stays full-size: it is what size-chart classification downloads.
        # `display_url` is the 570px variant the UI renders, matching how
        # ShopListingOut.thumbnail_url already picks its source.
        "images": image_entries(image_rows),
    }


def image_entries(images: dict[str, Any] | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """A reference listing's images as the payload stores them.

    `url` stays full-size: it is what size-chart classification downloads.
    `display_url` is the 570px variant the UI renders, matching how
    ShopListingOut.thumbnail_url already picks its source.
    """
    rows = images.get("results", []) if isinstance(images, dict) else images
    return [
        {
            "listing_image_id": row.get("listing_image_id"),
            "rank": row.get("rank"),
            "url": row.get("url_fullxfull") or row.get("url_570xN"),
            "display_url": row.get("url_570xN") or row.get("url_fullxfull"),
        }
        for row in (rows or [])
    ]


def common_title_prefix(
    titles: list[str], *, min_listings: int = 2, max_words: int = 4
) -> str:
    """Derive a brand-like title prefix shared by a cluster's listing titles.

    Detects the leading word run (case-insensitive, so "Comfort Colors" is caught as
    well as "COMFORT COLORS") that recurs across a **majority** of the titles, and
    returns it in the casing the reference uses. Conservative: needs at least
    ``min_listings`` titles and the run must repeat, else returns "" for the user to
    fill in — a single listing never yields a prefix.
    """
    cleaned = [[w for w in (t or "").replace(",", " ").split() if w] for t in titles]
    cleaned = [words for words in cleaned if words]
    n = len(cleaned)
    if n < min_listings:
        return ""

    threshold = max(min_listings, math.ceil(n / 2))  # "several" == a majority
    best_casing: list[str] = []
    for length in range(1, max_words + 1):
        sequences: Counter[tuple[str, ...]] = Counter()
        example: dict[tuple[str, ...], list[str]] = {}
        for words in cleaned:
            if len(words) >= length:
                key = tuple(w.lower() for w in words[:length])
                sequences[key] += 1
                example.setdefault(key, words[:length])  # reference casing
        if not sequences:
            break
        key, count = sequences.most_common(1)[0]
        if count < threshold:
            break
        best_casing = example[key]
    return " ".join(best_casing)


def _price_value(price: Any) -> float | None:
    if isinstance(price, dict) and price.get("divisor"):
        return float(price.get("amount", 0)) / float(price["divisor"])
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


def _kind(listing: dict[str, Any], band_width: float) -> tuple:
    """The detection cluster key, as far as a listing row shows it."""
    price = _price_value(listing.get("price"))
    return (
        listing.get("taxonomy_id"),
        tuple(production_partner_ids(listing)),
        int(price // band_width) if price is not None else None,
    )


def prefix_from_shop(
    reference: dict[str, Any], shop_listings: list[dict[str, Any]], *, band_width: float = 10.0
) -> str:
    """A title prefix for a profile the seller created by hand (docs/duzeltmeler-v5.md §B).

    Detection derives the prefix from the cluster it found. A hand-made profile
    has no cluster, so this rebuilds one from the seller's own cached listings of
    the same kind as the reference (category, production partner, price band, as
    detection groups them) and takes the prefix those titles share. The result is
    kept only if the reference title itself starts with it.
    """
    kind = _kind(reference, band_width)
    titles = {int(reference["listing_id"]): decode_etsy_text(reference.get("title"))}
    for row in shop_listings:
        if row.get("listing_id") is not None and _kind(row, band_width) == kind:
            titles.setdefault(int(row["listing_id"]), decode_etsy_text(row.get("title")))
    prefix = common_title_prefix(list(titles.values()))
    own = titles[int(reference["listing_id"])].replace(",", " ")
    if prefix and " ".join(own.split()).lower().startswith(prefix.lower()):
        return prefix
    return ""


def replace_title_block(description: str, title: str) -> str:
    """Replace the reference description's title block with the new title (B2).

    Everything before the first blank line is the title block and is replaced with
    ``title``; everything from the first blank line onward (sizes, shipping,
    returns, care) is carried over verbatim. If there is no blank line, only the
    first line is replaced.
    """
    lines = description.split("\n")
    blank = next((i for i, line in enumerate(lines) if line.strip() == ""), None)
    if blank is None:
        return "\n".join([title, *lines[1:]])
    # Keep the blank line (lines[blank]) and everything after it verbatim.
    return "\n".join([title, *lines[blank:]])


def _offering_price(offering: dict[str, Any]) -> float | None:
    """A read-back offering's price as a writable float, or ``None`` if it has none.

    Etsy returns price as ``{amount, divisor}`` on read but expects a plain float on
    write. A variation whose price is absent/zero is disabled in the seller's shop
    and must NOT be sent (v3 §C: priceless rows are dropped).
    """
    price = offering.get("price")
    if isinstance(price, dict):
        amount = price.get("amount")
        divisor = price.get("divisor") or 100
        if not amount:  # 0 or None -> priceless
            return None
        return round(float(amount) / float(divisor), 2)
    if price in (None, ""):
        return None
    try:
        value = float(price)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _offering(price: float, quantity: int, is_enabled: bool, readiness_state_id: int | None) -> dict[str, Any]:
    """A writable offering. ``readiness_state_id`` is required on every offering for
    physical listings ("All offerings need readiness state"); include it when known."""
    offer: dict[str, Any] = {"price": price, "quantity": quantity, "is_enabled": is_enabled}
    if readiness_state_id is not None:
        offer["readiness_state_id"] = readiness_state_id
    return offer


def build_inventory_from_reference(
    reference_products: list[dict[str, Any]],
    *,
    sku: str | None,
    quantity: int,
    fallback_price: float | None = None,
    readiness_state_id: int | None = None,
    price_on_property: list[int] | None = None,
    quantity_on_property: list[int] | None = None,
    sku_on_property: list[int] | None = None,
) -> dict[str, Any]:
    """Reshape a read-back inventory into a writable ``updateListingInventory`` body.

    A ``getListingInventory`` response is NOT directly writable: prices come back as
    ``{amount, divisor}`` (float on write), offerings carry read-only fields, and
    priceless rows are disabled. This converts the prices, keeps only priced
    offerings, drops any product left with no priced offering (v3 §C), sets our SKU
    on every product (§D), and reshapes ``property_values`` to the writable subset.

    The ``*_on_property`` lists declare which properties price/quantity/sku vary on;
    Etsy requires them at the top level (a size-varying listing whose price differs
    by size is rejected as "price must be consistent across all products" unless
    ``price_on_property`` names the size property). They are carried from the
    reference verbatim.

    Only the SKU is ours; prices, sizes, colours and quantities come from the
    reference (v3 §0 — nothing hardcoded). Falls back to a single bare product priced
    from ``fallback_price`` (the reference listing price) when there are no variations.
    """
    products: list[dict[str, Any]] = []
    for product in reference_products or []:
        offerings = []
        for offering in product.get("offerings", []):
            price = _offering_price(offering)
            if price is None:
                continue  # priceless / disabled variation -> skip (§C)
            # Each offering keeps its own readiness_state_id, else the listing-level one.
            offerings.append(
                _offering(
                    price,
                    quantity,
                    bool(offering.get("is_enabled", True)),
                    offering.get("readiness_state_id") or readiness_state_id,
                )
            )
        if not offerings:
            continue  # a variation with no priced offering is dropped entirely

        property_values = [
            {
                key: value[key]
                for key in ("property_id", "property_name", "scale_id", "value_ids", "values")
                if value.get(key) is not None
            }
            for value in product.get("property_values", [])
        ]
        products.append(
            {"sku": sku or "", "offerings": offerings, "property_values": property_values}
        )

    if not products:
        # No priced variations (or the reference had no inventory): a single product
        # priced from the reference listing price. Never a hardcoded price (§0).
        offerings = (
            [_offering(round(float(fallback_price), 2), quantity, True, readiness_state_id)]
            if fallback_price
            else []
        )
        products = [{"sku": sku or "", "offerings": offerings, "property_values": []}]
    return {
        "products": products,
        "price_on_property": list(price_on_property or []),
        "quantity_on_property": list(quantity_on_property or []),
        "sku_on_property": list(sku_on_property or []),
    }
