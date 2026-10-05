"""Shop groups (v8 §B): named groups of the account's shops.

A shop is in at most one group. The same data is edited from the Shops page
and the Profiles page; groups appear in the shop switcher, distribution,
scheduling and a profile's "Use in". The caller's own groups and shops only.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import active_tenant, get_session
from app.api.shops import shop_label
from app.core import audit
from app.db.models import ShopGroup, Tenant
from app.etsy.shops import active_shops

router = APIRouter(prefix="/api/shop-groups", tags=["shops"])

MAX_NAME = 40


class GroupShop(BaseModel):
    id: uuid.UUID
    name: str


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    shops: list[GroupShop]


class GroupsOut(BaseModel):
    groups: list[GroupOut]
    #: Shops in no group.
    ungrouped: list[GroupShop]


class GroupIn(BaseModel):
    name: str
    connection_ids: list[uuid.UUID] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("give the group a name")
        if len(value) > MAX_NAME:
            raise ValueError(f"at most {MAX_NAME} characters")
        return value


class GroupPatch(BaseModel):
    name: str | None = None
    connection_ids: list[uuid.UUID] | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else GroupIn._name(value)


async def groups_out(session: AsyncSession, tenant: Tenant) -> GroupsOut:
    groups = list((await session.execute(
        select(ShopGroup).where(ShopGroup.tenant_id == tenant.id).order_by(ShopGroup.position, ShopGroup.name)
    )).scalars())
    shops = await active_shops(session, tenant.id)
    return GroupsOut(
        groups=[GroupOut(id=g.id, name=g.name,
                         shops=[GroupShop(id=c.id, name=shop_label(c)) for c in shops if c.group_id == g.id])
                for g in groups],
        ungrouped=[GroupShop(id=c.id, name=shop_label(c)) for c in shops if c.group_id is None],
    )


async def _own(session: AsyncSession, tenant: Tenant, group_id: uuid.UUID) -> ShopGroup:
    group = await session.get(ShopGroup, group_id)
    if group is None or group.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="group not found")
    return group


async def _set_members(session: AsyncSession, tenant: Tenant, group: ShopGroup, ids: list[uuid.UUID]) -> None:
    """Exactly these shops are in the group; a shop moves out of any other group."""
    shops = {c.id: c for c in await active_shops(session, tenant.id)}
    if not set(ids) <= set(shops):
        raise HTTPException(status_code=404, detail="shop not found")
    for shop in shops.values():
        if shop.id in ids:
            shop.group_id = group.id
        elif shop.group_id == group.id:
            shop.group_id = None


async def _name_free(session: AsyncSession, tenant: Tenant, name: str, but: uuid.UUID | None = None) -> None:
    taken = await session.scalar(select(func.count()).select_from(ShopGroup).where(
        ShopGroup.tenant_id == tenant.id, func.lower(ShopGroup.name) == name.lower(),
        ShopGroup.id != but if but else True))
    if taken:
        raise HTTPException(status_code=409, detail="you already have a group with that name")


@router.get("", response_model=GroupsOut)
async def list_groups(session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)) -> GroupsOut:
    return await groups_out(session, tenant)


@router.post("", response_model=GroupsOut, status_code=201)
async def create_group(
    body: GroupIn, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> GroupsOut:
    await _name_free(session, tenant, body.name)
    count = await session.scalar(select(func.count()).select_from(ShopGroup).where(ShopGroup.tenant_id == tenant.id))
    group = ShopGroup(tenant_id=tenant.id, name=body.name, position=int(count or 0))
    session.add(group)
    await session.flush()
    await _set_members(session, tenant, group, body.connection_ids)
    await session.commit()
    return await groups_out(session, tenant)


@router.patch("/{group_id}", response_model=GroupsOut)
async def update_group(
    group_id: uuid.UUID, body: GroupPatch,
    session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant),
) -> GroupsOut:
    group = await _own(session, tenant, group_id)
    if body.name is not None:
        await _name_free(session, tenant, body.name, but=group.id)
        group.name = body.name
    if body.connection_ids is not None:
        await _set_members(session, tenant, group, body.connection_ids)
    await session.commit()
    return await groups_out(session, tenant)


@router.delete("/{group_id}", response_model=GroupsOut)
async def delete_group(
    group_id: uuid.UUID, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)
) -> GroupsOut:
    """The group goes; its shops stay, in no group."""
    group = await _own(session, tenant, group_id)
    for shop in await active_shops(session, tenant.id):
        if shop.group_id == group.id:
            shop.group_id = None
    audit.destructive(session, "shop_group.deleted", actor=tenant, tenant_id=tenant.id, shop_id=None, object_id=group.id)
    await session.delete(group)
    await session.commit()
    return await groups_out(session, tenant)
