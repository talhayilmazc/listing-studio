"""What the AI costs us is ours: no seller-facing endpoint may return it.

Token counts, model names and the dollar cost of writing a listing are our cost
of goods. They must not be in any response a seller's browser can receive (so
not in developer tools either). Admins see them at /api/admin/ai-cost only.

Three checks:

1. every response schema of every non-admin route (the OpenAPI document), by
   field name;
2. the actual JSON (and CSV) that seller endpoints return for an account whose
   listings carry a model name and token counts, by field name and by value;
3. the admin view has the figures, and a seller cannot open it.

The seller's own business figures (their Etsy fees, ad spend, product costs,
profit) are theirs and stay. A new field with "cost" in its name must be added
to SELLER_COST_FIELDS on purpose, or it fails here.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import update

from app.db.models import AiCall, Asset, GeneratedContent
from app.main import create_app
from tests.test_admin import world  # noqa: F401  (fixture)

MODEL = "claude-sonnet-5"

# Field names that are never a seller's business.
FORBIDDEN_FIELD = re.compile(r"token|(^|_)model(_|$)|model_used|usd|(^|_)(ai|llm)_|generation_cost|cost_per_listing", re.IGNORECASE)
# The seller's own costs, entered or read from their own shop: allowed, by name.
SELLER_COST_FIELDS = {
    "product_cost", "product_cost_by_profile", "product_cost_by_sku", "shipping_cost", "costs",
    "cost_settings", "fixed_costs", "other_costs", "unit_cost", "product_costs", "shipping_costs",
    "costs_entered",  # whether the seller has typed in their own product costs
}
# Values that would give a model or the provider away.
FORBIDDEN_VALUE = re.compile(r"claude|haiku|sonnet|opus|anthropic|max_tokens|LLM_API_KEY", re.IGNORECASE)


def _bad_field(name: str) -> bool:
    if FORBIDDEN_FIELD.search(name):
        return True
    return "cost" in name.lower() and name not in SELLER_COST_FIELDS


def _walk(value: Any, path: str, problems: list[str]) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if _bad_field(str(key)):
                problems.append(f"{path}: field {key!r}")
            _walk(inner, f"{path}.{key}", problems)
    elif isinstance(value, list):
        for inner in value:
            _walk(inner, path, problems)
    elif isinstance(value, str) and FORBIDDEN_VALUE.search(value):
        problems.append(f"{path}: value {value[:80]!r}")


def test_no_seller_response_schema_has_a_cost_token_or_model_field() -> None:
    spec = create_app().openapi()
    schemas = spec["components"]["schemas"]
    problems: list[str] = []
    checked = 0

    def fields(schema: Any, seen: set[str], where: str) -> None:
        if not isinstance(schema, dict):
            return
        if "$ref" in schema:
            name = schema["$ref"].rsplit("/", 1)[-1]
            if name not in seen:
                seen.add(name)
                fields(schemas[name], seen, f"{where} > {name}")
            return
        for name, inner in (schema.get("properties") or {}).items():
            if _bad_field(name):
                problems.append(f"{where}: {name}")
            fields(inner, seen, where)
        for key in ("items", "additionalProperties"):
            fields(schema.get(key), seen, where)
        for key in ("anyOf", "oneOf", "allOf"):
            for inner in schema.get(key) or []:
                fields(inner, seen, where)

    for path, operations in spec["paths"].items():
        if path.startswith("/api/admin/"):
            continue
        for method, operation in operations.items():
            for response in (operation.get("responses") or {}).values():
                for media in (response.get("content") or {}).values():
                    checked += 1
                    fields(media.get("schema"), set(), f"{method.upper()} {path}")
    assert checked > 60, "the OpenAPI document was not walked"
    assert problems == [], "\n".join(problems)
    # The removed endpoint is gone for everyone.
    assert "/api/batches/{batch_id}/cost" not in spec["paths"]


async def _costly(world) -> None:  # noqa: F811
    """Bob's listing was written by a named model for a counted number of tokens."""
    bob = world["bob"]
    now = datetime.now(timezone.utc)
    async with world["sm"]() as s:
        await s.execute(
            update(GeneratedContent)
            .where(GeneratedContent.id == bob.content_id)
            .values(model_used=MODEL, input_tokens=4321, output_tokens=987)
        )
        s.add(AiCall(at=now, day=now.date(), tenant_id=bob.tenant_id, purpose="content", model=MODEL, ok=True,
                     input_tokens=4321, output_tokens=987, cache_write_tokens=0, cache_write_1h_tokens=0,
                     cache_read_tokens=0, listings=1, cost_usd=Decimal("0.018512")))
        await s.commit()


