"""Worker jobs for reference-listing profiles and the dashboard listing cache.

Both run only via the queue (CLAUDE.md) and read only the authenticated seller's
own shop. ``refresh_profile`` copies a reference listing into a profile's
``cached_payload``; ``sync_shop_listings`` caches the seller's own active + draft
listings for the dashboard (6h Member Content window). Tokens are never logged.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.db.models import EtsyConnection, ListingProfile, ShopListingCache, Tenant
from app.etsy.api import EtsyApiClient, RateLimitExceeded
from app.etsy.connection import ConnectionService
from app.pipeline.clustering import ListingForCluster, cluster_listings, heuristic_name
from app.pipeline.imageclass import AnthropicImageKindClassifier, classify_reference_images
from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.reference import (
    build_profile_payload,
    common_title_prefix,
    decode_etsy_text,
    prefix_from_shop,
)
from app.pipeline.taxonomy import clothing_taxonomy_ids, infer_content_template
from app.etsy.calllog import current_job
from app.workers import gate

logger = logging.getLogger(__name__)


def _llm_client(settings) -> AnthropicLLMClient | None:  # noqa: ANN001
    """Return an LLM client if a key is configured, else None (skip LLM steps)."""
    if not settings.llm_api_key:
        return None
    return AnthropicLLMClient(api_key=settings.llm_api_key, model=settings.llm_model)


def _connection_service(settings) -> ConnectionService:  # noqa: ANN001
    return ConnectionService(
        get_cipher(),
        client_id=settings.etsy_client_id,
        token_url=settings.etsy_oauth_token_url,
    )


def _build_client(ctx: dict[str, Any], http: httpx.AsyncClient, settings) -> EtsyApiClient:  # noqa: ANN001
    return EtsyApiClient(
        client_id=settings.etsy_client_id,
        shared_secret=settings.etsy_client_secret,
        http_client=http,
        bucket=ctx["bucket"],
        quota=ctx["quota"],
        usage=ctx.get("usage"),
        cache=ctx.get("redis"),
    )


async def _resolve_shop_id(
    session, client: EtsyApiClient, connection: EtsyConnection, ctx_kwargs: dict[str, Any]
) -> int:  # noqa: ANN001
    if connection.shop_id is not None:
        return connection.shop_id
    if connection.etsy_user_id is None:
        raise ValueError("connection has no Etsy user id")
    resp = await client.get_shop_by_owner_user_id(connection.etsy_user_id, **ctx_kwargs)
    shop = resp["results"][0] if resp.get("results") else resp
    connection.shop_id = int(shop["shop_id"])
    connection.shop_name = shop.get("shop_name") or connection.shop_name
    await session.commit()
    return connection.shop_id


async def _run_gated(
    ctx: dict[str, Any],
    function: str,
    arg: str,
    tenant_id: uuid.UUID,
    body: Callable[[], Awaitable[str]],
) -> str:
    """Run a job that has no ``job`` row through the gate (see workers/gate.py).

    These jobs only read the seller's own shop and are safe to repeat, so one
    that meets the daily limit partway is simply run again after the reset.
    """
    async with ctx["sessionmaker"]() as session:
        tenant = await session.get(Tenant, tenant_id)
        verdict = await gate.check(ctx, tenant, function)
    if verdict.action == "suspended":
        return "suspended"
    if verdict.action == "run":
        current_job.set(f"{function}:{arg}")  # tags this job's Etsy requests
        try:
            return await body()
        except RateLimitExceeded:
            assert tenant is not None
            verdict = await gate.paused_by_wall(ctx, tenant)
    assert verdict.resumes_at is not None
    await gate.requeue(
        ctx,
        function,
        arg,
        resumes_at=verdict.resumes_at,
        job_key=gate.pause_key(function, arg, verdict.resumes_at),
    )
    return "deferred"


async def refresh_profile(ctx: dict[str, Any], profile_id: str) -> str:
    async with ctx["sessionmaker"]() as session:
        profile = await session.get(ListingProfile, uuid.UUID(profile_id))
        if profile is None:
            return "missing"
        tenant_id = profile.tenant_id
    return await _run_gated(
        ctx, "refresh_profile", profile_id, tenant_id, lambda: _refresh_profile(ctx, profile_id)
    )


async def _refresh_profile(ctx: dict[str, Any], profile_id: str) -> str:
    settings = get_settings()
    sessionmaker = ctx["sessionmaker"]
    service = _connection_service(settings)

    async with sessionmaker() as session:
        profile = await session.get(ListingProfile, uuid.UUID(profile_id))
        if profile is None:
            return "missing"
        connection = await service.get_active(session, profile.tenant_id)
        if connection is None:
            return "no-connection"

        tenant = await session.get(Tenant, profile.tenant_id)
        token = await service.get_valid_access_token(session, connection)
        async with httpx.AsyncClient(timeout=30.0) as http:
            client = _build_client(ctx, http, settings)
            kw = {
                "access_token": token,
                "tenant_id": profile.tenant_id,
                "tenant_limit": tenant.daily_quota if tenant else None,
            }
            shop_id = await _resolve_shop_id(session, client, connection, kw)
            ref_id = profile.reference_listing_id
            listing = await client.get_listing(ref_id, **kw)
            inventory = await client.get_listing_inventory(ref_id, **kw)
            images = await client.get_listing_images(ref_id, **kw)
            # Category attributes (neckline, sleeve length, ...) for v4 §B.
            properties = await client.get_listing_properties(shop_id, ref_id, **kw)

        payload = build_profile_payload(listing, inventory, images, properties)

        # Title prefix (docs/duzeltmeler-v5.md §B). Detection sets it from its cluster.
        # A profile made by hand starts with none (NULL), so fill it here, once, from
        # the seller's own listings of the same kind. Never overwrite: a prefix the
        # seller typed, or cleared on purpose (""), stays exactly as it is.
        if profile.title_prefix is None:
            fresh_since = datetime.now(timezone.utc) - timedelta(
                seconds=ShopListingCache.STALE_SECONDS
            )
            cached = await session.execute(
                select(ShopListingCache.payload).where(
                    ShopListingCache.tenant_id == profile.tenant_id,
                    ShopListingCache.fetched_at >= fresh_since,
                )
            )
            shop_rows = [row for row in cached.scalars() if row]
            derived = prefix_from_shop(listing, shop_rows)
            if derived:
                profile.title_prefix = derived
            logger.info(
                "profile %s: title prefix was unset; derived %r from %d cached listings",
                profile.id, derived, len(shop_rows),
            )
        else:
            logger.info("profile %s: title prefix kept as %r", profile.id, profile.title_prefix)

        # Classify the reference's non-primary images as size charts vs artwork and
        # auto-mark the charts as fixed images (B3). Done LOCALLY from the image
        # pixels (no third party); only ambiguous images fall back to the vision
        # model. Prior classifications are reused so this isn't re-run; fixed images
        # are only auto-set if the seller hasn't toggled them.
        prior = {
            img.get("listing_image_id"): img.get("kind")
            for img in (profile.cached_payload or {}).get("images", [])
            if img.get("kind")
        }
        llm = _llm_client(settings)
        vision = AnthropicImageKindClassifier(llm).classify if llm is not None else None
        # Fetch the seller's own image bytes to OUR server (no Etsy auth headers sent
        # to the CDN). A separate client avoids any header leakage to the image host.
        async with httpx.AsyncClient(timeout=20.0) as img_http:

            async def _fetch(url: str) -> tuple[bytes, str] | None:
                try:
                    resp = await img_http.get(url)
                    resp.raise_for_status()
                    ctype = resp.headers.get("content-type", "image/jpeg").split(";")[0]
                    return resp.content, ctype or "image/jpeg"
                except Exception:  # noqa: BLE001 - a fetch failure just skips this image
                    logger.warning("reference image fetch failed", exc_info=True)
                    return None

            charts = await classify_reference_images(
                payload["images"], fetch_bytes=_fetch, vision=vision, prior_kinds=prior
            )
        payload["images_classified"] = True
        if profile.fixed_image_ids is None and charts:
            profile.fixed_image_ids = charts

        profile.cached_payload = payload
        profile.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return "refreshed"


async def sync_shop_listings(ctx: dict[str, Any], tenant_id: str) -> str:
    return await _run_gated(
        ctx,
        "sync_shop_listings",
        tenant_id,
        uuid.UUID(tenant_id),
        lambda: _sync_shop_listings(ctx, tenant_id),
    )


async def _sync_shop_listings(ctx: dict[str, Any], tenant_id: str) -> str:
    settings = get_settings()
    sessionmaker = ctx["sessionmaker"]
    service = _connection_service(settings)
    tid = uuid.UUID(tenant_id)

    async with sessionmaker() as session:
        connection = await service.get_active(session, tid)
        if connection is None:
            return "no-connection"
        tenant = await session.get(Tenant, tid)
        token = await service.get_valid_access_token(session, connection)

        async with httpx.AsyncClient(timeout=30.0) as http:
            client = _build_client(ctx, http, settings)
            kw = {
                "access_token": token,
                "tenant_id": tid,
                "tenant_limit": tenant.daily_quota if tenant else None,
            }
            shop_id = await _resolve_shop_id(session, client, connection, kw)
            rows: list[dict[str, Any]] = []
            for state in ("active", "draft"):
                resp = await client.get_listings_by_shop(
                    shop_id, state=state, limit=100, includes=["Images"], **kw
                )
                rows.extend(resp.get("results", []))

        now = datetime.now(timezone.utc)
        for row in rows:
            listing_id = int(row["listing_id"])
            existing = await session.get(ShopListingCache, (tid, listing_id))
            if existing is None:
                session.add(
                    ShopListingCache(
                        tenant_id=tid, listing_id=listing_id, payload=row, fetched_at=now
                    )
                )
            else:
                existing.payload = row
                existing.fetched_at = now
        await session.commit()
        return f"synced:{len(rows)}"


async def detect_profiles(ctx: dict[str, Any], tenant_id: str) -> str:
    return await _run_gated(
        ctx,
        "detect_profiles",
        tenant_id,
        uuid.UUID(tenant_id),
        lambda: _detect_profiles(ctx, tenant_id),
    )


async def _detect_profiles(ctx: dict[str, Any], tenant_id: str) -> str:
    """Auto-detect candidate profiles by clustering the seller's own active listings.

    Clusters by taxonomy + production partner + variation structure + price band,
    names each cluster, and creates an **unconfirmed** profile per cluster (never
    used until the seller confirms it). Each new profile is then refreshed to build
    its cached payload and classify its images. Own-shop data only.
    """
    settings = get_settings()
    sessionmaker = ctx["sessionmaker"]
    service = _connection_service(settings)
    tid = uuid.UUID(tenant_id)

    async with sessionmaker() as session:
        connection = await service.get_active(session, tid)
        if connection is None:
            return "no-connection"
        tenant = await session.get(Tenant, tid)
        token = await service.get_valid_access_token(session, connection)

        async with httpx.AsyncClient(timeout=30.0) as http:
            client = _build_client(ctx, http, settings)
            kw = {
                "access_token": token,
                "tenant_id": tid,
                "tenant_limit": tenant.daily_quota if tenant else None,
            }
            shop_id = await _resolve_shop_id(session, client, connection, kw)
            resp = await client.get_listings_by_shop(
                shop_id, state="active", limit=100, includes=["Images"], **kw
            )
            listings_raw = resp.get("results", [])
            # Taxonomy tree (cached) -> which taxonomy ids are under Clothing (apparel).
            nodes = await client.get_seller_taxonomy_nodes(**kw)
            clothing_ids = clothing_taxonomy_ids(nodes)

            forcluster: list[ListingForCluster] = []
            for row in listings_raw:
                lid = int(row["listing_id"])
                inv = await client.get_listing_inventory(lid, **kw)
                props = {
                    pv.get("property_name")
                    for product in (inv.get("products") or [])
                    for pv in product.get("property_values", [])
                    if pv.get("property_name")
                }
                forcluster.append(
                    ListingForCluster(
                        listing_id=lid,
                        title=str(row.get("title") or ""),
                        taxonomy_id=row.get("taxonomy_id"),
                        price=_price_float(row.get("price")),
                        production_partner_ids=tuple(row.get("production_partner_ids") or []),
                        variation_properties=tuple(sorted(props)),
                        image_count=len(row.get("images") or []),
                    )
                )

        # Existing reference ids so we don't duplicate profiles on re-run.
        existing = await session.execute(
            select(ListingProfile.reference_listing_id).where(ListingProfile.tenant_id == tid)
        )
        known = set(existing.scalars())

        created: list[uuid.UUID] = []
        for cluster in cluster_listings(forcluster):
            ref = cluster.reference
            if ref.listing_id in known:
                continue
            # Named LOCALLY from the titles (no Etsy content sent to any provider);
            # the seller renames it on confirm anyway. Titles are HTML-decoded.
            titles = [decode_etsy_text(m.title) for m in cluster.listings]
            name = heuristic_name(titles)
            # Title prefix = the brand-like lead shared across the cluster's titles
            # (conservative; "" when it doesn't repeat, for the seller to fill).
            title_prefix = common_title_prefix(titles)
            # Template from the taxonomy (Clothing -> apparel), not variation shape.
            template = infer_content_template(
                ref.taxonomy_id, clothing_ids, default=settings.default_content_template
            )
            profile = ListingProfile(
                tenant_id=tid,
                name=name,
                reference_listing_id=ref.listing_id,
                content_template=template,
                title_prefix=title_prefix,
                source="detected",
                confirmed=False,  # never used until the seller confirms it
            )
            session.add(profile)
            await session.flush()
            created.append(profile.id)
        await session.commit()

    # Build each new profile's cached payload + image classification via the queue.
    for pid in created:
        await _enqueue_job(ctx, "refresh_profile", str(pid))
    return f"detected:{len(created)}"


async def _enqueue_job(ctx: dict[str, Any], function: str, *args: Any) -> None:
    """Enqueue a follow-up job. Tests inject ``ctx['enqueue']``; arq provides
    ``ctx['redis']`` (the pool) with ``enqueue_job``."""
    enqueue = ctx.get("enqueue")
    if enqueue is not None:
        await enqueue(function, *args)
        return
    redis = ctx.get("redis")
    if redis is not None and hasattr(redis, "enqueue_job"):
        await redis.enqueue_job(function, *args)


def _price_float(price: Any) -> float | None:
    if isinstance(price, dict) and price.get("divisor"):
        return round(float(price.get("amount", 0)) / float(price["divisor"]), 2)
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None
