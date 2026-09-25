"""The seller's own shop listings for the dashboard (Section B4).

Listings are served from ``shop_listing_cache`` (Member Content, 6h window). When
the cache is empty or stale, a ``sync_shop_listings`` job is enqueued to refresh it
through the queue; the endpoint returns whatever is cached plus a ``stale`` flag.
Only the authenticated seller's own listings are ever read.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_connection_service, get_enqueuer, get_session
from app.api.shops import selected_shop
from app.db.models import (
    EtsyConnection,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingPublication,
    ShopListingCache,
    Tenant,
    UploadBatch,
)
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
        state_timestamp=_as_int(row.get("state_timestamp")),
    )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _month_start(moment: datetime) -> datetime:
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _is_stale(fetched_at: datetime | None) -> bool:
    if fetched_at is None:
        return True
    # SQLite returns naive datetimes; treat a naive value as UTC (Postgres is aware).
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - fetched_at).total_seconds()
    return age >= ShopListingCache.STALE_SECONDS


async def _shop_rows(
    session: AsyncSession, tenant: Tenant, connection: EtsyConnection
) -> list[ShopListingCache]:
    rows = await session.execute(
        select(ShopListingCache).where(
            ShopListingCache.tenant_id == tenant.id,
            ShopListingCache.connection_id == connection.id,
        )
    )
    return list(rows.scalars())


@router.get("/listings", response_model=schemas.ShopListingsOut)
async def list_shop_listings(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ShopListingsOut:
    """One shop's own listings (``?shop=``, default the first shop)."""
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        return schemas.ShopListingsOut(listings=[], stale=False)
    cached = await _shop_rows(session, tenant, connection)
    newest = max((c.fetched_at for c in cached), default=None)
    stale = _is_stale(newest)
    if stale:
        await enqueuer.enqueue("sync_shop_listings", str(connection.id))
    # Expired rows are never shown (CLAUDE.md: past its age, listing content is
    # re-fetched, not displayed). The retention job deletes them; this filter
    # covers the minutes between a row expiring and the next sweep.
    return schemas.ShopListingsOut(
        listings=[_listing_out(c.payload) for c in cached if not _is_stale(c.fetched_at)],
        stale=stale,
    )


@router.get("/summary", response_model=schemas.ShopSummaryOut)
async def shop_summary(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ShopSummaryOut:
    """One shop's listing counts from the cache. Never triggers a sync (cf. ``/shop/listings``)."""
    connection = await selected_shop(session, tenant, shop)
    everything = await _shop_rows(session, tenant, connection) if connection else []
    newest = max((c.fetched_at for c in everything), default=None)
    # Counts are derived from listing content, so expired rows do not count.
    cached = [c for c in everything if not _is_stale(c.fetched_at)]

    now = datetime.now(timezone.utc)
    this_month = _month_start(now)
    last_month = _month_start(this_month - timedelta(days=1))

    active = draft = this_count = last_count = 0
    for row in cached:
        payload = row.payload or {}
        state = payload.get("state")
        if state == "active":
            active += 1
        elif state == "draft":
            draft += 1
        # "Published" = went live, which is what state_timestamp records for an
        # active listing. Drafts have never been published, so they never count.
        stamp = _as_int(payload.get("state_timestamp"))
        if state == "active" and stamp is not None:
            went_live = datetime.fromtimestamp(stamp, timezone.utc)
            if went_live >= this_month:
                this_count += 1
            elif went_live >= last_month:
                last_count += 1

    return schemas.ShopSummaryOut(
        total=len(cached),
        active=active,
        draft=draft,
        published_this_month=this_count,
        published_last_month=last_count,
        fetched_at=newest,
        stale=_is_stale(newest),
    )


@router.post("/detect-profiles", status_code=202)
async def detect_profiles_endpoint(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> dict[str, str]:
    """Kick off auto-detection of candidate profiles from the seller's own shop.

    Runs through the queue; detected profiles appear in GET /api/profiles as
    unconfirmed for the seller to confirm, rename or override (never used silently).
    """
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")
    await enqueuer.enqueue("detect_profiles", str(connection.id))
    return {"status": "detecting"}


async def _listing_shop(
    session: AsyncSession,
    tenant: Tenant,
    cached: ShopListingCache | None,
    shop: uuid.UUID | None,
) -> EtsyConnection:
    """The shop a listing is in: its cache row says, else the ``?shop=`` asked for."""
    connection = await selected_shop(
        session, tenant, cached.connection_id if cached is not None else shop
    )
    if connection is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")
    return connection


@router.post(
    "/listings/{listing_id}/use-as-profile",
    response_model=schemas.ProfileOut,
    status_code=201,
)
async def use_listing_as_profile(
    listing_id: int,
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ProfileOut:
    """Create a profile from an existing listing ("Yeni sürüm oluştur", B4).

    The new listing is then produced through the normal upload -> generate ->
    create-draft flow, using this profile as the reference. Name/template can be
    tuned afterwards via PATCH /api/profiles/{id}.
    """
    cached = await session.get(ShopListingCache, (tenant.id, listing_id))
    connection = await _listing_shop(session, tenant, cached, shop)
    default_name = (
        decode_etsy_text((cached.payload or {}).get("title")) if cached is not None else None
    )

    profile = ListingProfile(
        tenant_id=tenant.id,
        connection_id=connection.id,
        name=default_name or f"Listing {listing_id}",
        reference_listing_id=listing_id,
        source="manual",
        confirmed=True,  # the seller explicitly chose this listing
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    await enqueuer.enqueue("refresh_profile", str(profile.id))

    from app.api.profiles import _out

    return await _out(session, tenant, profile)


@router.post("/listings/{listing_id}/replace-images", response_model=schemas.ReplaceImagesOut)
async def replace_listing_images_endpoint(
    listing_id: int,
    body: schemas.ReplaceImagesRequest,
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    service: ConnectionService = Depends(get_connection_service),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ReplaceImagesOut:
    """Update an existing listing in place with a batch of new product photos (B4).

    Runs through the queue: deletes the listing's artwork images (keeps size charts),
    uploads the new photos, and refreshes title/tags/description — state and all
    metadata untouched. Snapshots before any write.
    """
    cached = await session.get(ShopListingCache, (tenant.id, listing_id))
    connection = await _listing_shop(session, tenant, cached, shop)
    batch = await session.get(UploadBatch, body.batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")

    payload: dict[str, object] = {"listing_id": listing_id, "batch_id": str(body.batch_id)}
    if body.group_key is not None:
        payload["group_key"] = body.group_key
    # A draft this app made: keep its shop's title prefix, and bring the listing's
    # text on the review page in line with what Etsy gets.
    made = (
        await session.execute(
            select(ListingPublication).where(
                ListingPublication.tenant_id == tenant.id,
                ListingPublication.connection_id == connection.id,
                ListingPublication.etsy_listing_id == listing_id,
            )
        )
    ).scalars().first()
    if made is not None:
        payload["content_id"] = str(made.content_id)
        profile = await session.get(ListingProfile, made.profile_id) if made.profile_id else None
        if profile is not None and profile.title_prefix:
            payload["title_prefix"] = profile.title_prefix
    job = Job(
        tenant_id=tenant.id,
        connection_id=connection.id,
        type=JobType.replace_images,
        payload=payload,
        batch_id=body.batch_id,
        status=JobStatus.queued,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    await enqueuer.enqueue("run_replace_images_job", str(job.id))
    return schemas.ReplaceImagesOut(listing_id=listing_id, job_id=job.id)
