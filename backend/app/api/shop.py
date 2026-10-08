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
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_connection_service, get_enqueuer, get_redis, get_session
from app.api.shops import selected_shop
from app.core import allowance
from app.db.models import (
    Asset,
    EtsyConnection,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingPublication,
    SalesDaily,
    ShopListingCache,
    Tenant,
    UploadBatch,
)
from app.etsy.connection import ConnectionService
from app.pipeline import upload_retention
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


# The summary may queue a shop sync at most this often (the cache's lifetime:
# four a day at most), and calls the shop "syncing" for this long after queueing.
#: The top strip's counts are how many listings the shop has in each state: not
#: listing content, so they may be a day old (CLAUDE.md: 24 hours).
COUNTS_FRESH_SECONDS = 24 * 3600
#: While the counts are unknown, ask for them at most this often.
SUMMARY_SYNC_EVERY_SECONDS = 3600
SUMMARY_SYNCING_SECONDS = 10 * 60


def _zone(tenant: Tenant) -> ZoneInfo:
    try:
        return ZoneInfo(tenant.time_zone or "UTC")
    except Exception:  # noqa: BLE001 - an unknown zone name falls back to UTC
        return ZoneInfo("UTC")


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
        # One at a time per shop, however many pages ask while it is stale.
        await enqueuer.enqueue("sync_shop_listings", str(connection.id), _job_id=f"sync:{connection.id}")
    # Expired rows are never shown (CLAUDE.md: past its age, listing content is
    # re-fetched, not displayed). The retention job deletes them; this filter
    # covers the minutes between a row expiring and the next sweep.
    return schemas.ShopListingsOut(
        listings=[_listing_out(c.payload) for c in cached if not _is_stale(c.fetched_at)],
        stale=stale,
    )


def has_feature(tenant: Tenant, name: str) -> bool:
    return bool((tenant.features or {}).get(name))


