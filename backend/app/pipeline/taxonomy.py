"""Infer a profile's content template from the reference listing's taxonomy.

Variation structure alone isn't enough (a plain tee has the same Size variation as
many products); the reliable signal is the Etsy taxonomy. If the listing's taxonomy
node sits under **Clothing**, it's apparel. Uses the seller-taxonomy tree from
``getSellerTaxonomyNodes`` (cached, not Member Content).
"""

from __future__ import annotations

from typing import Any


def clothing_taxonomy_ids(nodes_response: Any, *, root_name: str = "Clothing") -> set[int]:
    """Return every taxonomy id at or under a node named ``root_name``."""
    if isinstance(nodes_response, dict):
        results = nodes_response.get("results", [])
    else:
        results = nodes_response or []

    ids: set[int] = set()

    def walk(node: dict[str, Any], under: bool) -> None:
        here = under or str(node.get("name", "")).strip().lower() == root_name.lower()
        node_id = node.get("id")
        if here and node_id is not None:
            ids.add(int(node_id))
        for child in node.get("children") or []:
            walk(child, here)

    for node in results:
        walk(node, False)
    return ids


def category_path(nodes_response: Any, taxonomy_id: int | None) -> list[str]:
    """The names from the top of Etsy's category tree down to ``taxonomy_id``."""
    if taxonomy_id is None:
        return []
    results = nodes_response.get("results", []) if isinstance(nodes_response, dict) else nodes_response or []

    def walk(node: dict[str, Any], above: list[str]) -> list[str] | None:
        here = [*above, str(node.get("name", "")).strip()]
        if node.get("id") is not None and int(node["id"]) == int(taxonomy_id):
            return here
        for child in node.get("children") or []:
            found = walk(child, here)
            if found:
                return found
        return None

    for node in results:
        found = walk(node, [])
        if found:
            return [name for name in found if name]
    return []


def infer_content_template(
    taxonomy_id: int | None, clothing_ids: set[int], *, default: str = "apparel"
) -> str:
    """Apparel when the taxonomy is under Clothing; otherwise the default template."""
    if taxonomy_id is not None and int(taxonomy_id) in clothing_ids:
        return "apparel"
    return default
