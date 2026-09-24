"""Generated-content review: read, inline edit, and approve."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import active_tenant, get_session
from app.db.models import (
    Asset,
    EtsyConnection,
    GeneratedContent,
    ListingProfile,
    ListingPublication,
    Tenant,
    UploadBatch,
)
from app.etsy.manual_fields import manual_fields_for
from app.etsy.publisher import link_for
from app.pipeline.content import GeneratedListing, policy_for, validate_listing

router = APIRouter(prefix="/api", tags=["content"])


async def publications(
    session: AsyncSession, content_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[schemas.PublicationOut]]:
    """Each content's drafts, one per shop (v5 §E), with the shop's name and link."""
    from app.api.shops import shop_label

    found: dict[uuid.UUID, list[schemas.PublicationOut]] = {i: [] for i in content_ids}
    if not content_ids:
        return found
    rows = await session.execute(
        select(ListingPublication, EtsyConnection, ListingProfile.content_template)
        .join(EtsyConnection, EtsyConnection.id == ListingPublication.connection_id)
        .outerjoin(ListingProfile, ListingProfile.id == ListingPublication.profile_id)
        .where(ListingPublication.content_id.in_(content_ids))
        .order_by(EtsyConnection.position, EtsyConnection.connected_at)
    )
    for publication, connection, template in rows.all():
        found[publication.content_id].append(publication_out(publication, connection, template))
    return found


def publication_out(
    publication: ListingPublication, connection: EtsyConnection, template: str | None
) -> schemas.PublicationOut:
    from app.api.shops import shop_label

    return schemas.PublicationOut(
        connection_id=connection.id,
        shop_name=shop_label(connection),
        etsy_listing_id=publication.etsy_listing_id,
        state=publication.state,
        listing_link=link_for(publication.etsy_listing_id, publication.state),
        manual_steps=manual_steps(publication.state, template, publication.manual_done_keys()),
    )


def manual_steps(
    state: str, template: str | None, done: set[str] | frozenset[str] = frozenset()
) -> list[schemas.ManualStepOut]:
    """What a draft needs in Shop Manager, each with the seller's tick; nothing once live."""
    if state == "active":
        return []
    return [
        schemas.ManualStepOut(key=f.key, label=f.label, detail=f.detail, done=f.key in done)
        for f in manual_fields_for(template)
    ]


async def _template_of(session: AsyncSession, publication: ListingPublication) -> str | None:
    if publication.profile_id is None:
        return None
    profile = await session.get(ListingProfile, publication.profile_id)
    return profile.content_template if profile is not None else None


@router.put(
    "/content/{content_id}/publications/{connection_id}/manual-steps/{key}",
    response_model=schemas.PublicationOut,
)
async def tick_manual_step(
    content_id: uuid.UUID,
    connection_id: uuid.UUID,
    key: str,
    body: schemas.ManualStepTick,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.PublicationOut:
    """The seller confirms (or un-confirms) making one setting on one draft by hand."""
    content = await _get(session, tenant, content_id)
    rows = await session.execute(
        select(ListingPublication, EtsyConnection)
        .join(EtsyConnection, EtsyConnection.id == ListingPublication.connection_id)
        .where(
            ListingPublication.content_id == content.id,
            ListingPublication.connection_id == connection_id,
            ListingPublication.tenant_id == tenant.id,
        )
    )
    found = rows.first()
    if found is None:
        raise HTTPException(status_code=404, detail="draft not found")
    publication, connection = found
    template = await _template_of(session, publication)
    if key not in {f.key for f in manual_fields_for(template)}:
        raise HTTPException(status_code=404, detail="no such setting for this draft")
    ticks = dict(publication.manual_done or {})  # a new dict, so the change is saved
    if body.done:
        ticks[key] = publication.etsy_listing_id
    else:
        ticks.pop(key, None)
    publication.manual_done = ticks
    await session.commit()
    return publication_out(publication, connection, template)


@router.post("/batches/{batch_id}/manual-steps/done", response_model=schemas.ManualStepsDone)
async def mark_manual_steps_done(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ManualStepsDone:
    """'Mark all as done': every setting on every approved draft of this batch.

    For a seller who set them in Shop Manager for the whole batch at once, rather
    than ticking each draft. Live listings and unapproved content are left alone.
    """
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    rows = await session.execute(
        select(ListingPublication)
        .join(GeneratedContent, GeneratedContent.id == ListingPublication.content_id)
        .where(
            GeneratedContent.batch_id == batch_id,
            GeneratedContent.tenant_id == tenant.id,
            GeneratedContent.approved.is_(True),
            ListingPublication.state != "active",
        )
    )
    updated = 0
    for publication in rows.scalars():
        keys = {f.key for f in manual_fields_for(await _template_of(session, publication))}
        missing = keys - publication.manual_done_keys()
        if not missing:
            continue
        ticks = dict(publication.manual_done or {})
        ticks.update({k: publication.etsy_listing_id for k in keys})
        publication.manual_done = ticks
        updated += 1
    await session.commit()
    return schemas.ManualStepsDone(updated_drafts=updated)


async def _profile_shops(
    session: AsyncSession, contents: list[GeneratedContent]
) -> dict[uuid.UUID, uuid.UUID]:
    ids = {c.listing_profile_id for c in contents if c.listing_profile_id}
    if not ids:
        return {}
    rows = await session.execute(
        select(ListingProfile.id, ListingProfile.connection_id).where(ListingProfile.id.in_(ids))
    )
    return {pid: cid for pid, cid in rows.all()}


async def contents_out(
    session: AsyncSession, pairs: list[tuple[GeneratedContent, Asset]]
) -> list[schemas.ContentOut]:
    pubs = await publications(session, [c.id for c, _ in pairs])
    shops = await _profile_shops(session, [c for c, _ in pairs])
    return [
        _to_out(c, a, pubs[c.id], shops.get(c.listing_profile_id)) for c, a in pairs
    ]


def _to_out(
    content: GeneratedContent,
    asset: Asset,
    pubs: list[schemas.PublicationOut] | None = None,
    connection_id: uuid.UUID | None = None,
) -> schemas.ContentOut:
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
        connection_id=connection_id,
        publications=pubs or [],
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
    return await contents_out(session, [(content, asset) for content, asset in rows.all()])


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
        content=(await contents_out(session, [(content, asset)]))[0], validation=await _validation(session, content)
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
        content=(await contents_out(session, [(content, asset)]))[0], validation=validation
    )
