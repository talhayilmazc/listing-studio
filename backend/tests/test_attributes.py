"""Required-clothing-attribute resolution (v4 §B): reference -> vision -> missing."""

from app.pipeline.attributes import resolve_required_attributes

# A taxonomy with two required attributes and one optional.
PROPS = [
    {
        "property_id": 100,
        "property_name": "Neckline",
        "is_required": True,
        "possible_values": [
            {"value_id": 11, "name": "Crew Neck"},
            {"value_id": 12, "name": "V-Neck"},
        ],
    },
    {
        "property_id": 200,
        "property_name": "Sleeve Length",
        "is_required": True,
        "possible_values": [
            {"value_id": 21, "name": "Short Sleeve"},
            {"value_id": 22, "name": "Long Sleeve"},
        ],
    },
    {"property_id": 300, "property_name": "Color", "is_required": False, "possible_values": []},
]


def test_required_attribute_copied_from_reference() -> None:
    reference = [{"property_id": 100, "value_ids": [12], "values": ["V-Neck"]}]
    vision = {"sleeve_length": "short sleeve"}  # fills the other required one
    resolved, missing = resolve_required_attributes(PROPS, reference, vision)
    assert missing == []
    by_id = {a.property_id: a for a in resolved}
    # Neckline copied verbatim from the reference (not from vision).
    assert by_id[100].value_ids == [12] and by_id[100].values == ["V-Neck"]
    # Sleeve length derived from vision, mapped to the controlled value id.
    assert by_id[200].value_ids == [21] and by_id[200].values == ["Short Sleeve"]


def test_required_attribute_derived_from_vision_when_reference_missing() -> None:
    vision = {"neckline": "crew neck", "sleeve_length": "long sleeve"}
    resolved, missing = resolve_required_attributes(PROPS, reference_attributes=None, vision=vision)
    assert missing == []
    by_id = {a.property_id: a for a in resolved}
    assert by_id[100].value_ids == [11]  # "crew neck" -> Crew Neck (id 11)
    assert by_id[200].value_ids == [22]  # "long sleeve" -> Long Sleeve (id 22)
    # Optional Color is never resolved (not required).
    assert 300 not in by_id


def test_unresolvable_required_attributes_are_reported_missing() -> None:
    # No reference values and vision doesn't cover them -> reported, never guessed.
    resolved, missing = resolve_required_attributes(PROPS, reference_attributes=[], vision={})
    assert resolved == []
    assert set(missing) == {"Neckline", "Sleeve Length"}


def test_free_text_property_takes_the_raw_vision_value() -> None:
    props = [
        {"property_id": 400, "property_name": "Clothing Style", "is_required": True, "possible_values": []}
    ]
    resolved, missing = resolve_required_attributes(props, None, {"clothing_style": "graphic tee"})
    assert missing == []
    assert resolved[0].values == ["graphic tee"] and resolved[0].value_ids == []
