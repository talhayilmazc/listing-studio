"""Reference-listing profile builders (pure functions, no I/O)."""

from app.pipeline.reference import (
    build_inventory_from_reference,
    build_profile_payload,
    common_title_prefix,
    decode_etsy_text,
    replace_title_block,
)


def test_common_title_prefix_detects_repeating_brand_any_case() -> None:
    # Title-case brand caught (not only ALL-CAPS), normalised to the reference casing.
    titles = ["Comfort Colors Retro Frog Tee", "Comfort Colors Flag Tee", "Comfort Colors Mom Shirt"]
    assert common_title_prefix(titles) == "Comfort Colors"
    caps = ["COMFORT COLORS Retro Tee", "COMFORT COLORS Flag Tee"]
    assert common_title_prefix(caps) == "COMFORT COLORS"


def test_common_title_prefix_uses_majority_not_all() -> None:
    titles = ["Comfort Colors Retro Tee", "Comfort Colors Flag Tee", "Standard Frog Shirt"]
    assert common_title_prefix(titles) == "Comfort Colors"  # 2 of 3 share it


def test_common_title_prefix_conservative_when_not_repeated() -> None:
    assert common_title_prefix(["Funny Frog Tee", "Retro Cat Shirt"]) == ""  # no shared lead
    assert common_title_prefix(["Comfort Colors Retro Tee"]) == ""  # single listing -> none
    assert common_title_prefix([]) == ""


def test_decode_etsy_text_unescapes_html_entities() -> None:
    assert decode_etsy_text("I&#39;m a Tee &amp; More") == "I'm a Tee & More"
    assert decode_etsy_text(None) == ""


def test_build_profile_payload_decodes_description() -> None:
    payload = build_profile_payload(
        {"description": "It&#39;s great\n\nSize: S-3XL"}, {"products": []}, {"results": []}
    )
    assert payload["description"].startswith("It's great")

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


def test_build_profile_payload_stores_all_v4_c_settings() -> None:
    listing = {
        **REF_LISTING,
        "return_policy_id": 88,
        "readiness_state_id": 42,
        "should_auto_renew": True,
        "is_customizable": True,
        "is_personalizable": False,
    }
    payload = build_profile_payload(listing, REF_INVENTORY, REF_IMAGES)
    assert payload["return_policy_id"] == 88
    assert payload["readiness_state_id"] == 42  # processing profile (v4 §A)
    assert payload["should_auto_renew"] is True
    assert payload["is_customizable"] is True
    assert payload["is_personalizable"] is False  # False stored, not dropped


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
    # No variations: a single product priced from the reference listing price (§0).
    inv = build_inventory_from_reference([], sku="BR5475", quantity=999, fallback_price=12.5)
    assert len(inv["products"]) == 1
    assert inv["products"][0]["sku"] == "BR5475"
    assert inv["products"][0]["offerings"][0]["price"] == 12.5


def test_inventory_converts_prices_and_drops_priceless_variations() -> None:
    # A read-back inventory: money objects on read; two rows have no price (disabled).
    ref = [
        {
            "offerings": [{"price": {"amount": 2599, "divisor": 100}, "is_enabled": True}],
            "property_values": [
                {"property_id": 200, "property_name": "Size", "value_ids": [1], "values": ["S"]}
            ],
        },
        {
            "offerings": [{"price": None}],  # priceless -> dropped (§C)
            "property_values": [
                {"property_id": 200, "property_name": "Size", "value_ids": [2], "values": ["M"]}
            ],
        },
        {
            "offerings": [{"price": {"amount": 0, "divisor": 100}}],  # zero -> dropped
            "property_values": [
                {"property_id": 200, "property_name": "Size", "value_ids": [3], "values": ["L"]}
            ],
        },
    ]
    inv = build_inventory_from_reference(ref, sku="BR5475", quantity=999)
    # Only the one priced (S) variation survives.
    assert len(inv["products"]) == 1
    assert inv["products"][0]["property_values"][0]["values"] == ["S"]
    # Price is a writable float, not the read-back {amount, divisor}.
    price = inv["products"][0]["offerings"][0]["price"]
    assert price == 25.99 and isinstance(price, float)
    # No offering is ever sent with a zero/None price (the 400 cause).
    for product in inv["products"]:
        for offering in product["offerings"]:
            assert offering["price"] and offering["price"] > 0