async def test_what_sellers_actually_receive_carries_no_cost_token_or_model(world) -> None:  # noqa: F811
    await _costly(world)
    bob, b = world["bob"], world["b"]
    # Every GET route a seller can call, found from the app itself, so a new
    # endpoint is checked without anyone adding it here.
    known = {
        "batch_id": str(bob.batch_id), "profile_id": str(bob.profile_id), "content_id": str(bob.content_id),
        "asset_id": str(bob.asset_id), "listing_id": "7001", "shop_id": "00000000-0000-0000-0000-000000000000",
    }
    paths: list[str] = []
    for path, operations in world["app"].openapi()["paths"].items():
        if "get" not in operations or not path.startswith("/api/"):
            continue
        if path.startswith("/api/admin/") or path.startswith("/api/auth/"):
            continue  # admin: allowed to see it; auth: redirects to Etsy
        names = re.findall(r"{(\w+)}", path)
        if all(n in known for n in names):
            paths.append(re.sub(r"{(\w+)}", lambda m: known[m.group(1)], path))
    problems: list[str] = []
    answered = 0
    for path in sorted(set(paths)):
        response = await b.get(path)
        assert response.status_code < 500, f"{path}: {response.status_code}"
        kind = response.headers.get("content-type", "")
        if response.status_code == 200 and kind.startswith("application/json"):
            answered += 1
            _walk(response.json(), path, problems)
        elif response.status_code == 200 and kind.startswith("text/"):
            answered += 1
            if FORBIDDEN_VALUE.search(response.text) or "token" in response.text.lower():
                problems.append(f"{path}: text mentions a model or tokens")
    assert answered >= 15, f"only {answered} seller endpoints answered"

    # Editing and approving return the listing again.
    edited = await b.patch(f"/api/content/{bob.content_id}", json={"description": "A new description."})
    _walk(edited.json(), "PATCH content", problems)
    assert problems == [], "\n".join(problems)

    listing = (await b.get(f"/api/batches/{bob.batch_id}/content")).json()[0]
    assert not {"model_used", "input_tokens", "output_tokens"} & set(listing)

    # The seller's CSV export, whichever view: their figures, none of ours.
    for view in ("overview", "listings"):
        export = await b.get(f"/api/analytics/export?view={view}")
        if export.status_code == 200:
            assert not FORBIDDEN_VALUE.search(export.text) and "token" not in export.text.lower(), view


async def test_the_cost_endpoint_is_gone_and_the_admin_view_is_admin_only(world) -> None:  # noqa: F811
    await _costly(world)
    bob = world["bob"]
    for client in (world["b"], world["a"], world["anon"]):
        assert (await client.get(f"/api/batches/{bob.batch_id}/cost")).status_code in (401, 404)
    for path in ("/api/admin/ai-cost", "/api/admin/ai-cost/series", "/api/admin/ai-cost/series?period=24h"):
        assert (await world["b"].get(path)).status_code == 404
        assert (await world["anon"].get(path)).status_code in (401, 404)

    seen = (await world["a"].get("/api/admin/ai-cost")).json()
    row = next(a for a in seen["sellers"] if a["email"] == "bob@example.com")
    today = row["today"]
    assert (today["calls"], today["listings"], today["input_tokens"], today["output_tokens"]) == (1, 1, 4321, 987)
    # $2 and $10 per million tokens: 4321 x 2 + 987 x 10 = 18,512 millionths of a dollar.
    assert today["cost_usd"] == "0.018512" and today["cost_per_listing_usd"] == "0.018512"
    # Counts only: nothing of the seller's work is in it.
    assert "title" not in str(seen) and str(bob.batch_id) not in str(seen)

    # Deleting the batch deletes the listing; what it cost us stays.
    assert (await world["b"].delete(f"/api/batches/{bob.batch_id}")).status_code in (200, 204)
    after = (await world["a"].get("/api/admin/ai-cost")).json()
    assert next(a for a in after["sellers"] if a["email"] == "bob@example.com")["this_month"]["cost_usd"] == "0.018512"


async def test_a_provider_error_reaches_the_seller_as_a_plain_sentence(world) -> None:  # noqa: F811
    """Stored failure reasons are served to sellers: they never quote the provider."""
    from app.pipeline.generation import SELLER_FAILURE

    bob = world["bob"]
    assert not FORBIDDEN_VALUE.search(SELLER_FAILURE) and "token" not in SELLER_FAILURE.lower()
    async with world["sm"]() as s:
        asset = await s.get(Asset, bob.asset_id)
        asset.error = SELLER_FAILURE
        await s.commit()
    problems: list[str] = []
    _walk((await world["b"].get(f"/api/batches/{bob.batch_id}")).json(), "batch", problems)
    assert problems == []
