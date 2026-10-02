"""Batch, asset, generation, and cost endpoints."""

from __future__ import annotations

import uuid
from typing import Any
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import (
    Enqueuer,
    active_tenant,
    get_cost_calculator,
    get_enqueuer,
    get_ingestor,
    get_session,
    get_storage,
)
from app.core.config import get_settings
from app.db.models import (
    Asset,
    AssetStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    ListingGroupSetting,
    ListingProfile,
    ListingPublication,
    ShopListingCache,
    Tenant,
    UploadBatch,
)
from app.compliance.trademarks import blocklist_for_tenant
from app.core import allowance, audit
from app.api.shops import shop_label
from app.etsy.refresh import request_refresh
from app.etsy.shops import active_shops, owned_shop
from app.pipeline.batch_names import clean_name, names_for
from app.pipeline.targets import shop_profiles
from app.pipeline.content import AnthropicContentGenerator, content_template_for, policy_for, search_style
from app.pipeline.reference import decode_etsy_text
from app.pipeline.cost import CostCalculator, UnknownModelError
from app.pipeline.generation import generate_listing_content
from app.pipeline.images import (
    PREVIEW_ASPECTS,
    PREVIEW_VERSION,
    PREVIEW_WIDTHS,
    ImageProcessingError,
    resize_preview,
)
from app.pipeline.ingest import BatchIngestor, UploadFile as IngestFile
from app.pipeline.archive import read_archive
from app.pipeline.uploads import UploadRejected, UploadTooLarge
from app.pipeline.llm import client_for
from app.pipeline.storage import Storage
from app.pipeline.templates import load_template
from app.pipeline.vision import AnthropicVisionAnalyzer

router = APIRouter(prefix="/api", tags=["batches"])


def _profile_is_fresh(profile: ListingProfile) -> bool:
    """A profile is usable once its reference payload is cached and <24h old."""
    if not profile.cached_payload or profile.updated_at is None:
        return False
    updated = profile.updated_at
    if updated.tzinfo is None:  # SQLite returns naive; treat as UTC.
        updated = updated.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    return age < ListingProfile.CACHE_MAX_AGE_SECONDS


async def _content_asset_ids(session: AsyncSession, batch_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await session.execute(
        select(GeneratedContent.asset_id).where(GeneratedContent.batch_id == batch_id)
    )
    return set(rows.scalars())


async def _summary(session: AsyncSession, batch: UploadBatch) -> schemas.BatchSummary:
    asset_count = await session.scalar(
        select(func.count()).select_from(Asset).where(Asset.batch_id == batch.id)
    )
    processed_count = await session.scalar(
        select(func.count())
        .select_from(Asset)
        .where(Asset.batch_id == batch.id, Asset.status == AssetStatus.processed)
    )
    approved_count = await session.scalar(
        select(func.count())
        .select_from(GeneratedContent)
        .where(GeneratedContent.batch_id == batch.id, GeneratedContent.approved.is_(True))
    )
    # Every shop this batch is in: its own, its groups', and where it has drafts.
    shop_ids = set(
        (
            await session.execute(
                select(ListingGroupSetting.connection_id).where(
                    ListingGroupSetting.batch_id == batch.id, ListingGroupSetting.connection_id.is_not(None)
                )
            )
        ).scalars()
    ) | set(
        (
            await session.execute(
                select(ListingPublication.connection_id)
                .join(GeneratedContent, GeneratedContent.id == ListingPublication.content_id)
                .where(GeneratedContent.batch_id == batch.id)
            )
        ).scalars()
    )
    if batch.connection_id is not None:
        shop_ids.add(batch.connection_id)
    shops = [c for c in await active_shops(session, batch.tenant_id) if c.id in shop_ids]
    own = next((c for c in shops if c.id == batch.connection_id), None)
    return schemas.BatchSummary(
        name=(await names_for(session, [batch]))[batch.id],
        named=bool(batch.name),
        id=batch.id,
        status=batch.status.value,
        file_count=batch.file_count,
        created_at=batch.created_at,
        asset_count=int(asset_count or 0),
        processed_count=int(processed_count or 0),
        approved_count=int(approved_count or 0),
        size_chart_profile_id=batch.size_chart_profile_id,
        connection_id=own.id if own else None,
        shop_name=shop_label(own) if own else None,
        shop_names=[shop_label(c) for c in shops],
    )


async def _get_batch(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID) -> UploadBatch:
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    return batch


# --- Upload flow ------------------------------------------------------------
@router.post("/batches", response_model=schemas.BatchSummary, status_code=201)
async def create_batch(
    body: schemas.BatchCreate | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    ingestor: BatchIngestor = Depends(get_ingestor),
) -> schemas.BatchSummary:
    """Start an upload, for the shop the seller is uploading for.

    With several shops connected the shop must be named: a batch is never put
    in a shop the seller did not pick. With one, it is that one.
    """
    shops = await active_shops(session, tenant.id)
    chosen: EtsyConnection | None = None
    if body is not None and body.connection_id is not None:
        chosen = await owned_shop(session, tenant.id, body.connection_id)
        if chosen is None:
            raise HTTPException(status_code=404, detail="shop not found")
    elif len(shops) == 1:
        chosen = shops[0]
    batch = await ingestor.create_batch(session, tenant.id)
    batch.connection_id = chosen.id if chosen else None
    await session.commit()
    await session.refresh(batch)
    return await _summary(session, batch)


@router.post("/batches/{batch_id}/assets", response_model=schemas.AssetOut, status_code=201)
async def add_asset(
    batch_id: uuid.UUID,
    file: UploadFile,
    group_key: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    ingestor: BatchIngestor = Depends(get_ingestor),
) -> schemas.AssetOut:
    await _get_batch(session, tenant, batch_id)
    # The security middleware has already capped the request body, so this read
    # is bounded; admission below still enforces the exact per-file limit.
    data = await file.read()
    try:
        asset = await ingestor.add_file(
            session,
            batch_id,
            tenant.id,
            IngestFile(filename=file.filename or "upload", data=data),
            group_key=group_key or None,
        )
    except UploadRejected as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    return schemas.AssetOut(
        id=asset.id,
        original_filename=asset.original_filename,
        parsed_sku=asset.parsed_sku,
        group_key=asset.group_key,
        rank=asset.rank,
        status=asset.status.value,
        mime_type=asset.mime_type,
        width=asset.width,
        height=asset.height,
        has_content=False,
        error=asset.error,
    )


def _asset_out(asset: Asset) -> schemas.AssetOut:
    return schemas.AssetOut(
        id=asset.id,
        original_filename=asset.original_filename,
        parsed_sku=asset.parsed_sku,
        group_key=asset.group_key,
        rank=asset.rank,
        status=asset.status.value,
        mime_type=asset.mime_type,
        width=asset.width,
        height=asset.height,
        has_content=False,
        error=asset.error,
        cover_crop=asset.cover_crop,
    )


@router.post("/batches/{batch_id}/archive", response_model=schemas.ArchiveResult, status_code=201)
async def add_archive(
    batch_id: uuid.UUID,
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    ingestor: BatchIngestor = Depends(get_ingestor),
) -> schemas.ArchiveResult:
    """Unpack a ZIP of designs into the batch (docs/duzeltmeler-v6.md §F).

    Its folders become listing groups exactly as a folder upload's do; files at
    its root are one group. Each image goes through the same admission as a
    single upload. What was skipped is counted, not silently lost.
    """
    await _get_batch(session, tenant, batch_id)
    settings = get_settings()
    data = await file.read()  # bounded by the security middleware
    if len(data) > settings.max_archive_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"a ZIP is limited to {settings.max_archive_bytes // (1024 * 1024)} MB",
        )
    try:
        contents = read_archive(
            data,
            max_files=settings.max_archive_files,
            max_total_bytes=settings.max_archive_unpacked_bytes,
            max_file_bytes=settings.max_upload_bytes,
        )
    except UploadRejected as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    del data

    result = schemas.ArchiveResult(
        skipped_unsupported=contents.unsupported,
        skipped_unsafe=contents.unsafe,
        skipped_nested=contents.nested,
        failed=[
            schemas.ArchiveFailure(
                filename=name,
                error=f"files are limited to {settings.max_upload_bytes // (1024 * 1024)} MB",
            )
            for name in contents.too_large
        ],
    )
    for entry in contents.files:
        try:
            asset = await ingestor.add_file(
                session,
                batch_id,
                tenant.id,
                IngestFile(filename=entry.filename, data=entry.data),
                group_key=entry.group_key,
            )
        except UploadTooLarge as exc:
            result.failed.append(schemas.ArchiveFailure(filename=entry.filename, error=str(exc)))
            if "batch" in str(exc):
                break  # the batch is full; the rest would fail the same way
            continue
        except UploadRejected as exc:
            result.failed.append(schemas.ArchiveFailure(filename=entry.filename, error=str(exc)))
            continue
        result.assets.append(_asset_out(asset))
    return result


