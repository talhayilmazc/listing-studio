"""Taxonomy-based content-template inference (apparel vs default)."""

from app.pipeline.taxonomy import clothing_taxonomy_ids, infer_content_template

NODES = {
    "results": [
        {
            "id": 1,
            "name": "Clothing",
            "children": [
                {"id": 10, "name": "Unisex Adult Clothing", "children": [{"id": 100, "name": "T-Shirts"}]},
                {"id": 11, "name": "Hoodies"},
            ],
        },
        {"id": 2, "name": "Home & Living", "children": [{"id": 200, "name": "Mugs"}]},
    ]
}


def test_clothing_ids_collects_everything_under_clothing() -> None:
    ids = clothing_taxonomy_ids(NODES)
    assert ids == {1, 10, 100, 11}
    assert 200 not in ids and 2 not in ids


def test_infer_apparel_for_clothing_taxonomy() -> None:
    ids = clothing_taxonomy_ids(NODES)
    assert infer_content_template(100, ids) == "apparel"  # under Clothing
    assert infer_content_template(11, ids) == "apparel"


def test_infer_falls_back_to_default_off_clothing() -> None:
    ids = clothing_taxonomy_ids(NODES)
    assert infer_content_template(200, ids, default="apparel") == "apparel"
    assert infer_content_template(200, ids, default="digital_products") == "digital_products"
    assert infer_content_template(None, ids, default="digital_products") == "digital_products"
