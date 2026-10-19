"""Which shops an account has, and how many more it may connect (v5 §E).

One Etsy account owns exactly one shop, so each connection is one shop, and a
seller with several shops connects once per shop, signed in to Etsy as that
shop's owner. Two ceilings apply:

* per account: ``tenant.max_shops`` if an admin set one (any positive number, no
  upper cap), else the default MAX_SHOPS_PER_TENANT;
* app-wide: MAX_SHOPS_APP_WIDE, because every connected shop spends part of the
  one shared daily request budget (100,000) just to stay in sync.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import ConnectionStatus, EtsyConnection, ShopGroup, ShopGroupMember, Tenant


class ShopLimitReached(Exception):
    """Connecting one more shop would pass a ceiling. ``scope``: "account" | "app"."""

    def __init__(self, scope: str) -> None:
        super().__init__(f"shop limit reached ({scope})")
        self.scope = scope


class ShopTaken(Exception):
    """The shop is already connected to another account."""


@dataclass(frozen=True)
class ShopSlots:
    used: int
    limit: int
    app_used: int
    app_limit: int
    #: New connections are paused by the operator (core/app_settings.py).
    paused: bool = False

    @property
    def can_add(self) -> bool:
        return not self.paused and self.used < self.limit and self.app_used < self.app_limit


def tenant_shop_limit(tenant: Tenant) -> int:
    return tenant.max_shops if tenant.max_shops is not None else get_settings().max_shops_per_tenant


async def active_shops(session: AsyncSession, tenant_id: uuid.UUID) -> list[EtsyConnection]:
    rows = await session.execute(
        select(EtsyConnection)
        .where(
            EtsyConnection.tenant_id == tenant_id,
            EtsyConnection.status == ConnectionStatus.active,
        )
        .order_by(EtsyConnection.position, EtsyConnection.connected_at)
    )
    return list(rows.scalars())


async def group_shops(
    session: AsyncSession, tenant_id: uuid.UUID, shops: list[EtsyConnection] | None = None
) -> dict[uuid.UUID, list[EtsyConnection]]:
    """Every group of the account -> its connected shops, in shop order. A shop
    may be in several groups; a group with no connected shop maps to []."""
    shops = shops if shops is not None else await active_shops(session, tenant_id)
    by_id = {c.id: c for c in shops}
    out: dict[uuid.UUID, list[EtsyConnection]] = {
        gid: [] for gid in (await session.execute(
            select(ShopGroup.id).where(ShopGroup.tenant_id == tenant_id)
            .order_by(ShopGroup.position, ShopGroup.name))).scalars()
    }
    rows = (await session.execute(
        select(ShopGroupMember.group_id, ShopGroupMember.connection_id)
        .join(ShopGroup, ShopGroup.id == ShopGroupMember.group_id)
        .where(ShopGroup.tenant_id == tenant_id)
    )).all()
    members: dict[uuid.UUID, set[uuid.UUID]] = {}
    for gid, cid in rows:
        members.setdefault(gid, set()).add(cid)
    for gid in out:
        out[gid] = [c for c in shops if c.id in members.get(gid, set()) and c.id in by_id]
    return out


async def shop_groups_of(session: AsyncSession, tenant_id: uuid.UUID) -> dict[uuid.UUID, list[uuid.UUID]]:
    """Every shop of the account -> the groups it is in (group order)."""
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for gid, shops in (await group_shops(session, tenant_id)).items():
        for shop in shops:
            out.setdefault(shop.id, []).append(gid)
    return out


async def app_shop_count(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(EtsyConnection)
                .where(EtsyConnection.status == ConnectionStatus.active)
            )
        ).scalar_one()
    )


async def shop_slots(session: AsyncSession, tenant: Tenant) -> ShopSlots:
    from app.core import app_settings

    return ShopSlots(
        used=len(await active_shops(session, tenant.id)),
        limit=tenant_shop_limit(tenant),
        app_used=await app_shop_count(session),
        app_limit=get_settings().max_shops_app_wide,
        paused=await app_settings.connections_paused(session),
    )


async def owned_shop(
    session: AsyncSession, tenant_id: uuid.UUID, connection_id: uuid.UUID | None
) -> EtsyConnection | None:
    """This account's active shop with that id, or None: never another account's."""
    if connection_id is None:
        return None
    connection = await session.get(EtsyConnection, connection_id)
    if (
        connection is None
        or connection.tenant_id != tenant_id
        or connection.status is not ConnectionStatus.active
    ):
        return None
    return connection
