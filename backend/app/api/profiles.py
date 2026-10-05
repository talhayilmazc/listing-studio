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
from app.core import audit
from app.db.models import ListingProfile, ProfileShopLink, ShopListingCache, Tenant
from app.pipeline import links as links_mod
from app.pipeline import profile_shops
from app.etsy.refresh import in_use, request_refresh
from app.pipeline.personalization import effective as effective_personalization
from app.pipeline.personalization import validate as validate_personalization
from app.etsy.shops import active_shops, owned_shop
from app.pipeline.reference import decode_etsy_text

from app.pipeline.content import _SEARCH_TEMPLATES, bounds_for, uses_search_style
from app.pipeline.search_rules import SEARCH_TITLE

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


def _payload_age(profile: ListingProfile, stamp: datetime | None = None) -> float | None:
    """Seconds since the reference was fetched, or None if it holds nothing."""
    updated = stamp if stamp is not None else profile.updated_at
    if not profile.cached_payload or updated is None:
        return None
    if updated.tzinfo is None:  # SQLite returns naive; treat as UTC.
        updated = updated.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - updated).total_seconds()


def _is_fresh(profile: ListingProfile) -> bool:
    """Structural reference data is within its 24-hour limit."""
    age = _payload_age(profile)
    return age is not None and age < ListingProfile.CACHE_MAX_AGE_SECONDS


def _images_displayable(profile: ListingProfile) -> bool:
    """Reference image links are within the 6-hour display limit. Their clock is
    their own: auto-refresh renews them more often than the rest (v6 §H)."""
    age = _payload_age(profile, profile.images_updated_at or profile.updated_at)
    return age is not None and age < ListingProfile.DISPLAY_MAX_AGE_SECONDS


async def shop_names(session: AsyncSession, tenant: Tenant) -> dict[uuid.UUID, str]:
    from app.api.shops import shop_label

    return {c.id: shop_label(c) for c in await active_shops(session, tenant.id)}


def _personalization_out(profile: ListingProfile) -> schemas.PersonalizationOut | None:
    setting = effective_personalization(profile.personalization, profile.cached_payload)
    if setting is None:
        return None
    return schemas.PersonalizationOut(
        enabled=bool(setting.get("enabled")),
        question_text=setting.get("question_text"),
        instructions=setting.get("instructions") or None,
        required=bool(setting.get("required")),
        max_allowed_characters=setting.get("max_allowed_characters"),
    )


def _to_out(profile: ListingProfile, shop_name: str | None = None) -> schemas.ProfileOut:
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
        connection_id=profile.connection_id,
        shop_name=shop_name,
        name=profile.name,
        reference_listing_id=profile.reference_listing_id,
        content_template=profile.content_template,
        source=profile.source,
        confirmed=profile.confirmed,
        title_prefix=profile.title_prefix or "",
        listing_style=profile.listing_style if uses_search_style(profile) else "classic",
        search_style_available=profile.content_template in _SEARCH_TEMPLATES,
        title_min_length=bounds_for(profile).min_length,
        title_max_length=bounds_for(profile).max_length,
        title_length_custom=profile.title_min_length is not None or profile.title_max_length is not None,
        attribute_lists=(
            len(payload["category_attributes"]) if isinstance(payload.get("category_attributes"), dict) else None
        ),
        fixed_image_ids=list(profile.fixed_image_ids or []),
        updated_at=profile.updated_at,
        is_fresh=_is_fresh(profile),
        reference_images=images,
        reference_images_expired=bool(images) and not displayable,
        images_updated_at=profile.images_updated_at,
        personalization=_personalization_out(profile),
        personalization_source=(
            "custom" if profile.personalization is not None
            else "reference" if "personalization" in (profile.cached_payload or {})
            else "unknown"
        ),
        refresh_error=profile.refresh_error,
        refresh_failed_at=profile.refresh_failed_at,
    )


def links_out(profile: ListingProfile, links: list[ProfileShopLink], names: dict[uuid.UUID, str]) -> list[schemas.ProfileLinkOut]:
    """The profile's shops, the main one first; only shops still connected."""
    by_shop = {link.connection_id: link for link in links}
    out: list[schemas.ProfileLinkOut] = []
    if profile.connection_id in names:
        out.append(schemas.ProfileLinkOut(connection_id=profile.connection_id, shop_name=names[profile.connection_id],
                                          main=True, ready=True, status="ready"))
    for shop_id, link in sorted(by_shop.items(), key=lambda kv: names.get(kv[0], "")):
        if shop_id == profile.connection_id or shop_id not in names:
            continue
        state = profile_shops.state_of(profile, link)
        assert state is not None
        out.append(schemas.ProfileLinkOut(
            connection_id=shop_id, shop_name=names[shop_id], ready=state.ready, status=state.status,
            reason=state.reason, checked_at=link.checked_at,
            missing=[schemas.LinkMissingOut(resource=r, label=links_mod.LABEL[r], reason=why)
                     for r, why in state.missing.items()],
        ))
    return out


async def _out(session: AsyncSession, tenant: Tenant, profile: ListingProfile) -> schemas.ProfileOut:
    names = await shop_names(session, tenant)
    item = _to_out(profile, names.get(profile.connection_id))
    item.links = links_out(profile, (await profile_shops.links_of(session, [profile.id]))[profile.id], names)
    return item


