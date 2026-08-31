"""Reference-listing profile builders (pure functions, no I/O)."""

from app.pipeline.reference import (
    build_inventory_from_reference,
    build_profile_payload,
    replace_title_block,
)

REF_LISTING = {
    "listing_id": 111,
    "taxonomy_id": 2078,
    "price": {"amount": 2599, "divisor": 100, "currency_code": "USD"},
    "shipping_profile_id": 55,
    "production_partner_ids": [7],
    "who_made": "i_did",
    "when_made": "made_to_order",
    "is_supply": False,
    "processing_min": 1,
    "processing_max": 3,
    "type": "physical",
    "description": "Old Title Here\nSize: S-3XL\nShips in 3 days.\nReturns accepted.",
}
REF_INVENTORY = {
    "products": [
        {
            "sku": "OLD-S",
            "offerings": [{"price": {"amount": 2599, "divisor": 100}, "quantity": 10}],
            "property_values": [
                {"property_id": 200, "property_name": "Size", "value_ids": [1], "values": ["S"]}
            ],
        },
        {
            "sku": "OLD-M",
            "offerings": [{"price": {"amount": 2599, "divisor": 100}, "quantity": 10}],
            "property_values": [
                {"property_id": 200, "property_name": "Size", "value_ids": [2], "values": ["M"]}
            ],
        },
    ]
}
REF_IMAGES = {
    "results": [
        {"listing_image_id": 900, "rank": 1, "url_fullxfull": "https://img/1.jpg"},
        {"listing_image_id": 901, "rank": 2, "url_fullxfull": "https://img/2.jpg"},
    ]
}


def test_build_profile_payload_copies_reference_fields() -> None:
    payload = build_profile_payload(REF_LISTING, REF_INVENTORY, REF_IMAGES)
    assert payload["taxonomy_id"] == 2078
    assert payload["price"] == 25.99
    assert payload["currency"] == "USD"
    assert payload["shipping_profile_id"] == 55
    assert payload["production_partner_ids"] == [7]
    assert payload["who_made"] == "i_did"
    assert payload["is_supply"] is False
    assert payload["processing_min"] == 1 and payload["processing_max"] == 3
    assert payload["description"].startswith("Old Title Here")
    assert len(payload["inventory_products"]) == 2
    assert [img["listing_image_id"] for img in payload["images"]] == [900, 901]


def test_replace_title_block_replaces_up_to_first_blank_line() -> None:
    # Two-line title block, then a blank line, then the body.
    desc = "Old Title Line One\nOld Subtitle Line Two\n\nSize: S-3XL\nShips in 3 days."
    out = replace_title_block(desc, "Brand New Generated Title")
    # The whole title block is replaced; body (from the blank line) is verbatim.
    assert out == "Brand New Generated Title\n\nSize: S-3XL\nShips in 3 days."
    assert "Old Title Line One" not in out and "Old Subtitle Line Two" not in out


def test_replace_title_block_no_blank_line_replaces_first_line_only() -> None:
    desc = "Old Title Here\nSize: S-3XL\nShips in 3 days."
    out = replace_title_block(desc, "New Title")
    assert out == "New Title\nSize: S-3XL\nShips in 3 days."


def test_build_inventory_from_reference_applies_sku_and_keeps_variations() -> None:
    inv = build_inventory_from_reference(REF_INVENTORY["products"], sku="BR5475", quantity=999)
    assert len(inv["products"]) == 2  # both size variations preserved
    assert all(p["sku"] == "BR5475" for p in inv["products"])  # our SKU on every product (A2)
    # Reference price carried through; our quantity applied.
    assert inv["products"][0]["offerings"][0]["price"] == 25.99
    assert inv["products"][0]["offerings"][0]["quantity"] == 999
    # Variation structure (property values) preserved.
    assert inv["products"][0]["property_values"][0]["values"] == ["S"]


def test_build_inventory_from_reference_falls_back_to_single_product() -> None:
    inv = build_inventory_from_reference([], sku="BR5475", quantity=999)
    assert len(inv["products"]) == 1
    assert inv["products"][0]["sku"] == "BR5475"
