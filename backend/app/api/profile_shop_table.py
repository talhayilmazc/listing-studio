"""The Shops table on a profile (B7): one row per shop the profile is used in.

``GET /api/profiles/{id}/shops``                  the rows: section, title prefix and daily limit
                                                  per shop (shipping, returns and processing come
                                                  from ``GET /api/profiles/{id}/setup``, by name).
``PUT /api/profiles/{id}/shops/{connection_id}``  one value for one shop: ``section_title`` or
                                                  ``title_prefix`` (``follow``: the shop follows the
                                                  profile again).
``PUT /api/shops/{connection_id}/daily-limit``    the shop's listings a day when a schedule spreads
                                                  them out (every profile, every schedule).

Nothing is sent to Etsy here. A shop's sections are read through the queue when they are not
fresh (24 hours, like the review card). The caller's own profiles and shops only (404).
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session
from app.api.item_options import ask_for_sections, fresh_sections
from app.api.shops import shop_label
from app.core import audit
from app.db.models import EtsyConnection, ListingProfile, ProfileShopLink, Tenant
from app.etsy.shops import owned_shop
from app.pipeline import profile_shop_settings as S

router = APIRouter(prefix="/api", tags=["profiles"])


class ShopRow(BaseModel):
    connection_id: uuid.UUID
    shop_name: str
    main: bool
    #: The profile's section in this shop (a title), or None: the design's best fit.
    section_title: str | None
    #: The shop's section titles (fresh), or None while they are read.
    sections: list[str] | None
    #: The prefix the profile's titles carry here, and whether it is this shop's own.
    title_prefix: str | None
    prefix_own: bool
    daily_limit: int | None


class ShopTableOut(BaseModel):
    profile_id: uuid.UUID
    rows: list[ShopRow]


class ShopValueIn(BaseModel):
    field: Literal["section_title", "title_prefix"]
    value: str | None = None
    follow: bool = False


class DailyLimitIn(BaseModel):
    daily_limit: int | None = Field(default=None, ge=1, le=50)


class DailyLimitOut(BaseModel):
    connection_id: uuid.UUID
    daily_limit: int | None


async def _profile(session: AsyncSession, tenant: Tenant, profile_id: uuid.UUID) -> ListingProfile:
    profile = await session.get(ListingProfile, profile_id)
    if profile is None or profile.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="profile not found")
    return profile


async def _shops(session: AsyncSession, tenant: Tenant, profile: ListingProfile) -> list[EtsyConnection]:
    """The profile's main shop, then every connected shop it is used in."""
    ids = [profile.connection_id] + list((await session.execute(
        select(ProfileShopLink.connection_id).where(ProfileShopLink.profile_id == profile.id))).scalars())
    out: list[EtsyConnection] = []
    for cid in dict.fromkeys(ids):
        shop = await owned_shop(session, tenant.id, cid)
        if shop is not None:
            out.append(shop)
    return out


async def _table(session: AsyncSession, tenant: Tenant, profile: ListingProfile, enqueuer: Enqueuer) -> ShopTableOut:
    rows = []
    for shop in await _shops(session, tenant, profile):
        sections = fresh_sections(shop)
        if sections is None:
            await ask_for_sections(enqueuer, shop)
        main = shop.id == profile.connection_id
        own = ((profile.shop_settings or {}).get(str(shop.id)) or {})
        rows.append(ShopRow(
            connection_id=shop.id, shop_name=shop_label(shop), main=main,
            section_title=S.shop_section(profile, shop.id),
            sections=[str(s["title"]) for s in sections] if sections is not None else None,
            title_prefix=S.shop_prefix(profile, shop.id), prefix_own=main or "title_prefix" in own,
            daily_limit=shop.daily_limit,
        ))
    return ShopTableOut(profile_id=profile.id, rows=rows)


@router.get("/profiles/{profile_id}/shops", response_model=ShopTableOut)
async def shop_table(profile_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                     tenant: Tenant = Depends(active_tenant), enqueuer: Enqueuer = Depends(get_enqueuer)) -> ShopTableOut:
    return await _table(session, tenant, await _profile(session, tenant, profile_id), enqueuer)


@router.put("/profiles/{profile_id}/shops/{connection_id}", response_model=ShopTableOut)
async def set_shop_value(profile_id: uuid.UUID, connection_id: uuid.UUID, body: ShopValueIn,
                         session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant),
                         enqueuer: Enqueuer = Depends(get_enqueuer)) -> ShopTableOut:
    profile = await _profile(session, tenant, profile_id)
    if connection_id not in {s.id for s in await _shops(session, tenant, profile)}:
        raise HTTPException(status_code=404, detail="the profile is not used in that shop")
    try:
        S.set_value(profile, connection_id, body.field, body.value, follow=body.follow)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    audit.record(session, "profile.shop_setting_changed", actor=tenant, target_tenant_id=tenant.id,
                 profile_id=str(profile.id), shop_id=str(connection_id), field=body.field, follow=body.follow)
    await session.commit()
    return await _table(session, tenant, profile, enqueuer)


@router.put("/shops/{connection_id}/daily-limit", response_model=DailyLimitOut)
async def set_daily_limit(connection_id: uuid.UUID, body: DailyLimitIn, session: AsyncSession = Depends(get_session),
                          tenant: Tenant = Depends(active_tenant)) -> DailyLimitOut:
    shop = await owned_shop(session, tenant.id, connection_id)
    if shop is None:
        raise HTTPException(status_code=404, detail="shop not found")
    shop.daily_limit = body.daily_limit
    audit.record(session, "shop.daily_limit_changed", actor=tenant, target_tenant_id=tenant.id,
                 shop_id=str(shop.id), daily_limit=body.daily_limit)
    await session.commit()
    return DailyLimitOut(connection_id=shop.id, daily_limit=shop.daily_limit)
