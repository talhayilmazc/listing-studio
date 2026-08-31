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
from app.api.deps import Enqueuer, current_tenant, get_connection_service, get_enqueuer, get_session
from app.db.models import Job, JobStatus, JobType, ListingProfile, ShopListingCache, Tenant, UploadBatch
from app.etsy.connection import ConnectionService
from app.pipeline.reference import decode_etsy_text

router = APIRouter(prefix="/api/shop", tags=["shop"])


def _listing_out(row: dict[str, Any]) -> schemas.ShopListingOut:
    images = row.get("images") or []
    thumb = None
    if images:
        thumb = images[0].get("url_570xN") or images[0].get("url_fullxfull")
    skus = row.get("skus") or []
    return schemas.ShopListingOut(
        listing_id=int(row["listing_id"]),
        title=decode_etsy_text(row.get("title")) or None,
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


@router.post("/detect-profiles", status_code=202)
async def detect_profiles_endpoint(
    tenant: Tenant = Depends(current_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> dict[str, str]:
    """Kick off auto-detection of candidate profiles from the seller's own shop.

    Runs through the queue; detected profiles appear in GET /api/profiles as
    unconfirmed for the seller to confirm, rename or override (never used silently).
    """
    await enqueuer.enqueue("detect_profiles", str(tenant.id))
    return {"status": "detecting"}


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
    default_name = (
        decode_etsy_text((cached.payload or {}).get("title")) if cached is not None else None
    )

    profile = ListingProfile(
        tenant_id=tenant.id,
        name=default_name or f"Listing {listing_id}",
        reference_listing_id=listing_id,
        source="manual",
        confirmed=True,  # the seller explicitly chose this listing
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    await enqueuer.enqueue("refresh_profile", str(profile.id))

    from app.api.profiles import _to_out

    return _to_out(profile)


@router.post("/listings/{listing_id}/replace-images", response_model=schemas.ReplaceImagesOut)
async def replace_listing_images_endpoint(
    listing_id: int,
    body: schemas.ReplaceImagesRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
    service: ConnectionService = Depends(get_connection_service),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ReplaceImagesOut:
    """Update an existing listing in place with a batch of new product photos (B4).

    Runs through the queue: deletes the listing's artwork images (keeps size charts),
    uploads the new photos, and refreshes title/tags/description — state and all
    metadata untouched. Snapshots before any write.
    """
    connection = await service.get_active(session, tenant.id)
    if connection is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")
    batch = await session.get(UploadBatch, body.batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")

    job = Job(
        tenant_id=tenant.id,
        connection_id=connection.id,
        type=JobType.replace_images,
        payload={"listing_id": listing_id, "batch_id": str(body.batch_id)},
        batch_id=body.batch_id,
        status=JobStatus.queued,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    await enqueuer.enqueue("run_replace_images_job", str(job.id))
    return schemas.ReplaceImagesOut(listing_id=listing_id, job_id=job.id)