async def _reference_titles(session: AsyncSession, tenant: Tenant, listing_ids: list[int]) -> dict[int, str]:
    """Reference listings' titles from the seller's own listing cache, while fresh."""
    if not listing_ids:
        return {}
    rows = await session.execute(
        select(ShopListingCache).where(
            ShopListingCache.tenant_id == tenant.id, ShopListingCache.listing_id.in_(set(listing_ids))
        )
    )
    now = datetime.now(timezone.utc)
    out: dict[int, str] = {}
    for row in rows.scalars():
        fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
        title = decode_etsy_text((row.payload or {}).get("title"))
        if title and (now - fetched).total_seconds() < ShopListingCache.STALE_SECONDS:
            out[row.listing_id] = title
    return out


async def _get(session: AsyncSession, tenant: Tenant, profile_id: uuid.UUID) -> ListingProfile:
    profile = await session.get(ListingProfile, profile_id)
    if profile is None or profile.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="profile not found")
    return profile


@router.get("", response_model=list[schemas.ProfileOut])
async def list_profiles(
    shop: uuid.UUID | None = None,
    refresh: bool = False,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> list[schemas.ProfileOut]:
    """Every profile of the account, or only one shop's with ``?shop=`` (v5 §E).

    ``?refresh=1`` is the Profiles page opening: each profile shown there that
    has passed a refresh point is refreshed now, since profiles not in use are
    not kept warm in the background (etsy/refresh.py). Those come back marked
    ``refreshing`` for the page to look again shortly.
    """
    query = select(ListingProfile).where(ListingProfile.tenant_id == tenant.id)
    if shop is not None:
        # The profiles usable in that shop: its own and those linked to it (v8 §C).
        if await owned_shop(session, tenant.id, shop) is None:
            raise HTTPException(status_code=404, detail="shop not found")
        linked = select(ProfileShopLink.profile_id).where(ProfileShopLink.connection_id == shop)
        query = query.where((ListingProfile.connection_id == shop) | ListingProfile.id.in_(linked))
    rows = await session.execute(query.order_by(ListingProfile.created_at.desc()))
    profiles = list(rows.scalars())
    names = await shop_names(session, tenant)
    all_links = await profile_shops.links_of(session, [p.id for p in profiles])
    warm = await in_use(session, (p.id for p in profiles))
    titles = await _reference_titles(session, tenant, [p.reference_listing_id for p in profiles if p.reference_listing_id])
    out = []
    for p in profiles:
        item = _to_out(p, names.get(p.connection_id))
        item.links = links_out(p, all_links.get(p.id, []), names)
        item.in_use = p.id in warm
        item.reference_title = titles.get(p.reference_listing_id) if p.reference_listing_id else None
        # Only profiles of a connected shop: a disconnected one cannot be read.
        if refresh and p.connection_id in names:
            item.refreshing = await request_refresh(enqueuer.enqueue, p, origin="view")
        out.append(item)
    return out


@router.post("", response_model=schemas.ProfileOut, status_code=201)
async def create_profile(
    body: schemas.ProfileCreate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ProfileOut:
    # The reference listing is in this shop, which must be one of the caller's own.
    if await owned_shop(session, tenant.id, body.connection_id) is None:
        raise HTTPException(status_code=404, detail="shop not found")
    profile = ListingProfile(
        tenant_id=tenant.id,
        connection_id=body.connection_id,
        name=body.name,
        reference_listing_id=body.reference_listing_id,
        content_template=body.content_template,
        fixed_image_ids=body.fixed_image_ids or None,
        source="manual",
        confirmed=True,  # a profile the seller created by hand is confirmed
    )
    session.add(profile)
    await session.flush()
    await profile_shops.ensure_main_link(session, profile)
    await session.commit()
    await session.refresh(profile)
    # Populate the cached reference payload in the background (queued Etsy read).
    await enqueuer.enqueue("refresh_profile", str(profile.id))
    return await _out(session, tenant, profile)


@router.get("/{profile_id}", response_model=schemas.ProfileOut)
async def get_profile(
    profile_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ProfileOut:
    return await _out(session, tenant, await _get(session, tenant, profile_id))


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
    if body.listing_style is not None:
        if body.listing_style == "search" and profile.content_template not in _SEARCH_TEMPLATES:
            raise HTTPException(status_code=422, detail="the search style is available for apparel profiles")
        profile.listing_style = body.listing_style
    for name in ("title_min_length", "title_max_length"):
        if name in body.model_fields_set:
            setattr(profile, name, getattr(body, name))
    low, high = profile.title_min_length, profile.title_max_length
    if (low or SEARCH_TITLE.min_length) > (high or SEARCH_TITLE.max_length):
        raise HTTPException(status_code=422, detail="the shortest title cannot be longer than the longest")
    if "personalization" in body.model_fields_set:
        # null: back to the reference's question; otherwise the seller's own (v7 §D4).
        if body.personalization is None:
            profile.personalization = None
        else:
            try:
                profile.personalization = validate_personalization(body.personalization)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    await session.refresh(profile)
    return await _out(session, tenant, profile)


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
    return await _out(session, tenant, profile)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> None:
    profile = await _get(session, tenant, profile_id)
    audit.destructive(
        session, "profile.deleted", actor=tenant, tenant_id=tenant.id,
        shop_id=profile.connection_id, object_id=profile.id, confirmed=profile.confirmed,
    )
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
    return await _out(session, tenant, profile)
