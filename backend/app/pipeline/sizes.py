"""Size variations for a listing's inventory.

The size set comes from ``prompts/sizes/<name>.json``. Variations are only added
when the taxonomy actually supports the size property (validated against
``getPropertiesByTaxonomyId``); otherwise the listing gets a single product.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SIZES_DIR = Path(__file__).parent / "prompts" / "sizes"


@dataclass
class SizeConfig:
    property: str
    values: list[str]
    price_offsets: dict[str, float] = field(default_factory=dict)


def load_size_config(name: str = "default", base_dir: Path | None = None) -> SizeConfig:
    data = json.loads(((base_dir or _SIZES_DIR) / f"{name}.json").read_text(encoding="utf-8"))
    return SizeConfig(
        property=data["property"],
        values=list(data.get("values", [])),
        price_offsets={k: float(v) for k, v in data.get("price_offsets", {}).items()},
    )


def taxonomy_supports_property(properties_response: dict[str, Any], property_name: str) -> bool:
    for prop in properties_response.get("results", []):
        if str(prop.get("name", "")).lower() == property_name.lower():
            return True
    return False


def build_inventory(
    *,
    sku: str | None,
    base_price: float,
    quantity: int,
    size_config: SizeConfig | None,
) -> dict[str, Any]:
    """Build an updateListingInventory body, with size variations when available."""
    if size_config is None or not size_config.values:
        return {
            "products": [
                {
                    "sku": sku or "",
                    "offerings": [
                        {"quantity": quantity, "price": round(base_price, 2), "is_enabled": True}
                    ],
                    "property_values": [],
                }
            ]
        }

    products = []
    for value in size_config.values:
        price = base_price + size_config.price_offsets.get(value, 0.0)
        products.append(
            {
                "sku": f"{sku}-{value}" if sku else "",
                "offerings": [
                    {"quantity": quantity, "price": round(price, 2), "is_enabled": True}
                ],
                "property_values": [
                    {"property_name": size_config.property, "values": [value]}
                ],
            }
        )
    return {"products": products}
