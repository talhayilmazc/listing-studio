"""Batch, asset, generation, and cost endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import current_tenant, get_cost_calculator, get_ingestor, get_session, get_storage
from app.core.config import get_settings
from app.db.models import (
    Asset,
    AssetStatus,
    GeneratedContent,
    ListingGroupSetting,
    ListingProfile,
    Tenant,
    UploadBatch,
)
from app.pipeline.content import AnthropicContentGenerator, policy_for
from app.pipeline.cost import CostCalculator, UnknownModelError
from app.pipeline.generation import generate_listing_content
from app.pipeline.images import PREVIEW_WIDTHS, ImageProcessingError, resize_preview
from app.pipeline.ingest import BatchIngestor, UploadFile as IngestFile
from app.pipeline.llm import AnthropicLLMClient
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
    return schemas.BatchSummary(
        id=batch.id,
        status=batch.status.value,
        file_count=batch.file_count,
        created_at=batch.created_at,
        asset_count=int(asset_count or 0),
        processed_count=int(processed_count or 0),
        approved_count=int(approved_count or 0),
        size_chart_profile_id=batch.size_chart_profile_id,
    )


async def _get_batch(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID) -> UploadBatch:
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    return batch


# --- Upload flow ------------------------------------------------------------
@router.post("/batches", response_model=schemas.BatchSummary, status_code=201)
async def create_batch(
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
    ingestor: BatchIngestor = Depends(get_ingestor),
) -> schemas.BatchSummary:
    batch = await ingestor.create_batch(session, tenant.id)
    await session.commit()
    await session.refresh(batch)
    return await _summary(session, batch)


@router.post("/batches/{batch_id}/assets", response_model=schemas.AssetOut, status_code=201)
async def add_asset(
    batch_id: uuid.UUID,
    file: UploadFile,
    group_key: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
    ingestor: BatchIngestor = Depends(get_ingestor),
) -> schemas.AssetOut:
    await _get_batch(session, tenant, batch_id)
    data = await file.read()
    asset = await ingestor.add_file(
        session,
        batch_id,
        tenant.id,
        IngestFile(filename=file.filename or "upload", data=data),
        group_key=group_key or None,
    )
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


@router.put("/batches/{batch_id}/size-chart-profile", response_model=schemas.BatchSummary)
async def set_size_chart_profile(
    batch_id: uuid.UUID,
    body: schemas.SizeChartProfileUpdate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
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
    tenant: Tenant = Depends(current_tenant),
    ingestor: BatchIngestor = Depends(get_ingestor),
) -> schemas.BatchSummary:
    await _get_batch(session, tenant, batch_id)
    batch = await ingestor.finalize_batch(session, batch_id)
    return await _summary(session, batch)


# --- Reading batches --------------------------------------------------------
@router.get("/batches", response_model=list[schemas.BatchSummary])
async def list_batches(
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
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
    tenant: Tenant = Depends(current_tenant),
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
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    asset = await session.get(Asset, asset_id)
    if asset is None or asset.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="asset not found")
    key = asset.processed_key or asset.storage_key

    # Additive preview path: only taken when ?w= is supplied. Widths are limited to
    # an allowlist so an arbitrary ?w= cannot force unbounded resize work, and each
    # result is cached beside its source so the resize runs once per asset per width.
    if w is not None:
        if w not in PREVIEW_WIDTHS:
            raise HTTPException(
                status_code=400, detail=f"w must be one of {sorted(PREVIEW_WIDTHS)}"
            )
        cache_key = f"{key}.w{w}.jpg"
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
            preview = resize_preview(source, w)
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


# --- Per-group profile selection (v4 §E) -----------------------------------
async def _batch_groups(
    session: AsyncSession, batch_id: uuid.UUID
) -> list[schemas.GroupOut]:
    rows = await session.execute(select(Asset).where(Asset.batch_id == batch_id))
    assets = list(rows.scalars())
    with_content = await _content_asset_ids(session, batch_id)
    setting_rows = await session.execute(
        select(ListingGroupSetting).where(ListingGroupSetting.batch_id == batch_id)
    )
    group_settings = {s.group_key: s for s in setting_rows.scalars()}

    grouped: dict[str, list[Asset]] = {}
    for asset in assets:
        grouped.setdefault(asset.group_key or "", []).append(asset)

    out: list[schemas.GroupOut] = []
    for key in sorted(grouped):
        members = grouped[key]
        s = group_settings.get(key)
        out.append(
            schemas.GroupOut(
                group_key=key,
                sku=next((m.parsed_sku for m in members if m.parsed_sku), None),
                image_count=len(members),
                has_content=any(m.id in with_content for m in members),
                profile_id=s.profile_id if s else None,
                size_chart_profile_id=s.size_chart_profile_id if s else None,
                manual=s.manual if s else False,
            )
        )
    return out


@router.get("/batches/{batch_id}/groups", response_model=list[schemas.GroupOut])
async def list_groups(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> list[schemas.GroupOut]:
    await _get_batch(session, tenant, batch_id)
    return await _batch_groups(session, batch_id)


@router.put("/batches/{batch_id}/groups", response_model=list[schemas.GroupOut])
async def assign_group_profile(
    batch_id: uuid.UUID,
    body: schemas.GroupAssign,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
) -> list[schemas.GroupOut]:
    """Assign a profile (and size-chart profile) to one group, or bulk-apply to all.

    With a ``group_key`` it sets that group explicitly (``manual``); without one it
    applies to every group the seller hasn't set manually, without clobbering the
    field it didn't provide (v4 §E).
    """
    await _get_batch(session, tenant, batch_id)
    for pid in (body.profile_id, body.size_chart_profile_id):
        if pid is not None:
            p = await session.get(ListingProfile, pid)
            if p is None or p.tenant_id != tenant.id:
                raise HTTPException(status_code=404, detail="profile not found")

    existing = {
        s.group_key: s
        for s in (
            await session.execute(
                select(ListingGroupSetting).where(ListingGroupSetting.batch_id == batch_id)
            )
        ).scalars()
    }

    if body.group_key is not None:
        keys = [body.group_key]
    else:
        all_keys = {
            (a.group_key or "")
            for a in (
                await session.execute(select(Asset).where(Asset.batch_id == batch_id))
            ).scalars()
        }
        keys = [k for k in all_keys if not (existing.get(k) and existing[k].manual)]

    for key in keys:
        setting = existing.get(key)
        if setting is None:
            setting = ListingGroupSetting(tenant_id=tenant.id, batch_id=batch_id, group_key=key)
            session.add(setting)
            existing[key] = setting
        if body.group_key is not None:
            # Explicit single-group set: apply exactly what was sent (clearing allowed).
            setting.profile_id = body.profile_id
            setting.size_chart_profile_id = body.size_chart_profile_id
            setting.manual = True
        else:
            # Bulk: fill only the fields provided; never clobber the other.
            if body.profile_id is not None:
                setting.profile_id = body.profile_id
            if body.size_chart_profile_id is not None:
                setting.size_chart_profile_id = body.size_chart_profile_id
    await session.commit()
    return await _batch_groups(session, batch_id)


# --- Content generation -----------------------------------------------------
@router.post("/batches/{batch_id}/generate", response_model=schemas.GenerateResult)
async def generate_content(
    batch_id: uuid.UUID,
    body: schemas.GenerateRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
    storage: Storage = Depends(get_storage),
) -> schemas.GenerateResult:
    settings = get_settings()
    if not settings.llm_api_key:
        raise HTTPException(
            status_code=503,
            detail="LLM_API_KEY is not configured; content generation is unavailable.",
        )
    await _get_batch(session, tenant, batch_id)

    client = AnthropicLLMClient(api_key=settings.llm_api_key, model=settings.llm_model)
    analyzer = AnthropicVisionAnalyzer(client)

    # Per-group profile selection (v4 §E): each group uses its own assigned profile,
    # falling back to the batch-level default in the request body.
    group_rows = await session.execute(
        select(ListingGroupSetting).where(ListingGroupSetting.batch_id == batch_id)
    )
    group_profile = {s.group_key: s.profile_id for s in group_rows.scalars()}
    profile_cache: dict[uuid.UUID, ListingProfile] = {}
    generator_cache: dict[uuid.UUID, AnthropicContentGenerator] = {}

    async def _profile(profile_id: uuid.UUID) -> ListingProfile | None:
        if profile_id not in profile_cache:
            p = await session.get(ListingProfile, profile_id)
            profile_cache[profile_id] = p if p and p.tenant_id == tenant.id else None
        return profile_cache[profile_id]

    def _generator(p: ListingProfile) -> AnthropicContentGenerator:
        if p.id not in generator_cache:
            generator_cache[p.id] = AnthropicContentGenerator(
                client,
                template=load_template(f"content/{p.content_template}"),
                policy=policy_for(p.content_template),
                title_prefix=p.title_prefix or "",
            )
        return generator_cache[p.id]

    # One folder group = one listing (D1). Generate once per group, from its
    # primary (rank-1, alphabetically-first) image; the whole group's images are
    # attached at publish time. A group whose primary already has content is skipped.
    already = await _content_asset_ids(session, batch_id)
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
    for key, members in groups.items():
        if not members:
            continue
        primary = min(
            members,
            key=lambda a: (a.rank if a.rank is not None else 1_000_000, a.original_filename.lower()),
        )
        if primary.id in already:
            skipped += 1
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
            _fail("profile has no fresh reference data; refresh it first")
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
        )
        if outcome.status == "generated":
            generated += 1
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
        generated=generated, failed=failed, skipped=skipped, failures=failures
    )


# --- Cost -------------------------------------------------------------------
@router.get("/batches/{batch_id}/cost", response_model=schemas.BatchCostOut)
async def batch_cost(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
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
