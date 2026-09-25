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
from sqlalchemy import delete, select, true

from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.db.models import (
    ConnectionStatus,
    EtsyConnection,
    ListingProfile,
    ShopListingCache,
    Tenant,
    TenantStatus,
)
from app.etsy.api import EtsyApiClient, RateLimitExceeded
from app.etsy.errors import EtsyClientError
from app.etsy.refresh import FULL, IMAGES, refresh_due, used_recently
from app.etsy.connection import ConnectionService
from app.pipeline.clustering import ListingForCluster, cluster_listings, heuristic_name
from app.pipeline.imageclass import AnthropicImageKindClassifier, classify_reference_images
from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.reference import (
    build_profile_payload,
    common_title_prefix,
    decode_etsy_text,
    image_entries,
    prefix_from_shop,
    production_partner_ids,
)
from app.pipeline.taxonomy import clothing_taxonomy_ids, infer_content_template
from app.etsy.calllog import current_job
from app.workers import gate

logger = logging.getLogger(__name__)

# The shop listing sync pages through the shop 100 at a time, up to this many
# pages per state (active, draft): 1,000 each. Every page is one Etsy request,
# which is why workers/gate.py JOB_COST budgets 1 + 2 x SYNC_MAX_PAGES for it.
SYNC_PAGE_SIZE = 100
SYNC_MAX_PAGES = 10
# Detection reads inventory with each page; a listing that comes back without it
# is read separately, at most this many times per run (workers/gate.py JOB_COST).
DETECT_MAX_INVENTORY_READS = 100


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


def _build_client(
    ctx: dict[str, Any], http: httpx.AsyncClient, settings, shop: uuid.UUID | None = None  # noqa: ANN001
) -> EtsyApiClient:
    return EtsyApiClient(
        client_id=settings.etsy_client_id,
        shared_secret=settings.etsy_client_secret,
        http_client=http,
        bucket=ctx["bucket"],
        quota=ctx["quota"],
        usage=ctx.get("usage"),
        cache=ctx.get("redis"),
        shop=shop,
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


async def _active_shop(session, connection_id: uuid.UUID) -> EtsyConnection | None:  # noqa: ANN001
    """The shop a job works on, if it is still connected."""
    connection = await session.get(EtsyConnection, connection_id)
    if connection is None or connection.status is not ConnectionStatus.active:
        return None
    return connection


async def _shop_owner(ctx: dict[str, Any], connection_id: str) -> uuid.UUID | None:
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, uuid.UUID(connection_id))
        return connection.tenant_id if connection is not None else None


async def refresh_profile(ctx: dict[str, Any], profile_id: str) -> str:
    async with ctx["sessionmaker"]() as session:
        profile = await session.get(ListingProfile, uuid.UUID(profile_id))
        if profile is None:
            return "missing"
        tenant_id = profile.tenant_id
    return await _run_gated(
        ctx, "refresh_profile", profile_id, tenant_id, lambda: _refresh_profile(ctx, profile_id)
    )


class ShopAccessLost(Exception):
    """The shop's sign-in could not be renewed; the seller must reconnect it."""


NO_SHOP = (
    "This profile's shop is no longer connected. Reconnect it to keep the profile up to date."
)
REFERENCE_GONE = (
    "The reference listing is no longer on Etsy (deleted, sold out or expired). "
    "Choose another reference listing for this profile."
)
ACCESS_LOST = "Etsy refused access to this shop. Reconnect the shop, then refresh the profile."
TRANSIENT = "Etsy could not be reached to refresh this profile. It will try again automatically."


def refresh_failure(exc: BaseException) -> str:
    """What the seller is told about a failed refresh. Never Etsy's own text."""
    if isinstance(exc, ShopAccessLost):
        return ACCESS_LOST
    if isinstance(exc, EtsyClientError):
        if exc.status_code in (404, 410):
            return REFERENCE_GONE
        if exc.status_code in (401, 403):
            return ACCESS_LOST
    return TRANSIENT


async def _record_refresh(
    ctx: dict[str, Any], profile_id: str, error: str | None
) -> None:
    """Note a refresh's outcome on the profile, in a session of its own (the
    failing one may be unusable). ``None`` clears an earlier failure."""
    async with ctx["sessionmaker"]() as session:
        profile = await session.get(ListingProfile, uuid.UUID(profile_id))
        if profile is None:
            return
        profile.refresh_error = error
        profile.refresh_failed_at = datetime.now(timezone.utc) if error else None
        await session.commit()


