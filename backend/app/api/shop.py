"""The seller's own shop listings for the dashboard (Section B4).

Listings are served from ``shop_listing_cache`` (Member Content, 6h window). When
the cache is empty or stale, a ``sync_shop_listings`` job is enqueued to refresh it
through the queue; the endpoint returns whatever is cached plus a ``stale`` flag.
Only the authenticated seller's own listings are ever read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, current_tenant, get_enqueuer, get_session
from app.db.models import ListingProfile, ShopListingCache, Tenant

router = APIRouter(prefix="/api/shop", tags=["shop"])


def _listing_out(row: dict[str, Any]) -> schemas.ShopListingOut:
    images = row.get("images") or []
    thumb = None
    if images:
        thumb = images[0].get("url_570xN") or images[0].get("url_fullxfull")
    skus = row.get("skus") or []
    return schemas.ShopListingOut(
        listing_id=int(row["listing_id"]),
        title=row.get("title"),
        state=row.get("state"),
        sku=skus[0] if skus else None,
        shop_section_id=row.get("shop_section_id"),
        url=row.get("url"),
        thumbnail_url=thumb,
    )


def _is_stale(fetched_at: datetime | None) -> bool:
    if fetched_at is None:
        return True
    # SQLite returns naive datetimes; treat a naive value as UTC (Postgres is aware).
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
    return age >= ShopListingCache.STALE_SECONDS


@router.get("/listings", response_model=schemas.ShopListingsOut)
async def list_shop_listings(
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ShopListingsOut:
    rows = await session.execute(
        select(ShopListingCache).where(ShopListingCache.tenant_id == tenant.id)
    )
    cached = list(rows.scalars())
    newest = max((c.fetched_at for c in cached), default=None)
    stale = _is_stale(newest)
    if stale:
        # Refresh in the background; return whatever we already have meanwhile.
        await enqueuer.enqueue("sync_shop_listings", str(tenant.id))
    return schemas.ShopListingsOut(
        listings=[_listing_out(c.payload) for c in cached], stale=stale
    )


@router.post(
    "/listings/{listing_id}/use-as-profile",
    response_model=schemas.ProfileOut,
    status_code=201,
)
async def use_listing_as_profile(
    listing_id: int,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ProfileOut:
    """Create a profile from an existing listing ("Yeni sürüm oluştur", B4).

    The new listing is then produced through the normal upload -> generate ->
    create-draft flow, using this profile as the reference. Name/template can be
    tuned afterwards via PATCH /api/profiles/{id}.
    """
    cached = await session.get(ShopListingCache, (tenant.id, listing_id))
    default_name = (cached.payload or {}).get("title") if cached is not None else None

    profile = ListingProfile(
        tenant_id=tenant.id,
        name=default_name or f"Listing {listing_id}",
        reference_listing_id=listing_id,
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    await enqueuer.enqueue("refresh_profile", str(profile.id))

    from app.api.profiles import _to_out

    return _to_out(profile)
