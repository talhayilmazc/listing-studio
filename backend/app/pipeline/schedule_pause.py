"""Paused go-lives (B8): "Pause" on a group of the Scheduled page, or "Pause all".

A paused group holds every scheduled go-live and every planned draft of its shops (a shop in
two groups is held while either is paused); "Pause all" (``tenant.schedules_paused``) holds the
account's. Nothing is cancelled: the crons pass them over until they are resumed, and a time
that passed meanwhile goes at the next release (workers/plans.py, workers/schedule.py).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ShopGroup, ShopGroupMember, Tenant


async def paused_shops(session: AsyncSession, tenant_ids: set[uuid.UUID] | None = None) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """(accounts with everything paused, shops held by a paused group)."""
    query = select(Tenant.id).where(Tenant.schedules_paused.is_(True))
    if tenant_ids is not None:
        query = query.where(Tenant.id.in_(tenant_ids))
    accounts = set((await session.execute(query)).scalars())
    groups = select(ShopGroup).where(ShopGroup.schedule.is_not(None))
    if tenant_ids is not None:
        groups = groups.where(ShopGroup.tenant_id.in_(tenant_ids))
    held: set[uuid.UUID] = set()
    paused_groups = [g.id for g in (await session.execute(groups)).scalars() if (g.schedule or {}).get("paused")]
    if paused_groups:
        held = set((await session.execute(select(ShopGroupMember.connection_id).where(
            ShopGroupMember.group_id.in_(paused_groups)))).scalars())
    return accounts, held


def is_paused(tenant_id: uuid.UUID, shop_id: uuid.UUID, paused: tuple[set[uuid.UUID], set[uuid.UUID]]) -> bool:
    accounts, shops = paused
    return tenant_id in accounts or shop_id in shops
