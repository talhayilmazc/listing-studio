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
        "production_partner_ids": list(listing.get("production_partner_ids") or []),
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
        "images": [
            {
                "listing_image_id": row.get("listing_image_id"),
                "rank": row.get("rank"),
                "url": row.get("url_fullxfull") or row.get("url_570xN"),
            }
            for row in (image_rows or [])
        ],
    }


def leading_title_prefix(title: str) -> str:
    """Derive a title prefix from a reference title's leading ALL-CAPS words.

    e.g. "COMFORT COLORS Retro Frog Tee" -> "COMFORT COLORS"; a normal
    title-cased title like "Motherhood is Kingdom Work" -> "" (no prefix).
    """
    prefix: list[str] = []
    for word in (title or "").split():
        core = word.strip(",")
        if len(core) >= 2 and core.isupper() and any(c.isalpha() for c in core):
            prefix.append(core)
        else:
            break
    return " ".join(prefix)


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
