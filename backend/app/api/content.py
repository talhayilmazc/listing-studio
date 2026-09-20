"""Generated-content review: read, inline edit, and approve."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import active_tenant, get_session
from app.db.models import Asset, GeneratedContent, ListingProfile, Tenant, UploadBatch
from app.etsy.publisher import link_for
from app.pipeline.content import GeneratedListing, policy_for, validate_listing

router = APIRouter(prefix="/api", tags=["content"])


def _to_out(content: GeneratedContent, asset: Asset) -> schemas.ContentOut:
    return schemas.ContentOut(
        id=content.id,
        asset_id=content.asset_id,
        title=content.title,
        tags=list(content.tags or []),
        description=content.description,
        approved=content.approved,
        model_used=content.model_used,
        input_tokens=content.input_tokens,
        output_tokens=content.output_tokens,
        etsy_listing_id=content.etsy_listing_id,
        etsy_listing_state=content.etsy_listing_state,
        listing_link=(
            link_for(content.etsy_listing_id, content.etsy_listing_state)
            if content.etsy_listing_id
            else None
        ),
        original_filename=asset.original_filename,
        parsed_sku=asset.parsed_sku,
        rank=asset.rank,
    )


async def _validation(
    session: AsyncSession, content: GeneratedContent
) -> schemas.ValidationInfo:
    # Apply the product-type policy from the content's profile (apparel bans
    # "digital download" etc.; digital-products does not).
    policy = None
    if content.listing_profile_id is not None:
        profile = await session.get(ListingProfile, content.listing_profile_id)
        if profile is not None:
            policy = policy_for(profile.content_template)
    listing = GeneratedListing(
        title=content.title or "",
        tags=list(content.tags or []),
        description=content.description or "",
    )
    errors = validate_listing(listing, policy)
    return schemas.ValidationInfo(valid=not errors, errors=errors)


async def _get(session: AsyncSession, tenant: Tenant, content_id: uuid.UUID) -> GeneratedContent:
    content = await session.get(GeneratedContent, content_id)
    if content is None or content.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="content not found")
    return content


@router.get("/batches/{batch_id}/content", response_model=list[schemas.ContentOut])
async def list_batch_content(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[schemas.ContentOut]:
    # A batch the caller does not own must look exactly like one that does not
    # exist (B3). The query below is tenant-filtered too, so nothing leaks either
    # way, but an empty 200 would still confirm the id is well-formed and absent.
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")

    rows = await session.execute(
        select(GeneratedContent, Asset)
        .join(Asset, Asset.id == GeneratedContent.asset_id)
        .where(GeneratedContent.batch_id == batch_id, GeneratedContent.tenant_id == tenant.id)
        .order_by(Asset.rank)
    )
    return [_to_out(content, asset) for content, asset in rows.all()]


@router.patch("/content/{content_id}", response_model=schemas.ContentUpdateResult)
async def update_content(
    content_id: uuid.UUID,
    body: schemas.ContentUpdate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ContentUpdateResult:
    content = await _get(session, tenant, content_id)
    if body.title is not None:
        content.title = body.title
    if body.tags is not None:
        content.tags = body.tags
    if body.description is not None:
        content.description = body.description
    await session.commit()
    await session.refresh(content)
    asset = await session.get(Asset, content.asset_id)
    return schemas.ContentUpdateResult(
        content=_to_out(content, asset), validation=await _validation(session, content)
    )


@router.post("/content/{content_id}/approve", response_model=schemas.ContentUpdateResult)
async def approve_content(
    content_id: uuid.UUID,
    body: schemas.ApproveUpdate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ContentUpdateResult:
    content = await _get(session, tenant, content_id)
    validation = await _validation(session, content)
    if body.approved and not validation.valid:
        raise HTTPException(
            status_code=422,
            detail={"message": "cannot approve invalid content", "errors": validation.errors},
        )
    content.approved = body.approved
    await session.commit()
    await session.refresh(content)
    asset = await session.get(Asset, content.asset_id)
    return schemas.ContentUpdateResult(
        content=_to_out(content, asset), validation=validation
    )
