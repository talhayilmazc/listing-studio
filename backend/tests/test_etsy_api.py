"""EtsyApiClient tests. The API is fully mocked with httpx.MockTransport."""

import uuid

import httpx
import pytest
from fakeredis import FakeAsyncRedis

from app.etsy.api import EtsyApiClient, RateLimitExceeded
from app.etsy.errors import EtsyClientError, EtsyRateLimited, EtsyServerError
from app.etsy.rate_limiter import DailyQuota, TokenBucket


def _make(handler, **kwargs) -> tuple[EtsyApiClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = EtsyApiClient(
        client_id="mykey", shared_secret="mysecret", http_client=http, **kwargs
    )
    return client, http


async def test_headers_and_create_draft_never_sets_state() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"listing_id": 123})

    client, http = _make(handler)
    async with http:
        await client.create_draft_listing(
            55, listing={"title": "x", "quantity": 1}, access_token="900.abc"
        )

    req = seen[0]
    # Verified header format: keystring:shared_secret (not keystring alone).
    assert req.headers["x-api-key"] == "mykey:mysecret"
    assert req.headers["authorization"] == "Bearer 900.abc"
    assert req.method == "POST"
    assert req.url.path == "/v3/application/shops/55/listings"
    assert "state" not in req.content.decode()  # never auto-publish


async def test_create_draft_sends_all_tags_as_one_comma_field() -> None:
    """A1: 13 tags must reach Etsy as a single comma-separated field, not repeated
    keys (which Etsy collapses to one tag)."""
    from urllib.parse import parse_qs

    seen: list[httpx.Request] = []
    client, http = _make(
        lambda req: (seen.append(req), httpx.Response(200, json={"listing_id": 1}))[1]
    )
    tags = [f"tag{i}" for i in range(13)]
    async with http:
        await client.create_draft_listing(
            9, listing={"title": "t", "tags": tags}, access_token="tok"
        )

    body = parse_qs(seen[0].content.decode())
    assert body["tags"] == [",".join(tags)]  # exactly one field, comma-joined
    assert len(body["tags"][0].split(",")) == 13


async def test_update_listing_serializes_tags_the_same_way() -> None:
    from urllib.parse import parse_qs

    seen: list[httpx.Request] = []
    client, http = _make(
        lambda req: (seen.append(req), httpx.Response(200, json={"listing_id": 1}))[1]
    )
    tags = [f"t{i}" for i in range(13)]
    async with http:
        await client.update_listing(1, updates={"tags": tags}, access_token="tok")

    body = parse_qs(seen[0].content.decode())
    assert len(body["tags"][0].split(",")) == 13


async def test_update_listing_uses_patch_and_unscoped_path() -> None:
    """Etsy's updateListing is PATCH /application/listings/{id} -- not shop-scoped,
    not PUT (a shop-scoped PUT 404s)."""
    seen: list[httpx.Request] = []
    client, http = _make(
        lambda req: (seen.append(req), httpx.Response(200, json={"listing_id": 4565991052}))[1]
    )
    async with http:
        await client.update_listing(4565991052, updates={"state": "active"}, access_token="tok")

    req = seen[0]
    assert req.method == "PATCH"
    assert req.url.path == "/v3/application/listings/4565991052"
    assert "/shops/" not in req.url.path  # never shop-scoped


@pytest.mark.parametrize(
    ("status", "exc"),
    [(429, EtsyRateLimited), (404, EtsyClientError), (500, EtsyServerError)],
)
async def test_error_status_mapping(status: int, exc: type[Exception]) -> None:
    client, http = _make(lambda req: httpx.Response(status, json={}))
    async with http:
        with pytest.raises(exc):
            await client.get_listing(1, access_token="t")


