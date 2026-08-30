"""Batch, asset, generation, and cost endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import current_tenant, get_cost_calculator, get_ingestor, get_session, get_storage
from app.core.config import get_settings
from app.db.models import (
    Asset,
    AssetStatus,
    GeneratedContent,
    ListingProfile,
    Tenant,
    UploadBatch,
)
from app.pipeline.content import AnthropicContentGenerator
from app.pipeline.cost import CostCalculator, UnknownModelError
from app.pipeline.generation import generate_listing_content
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
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
    ingestor: BatchIngestor = Depends(get_ingestor),
) -> schemas.AssetOut:
    await _get_batch(session, tenant, batch_id)
    data = await file.read()
    asset = await ingestor.add_file(
        session, batch_id, tenant.id, IngestFile(filename=file.filename or "upload", data=data)
    )
    return schemas.AssetOut(
        id=asset.id,
        original_filename=asset.original_filename,
        parsed_sku=asset.parsed_sku,
        rank=asset.rank,
        status=asset.status.value,
        mime_type=asset.mime_type,
        width=asset.width,
        height=asset.height,
        has_content=False,
        error=asset.error,
    )


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


@router.get("/assets/{asset_id}/image")
async def get_asset_image(
    asset_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(current_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    asset = await session.get(Asset, asset_id)
    if asset is None or asset.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="asset not found")
    key = asset.processed_key or asset.storage_key
    try:
        data = storage.get(key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="image not found") from exc
    return Response(content=data, media_type=asset.mime_type or "application/octet-stream")


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

    # A reference-listing profile is required and must have a fresh cached payload.
    profile = await session.get(ListingProfile, body.profile_id)
    if profile is None or profile.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="profile not found")
    if not _profile_is_fresh(profile):
        raise HTTPException(
            status_code=409,
            detail="profile has no fresh reference data; refresh the profile first",
        )

    client = AnthropicLLMClient(api_key=settings.llm_api_key, model=settings.llm_model)
    analyzer = AnthropicVisionAnalyzer(client)
    generator = AnthropicContentGenerator(
        client, template=load_template(f"content/{profile.content_template}")
    )

    already = await _content_asset_ids(session, batch_id)
    rows = await session.execute(
        select(Asset).where(
            Asset.batch_id == batch_id, Asset.status == AssetStatus.processed
        )
    )
    assets = [a for a in rows.scalars() if a.id not in already]

    generated = failed = 0
    failures: list[schemas.AssetFailure] = []
    for asset in assets:
        if asset.processed_key is None:
            continue
        data = storage.get(asset.processed_key)
        outcome = await generate_listing_content(
            session,
            tenant_id=tenant.id,
            batch_id=batch_id,
            asset_id=asset.id,
            image_data=data,
            media_type=asset.mime_type or "image/jpeg",
            sku=asset.parsed_sku,
            analyzer=analyzer,
            generator=generator,
            profile=profile,
        )
        if outcome.status == "generated":
            generated += 1
        else:
            failed += 1
            failures.append(
                schemas.AssetFailure(
                    asset_id=asset.id,
                    original_filename=asset.original_filename,
                    error=outcome.error or "unknown error",
                )
            )

    return schemas.GenerateResult(
        generated=generated, failed=failed, skipped=len(already), failures=failures
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
