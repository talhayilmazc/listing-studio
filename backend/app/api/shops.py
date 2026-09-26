"""The account's connected Etsy shops (docs/duzeltmeler-v5.md §E).

One account can connect several shops, up to its own ceiling and the app-wide
one. Each shop is one Etsy connection. A seller sees and manages only their own
shops: another account's shop is a 404 here, exactly like a shop that does not
exist (production-spec B3).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.core.config import get_settings
from app.api.deps import active_tenant, get_connection_service, get_session
from app.db.models import EtsyConnection, Tenant
from app.etsy.connection import ConnectionService
from app.etsy.shops import active_shops, owned_shop, shop_slots

router = APIRouter(prefix="/api/shops", tags=["shops"])

_NOT_FOUND = HTTPException(status_code=404, detail="shop not found")


def shop_label(connection: EtsyConnection) -> str:
    return connection.display_name or connection.shop_name or "Etsy shop (loading name)"


def shop_out(connection: EtsyConnection) -> schemas.ShopOut:
    return schemas.ShopOut(
        id=connection.id,
        name=shop_label(connection),
        shop_name=connection.shop_name,
        display_name=connection.display_name,
        shop_id=connection.shop_id,
        position=connection.position,
        connected_at=connection.connected_at,
        missing_scopes=missing_scopes(connection),
    )


def missing_scopes(connection: EtsyConnection) -> list[str]:
    """What the app asks for now that this shop's grant does not include."""
    granted = set(connection.scopes or [])
    return [s for s in get_settings().etsy_scopes.split() if s not in granted]


async def selected_shop(
    session: AsyncSession, tenant: Tenant, shop: uuid.UUID | None
) -> EtsyConnection | None:
    """The shop a ``?shop=`` parameter names, or the account's first shop.

    Returns None only when the account has no shop connected. A shop id that is
    not one of this account's active shops is a 404.
    """
    if shop is not None:
        connection = await owned_shop(session, tenant.id, shop)
        if connection is None:
            raise _NOT_FOUND
        return connection
    shops = await active_shops(session, tenant.id)
    return shops[0] if shops else None


async def _shops_out(session: AsyncSession, tenant: Tenant) -> schemas.ShopsOut:
    slots = await shop_slots(session, tenant)
    return schemas.ShopsOut(
        shops=[shop_out(c) for c in await active_shops(session, tenant.id)],
        slots=schemas.ShopSlotsOut(
            used=slots.used,
            limit=slots.limit,
            app_used=slots.app_used,
            app_limit=slots.app_limit,
            can_add=slots.can_add,
        ),
    )


@router.get("", response_model=schemas.ShopsOut)
async def list_shops(
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ShopsOut:
    return await _shops_out(session, tenant)


@router.patch("/{shop_id}", response_model=schemas.ShopOut)
async def rename_shop(
    shop_id: uuid.UUID,
    body: schemas.ShopUpdate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ShopOut:
    connection = await owned_shop(session, tenant.id, shop_id)
    if connection is None:
        raise _NOT_FOUND
    connection.display_name = (body.display_name or "").strip() or None
    await session.commit()
    return shop_out(connection)


@router.post("/order", response_model=schemas.ShopsOut)
async def order_shops(
    body: schemas.ShopOrder,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ShopsOut:
    """Set the switcher order. Ids that are not this account's shops are ignored."""
    mine = {c.id: c for c in await active_shops(session, tenant.id)}
    ordered = [mine[i] for i in body.ids if i in mine]
    ordered += [c for c in mine.values() if c not in ordered]
    for position, connection in enumerate(ordered):
        connection.position = position
    await session.commit()
    return await _shops_out(session, tenant)


@router.post("/{shop_id}/disconnect", response_model=schemas.ShopsOut)
async def disconnect_shop(
    shop_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    service: ConnectionService = Depends(get_connection_service),
) -> schemas.ShopsOut:
    """Disconnect one shop; the others are untouched (v5 §E)."""
    connection = await owned_shop(session, tenant.id, shop_id)
    if connection is None:
        raise _NOT_FOUND
    await service.disconnect(session, connection)
    return await _shops_out(session, tenant)