async def _reported(ctx: dict[str, Any], profile_id: str, body: Callable[[], Awaitable[str]]) -> str:
    """Run a refresh, telling the seller when it fails (v6 §H).

    Running out of daily budget is not a failure: the gate defers the job.
    """
    try:
        result = await body()
    except RateLimitExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - every failure is reported, then logged
        logger.warning("profile %s: refresh failed (%s)", profile_id, type(exc).__name__, exc_info=True)
        await _record_refresh(ctx, profile_id, refresh_failure(exc))
        return "failed"
    if result == "no-connection":
        await _record_refresh(ctx, profile_id, NO_SHOP)
    return result


async def _token(service: ConnectionService, session, connection: EtsyConnection) -> str:  # noqa: ANN001
    try:
        return await service.get_valid_access_token(session, connection)
    except Exception:  # noqa: BLE001 - the renewal's own error may carry token detail
        raise ShopAccessLost() from None


async def _refresh_profile(ctx: dict[str, Any], profile_id: str) -> str:
    return await _reported(ctx, profile_id, lambda: _refresh_profile_body(ctx, profile_id))


async def _refresh_profile_body(ctx: dict[str, Any], profile_id: str) -> str:
    settings = get_settings()
    sessionmaker = ctx["sessionmaker"]
    service = _connection_service(settings)

    async with sessionmaker() as session:
        profile = await session.get(ListingProfile, uuid.UUID(profile_id))
        if profile is None:
            return "missing"
        # The profile's own shop (v5 §E), never another of the account's shops.
        connection = await _active_shop(session, profile.connection_id)
        if connection is None or connection.tenant_id != profile.tenant_id:
            return "no-connection"

        tenant = await session.get(Tenant, profile.tenant_id)
        token = await _token(service, session, connection)
        async with httpx.AsyncClient(timeout=30.0) as http:
            client = _build_client(ctx, http, settings, shop=connection.id)
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
                    ShopListingCache.connection_id == profile.connection_id,
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
        profile.updated_at = profile.images_updated_at = datetime.now(timezone.utc)
        profile.refresh_error = profile.refresh_failed_at = None
        await session.commit()
        return "refreshed"


async def refresh_profile_images(ctx: dict[str, Any], profile_id: str) -> str:
    """Renew only a profile's image links (6-hour display limit), one request.

    Auto-refresh runs this every 5 hours between the 20-hourly full refreshes
    (v6 §H). If the reference's images changed, new ones need classifying, so
    it does the full refresh instead.
    """
    async with ctx["sessionmaker"]() as session:
        profile = await session.get(ListingProfile, uuid.UUID(profile_id))
        if profile is None:
            return "missing"
        tenant_id = profile.tenant_id
    return await _run_gated(
        ctx,
        "refresh_profile_images",
        profile_id,
        tenant_id,
        lambda: _reported(ctx, profile_id, lambda: _refresh_images_body(ctx, profile_id)),
    )


async def _refresh_images_body(ctx: dict[str, Any], profile_id: str) -> str:
    settings = get_settings()
    service = _connection_service(settings)
    async with ctx["sessionmaker"]() as session:
        profile = await session.get(ListingProfile, uuid.UUID(profile_id))
        if profile is None:
            return "missing"
        if not profile.cached_payload:  # the structure has lapsed too
            return await _refresh_profile_body(ctx, profile_id)
        connection = await _active_shop(session, profile.connection_id)
        if connection is None or connection.tenant_id != profile.tenant_id:
            return "no-connection"
        tenant = await session.get(Tenant, profile.tenant_id)
        token = await _token(service, session, connection)
        async with httpx.AsyncClient(timeout=30.0) as http:
            client = _build_client(ctx, http, settings, shop=connection.id)
            rows = await client.get_listing_images(
                profile.reference_listing_id,
                access_token=token,
                tenant_id=profile.tenant_id,
                tenant_limit=tenant.daily_quota if tenant else None,
            )
        fresh = image_entries(rows)
        known = {img.get("listing_image_id"): img for img in profile.cached_payload.get("images", [])}
        if {img["listing_image_id"] for img in fresh} != set(known):
            return await _refresh_profile_body(ctx, profile_id)
        # Same images: keep each one's classification, renew only the links.
        payload = dict(profile.cached_payload)
        payload["images"] = [
            {**known[img["listing_image_id"]], **img} for img in fresh
        ]
        profile.cached_payload = payload
        profile.images_updated_at = datetime.now(timezone.utc)
        profile.refresh_error = profile.refresh_failed_at = None
        await session.commit()
        return "images-refreshed"


