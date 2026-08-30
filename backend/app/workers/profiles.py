"""Worker jobs for reference-listing profiles and the dashboard listing cache.

Both run only via the queue (CLAUDE.md) and read only the authenticated seller's
own shop. ``refresh_profile`` copies a reference listing into a profile's
``cached_payload``; ``sync_shop_listings`` caches the seller's own active + draft
listings for the dashboard (6h Member Content window). Tokens are never logged.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.db.models import EtsyConnection, ListingProfile, ShopListingCache, Tenant
from app.etsy.api import EtsyApiClient
from app.etsy.connection import ConnectionService
from app.pipeline.reference import build_profile_payload

logger = logging.getLogger(__name__)


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


async def refresh_profile(ctx: dict[str, Any], profile_id: str) -> str:
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
            await _resolve_shop_id(session, client, connection, kw)
            ref_id = profile.reference_listing_id
            listing = await client.get_listing(ref_id, **kw)
            inventory = await client.get_listing_inventory(ref_id, **kw)
            images = await client.get_listing_images(ref_id, **kw)

        profile.cached_payload = build_profile_payload(listing, inventory, images)
        profile.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return "refreshed"


async def sync_shop_listings(ctx: dict[str, Any], tenant_id: str) -> str:
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