async def test_client_error_captures_body_and_path_and_logs(caplog) -> None:
    """A 400 must surface Etsy's rejected-field body + the request path (for logs),
    while the exception str() stays generic (no leak to the UI)."""
    detail = "Inventory instances are invalid: offering price is below the minimum"
    client, http = _make(lambda req: httpx.Response(400, json={"error": detail}))
    with caplog.at_level("WARNING"):
        async with http:
            with pytest.raises(EtsyClientError) as exc:
                await client.update_listing_inventory(
                    77, inventory={"products": []}, access_token="t"
                )

    err = exc.value
    assert err.status_code == 400
    assert detail in (err.body or "")
    assert err.path == "/application/listings/77/inventory"
    assert err.method == "PUT"
    assert str(err) == "etsy client error (400)"  # generic -> safe for job.last_error
    assert any(detail in rec.getMessage() for rec in caplog.records)  # logged server-side


async def test_quota_and_bucket_gate_each_call() -> None:
    redis = FakeAsyncRedis()
    quota = DailyQuota(redis, global_daily_limit=100)
    bucket = TokenBucket(redis, time_func=lambda: 0.0)
    seen: list[httpx.Request] = []

    client, http = _make(
        lambda req: (seen.append(req), httpx.Response(200, json={"ok": True}))[1],
        bucket=bucket,
        quota=quota,
    )
    tid = uuid.uuid4()
    async with http:
        await client.get_shop(1, access_token="t", tenant_id=tid, tenant_limit=1)
        used, _ = await quota.usage(tid)
        assert used == 1
        # tenant_limit reached -> next call is refused before any HTTP.
        with pytest.raises(RateLimitExceeded):
            await client.get_shop(1, access_token="t", tenant_id=tid, tenant_limit=1)
    assert len(seen) == 1


async def test_taxonomy_response_is_cached() -> None:
    redis = FakeAsyncRedis()
    calls: list[httpx.Request] = []

    client, http = _make(
        lambda req: (calls.append(req), httpx.Response(200, json={"results": []}))[1],
        cache=redis,
    )
    async with http:
        await client.get_seller_taxonomy_nodes(access_token="t")
        await client.get_seller_taxonomy_nodes(access_token="t")
    assert len(calls) == 1  # second call served from the 24h cache


async def test_get_listing_inventory_and_images_paths() -> None:
    seen: list[httpx.Request] = []
    client, http = _make(
        lambda req: (seen.append(req), httpx.Response(200, json={"results": []}))[1]
    )
    async with http:
        await client.get_listing_inventory(42, access_token="t")
        await client.get_listing_images(42, access_token="t")
    assert seen[0].url.path == "/v3/application/listings/42/inventory"
    assert seen[1].url.path == "/v3/application/listings/42/images"


async def test_upload_image_by_id_copies_without_file() -> None:
    from urllib.parse import parse_qs

    seen: list[httpx.Request] = []
    client, http = _make(
        lambda req: (seen.append(req), httpx.Response(201, json={"listing_image_id": 9}))[1]
    )
    async with http:
        await client.upload_listing_image(
            5, 55, rank=3, listing_image_id=900, access_token="t"
        )
    req = seen[0]
    # Copying uses the listing_image_id form field, not a multipart file upload.
    assert "multipart/form-data" not in req.headers.get("content-type", "")
    body = parse_qs(req.content.decode())
    assert body["listing_image_id"] == ["900"] and body["rank"] == ["3"]


async def test_listing_property_read_and_write_paths() -> None:
    seen: list[httpx.Request] = []
    client, http = _make(
        lambda req: (seen.append(req), httpx.Response(200, json={"results": []}))[1]
    )
    async with http:
        await client.get_listing_properties(63829375, 4565991052, access_token="t")
        await client.update_listing_property(
            63829375, 4565991052, 100, value_ids=[11], values=["Crew Neck"], access_token="t"
        )
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/v3/application/shops/63829375/listings/4565991052/properties"
    assert seen[1].method == "PUT"
    assert (
        seen[1].url.path
        == "/v3/application/shops/63829375/listings/4565991052/properties/100"
    )


async def test_get_shop_sections_path() -> None:
    seen: list[httpx.Request] = []
    client, http = _make(
        lambda req: (seen.append(req), httpx.Response(200, json={"results": []}))[1]
    )
    async with http:
        await client.get_shop_sections(7, access_token="t")
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/v3/application/shops/7/sections"