async def auto_refresh_profiles(ctx: dict[str, Any]) -> dict[str, int]:
    """Queue a refresh for every profile in use that is about to pass a limit (v6 §H).

    Cron. The structure (24-hour limit) is renewed after 20 hours and the image
    links (6-hour limit) after 5, so a profile in use never shows expired data.
    Only confirmed profiles that wrote a listing or made a draft in the last two
    weeks are kept warm (etsy/refresh.py); the rest refresh on demand, when the
    Profiles page opens or they are chosen for a batch. Each job still goes
    through the gate, and a profile whose refresh just failed waits a few hours.
    """
    async with ctx["sessionmaker"]() as session:
        rows = await session.execute(
            select(ListingProfile)
            .join(EtsyConnection, EtsyConnection.id == ListingProfile.connection_id)
            .join(Tenant, Tenant.id == ListingProfile.tenant_id)
            .where(
                ListingProfile.confirmed.is_(True),
                EtsyConnection.status == ConnectionStatus.active,
                Tenant.status != TenantStatus.suspended,
                used_recently(),
            )
        )
        profiles = list(rows.scalars())

    queued = {FULL: 0, IMAGES: 0}
    for p in profiles:
        function = refresh_due(p)
        if function is None:
            continue
        # One queued refresh per profile, however often the cron fires.
        await _enqueue_job(ctx, function, str(p.id), _job_id=f"auto:{function}:{p.id}")
        queued[function] += 1
    if any(queued.values()):
        logger.info("auto-refresh: queued %s", queued)
    return queued

async def sync_shop_listings(ctx: dict[str, Any], connection_id: str) -> str:
    """Cache one shop's own active and draft listings (6 hours)."""
    tenant_id = await _shop_owner(ctx, connection_id)
    if tenant_id is None:
        return "no-connection"
    return await _run_gated(
        ctx,
        "sync_shop_listings",
        connection_id,
        tenant_id,
        lambda: _sync_shop_listings(ctx, connection_id),
    )


async def _sync_shop_listings(ctx: dict[str, Any], connection_id: str) -> str:
    settings = get_settings()
    sessionmaker = ctx["sessionmaker"]
    service = _connection_service(settings)

    async with sessionmaker() as session:
        connection = await _active_shop(session, uuid.UUID(connection_id))
        if connection is None:
            return "no-connection"
        tid = connection.tenant_id
        tenant = await session.get(Tenant, tid)
        token = await service.get_valid_access_token(session, connection)

        async with httpx.AsyncClient(timeout=30.0) as http:
            client = _build_client(ctx, http, settings, shop=connection.id)
            kw = {
                "access_token": token,
                "tenant_id": tid,
                "tenant_limit": tenant.daily_quota if tenant else None,
            }
            shop_id = await _resolve_shop_id(session, client, connection, kw)
            rows: list[dict[str, Any]] = []
            complete = True
            for state in ("active", "draft"):
                # Page through the whole shop. One page of 100 made any shop with
                # more listings look smaller than it is (docs/duzeltmeler-v6.md §A4).
                for page in range(SYNC_MAX_PAGES):
                    resp = await client.get_listings_by_shop(
                        shop_id,
                        state=state,
                        limit=SYNC_PAGE_SIZE,
                        offset=page * SYNC_PAGE_SIZE,
                        includes=["Images"],
                        **kw,
                    )
                    results = resp.get("results", [])
                    rows.extend(results)
                    total = resp.get("count")
                    if len(results) < SYNC_PAGE_SIZE or (
                        total is not None and (page + 1) * SYNC_PAGE_SIZE >= int(total)
                    ):
                        break
                else:
                    complete = False  # more than the page cap: keep what we have
                    logger.warning(
                        "sync: shop %s has more than %d %s listings; showing the first %d",
                        connection.id, SYNC_MAX_PAGES * SYNC_PAGE_SIZE, state,
                        SYNC_MAX_PAGES * SYNC_PAGE_SIZE,
                    )

        now = datetime.now(timezone.utc)
        # Listings no longer in the shop (deleted, sold out, expired) leave the
        # cache now, instead of being counted until their six hours run out.
        if complete:
            seen = {int(row["listing_id"]) for row in rows}
            await session.execute(
                delete(ShopListingCache).where(
                    ShopListingCache.connection_id == connection.id,
                    ShopListingCache.listing_id.not_in(seen) if seen else true(),
                )
            )
        for row in rows:
            listing_id = int(row["listing_id"])
            existing = await session.get(ShopListingCache, (tid, listing_id))
            if existing is None:
                session.add(
                    ShopListingCache(
                        tenant_id=tid,
                        connection_id=connection.id,
                        listing_id=listing_id,
                        payload=row,
                        fetched_at=now,
                    )
                )
            else:
                existing.connection_id = connection.id
                existing.payload = row
                existing.fetched_at = now
        await session.commit()
        return f"synced:{len(rows)}"


