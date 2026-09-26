"""Real Etsy Open API v3 client.

Every method makes exactly one HTTP request through :meth:`_request`, which:

1. reserves the per-tenant + global **daily quota** (5.000/day),
2. paces via the global **3 req/s token bucket** (no bursts; see rate_limiter),
3. sends with the correct headers -- ``x-api-key: {keystring}:{shared_secret}``
   (verified: keystring alone -> 403) plus the OAuth ``Authorization: Bearer``,
4. maps non-2xx responses onto the :mod:`app.etsy.errors` hierarchy,
5. on **429** waits (``Retry-After``, else exponential backoff), slows every worker
   down by the same amount, and retries -- each retry again through the quota and
   the bucket, so a 429 never turns into a burst of retries,
6. logs every request with its time, path, job and status (:mod:`app.etsy.calllog`).

This client only ever runs inside the worker (jobs go through the queue per
CLAUDE.md); the service layer never calls Etsy directly. Tokens are never logged.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import date
import json
from typing import Any

import httpx
from redis.asyncio import Redis

from app.etsy.client import ETSY_API_BASE, auth_headers
from app.etsy.calllog import CallLog
from app.etsy.errors import raise_for_etsy_status
from app.etsy.rate_limiter import DailyQuota, TokenBucket
from app.etsy.usage import UsageRecorder

logger = logging.getLogger(__name__)

# Taxonomy rarely changes and is NOT Member Content, so the 24h cache rule applies.
_TAXONOMY_TTL = 24 * 3600


# 429 handling. A 429 means Etsy refused the request, so resending it is safe even
# for a POST. A Retry-After longer than this is not a per-second problem; give up
# rather than hold a worker slot for minutes.
RATE_LIMIT_RETRIES = 4
MAX_RETRY_WAIT_SECONDS = 60.0


def rate_limit_wait(attempt: int, retry_after: float | None) -> float:
    """Seconds to wait before retry ``attempt`` (1-based) after a 429."""
    if retry_after is not None and retry_after >= 0:
        return retry_after
    return min(2.0 ** (attempt - 1), 30.0)  # 1, 2, 4, 8


class RateLimitExceeded(Exception):
    """The tenant/global daily budget is exhausted; the job should be deferred."""


def _encode_form(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Serialize list-valued form fields the way Etsy's form API expects them.

    Etsy takes multi-value fields (``tags``, ``materials``, ...) as a single
    **comma-separated string**. If a Python list is handed straight to httpx it is
    form-encoded as repeated keys (``tags=a&tags=b``) and Etsy keeps only one
    value -- the cause of "only 1 tag on the draft". Join list/tuple values here.
    """
    if not data:
        return data
    return {
        key: ",".join(str(item) for item in value)
        if isinstance(value, (list, tuple))
        else value
        for key, value in data.items()
    }


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class EtsyApiClient:
    def __init__(
        self,
        *,
        client_id: str,
        shared_secret: str,
        http_client: httpx.AsyncClient,
        base_url: str = ETSY_API_BASE,
        bucket: TokenBucket | None = None,
        quota: DailyQuota | None = None,
        usage: UsageRecorder | None = None,
        cache: Redis | None = None,
        call_log: CallLog | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        shop: Any = None,
        upkeep: bool = False,
    ) -> None:
        self._client_id = client_id
        self._shared_secret = shared_secret
        self._http = http_client
        self._base = base_url.rstrip("/")
        self._bucket = bucket
        self._quota = quota
        self._cache = cache
        self._usage = usage
        self._calls = call_log or CallLog(bucket.redis if bucket is not None else None)
        self._sleep = sleep or asyncio.sleep
        # The connection this client works for, so usage can be shown per shop.
        self._shop = shop
        # Upkeep (profile refresh, shop sync) counts app-wide, not in the
        # seller's own daily limit (v7 §D3).
        self._upkeep = upkeep

    async def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        json: Any = None,
        files: Any = None,
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            attempt += 1
            # 1) daily quota  2) token bucket  3) Etsy API  (order per CLAUDE.md).
            # Every attempt, retries included, passes both: a refused request still
            # counts against Etsy's budget, and an unpaced retry makes more 429s.
            if self._quota is not None and tenant_id is not None and self._upkeep:
                if not await self._quota.reserve_upkeep(tenant_id, shop=self._shop):
                    raise RateLimitExceeded("daily Etsy API budget exhausted")
            elif self._quota is not None and tenant_id is not None and tenant_limit is not None:
                if not await self._quota.reserve(tenant_id, tenant_limit, shop=self._shop):
                    raise RateLimitExceeded("daily Etsy API budget exhausted")
            waited = time.monotonic()
            if self._bucket is not None:
                await self._bucket.acquire()
            waited = time.monotonic() - waited

            sent_at, in_second = await self._calls.sent()
            try:
                resp = await self._http.request(
                    method,
                    f"{self._base}{path}",
                    headers=auth_headers(self._client_id, self._shared_secret, access_token),
                    params=params,
                    data=_encode_form(data),
                    json=json,
                    files=files,
                )
            except httpx.TransportError:
                # No answer (timeout, dropped connection): Etsy may still have acted.
                await self._calls.record(
                    sent_at=sent_at, in_second=in_second, method=method, path=path,
                    status=0, waited=waited, attempt=attempt,
                )
                raise
            await self._calls.record(
                sent_at=sent_at,
                in_second=in_second,
                method=method,
                path=path,
                status=resp.status_code,
                waited=waited,
                attempt=attempt,
            )
            if resp.status_code != 429 or attempt > RATE_LIMIT_RETRIES:
                break
            wait = rate_limit_wait(attempt, _retry_after(resp))
            if wait > MAX_RETRY_WAIT_SECONDS:
                break
            if self._bucket is not None:
                # Everyone backs off, not just this request.
                await self._bucket.penalize(wait)
            else:
                await self._sleep(wait)

        if resp.status_code >= 400:
            # Etsy names the rejected field in the body — capture it (no token is in
            # the response body or the path). Logged server-side; kept off the UI.
            body = resp.text[:2000] if resp.content else ""
            logger.warning(
                "Etsy API %s %s -> %d: %s", method, path, resp.status_code, body
            )
            raise_for_etsy_status(
                resp.status_code, _retry_after(resp), body=body, path=path, method=method
            )
        # The seller's usage history is their own requests; upkeep is the app's.
        if self._usage is not None and tenant_id is not None and not self._upkeep:
            self._usage.record(tenant_id, date.today())
        return resp.json() if resp.content else {}

    async def _cache_get(self, key: str) -> dict[str, Any] | None:
        if self._cache is None:
            return None
        raw = await self._cache.get(key)
        return json.loads(raw) if raw else None

    async def _cache_set(self, key: str, value: dict[str, Any]) -> None:
        if self._cache is not None:
            await self._cache.set(key, json.dumps(value), ex=_TAXONOMY_TTL)

    # --- Taxonomy (cached 24h; not Member Content) -------------------------
    async def get_seller_taxonomy_nodes(
        self, *, access_token: str, tenant_id: Any = None, tenant_limit: int | None = None
    ) -> dict[str, Any]:
        key = "etsy:taxonomy:nodes"
        cached = await self._cache_get(key)
        if cached is not None:
            return cached
        result = await self._request(
            "GET",
            "/application/seller-taxonomy/nodes",
            access_token=access_token,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )
        await self._cache_set(key, result)
        return result

    async def get_properties_by_taxonomy_id(
        self,
        taxonomy_id: int,
        *,
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        key = f"etsy:taxonomy:props:{taxonomy_id}"
        cached = await self._cache_get(key)
        if cached is not None:
            return cached
        result = await self._request(
            "GET",
            f"/application/seller-taxonomy/nodes/{taxonomy_id}/properties",
            access_token=access_token,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )
        await self._cache_set(key, result)
        return result

    # --- Shop / listing reads ---------------------------------------------
    async def get_shop(
        self, shop_id: int, *, access_token: str, tenant_id: Any = None, tenant_limit: int | None = None
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/application/shops/{shop_id}",
            access_token=access_token,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def get_shop_by_owner_user_id(
        self,
        user_id: int,
        *,
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/application/users/{user_id}/shops",
            access_token=access_token,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def get_listings_by_shop(
        self,
        shop_id: int,
        *,
        access_token: str,
        state: str = "draft",
        limit: int = 25,
        offset: int = 0,
        includes: list[str] | None = None,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"state": state, "limit": limit}
        if offset:
            params["offset"] = offset
        if includes:
            params["includes"] = ",".join(includes)
        return await self._request(
            "GET",
            f"/application/shops/{shop_id}/listings",
            access_token=access_token,
            params=params,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def get_listing(
        self,
        listing_id: int,
        *,
        includes: list[str] | None = None,
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/application/listings/{listing_id}",
            access_token=access_token,
            params={"includes": ",".join(includes)} if includes else None,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def get_listing_inventory(
        self,
        listing_id: int,
        *,
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        """Read a listing's inventory (variation structure) — seller's own listing."""
        return await self._request(
            "GET",
            f"/application/listings/{listing_id}/inventory",
            access_token=access_token,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def get_listing_images(
        self,
        listing_id: int,
        *,
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/application/listings/{listing_id}/images",
            access_token=access_token,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def get_listing_personalization(
        self,
        listing_id: int,
        *,
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        """getListingPersonalization: the listing's personalization questions (v7 §D4)."""
        return await self._request(
            "GET",
            f"/application/listings/{listing_id}/personalization",
            access_token=access_token,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    # --- Writes ------------------------------------------------------------
    async def update_listing_personalization(
        self,
        shop_id: int,
        listing_id: int,
        *,
        questions: list[dict[str, Any]],
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        """updateListingPersonalization: set the listing's questions (JSON body)."""
        return await self._request(
            "POST",
            f"/application/shops/{shop_id}/listings/{listing_id}/personalization",
            access_token=access_token,
            json={"personalization_questions": questions},
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def create_draft_listing(
        self,
        shop_id: int,
        *,
        listing: dict[str, Any],
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        """Create a DRAFT listing. Never sets ``state`` -- Etsy creates it as draft."""
        return await self._request(
            "POST",
            f"/application/shops/{shop_id}/listings",
            access_token=access_token,
            data=listing,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def upload_listing_image(
        self,
        shop_id: int,
        listing_id: int,
        *,
        rank: int,
        access_token: str,
        image_bytes: bytes | None = None,
        filename: str = "image.jpg",
        mime_type: str = "image/jpeg",
        listing_image_id: int | None = None,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        """Upload image bytes, or copy an existing image by ``listing_image_id``.

        Copying (B3, e.g. a size chart from the reference listing) is done through
        Etsy's own ``listing_image_id`` parameter — the seller's image is never
        downloaded from the site.
        """
        if listing_image_id is not None:
            return await self._request(
                "POST",
                f"/application/shops/{shop_id}/listings/{listing_id}/images",
                access_token=access_token,
                data={"rank": rank, "listing_image_id": listing_image_id},
                tenant_id=tenant_id,
                tenant_limit=tenant_limit,
            )
        if image_bytes is None:
            raise ValueError("upload_listing_image needs image_bytes or listing_image_id")
        return await self._request(
            "POST",
            f"/application/shops/{shop_id}/listings/{listing_id}/images",
            access_token=access_token,
            data={"rank": rank},
            files={"image": (filename, image_bytes, mime_type)},
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def delete_listing_image(
        self,
        shop_id: int,
        listing_id: int,
        listing_image_id: int,
        *,
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "DELETE",
            f"/application/shops/{shop_id}/listings/{listing_id}/images/{listing_image_id}",
            access_token=access_token,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def update_listing_inventory(
        self,
        listing_id: int,
        *,
        inventory: dict[str, Any],
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "PUT",
            f"/application/listings/{listing_id}/inventory",
            access_token=access_token,
            json=inventory,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def get_shop_sections(
        self,
        shop_id: int,
        *,
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/application/shops/{shop_id}/sections",
            access_token=access_token,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def create_shop_section(
        self,
        shop_id: int,
        *,
        title: str,
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/application/shops/{shop_id}/sections",
            access_token=access_token,
            data={"title": title},
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def update_listing(
        self,
        shop_id: int,
        listing_id: int,
        *,
        updates: dict[str, Any],
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        # Etsy's updateListing is PATCH (not PUT) and IS shop-scoped:
        # PATCH /application/shops/{shop_id}/listings/{listing_id}.
        return await self._request(
            "PATCH",
            f"/application/shops/{shop_id}/listings/{listing_id}",
            access_token=access_token,
            data=updates,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    # --- Category attributes (neckline, sleeve length, ...) ----------------
    async def get_listing_properties(
        self,
        shop_id: int,
        listing_id: int,
        *,
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        """Read a listing's category attribute values (the seller's own listing)."""
        return await self._request(
            "GET",
            f"/application/shops/{shop_id}/listings/{listing_id}/properties",
            access_token=access_token,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )

    async def update_listing_property(
        self,
        shop_id: int,
        listing_id: int,
        property_id: int,
        *,
        value_ids: list[int] | None = None,
        values: list[str] | None = None,
        scale_id: int | None = None,
        access_token: str,
        tenant_id: Any = None,
        tenant_limit: int | None = None,
    ) -> dict[str, Any]:
        """Set one category attribute on a listing (e.g. neckline, sleeve length)."""
        data: dict[str, Any] = {}
        if value_ids:
            data["value_ids"] = value_ids
        if values:
            data["values"] = values
        if scale_id is not None:
            data["scale_id"] = scale_id
        return await self._request(
            "PUT",
            f"/application/shops/{shop_id}/listings/{listing_id}/properties/{property_id}",
            access_token=access_token,
            data=data,
            tenant_id=tenant_id,
            tenant_limit=tenant_limit,
        )
