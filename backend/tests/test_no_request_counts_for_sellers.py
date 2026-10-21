"""Etsy request counts are for admins only (A3).

The review page showed "3 drafts to create ≈ 69 requests", "6 drafts in all ≈ 138 Etsy requests
(about 23 each)" and "Etsy requests today: 20,000 of your 20,000 left". Request counts and
budgets are a technical limit, not something a seller acts on: no seller response carries
one (fields are excluded from the schema and the JSON, as for AI cost), and no message
a seller reads names a number of requests. Admins keep them.

One exception stays on purpose: the Overview page's "Etsy requests today" card
(``QuotaOut.ceiling``), the remaining-quota indicator CLAUDE.md lists as a required
compliance element; the pause notice says which limit holds work, without numbers.
"""

from __future__ import annotations

import re
from typing import Any

from app.api.sales_reread import RereadAdminReport, RereadAdminShop, RereadReport, RereadShop
from app.core import limits
from app.main import create_app

COUNT_FIELD = re.compile(r"request|calls|budget|ceiling|capacity", re.IGNORECASE)
#: The one seller-facing figure kept: the Overview card's remaining quota (Etsy's terms).
ALLOWED = {"QuotaOut.ceiling"}
NUMBER_OF_REQUESTS = re.compile(r"\d[\d,]*\s+(Etsy\s+)?requests?\b", re.IGNORECASE)


def test_no_seller_response_carries_a_request_count() -> None:
    spec = create_app().openapi()
    schemas = spec["components"]["schemas"]
    found: set[str] = set()

    def walk(schema: Any, seen: set[str], where: str) -> None:
        if not isinstance(schema, dict):
            return
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            if name not in seen:
                seen.add(name)
                walk(schemas[name], seen, name)
            return
        for name, inner in (schema.get("properties") or {}).items():
            if COUNT_FIELD.search(name):
                found.add(f"{where}.{name}")
            walk(inner, seen, where)
        for key in ("items", "additionalProperties"):
            walk(schema.get(key), seen, where)
        for key in ("anyOf", "oneOf", "allOf"):
            for inner in schema.get(key) or []:
                walk(inner, seen, where)

    checked = 0
    for path, operations in spec["paths"].items():
        if path.startswith("/api/admin/"):
            continue
        for operation in operations.values():
            for response in (operation.get("responses") or {}).values():
                for media in (response.get("content") or {}).values():
                    checked += 1
                    walk(media.get("schema"), set(), path)
    assert checked > 60
    assert found <= ALLOWED, sorted(found - ALLOWED)


def test_admins_keep_the_counts_sellers_never_receive() -> None:
    shop = dict(connection_id="00000000-0000-0000-0000-000000000001", status="reading", requests_used=12, requests_left=40)
    common = dict(active=True, reads_back_to="2025-10-01", resets_at="2026-10-23T00:00:00Z", requests_left=40)
    seller = RereadReport(**common, shops=[RereadShop(**shop)]).model_dump(mode="json")
    admin = RereadAdminReport(**common, nightly_cap=100, used_tonight=5, shops=[RereadAdminShop(**shop)]).model_dump(mode="json")
    assert "requests_left" not in seller and "requests_used" not in seller["shops"][0]
    assert admin["requests_left"] == 40 and admin["shops"][0]["requests_used"] == 12


def test_the_messages_a_seller_reads_name_no_number_of_requests() -> None:
    ceiling = limits.EtsyCeiling(limit=20_000, used=19_000, follows_default=True, default=20_000, upkeep=0,
                                  resets_at=limits.utc_reset(), time_zone="America/Chicago")
    for message in (limits.app_budget_message(None, ceiling), limits.ceiling_message(None, 20_000)):
        assert not NUMBER_OF_REQUESTS.search(message), message
        assert "20,000" not in message and "19,000" not in message
    assert "It is not your own limit" in limits.app_budget_message(None, ceiling)


async def test_the_publish_preview_says_which_limit_without_numbers(world) -> None:  # noqa: F811
    await world["redis"].set(f"quota:tenant:{world['alice']}:{TODAY}", 19_995)
    preview = (await world["a"].post(f"/api/batches/{world['batch']}/publish/preview", json={})).json()
    text = repr(preview)
    for name in ("estimated_calls", "calls_per_draft", "budget_remaining", "ceiling"):
        assert name not in preview, name
    assert not NUMBER_OF_REQUESTS.search(text), text[:400]


from tests.test_multi_shop import TODAY, world  # noqa: E402,F401  (fixture)