async def detect_profiles(ctx: dict[str, Any], connection_id: str) -> str:
    tenant_id = await _shop_owner(ctx, connection_id)
    if tenant_id is None:
        return "no-connection"
    return await _run_gated(
        ctx,
        "detect_profiles",
        connection_id,
        tenant_id,
        lambda: _detect_profiles(ctx, connection_id),
    )


async def _detect_profiles(ctx: dict[str, Any], connection_id: str) -> str:
    """Auto-detect candidate profiles by clustering the seller's own active listings.

    Clusters by taxonomy + production partner + variation structure + price band,
    names each cluster, and creates an **unconfirmed** profile per cluster (never
    used until the seller confirms it). Each new profile is then refreshed to build
    its cached payload and classify its images. Own-shop data only.
    """
    settings = get_settings()
    sessionmaker = ctx["sessionmaker"]
    service = _connection_service(settings)

    async with sessionmaker() as session:
        connection = await _active_shop(session, uuid.UUID(connection_id))
        if connection is None:
            return "no-connection"
        tid = connection.tenant_id
        tenant = await session.get(Tenant, tid)
        token = await service.get_valid_access_token(session, connection)

        async with httpx.AsyncClient(timeout=30.0) as http:
            client = _build_client(ctx, http, settings, shop=connection.id)
            kw = {
                "access_token": token,
                "tenant_id": tid,
                "tenant_limit": tenant.daily_quota if tenant else None,
            }
            shop_id = await _resolve_shop_id(session, client, connection, kw)
            # Every active listing, a page at a time, with its inventory in the same
            # request: one page of 100 missed most of a large shop, and one inventory
            # read per listing would spend the day's budget on it (v6 §B).
            listings_raw: list[dict[str, Any]] = []
            for page in range(SYNC_MAX_PAGES):
                resp = await client.get_listings_by_shop(
                    shop_id,
                    state="active",
                    limit=SYNC_PAGE_SIZE,
                    offset=page * SYNC_PAGE_SIZE,
                    includes=["Images", "Inventory"],
                    **kw,
                )
                results = resp.get("results", [])
                listings_raw.extend(results)
                total = resp.get("count")
                if len(results) < SYNC_PAGE_SIZE or (
                    total is not None and (page + 1) * SYNC_PAGE_SIZE >= int(total)
                ):
                    break
            else:
                logger.warning(
                    "detect: shop %s has more than %d active listings; clustering the first %d",
                    connection.id, SYNC_MAX_PAGES * SYNC_PAGE_SIZE, SYNC_MAX_PAGES * SYNC_PAGE_SIZE,
                )
            # Taxonomy tree (cached) -> which taxonomy ids are under Clothing (apparel).
            nodes = await client.get_seller_taxonomy_nodes(**kw)
            clothing_ids = clothing_taxonomy_ids(nodes)

            forcluster: list[ListingForCluster] = []
            separate_reads = 0
            for row in listings_raw:
                lid = int(row["listing_id"])
                inv = row.get("inventory")
                if not isinstance(inv, dict):
                    # Not attached to the page: read it on its own, within the
                    # budget JOB_COST allows for that; past it, cluster without.
                    if separate_reads < DETECT_MAX_INVENTORY_READS:
                        separate_reads += 1
                        inv = await client.get_listing_inventory(lid, **kw)
                    else:
                        inv = {}
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
                        production_partner_ids=tuple(production_partner_ids(row)),
                        variation_properties=tuple(sorted(props)),
                        image_count=len(row.get("images") or []),
                    )
                )

        # Existing reference ids so we don't duplicate profiles on re-run.
        existing = await session.execute(
            select(ListingProfile.reference_listing_id).where(
                ListingProfile.connection_id == connection.id
            )
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
                connection_id=connection.id,
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


async def _enqueue_job(ctx: dict[str, Any], function: str, *args: Any, **kwargs: Any) -> None:
    """Enqueue a follow-up job. Tests inject ``ctx['enqueue']``; arq provides
    ``ctx['redis']`` (the pool) with ``enqueue_job``."""
    enqueue = ctx.get("enqueue")
    if enqueue is not None:
        await enqueue(function, *args, **kwargs)
        return
    redis = ctx.get("redis")
    if redis is not None and hasattr(redis, "enqueue_job"):
        await redis.enqueue_job(function, *args, **kwargs)


def _price_float(price: Any) -> float | None:
    if isinstance(price, dict) and price.get("divisor"):
        return round(float(price.get("amount", 0)) / float(price["divisor"]), 2)
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None