@router.get("/pattern-listings", response_model=list[schemas.PatternListingOut])
async def pattern_listings(
    shop: uuid.UUID | None = None,
    q: str = "",
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> list[schemas.PatternListingOut]:
    """The seller's OWN active listings to model a new listing on (v7 §B).

    Only this account's shop, from its 6-hour listing cache: never another
    seller's listing (ToU §1 and §5). Needs the feature turned on by an admin.
    """
    if not has_feature(tenant, "own_patterns"):
        raise HTTPException(status_code=404, detail="not available for this account")
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        return []
    cached = await _shop_rows(session, tenant, connection)
    if _is_stale(max((c.fetched_at for c in cached), default=None)):
        await enqueuer.enqueue("sync_shop_listings", str(connection.id), _job_id=f"sync:{connection.id}")
    words = [w for w in q.casefold().split() if w]
    # "My best sellers" (v7 §B): units in the last 90 days from the shop's own
    # daily sales totals, when the seller has allowed reading them.
    sold: dict[int, int] | None = None
    if "transactions_r" in (connection.scopes or []):
        since = datetime.now(timezone.utc).date() - timedelta(days=89)
        totals = await session.execute(
            select(SalesDaily.listing_id, func.sum(SalesDaily.units))
            .where(SalesDaily.connection_id == connection.id, SalesDaily.day >= since)
            .group_by(SalesDaily.listing_id)
        )
        sold = {int(lid): int(n or 0) for lid, n in totals.all()}
    out: list[schemas.PatternListingOut] = []
    for c in cached:
        row = c.payload or {}
        if _is_stale(c.fetched_at) or row.get("state") != "active":
            continue
        title = decode_etsy_text(row.get("title")) or ""
        tags = [str(t) for t in row.get("tags") or []]
        hay = (title + " " + " ".join(tags)).casefold()
        if words and not all(w in hay for w in words):
            continue
        item = _listing_out(row)
        out.append(
            schemas.PatternListingOut(
                listing_id=item.listing_id,
                title=title or None,
                tags=tags,
                state=item.state,
                thumbnail_url=item.thumbnail_url,
                url=item.url or f"https://www.etsy.com/listing/{item.listing_id}",
                units_90d=None if sold is None else sold.get(item.listing_id, 0),
            )
        )
    # Best sellers first, then by title.
    out.sort(key=lambda x: (-(x.units_90d or 0), (x.title or "").casefold()))
    return out


@router.post("/listings/sync", status_code=202)
async def sync_shop_listings_now(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> dict[str, bool]:
    """ "Sync shop listings" (v7 §D2): the seller changed something on Etsy and
    wants it here now, not when the six-hour cache runs out. One queued sync per
    shop, however often it is pressed; it is upkeep, not the seller's quota."""
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")
    await enqueuer.enqueue("sync_shop_listings", str(connection.id), _job_id=f"manual-sync:{connection.id}")
    return {"queued": True}


@router.get("/summary", response_model=schemas.ShopSummaryOut)
async def shop_summary(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
    redis: Redis = Depends(get_redis),
) -> schemas.ShopSummaryOut:
    """One shop's headline counts (see :class:`ShopSummaryOut`).

    "Published this month" is the app's own record of what was published
    through it. It used to be counted from the listing cache, which is empty
    whenever it has expired, so it read 0 for a seller who published every day.
    """
    connection = await selected_shop(session, tenant, shop)
    everything = await _shop_rows(session, tenant, connection) if connection else []
    newest = max((c.fetched_at for c in everything), default=None)
    # Counts are derived from listing content, so expired rows do not count.
    cached = [c for c in everything if not _is_stale(c.fetched_at)]
    # How many listings went live this month needs the listings themselves.
    published_known = bool(cached)
    # How many there are in each state does not: Etsy reports that with one
    # request per state, kept for a day (workers/profiles.py::sync_shop_counts).
    counts = None
    if connection is not None and connection.listing_counts and connection.listing_counts_at is not None:
        counted_at = connection.listing_counts_at
        if counted_at.tzinfo is None:  # SQLite hands back naive datetimes
            counted_at = counted_at.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - counted_at).total_seconds() < COUNTS_FRESH_SECONDS:
            counts = connection.listing_counts
    known = published_known or counts is not None

    # The months are the seller's own: the 1st at midnight in their time zone.
    zone = _zone(tenant)
    now = datetime.now(timezone.utc)
    this_month = _month_start(now.astimezone(zone))
    last_month = _month_start(this_month - timedelta(days=1))

    app_this = app_last = 0
    if connection is not None:
        rows = await session.execute(
            select(ListingPublication.published_at).where(
                ListingPublication.tenant_id == tenant.id,
                ListingPublication.connection_id == connection.id,
                ListingPublication.state == "active",
                ListingPublication.published_at >= last_month,
            )
        )
        for (published_at,) in rows.all():
            if published_at.tzinfo is None:  # SQLite hands back naive datetimes
                published_at = published_at.replace(tzinfo=timezone.utc)
            if published_at >= this_month:
                app_this += 1
            else:
                app_last += 1

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

    if counts is not None and not published_known:
        # No fresh copy of the listings: the per-state counts Etsy reported.
        active, draft = int(counts.get("active") or 0), int(counts.get("draft") or 0)
    total = len(cached) if published_known else sum(int(v or 0) for v in (counts or {}).values())

    # Unknown is not zero: say "syncing", and make it true. This runs on every
    # page, so it asks for the **counts** only (one request per listing state,
    # once a day), never for a full sync of every listing: that is left to the
    # pages that list listings. While the counts are unknown it asks at most
    # once an hour. "Syncing" is said only while that read can still be running.
    syncing = False
    if connection is not None and not known:
        marker = f"summary-sync:{connection.id}"
        if await redis.set(marker, int(now.timestamp()), nx=True, ex=SUMMARY_SYNC_EVERY_SECONDS):
            await enqueuer.enqueue("sync_shop_counts", str(connection.id), _job_id=f"counts:{connection.id}")
            syncing = True
        else:
            queued_at = _as_int(await redis.get(marker))
            syncing = queued_at is not None and now.timestamp() - queued_at < SUMMARY_SYNCING_SECONDS

    return schemas.ShopSummaryOut(
        app_published_this_month=app_this,
        app_published_last_month=app_last,
        shop_counts_known=known,
        published_known=published_known,
        syncing=syncing,
        total=total,
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
    photos = select(Asset.id).where(Asset.batch_id == batch.id, Asset.files_removed_at.is_not(None))
    if body.group_key is not None:
        photos = photos.where(Asset.group_key == body.group_key if body.group_key else or_(Asset.group_key.is_(None), Asset.group_key == ""))
    if (await session.execute(photos.limit(1))).first() is not None:
        # Upload retention deleted them: there is nothing to send to Etsy.
        raise HTTPException(status_code=409, detail=upload_retention.REMOVED)
    if body.mode == "full":
        # A new title and tags are written: one listing generated (core/allowance.py).
        # Photos only writes nothing and calls no AI, so the allowance is not asked.
        try:
            await allowance.check(session, tenant, 1)
        except allowance.AllowanceExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from None

    payload: dict[str, object] = {"listing_id": listing_id, "batch_id": str(body.batch_id), "mode": body.mode}
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
        if made.content_id is not None:  # the content is gone once its batch is deleted
            payload["content_id"] = str(made.content_id)
        profile = await session.get(ListingProfile, made.profile_id) if made.profile_id else None
        if profile is not None and profile.tenant_id == tenant.id:
            # "full" writes in the profile's title style (short or long), like a new listing.
            payload["profile_id"] = str(profile.id)
        if profile is not None and profile.title_prefix:
            payload["title_prefix"] = profile.title_prefix
        if profile is not None and profile.fixed_image_ids:
            # The size charts this app put on the draft: known without looking at them.
            payload["chart_ids"] = [int(i) for i in profile.fixed_image_ids]
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