def test_build_profile_payload_stores_on_property_declarations() -> None:
    inventory = {
        "products": [],
        "price_on_property": [513],
        "quantity_on_property": [],
        "sku_on_property": [513],
    }
    payload = build_profile_payload({"description": "x"}, inventory, {"results": []})
    assert payload["price_on_property"] == [513]
    assert payload["quantity_on_property"] == []
    assert payload["sku_on_property"] == [513]


def test_inventory_carries_on_property_declarations() -> None:
    # The *_on_property lists must survive the read-back -> writable conversion.
    inv = build_inventory_from_reference(
        REF_INVENTORY["products"],
        sku="X",
        quantity=1,
        price_on_property=[513],
        quantity_on_property=[],
        sku_on_property=[513],
    )
    assert inv["price_on_property"] == [513]
    assert inv["quantity_on_property"] == []
    assert inv["sku_on_property"] == [513]


def test_size_varying_reference_declares_price_on_property() -> None:
    # A Sweatshirt priced by size: S-XL $40.50, 2XL $43.50, 3XL $46.50.
    products = [
        {
            "offerings": [{"price": {"amount": 4050, "divisor": 100}}],
            "property_values": [{"property_id": 513, "property_name": "Size", "values": ["S"]}],
        },
        {
            "offerings": [{"price": {"amount": 4350, "divisor": 100}}],
            "property_values": [{"property_id": 513, "property_name": "Size", "values": ["2XL"]}],
        },
        {
            "offerings": [{"price": {"amount": 4650, "divisor": 100}}],
            "property_values": [{"property_id": 513, "property_name": "Size", "values": ["3XL"]}],
        },
    ]
    inv = build_inventory_from_reference(products, sku="X", quantity=1, price_on_property=[513])
    # Non-empty price_on_property -> Etsy accepts prices that vary by size.
    assert inv["price_on_property"] == [513]
    assert [p["offerings"][0]["price"] for p in inv["products"]] == [40.5, 43.5, 46.5]


def test_inventory_offerings_carry_readiness_state_id() -> None:
    # Every offering needs a readiness_state_id: keep its own, else the listing-level.
    products = [
        {
            "offerings": [{"price": {"amount": 4050, "divisor": 100}, "readiness_state_id": 7}],
            "property_values": [],
        },
        {
            "offerings": [{"price": {"amount": 4350, "divisor": 100}}],  # no per-offering value
            "property_values": [],
        },
    ]
    inv = build_inventory_from_reference(products, sku="X", quantity=1, readiness_state_id=42)
    offers = [p["offerings"][0] for p in inv["products"]]
    assert offers[0]["readiness_state_id"] == 7  # kept its own
    assert offers[1]["readiness_state_id"] == 42  # fell back to listing-level


def test_inventory_preserves_offering_is_enabled() -> None:
    ref = [
        {
            "offerings": [{"price": {"amount": 2000, "divisor": 100}, "is_enabled": False}],
            "property_values": [],
        }
    ]
    inv = build_inventory_from_reference(ref, sku="X", quantity=5)
    assert inv["products"][0]["offerings"][0]["is_enabled"] is False


def test_reference_images_carry_both_full_and_display_urls() -> None:
    """Classification keeps the full-size url; the UI gets the 570px variant."""
    payload = build_profile_payload(
        {"listing_id": 5, "title": "T"},
        {},
        {"results": [
            {
                "listing_image_id": 900,
                "rank": 1,
                "url_fullxfull": "https://img/full-1.jpg",
                "url_570xN": "https://img/570-1.jpg",
            },
            # Only a full-size url available: display falls back to it.
            {"listing_image_id": 901, "rank": 2, "url_fullxfull": "https://img/full-2.jpg"},
        ]},
    )
    first, second = payload["images"]
    assert first["url"] == "https://img/full-1.jpg"
    assert first["display_url"] == "https://img/570-1.jpg"
    assert second["url"] == second["display_url"] == "https://img/full-2.jpg"
