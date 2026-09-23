"""Reference-listing profile management (Section B).

A profile copies category/price/variations/description from one of the seller's
own listings. Fetching the reference runs through the queue (``refresh_profile``
job); the endpoints here only read/write the profile row and enqueue that work.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session
from app.db.models import ListingProfile, Tenant

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


def _payload_age(profile: ListingProfile) -> float | None:
    """Seconds since the reference was fetched, or None if it holds nothing."""
    if not profile.cached_payload or profile.updated_at is None:
        return None
    updated = profile.updated_at
    if updated.tzinfo is None:  # SQLite returns naive; treat as UTC.
        updated = updated.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - updated).total_seconds()


def _is_fresh(profile: ListingProfile) -> bool:
    """Structural reference data is within its 24-hour limit."""
    age = _payload_age(profile)
    return age is not None and age < ListingProfile.CACHE_MAX_AGE_SECONDS


def _images_displayable(profile: ListingProfile) -> bool:
    """Reference image links are within the 6-hour display limit."""
    age = _payload_age(profile)
    return age is not None and age < ListingProfile.DISPLAY_MAX_AGE_SECONDS


def _to_out(profile: ListingProfile) -> schemas.ProfileOut:
    payload = profile.cached_payload or {}
    fixed = set(profile.fixed_image_ids or [])
    # Past 6 hours the links are withheld even before retention strips them, so
    # nothing expired is ever displayed. The ids and classifications remain, so
    # the card can still show which size charts are selected.
    displayable = _images_displayable(profile)
    images = [
        schemas.ReferenceImageOut(
            listing_image_id=img.get("listing_image_id"),
            rank=img.get("rank"),
            url=(img.get("display_url") or img.get("url")) if displayable else None,
            kind=img.get("kind"),
            is_fixed=img.get("listing_image_id") in fixed,
        )
        for img in payload.get("images", [])
    ]
    return schemas.ProfileOut(
        id=profile.id,
        name=profile.name,
        reference_listing_id=profile.reference_listing_id,
        content_template=profile.content_template,
        source=profile.source,
        confirmed=profile.confirmed,
        title_prefix=profile.title_prefix or "",
        fixed_image_ids=list(profile.fixed_image_ids or []),
        updated_at=profile.updated_at,
        is_fresh=_is_fresh(profile),
        reference_images=images,
        reference_images_expired=bool(images) and not displayable,
    )


async def _get(session: AsyncSession, tenant: Tenant, profile_id: uuid.UUID) -> ListingProfile:
    profile = await session.get(ListingProfile, profile_id)
    if profile is None or profile.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="profile not found")
    return profile


@router.get("", response_model=list[schemas.ProfileOut])
async def list_profiles(
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[schemas.ProfileOut]:
    rows = await session.execute(
        select(ListingProfile)
        .where(ListingProfile.tenant_id == tenant.id)
        .order_by(ListingProfile.created_at.desc())
    )
    return [_to_out(p) for p in rows.scalars()]


@router.post("", response_model=schemas.ProfileOut, status_code=201)
async def create_profile(
    body: schemas.ProfileCreate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ProfileOut:
    profile = ListingProfile(
        tenant_id=tenant.id,
        name=body.name,
        reference_listing_id=body.reference_listing_id,
        content_template=body.content_template,
        fixed_image_ids=body.fixed_image_ids or None,
        source="manual",
        confirmed=True,  # a profile the seller created by hand is confirmed
    )
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    # Populate the cached reference payload in the background (queued Etsy read).
    await enqueuer.enqueue("refresh_profile", str(profile.id))
    return _to_out(profile)


@router.get("/{profile_id}", response_model=schemas.ProfileOut)
async def get_profile(
    profile_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ProfileOut:
    return _to_out(await _get(session, tenant, profile_id))


@router.patch("/{profile_id}", response_model=schemas.ProfileOut)
async def update_profile(
    profile_id: uuid.UUID,
    body: schemas.ProfileUpdate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ProfileOut:
    profile = await _get(session, tenant, profile_id)
    if body.name is not None:
        profile.name = body.name
    if body.content_template is not None:
        profile.content_template = body.content_template
    if body.fixed_image_ids is not None:
        profile.fixed_image_ids = body.fixed_image_ids or None
    if body.confirmed is not None:
        profile.confirmed = body.confirmed
    if body.title_prefix is not None:
        profile.title_prefix = body.title_prefix
    await session.commit()
    await session.refresh(profile)
    return _to_out(profile)


@router.post("/{profile_id}/confirm", response_model=schemas.ProfileOut)
async def confirm_profile(
    profile_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ProfileOut:
    """Confirm an auto-detected profile for use (never used silently before this)."""
    profile = await _get(session, tenant, profile_id)
    profile.confirmed = True
    await session.commit()
    await session.refresh(profile)
    return _to_out(profile)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> None:
    profile = await _get(session, tenant, profile_id)
    await session.delete(profile)
    await session.commit()


@router.post("/{profile_id}/refresh", response_model=schemas.ProfileOut)
async def refresh_profile_endpoint(
    profile_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ProfileOut:
    profile = await _get(session, tenant, profile_id)
    await enqueuer.enqueue("refresh_profile", str(profile.id))
    return _to_out(profile)
