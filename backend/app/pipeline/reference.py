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
) -> dict[str, Any]:
    """Extract the reusable fields from a reference listing into ``cached_payload``."""
    price = listing.get("price")
    image_rows = images.get("results", images) if isinstance(images, dict) else images
    return {
        "taxonomy_id": listing.get("taxonomy_id"),
        "price": _money_to_float(price) if price is not None else None,
        "currency": (price or {}).get("currency_code") if isinstance(price, dict) else None,
        "shipping_profile_id": listing.get("shipping_profile_id"),
        "production_partner_ids": list(listing.get("production_partner_ids") or []),
        "who_made": listing.get("who_made"),
        "when_made": listing.get("when_made"),
        "is_supply": listing.get("is_supply"),
        "processing_min": listing.get("processing_min"),
        "processing_max": listing.get("processing_max"),
        "description": decode_etsy_text(listing.get("description")),
        # Reference variation structure, reused (with the new SKU) at publish time.
        "inventory_products": list((inventory or {}).get("products") or []),
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


def build_inventory_from_reference(
    reference_products: list[dict[str, Any]],
    *,
    sku: str | None,
    quantity: int,
) -> dict[str, Any]:
    """Rebuild an ``updateListingInventory`` body from the reference variations.

    Copies the reference variation structure (property values) and prices, but sets
    our SKU on every product (A2) and our quantity. Falls back to a single product
    when the reference has no variations.
    """
    if not reference_products:
        return {
            "products": [
                {
                    "sku": sku or "",
                    "offerings": [{"price": 0.0, "quantity": quantity, "is_enabled": True}],
                    "property_values": [],
                }
            ]
        }

    products: list[dict[str, Any]] = []
    for product in reference_products:
        offerings = [
            {
                "price": _money_to_float(off.get("price")),
                "quantity": quantity,
                "is_enabled": True,
            }
            for off in product.get("offerings", [])
        ] or [{"price": 0.0, "quantity": quantity, "is_enabled": True}]

        property_values = []
        for value in product.get("property_values", []):
            property_values.append(
                {
                    k: value[k]
                    for k in ("property_id", "property_name", "scale_id", "value_ids", "values")
                    if value.get(k) is not None
                }
            )
        products.append(
            {"sku": sku or "", "offerings": offerings, "property_values": property_values}
        )
    return {"products": products}