@router.put("/batches/{batch_id}/size-chart-profile", response_model=schemas.BatchSummary)
async def set_size_chart_profile(
    batch_id: uuid.UUID,
    body: schemas.SizeChartProfileUpdate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.BatchSummary:
    """Choose which profile's size charts (fixed images) to append for this batch (Task 4)."""
    batch = await _get_batch(session, tenant, batch_id)
    if body.profile_id is not None:
        profile = await session.get(ListingProfile, body.profile_id)
        if profile is None or profile.tenant_id != tenant.id:
            raise HTTPException(status_code=404, detail="profile not found")
    batch.size_chart_profile_id = body.profile_id
    await session.commit()
    return await _summary(session, batch)


@router.post("/batches/{batch_id}/finalize", response_model=schemas.BatchSummary)
async def finalize_batch(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    ingestor: BatchIngestor = Depends(get_ingestor),
) -> schemas.BatchSummary:
    await _get_batch(session, tenant, batch_id)
    batch = await ingestor.finalize_batch(session, batch_id)
    return await _summary(session, batch)


@router.patch("/batches/{batch_id}", response_model=schemas.BatchSummary)
async def rename_batch(
    batch_id: uuid.UUID,
    body: schemas.BatchRename,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.BatchSummary:
    """Name a batch, so it can be found again. An empty name goes back to the
    one derived from its contents."""
    batch = await _get_batch(session, tenant, batch_id)
    batch.name = clean_name(body.name)
    await session.commit()
    return await _summary(session, batch)


@router.get("/batches/{batch_id}/summary", response_model=schemas.BatchSummary)
async def batch_summary(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.BatchSummary:
    """The batch without its files: its name, shop and counts (page headers)."""
    return await _summary(session, await _get_batch(session, tenant, batch_id))


# --- Reading batches --------------------------------------------------------
@router.get("/batches", response_model=list[schemas.BatchSummary])
async def list_batches(
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[schemas.BatchSummary]:
    rows = await session.execute(
        select(UploadBatch)
        .where(UploadBatch.tenant_id == tenant.id)
        .order_by(UploadBatch.created_at.desc())
    )
    return [await _summary(session, b) for b in rows.scalars()]


@router.get("/batches/{batch_id}", response_model=schemas.BatchDetail)
async def get_batch(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.BatchDetail:
    batch = await _get_batch(session, tenant, batch_id)
    summary = await _summary(session, batch)
    with_content = await _content_asset_ids(session, batch_id)
    rows = await session.execute(
        select(Asset).where(Asset.batch_id == batch_id).order_by(Asset.rank)
    )
    assets = [
        schemas.AssetOut(
            id=a.id,
            original_filename=a.original_filename,
            parsed_sku=a.parsed_sku,
            group_key=a.group_key,
            rank=a.rank,
            status=a.status.value,
            mime_type=a.mime_type,
            width=a.width,
            height=a.height,
            has_content=a.id in with_content,
            error=a.error,
            cover_crop=a.cover_crop,
        )
        for a in rows.scalars()
    ]
    return schemas.BatchDetail(**summary.model_dump(), assets=assets)


# Browser cache for the resized previews only. These are the seller's own uploaded
# designs, not Etsy Member Content, so the ToU cache ceilings do not apply.
_PREVIEW_CACHE_HEADERS = {"Cache-Control": "private, max-age=3600"}


@router.get("/assets/{asset_id}/image")
async def get_asset_image(
    asset_id: uuid.UUID,
    w: int | None = Query(
        None, description=f"Optional preview width; one of {sorted(PREVIEW_WIDTHS)}."
    ),
    ar: str | None = Query(
        None,
        description=(
            "Optional tile aspect ratio, cropped around the artwork; one of "
            f"{sorted(PREVIEW_ASPECTS)}. Requires w."
        ),
    ),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    asset = await session.get(Asset, asset_id)
    if asset is None or asset.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="asset not found")
    key = asset.processed_key or asset.storage_key

    # Additive preview path: only taken when ?w= is supplied. Widths are limited to
    # an allowlist so an arbitrary ?w= cannot force unbounded resize work, and each
    # result is cached beside its source so the resize runs once per asset per width.
    if w is None and ar is not None:
        raise HTTPException(status_code=400, detail="ar requires w")

    if w is not None:
        if w not in PREVIEW_WIDTHS:
            raise HTTPException(
                status_code=400, detail=f"w must be one of {sorted(PREVIEW_WIDTHS)}"
            )
        if ar is not None and ar not in PREVIEW_ASPECTS:
            raise HTTPException(
                status_code=400, detail=f"ar must be one of {sorted(PREVIEW_ASPECTS)}"
            )
        suffix = f".v{PREVIEW_VERSION}.w{w}" + (f"-{ar.replace(':', 'x')}" if ar else "")
        cache_key = f"{key}{suffix}.jpg"
        try:
            if storage.exists(cache_key):
                return Response(
                    content=storage.get(cache_key),
                    media_type="image/jpeg",
                    headers=_PREVIEW_CACHE_HEADERS,
                )
        except FileNotFoundError:
            pass  # cache entry vanished between the check and the read; rebuild it
        try:
            source = storage.get(key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="image not found") from exc
        try:
            preview = resize_preview(source, w, ar)
        except ImageProcessingError as exc:
            raise HTTPException(status_code=422, detail="image cannot be resized") from exc
        storage.put(cache_key, preview, "image/jpeg")
        return Response(
            content=preview, media_type="image/jpeg", headers=_PREVIEW_CACHE_HEADERS
        )

    try:
        data = storage.get(key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="image not found") from exc
    return Response(content=data, media_type=asset.mime_type or "application/octet-stream")


# --- Per-group shop, profile and size charts (v4 §E; shop first, Priority 2) ---------
async def _batch_groups(
    session: AsyncSession, batch_id: uuid.UUID
) -> list[schemas.GroupOut]:
    batch = await session.get(UploadBatch, batch_id)
    rows = await session.execute(select(Asset).where(Asset.batch_id == batch_id))
    assets = list(rows.scalars())
    with_content = await _content_asset_ids(session, batch_id)
    setting_rows = await session.execute(
        select(ListingGroupSetting).where(ListingGroupSetting.batch_id == batch_id)
    )
    group_settings = {s.group_key: s for s in setting_rows.scalars()}
    shops = {c.id: c for c in await active_shops(session, batch.tenant_id)} if batch else {}

    grouped: dict[str, list[Asset]] = {}
    for asset in assets:
        grouped.setdefault(asset.group_key or "", []).append(asset)

    out: list[schemas.GroupOut] = []
    for key in sorted(grouped):
        members = grouped[key]
        s = group_settings.get(key)
        # A group with nothing set is in the batch's shop.
        shop_id = (s.connection_id if s and s.connection_id else None) or (batch.connection_id if batch else None)
        shop = shops.get(shop_id) if shop_id else None
        out.append(
            schemas.GroupOut(
                group_key=key,
                sku=next((m.parsed_sku for m in members if m.parsed_sku), None),
                image_count=len(members),
                has_content=any(m.id in with_content for m in members),
                connection_id=shop.id if shop else None,
                shop_name=shop_label(shop) if shop else None,
                profile_id=s.profile_id if s else None,
                size_chart_profile_id=s.size_chart_profile_id if s else None,
                manual=s.manual if s else False,
                pattern_listing_id=s.pattern_listing_id if s else None,
            )
        )
    return out


@router.get("/batches/{batch_id}/groups", response_model=list[schemas.GroupOut])
async def list_groups(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[schemas.GroupOut]:
    await _get_batch(session, tenant, batch_id)
    return await _batch_groups(session, batch_id)


async def _matching_profile(
    session: AsyncSession, shop_id: uuid.UUID, like: ListingProfile | None
) -> uuid.UUID | None:
    """The profile of ``shop_id`` that stands in for ``like`` (another shop's):
    the one with the same name, else the only one of the same kind, else the
    only one. None when the seller has to choose."""
    candidates = await shop_profiles(session, shop_id)
    if like is not None:
        named = [p for p in candidates if p.name.casefold() == like.name.casefold()]
        if named:
            return named[0].id
        same_kind = [p for p in candidates if p.content_template == like.content_template]
        if len(same_kind) == 1:
            return same_kind[0].id
    return candidates[0].id if len(candidates) == 1 else None


async def _move_to_shop(session: AsyncSession, setting: ListingGroupSetting, shop_id: uuid.UUID) -> None:
    """Put a group in ``shop_id``. A profile or size-chart profile of another
    shop cannot stay: the profile becomes that shop's matching one (or is left
    for the seller to choose), the size charts go back to the profile's own."""
    if setting.connection_id == shop_id:
        return
    setting.connection_id = shop_id
    current = await session.get(ListingProfile, setting.profile_id) if setting.profile_id else None
    if current is None or current.connection_id != shop_id:
        setting.profile_id = await _matching_profile(session, shop_id, current)
    chart = await session.get(ListingProfile, setting.size_chart_profile_id) if setting.size_chart_profile_id else None
    if chart is not None and chart.connection_id != shop_id:
        setting.size_chart_profile_id = None


async def _shop_profile(
    session: AsyncSession, tenant: Tenant, profile_id: uuid.UUID, shop_id: uuid.UUID | None, what: str
) -> ListingProfile:
    profile = await session.get(ListingProfile, profile_id)
    if profile is None or profile.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="profile not found")
    if shop_id is not None and profile.connection_id != shop_id:
        raise HTTPException(
            status_code=422,
            detail=f"that {what} belongs to another shop; choose the group's shop first, then one of its profiles",
        )
    return profile


@router.put("/batches/{batch_id}/shop", response_model=list[schemas.GroupOut])
async def set_batch_shop(
    batch_id: uuid.UUID,
    body: schemas.BatchShop,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[schemas.GroupOut]:
    """Choose the shop the batch is for. Groups the seller has not set themselves
    move with it (each to that shop's matching profile); groups set by hand and
    groups whose listing is already written stay where they are."""
    batch = await _get_batch(session, tenant, batch_id)
    shop = await owned_shop(session, tenant.id, body.connection_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="shop not found")
    batch.connection_id = shop.id
    written = {g.group_key for g in await _batch_groups(session, batch_id) if g.has_content}
    for setting in (
        await session.execute(select(ListingGroupSetting).where(ListingGroupSetting.batch_id == batch_id))
    ).scalars():
        if not setting.manual and setting.group_key not in written:
            await _move_to_shop(session, setting, shop.id)
    await session.commit()
    return await _batch_groups(session, batch_id)


@router.put("/batches/{batch_id}/groups", response_model=list[schemas.GroupOut])
async def assign_group_profile(
    batch_id: uuid.UUID,
    body: schemas.GroupAssign,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> list[schemas.GroupOut]:
    """Set the shop, the profile and the size-chart profile of one group, of a
    selection, or of every group the seller has not set (see GroupAssign).

    The shop comes first: a profile or a size-chart profile must belong to the
    group's shop, and moving a group to another shop swaps its profile for that
    shop's matching one. Only the fields sent are changed.
    """
    batch = await _get_batch(session, tenant, batch_id)
    sent = body.model_fields_set
    if "connection_id" in sent and body.connection_id is not None:
        if await owned_shop(session, tenant.id, body.connection_id) is None:
            raise HTTPException(status_code=404, detail="shop not found")

    existing = {
        s.group_key: s
        for s in (
            await session.execute(
                select(ListingGroupSetting).where(ListingGroupSetting.batch_id == batch_id)
            )
        ).scalars()
    }
    groups = await _batch_groups(session, batch_id)
    order = [g.group_key for g in groups]
    written = {g.group_key for g in groups if g.has_content}

    explicit = body.group_key is not None or body.group_keys is not None
    if body.group_keys is not None:
        keys = [k for k in body.group_keys if k in order]
    elif body.group_key is not None:
        keys = [body.group_key]
    else:
        keys = [k for k in order if not (existing.get(k) and existing[k].manual)]

    def setting_for(key: str) -> ListingGroupSetting:
        setting = existing.get(key)
        if setting is None:
            setting = ListingGroupSetting(
                tenant_id=tenant.id, batch_id=batch_id, group_key=key, connection_id=batch.connection_id
            )
            session.add(setting)
            existing[key] = setting
        elif setting.connection_id is None:
            setting.connection_id = batch.connection_id
        return setting

    for key in keys:
        setting = setting_for(key)
        if "connection_id" in sent and body.connection_id is not None:
            await _move_to_shop(session, setting, body.connection_id)
        if "profile_id" in sent:
            if body.profile_id is not None:
                profile = await _shop_profile(session, tenant, body.profile_id, setting.connection_id, "profile")
                # A group with no shop yet takes the shop of the profile chosen for it.
                setting.connection_id = setting.connection_id or profile.connection_id
            setting.profile_id = body.profile_id
        if "size_chart_profile_id" in sent:
            if body.size_chart_profile_id is not None:
                chart = await _shop_profile(
                    session, tenant, body.size_chart_profile_id, setting.connection_id, "size-chart profile"
                )
                setting.connection_id = setting.connection_id or chart.connection_id
            setting.size_chart_profile_id = body.size_chart_profile_id
        if explicit:
            setting.manual = True

    # Defaults carry forward: the groups after a group set on its own take the
    # same, until one the seller set themselves. Written listings are left alone.
    if body.group_key is not None and body.group_key in order:
        source = existing[body.group_key]
        for key in order[order.index(body.group_key) + 1:]:
            follower = existing.get(key)
            if follower is not None and follower.manual:
                break
            if key in written:
                continue
            follower = setting_for(key)
            follower.connection_id = source.connection_id
            follower.profile_id = source.profile_id
            follower.size_chart_profile_id = source.size_chart_profile_id

    await session.commit()
    # Chosen for generation: bring its reference up to date now, since a profile
    # not used lately is not kept warm in the background (etsy/refresh.py).
    for profile_id in {existing[k].profile_id for k in keys if existing[k].profile_id}:
        chosen = await session.get(ListingProfile, profile_id)
        if chosen is not None:
            await request_refresh(enqueuer.enqueue, chosen, origin="use")
    return await _batch_groups(session, batch_id)


# --- Cover crop (the seller's square for the listing's main photo) ------------------
#: Zooming further than this would upload a very soft photo.
MAX_COVER_ZOOM = 5


async def _own_asset(session: AsyncSession, tenant: Tenant, asset_id: uuid.UUID) -> Asset:
    asset = await session.get(Asset, asset_id)
    if asset is None or asset.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="image not found")
    return asset


@router.put("/assets/{asset_id}/cover-crop", response_model=schemas.CoverCrop)
async def set_cover_crop(
    asset_id: uuid.UUID,
    body: schemas.CoverCrop,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.CoverCrop:
    """Save where the cover photo is cut: a square on the processed image.

    Etsy's API takes no crop, so when this image is a listing's cover the
    cropped square itself is uploaded as the first photo (on draft creation
    and on "Replace images"); the uncropped image is not added separately.
    """
    asset = await _own_asset(session, tenant, asset_id)
    if asset.status is not AssetStatus.processed or not asset.width or not asset.height:
        raise HTTPException(status_code=422, detail="only an image that processed can be cropped")
    w, h = asset.width, asset.height
    shortest = min(w, h)
    if body.size > shortest + 1 or body.size < shortest / MAX_COVER_ZOOM - 1:
        raise HTTPException(status_code=422, detail=f"the square must be between 1/{MAX_COVER_ZOOM} of the image and all of it")
    if body.x + body.size > w + 1 or body.y + body.size > h + 1:
        raise HTTPException(status_code=422, detail="the square must lie within the image")
    size = min(body.size, shortest)
    crop = {"x": min(body.x, w - size), "y": min(body.y, h - size), "size": size, "width": w, "height": h}
    asset.cover_crop = crop
    await session.commit()
    return schemas.CoverCrop(**crop)


@router.delete("/assets/{asset_id}/cover-crop", status_code=204)
async def reset_cover_crop(
    asset_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> None:
    """Back to the automatic square."""
    asset = await _own_asset(session, tenant, asset_id)
    asset.cover_crop = None
    await session.commit()


# --- Following one of the seller's own listings (docs/duzeltmeler-v7.md §B) -------------
@router.put("/batches/{batch_id}/groups/pattern", response_model=list[schemas.GroupOut])
async def assign_group_pattern(
    batch_id: uuid.UUID,
    body: schemas.PatternAssign,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[schemas.GroupOut]:
    """Model one group's listing on one of the seller's OWN listings, or clear it.

    The listing must be in this account's own shop cache: another seller's
    listing, or another account's, is never accepted (ToU §1 and §5).
    """
    if not (tenant.features or {}).get("own_patterns"):
        raise HTTPException(status_code=404, detail="not available for this account")
    await _get_batch(session, tenant, batch_id)
    if body.pattern_listing_id is not None:
        own = await session.get(ShopListingCache, (tenant.id, body.pattern_listing_id))
        if own is None:
            raise HTTPException(status_code=404, detail="not one of your shop's listings")
    setting = (
        await session.execute(
            select(ListingGroupSetting).where(
                ListingGroupSetting.batch_id == batch_id,
                ListingGroupSetting.group_key == body.group_key,
            )
        )
    ).scalars().first()
    if setting is None:
        setting = ListingGroupSetting(tenant_id=tenant.id, batch_id=batch_id, group_key=body.group_key)
        session.add(setting)
    setting.pattern_listing_id = body.pattern_listing_id
    await session.commit()
    return await _batch_groups(session, batch_id)


async def _pattern_for(
    session: AsyncSession, tenant: Tenant, listing_id: int | None, enqueuer: Enqueuer
) -> tuple[dict[str, Any] | None, str | None]:
    """The example listing's title and tags from the seller's own cache, or why not."""
    if listing_id is None or not (tenant.features or {}).get("own_patterns"):
        return None, None
    row = await session.get(ShopListingCache, (tenant.id, listing_id))
    if row is None:
        return None, "the example listing is no longer in your shop; choose another"
    fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - fetched).total_seconds() >= ShopListingCache.STALE_SECONDS:
        await enqueuer.enqueue("sync_shop_listings", str(row.connection_id))
        return None, "your shop's listings are being refreshed from Etsy; generate again in a minute"
    payload = row.payload or {}
    return {
        "title": decode_etsy_text(payload.get("title")) or "",
        "tags": [str(t) for t in payload.get("tags") or []],
    }, None


# --- Deleting batches (docs/duzeltmeler-v7.md §E3) ---------------------------------
async def _delete_batch(
    session: AsyncSession, storage: Storage, tenant: Tenant, batch: UploadBatch, result: schemas.BatchDeleteResult
) -> None:
    """Delete one batch: its uploads, their derivatives and previews, its generated
    content and settings. Queued work for it is cancelled.

    What it does not delete: the drafts and live listings on Etsy, and the
    record that they were created and published (``listing_publication``, job
    history). Those records are detached from the content and kept; a schedule
    still waiting on one of them is cancelled with the batch, since there is no
    longer anything to review it against."""
    content_ids = select(GeneratedContent.id).where(GeneratedContent.batch_id == batch.id)
    kept = list(
        (await session.execute(select(ListingPublication).where(ListingPublication.content_id.in_(content_ids)))).scalars()
    )
    on_etsy = len(kept)
    for publication in kept:
        if publication.scheduled_for is not None and publication.state != "active":
            publication.scheduled_for, publication.schedule_job_id = None, None
            publication.schedule_note = "cancelled: its batch was deleted"
    files = await session.scalar(select(func.count()).select_from(Asset).where(Asset.batch_id == batch.id))
    queued = (
        await session.execute(
            select(Job).where(
                Job.tenant_id == tenant.id,
                Job.batch_id == batch.id,
                Job.status == JobStatus.queued,
            )
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for job in queued:
        job.status = JobStatus.cancelled
        job.finished_at = now
        job.last_error = "cancelled: the batch was deleted"
    audit.record(session, "batch.deleted", actor=tenant, target_tenant_id=tenant.id, batch_id=str(batch.id),
                 files=int(files or 0), publications_kept=on_etsy, jobs_cancelled=len(queued))
    # Assets, content and group settings cascade; publications are detached (content_id -> NULL).
    await session.delete(batch)
    await session.flush()
    storage.delete_prefix(f"{tenant.id}/{batch.id}")
    result.deleted += 1
    result.files_removed += int(files or 0)
    result.listings_left_on_etsy += int(on_etsy or 0)
    result.jobs_cancelled += len(queued)


@router.delete("/batches/{batch_id}", response_model=schemas.BatchDeleteResult)
async def delete_batch(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> schemas.BatchDeleteResult:
    batch = await _get_batch(session, tenant, batch_id)
    result = schemas.BatchDeleteResult()
    await _delete_batch(session, storage, tenant, batch, result)
    await session.commit()
    return result


@router.post("/batch-actions/delete", response_model=schemas.BatchDeleteResult)
async def delete_batches(
    body: schemas.BatchDeleteRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> schemas.BatchDeleteResult:
    """Delete several batches. Every one must be the caller's, or none is deleted."""
    batches = [await _get_batch(session, tenant, b) for b in dict.fromkeys(body.batch_ids)]
    result = schemas.BatchDeleteResult()
    for batch in batches:
        await _delete_batch(session, storage, tenant, batch, result)
    await session.commit()
    return result


# --- Deleting one image of a group ---------------------------------------------------
@router.delete("/assets/{asset_id}", response_model=schemas.ImageDeleteResult)
async def delete_image(
    asset_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> schemas.ImageDeleteResult:
    """Remove one image from its listing group: the upload, what was made from
    it, and its place in the order.

    The listing written for the group is about the design, not one photo, so it
    stays and moves to the new cover when the cover is the image deleted (the
    crop saved for the old cover goes with it). Deleting the group's last image
    removes the group and the listing written for it.

    Nothing on Etsy changes: a draft or live listing already made from the
    group keeps the photo. The deletion counts for drafts created from now on
    and for "Replace images".
    """
    asset = await _own_asset(session, tenant, asset_id)
    batch_id, key = asset.batch_id, asset.group_key
    members = sorted(
        (
            await session.execute(
                select(Asset).where(
                    Asset.batch_id == batch_id,
                    Asset.tenant_id == tenant.id,
                    Asset.group_key == key if key is not None else Asset.group_key.is_(None),
                )
            )
        ).scalars(),
        key=lambda a: (a.rank if a.rank is not None else 10_000, a.original_filename),
    )
    was_cover = members[0].id == asset.id
    remaining = [a for a in members if a.id != asset.id]
    contents = list(
        (await session.execute(select(GeneratedContent).where(GeneratedContent.asset_id.in_([a.id for a in members])))).scalars()
    )
    content_ids = [c.id for c in contents]
    publications = list(
        (await session.execute(select(ListingPublication).where(ListingPublication.content_id.in_(content_ids)))).scalars()
    ) if content_ids else []

    if remaining:
        # The cover must be an image that processed: the first such image leads.
        usable = [a for a in remaining if a.status is AssetStatus.processed and a.processed_key]
        if usable and remaining[0] is not usable[0]:
            remaining.remove(usable[0])
            remaining.insert(0, usable[0])
        cover = remaining[0]
        for rank, member in enumerate(remaining, start=1):
            member.rank = rank
        for content in contents:  # before the image goes, or the listing would go with it
            content.asset_id = cover.id
        if was_cover:
            cover.cover_crop = None  # a crop belongs to the photo it was made on
    else:
        # The last image: the group goes, and the listing written for it. What was
        # created on Etsy from it stays there and stays on record (detached).
        now = datetime.now(timezone.utc)
        for publication in publications:
            if publication.scheduled_for is not None and publication.state != "active":
                publication.scheduled_for, publication.schedule_job_id = None, None
                publication.schedule_note = "cancelled: its images were deleted"
        if content_ids:
            wanted = {str(c) for c in content_ids}
            for job in (
                await session.execute(
                    select(Job).where(Job.tenant_id == tenant.id, Job.batch_id == batch_id, Job.status == JobStatus.queued)
                )
            ).scalars():
                if (job.payload or {}).get("content_id") in wanted:
                    job.status, job.finished_at = JobStatus.cancelled, now
                    job.last_error = "cancelled: the group's images were deleted"
        setting = (
            await session.execute(
                select(ListingGroupSetting).where(
                    ListingGroupSetting.batch_id == batch_id, ListingGroupSetting.group_key == (key or "")
                )
            )
        ).scalar_one_or_none()
        if setting is not None:
            await session.delete(setting)

    stored = [k for k in (asset.storage_key, asset.processed_key) if k]
    audit.record(session, "image.deleted", actor=tenant, target_tenant_id=tenant.id, batch_id=str(batch_id),
                 group_removed=not remaining, was_cover=was_cover, listings_on_etsy=len(publications))
    await session.delete(asset)
    await session.commit()
    for stored_key in dict.fromkeys(stored):
        storage.delete_with_derivatives(stored_key)
    return schemas.ImageDeleteResult(
        batch=await get_batch(batch_id, session, tenant),
        group_key=key or "",
        group_removed=not remaining,
        cover_changed=was_cover and bool(remaining),
        listings_on_etsy=len(publications),
    )


# --- Image order and cover (docs/duzeltmeler-v6.md §E) ----------------------------
@router.put("/batches/{batch_id}/groups/order", response_model=schemas.BatchDetail)
async def order_group(
    batch_id: uuid.UUID,
    body: schemas.GroupOrder,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.BatchDetail:
    """Save one group's image order as ``rank``; the first image is the cover.

    Etsy receives the images in this order, the cover first. Content already
    written for the group moves to the new cover, so the review page and the
    draft both lead with it (the text is about the design, not one photo).
    """
    await _get_batch(session, tenant, batch_id)
    key = body.group_key or None
    rows = await session.execute(
        select(Asset).where(
            Asset.batch_id == batch_id,
            Asset.tenant_id == tenant.id,
            Asset.group_key == key if key is not None else Asset.group_key.is_(None),
        )
    )
    members = {a.id: a for a in rows.scalars()}
    if not members:
        raise HTTPException(status_code=404, detail="group not found")
    if len(body.asset_ids) != len(members) or set(body.asset_ids) != set(members):
        raise HTTPException(
            status_code=422, detail="send every image of the group exactly once, in the new order"
        )
    cover = members[body.asset_ids[0]]
    if cover.status is not AssetStatus.processed or cover.processed_key is None:
        raise HTTPException(status_code=422, detail="the cover must be an image that processed")
    for rank, asset_id in enumerate(body.asset_ids, start=1):
        members[asset_id].rank = rank
    contents = await session.execute(
        select(GeneratedContent).where(GeneratedContent.asset_id.in_(list(members)))
    )
    for content in contents.scalars():
        content.asset_id = cover.id
    await session.commit()
    return await get_batch(batch_id, session, tenant)


# --- Content generation -----------------------------------------------------
@router.post("/batches/{batch_id}/generate", response_model=schemas.GenerateResult)
async def generate_content(
    batch_id: uuid.UUID,
    body: schemas.GenerateRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.GenerateResult:
    # Ownership first: a caller with no claim on this batch must learn nothing
    # about it, not even whether the service is configured (production-spec B3).
    await _get_batch(session, tenant, batch_id)
    if body.profile_id is not None:
        requested = await session.get(ListingProfile, body.profile_id)
        if requested is None or requested.tenant_id != tenant.id:
            raise HTTPException(status_code=404, detail="profile not found")

    settings = get_settings()
    if not settings.llm_api_key:
        raise HTTPException(
            status_code=503,
            detail="LLM_API_KEY is not configured; content generation is unavailable.",
        )
    # The product allowance: a listing is only written while some is left, and
    # the seller is told when it resets (checked where a group needs writing, so
    # "for all" with nothing to write isn't refused).
    allowance_now = await allowance.status(session, tenant)
    allowance_left = allowance_now.remaining

    # Image analysis and listing text may run on different models (v7 §A2).
    analyzer = AnthropicVisionAnalyzer(client_for(settings, "vision"))
    client = client_for(settings, "content")

    # Per-group profile selection (v4 §E): each group uses its own assigned profile,
    # falling back to the batch-level default in the request body.
    group_rows = await session.execute(
        select(ListingGroupSetting).where(ListingGroupSetting.batch_id == batch_id)
    )
    group_settings = list(group_rows.scalars())
    group_profile = {s.group_key: s.profile_id for s in group_settings}
    group_pattern = {s.group_key: s.pattern_listing_id for s in group_settings}
    profile_cache: dict[uuid.UUID, ListingProfile] = {}
    generator_cache: dict[uuid.UUID, AnthropicContentGenerator] = {}

    async def _profile(profile_id: uuid.UUID) -> ListingProfile | None:
        if profile_id not in profile_cache:
            p = await session.get(ListingProfile, profile_id)
            profile_cache[profile_id] = p if p and p.tenant_id == tenant.id else None
        return profile_cache[profile_id]

    trademarks = blocklist_for_tenant(tenant)  # the account's setting (v7 §A4)

    def _generator(p: ListingProfile) -> AnthropicContentGenerator:
        if p.id not in generator_cache:
            generator_cache[p.id] = AnthropicContentGenerator(
                client,
                template=load_template(content_template_for(p)),
                policy=policy_for(p.content_template),
                title_prefix=p.title_prefix or "",
                trademarks=trademarks,
                **search_style(p),
            )
        return generator_cache[p.id]

    # One folder group = one listing (D1). Generate once per group, from its
    # primary (rank-1, alphabetically-first) image; the whole group's images are
    # attached at publish time. A group that already has content is skipped,
    # unless this is "Regenerate" (``replace``).
    existing_rows = await session.execute(
        select(GeneratedContent).where(
            GeneratedContent.batch_id == batch_id, GeneratedContent.tenant_id == tenant.id
        )
    )
    content_by_asset: dict[uuid.UUID, list[GeneratedContent]] = {}
    for c in existing_rows.scalars():
        content_by_asset.setdefault(c.asset_id, []).append(c)
    on_etsy = set(
        (
            await session.execute(
                select(ListingPublication.content_id).where(
                    ListingPublication.content_id.in_(
                        [c.id for cs in content_by_asset.values() for c in cs]
                    )
                )
            )
        ).scalars()
    )
    rows = await session.execute(
        select(Asset).where(
            Asset.batch_id == batch_id, Asset.status == AssetStatus.processed
        )
    )
    groups: dict[str, list[Asset]] = {}
    for asset in rows.scalars():
        if asset.processed_key is not None:
            groups.setdefault(asset.group_key or "", []).append(asset)
    if body.group_key is not None:  # per-group action (D3)
        groups = {body.group_key: groups.get(body.group_key, [])}

    generated = failed = skipped = 0
    failures: list[schemas.AssetFailure] = []
    skipped_groups: list[schemas.GroupSkipped] = []
    for key, members in groups.items():
        if not members:
            continue
        primary = min(
            members,
            key=lambda a: (a.rank if a.rank is not None else 1_000_000, a.original_filename.lower()),
        )
        old = [c for m in members for c in content_by_asset.get(m.id, [])]
        if old:
            if not body.replace:  # generating "for all": groups with content are done
                skipped += 1
                continue
            reason = None
            if any(c.id in on_etsy for c in old):
                # Its draft is on Etsy: new text here would not reach it. Replace
                # images on Etsy changes the listing itself.
                reason = "its draft is already on Etsy; use Replace images to change it there"
            elif any(c.approved for c in old) and not body.replace_approved:
                reason = "its content is approved; confirm to replace it"
            if reason is not None:
                skipped += 1
                skipped_groups.append(schemas.GroupSkipped(group_key=key, reason=reason))
                continue

        def _fail(reason: str) -> None:
            nonlocal failed
            failed += 1
            failures.append(
                schemas.AssetFailure(
                    asset_id=primary.id,
                    original_filename=primary.original_filename,
                    error=reason,
                )
            )

        profile_id = group_profile.get(key) or body.profile_id
        if profile_id is None:
            _fail("no profile selected for this group")
            continue
        profile = await _profile(profile_id)
        if profile is None:
            _fail("profile not found")
            continue
        if not _profile_is_fresh(profile):
            # Not used lately, so not kept warm: fetch it now (etsy/refresh.py).
            await request_refresh(enqueuer.enqueue, profile, origin="use")
            _fail(
                "this profile's reference is being refreshed from Etsy now; "
                "generate again in a minute"
            )
            continue

        pattern, why = await _pattern_for(session, tenant, group_pattern.get(key), enqueuer)
        if why:
            _fail(why)
            continue

        if allowance_left <= 0:
            if body.group_key is not None:  # one group asked for: refuse it outright
                raise HTTPException(status_code=429, detail=allowance_now.message())
            _fail(allowance_now.message())
            continue

        data = storage.get(primary.processed_key)
        outcome = await generate_listing_content(
            session,
            tenant_id=tenant.id,
            batch_id=batch_id,
            asset_id=primary.id,
            image_data=data,
            media_type=primary.mime_type or "image/jpeg",
            sku=primary.parsed_sku,
            analyzer=analyzer,
            generator=_generator(profile),
            profile=profile,
            pattern=pattern,
        )
        if outcome.status == "generated":
            generated += 1
            allowance.record(session, tenant.id, allowance.GENERATION)
            allowance_left -= 1
            await session.commit()
            if old:
                await _retire(session, old, outcome.generated_content_id)
        else:
            failed += 1
            failures.append(
                schemas.AssetFailure(
                    asset_id=primary.id,
                    original_filename=primary.original_filename,
                    error=outcome.error or "unknown error",
                )
            )

    return schemas.GenerateResult(
        generated=generated,
        failed=failed,
        skipped=skipped,
        failures=failures,
        skipped_groups=skipped_groups,
    )


async def _retire(
    session: AsyncSession, old: list[GeneratedContent], new_id: uuid.UUID | None
) -> None:
    """Remove the content a regenerate replaced, once the new one is saved.

    Its token counts move to the new content, so the batch's cost still shows
    what was spent (a replacement is a second LLM call, not a free one). Only
    content with no draft on Etsy gets here.
    """
    new = await session.get(GeneratedContent, new_id) if new_id else None
    for c in old:
        if new is not None:
            new.input_tokens = (new.input_tokens or 0) + (c.input_tokens or 0)
            new.output_tokens = (new.output_tokens or 0) + (c.output_tokens or 0)
        await session.delete(c)
    await session.commit()


# --- Cost -------------------------------------------------------------------
@router.get("/batches/{batch_id}/cost", response_model=schemas.BatchCostOut)
async def batch_cost(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    calc: CostCalculator = Depends(get_cost_calculator),
) -> schemas.BatchCostOut:
    await _get_batch(session, tenant, batch_id)
    rows = await session.execute(
        select(GeneratedContent).where(GeneratedContent.batch_id == batch_id)
    )
    listings: list[schemas.ListingCost] = []
    total_in = 0
    total_out = 0
    total_cost = Decimal("0")
    for content in rows.scalars():
        model = content.model_used
        input_tokens = content.input_tokens or 0
        output_tokens = content.output_tokens or 0
        try:
            usage = _usage(model, input_tokens, output_tokens)
            cost = calc.cost_for(usage)
        except UnknownModelError:
            cost = Decimal("0")
        total_cost += cost
        total_in += input_tokens
        total_out += output_tokens
        listings.append(
            schemas.ListingCost(
                content_id=content.id,
                asset_id=content.asset_id,
                model_used=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=f"{cost:.6f}",
            )
        )
    return schemas.BatchCostOut(
        listing_count=len(listings),
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_cost_usd=f"{total_cost:.6f}",
        listings=listings,
    )


def _usage(model: str | None, input_tokens: int, output_tokens: int):
    from app.pipeline.llm import Usage

    return Usage(model=model or "", input_tokens=input_tokens, output_tokens=output_tokens)
