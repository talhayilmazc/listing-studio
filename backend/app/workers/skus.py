"""Update a draft's SKU on Etsy, only after the seller pressed "Update SKU on Etsy"
(pipeline/skus.py, api/skus.py).

One shop's draft per job: its inventory is read (getListingInventory), every
product gets the new SKU (its per-size pattern kept: ``CC7001-S``, ``CC7001-M``),
prices, quantities and variations exactly as Etsy has them, and the inventory is
written back (updateListingInventory) and read again to check the SKUs Etsy kept.
Nothing but the SKU changes. The seller's own work: it counts against their daily
limit (about 3 requests per shop). The publication records the new SKU, a new
content version carries it, and the change is audited.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core import audit, limits
from app.core.config import get_settings
from app.db.models import ListingPublication, Tenant
from app.pipeline import skus, versions
from app.pipeline.reference import _offering, _offering_price
from app.workers.profiles import (
    _active_shop,
    _build_client,
    _connection_service,
    _resolve_shop_id,
    _run_gated,
)

#: getListingInventory, updateListingInventory, getListingInventory again.
REQUESTS_PER_SHOP = 3


def update_spec(publication_id: uuid.UUID | str, sku: str) -> str:
    return json.dumps({"publication_id": str(publication_id), "sku": sku})


def writable_inventory(inventory: dict[str, Any], new_skus: list[str]) -> dict[str, Any]:
    """The inventory as Etsy has it, writable, with only the SKUs changed."""
    products = []
    for product, sku in zip(inventory.get("products") or [], new_skus):
        offerings = []
        for offering in product.get("offerings") or []:
            price = _offering_price(offering)
            if price is None:
                continue
            offerings.append(_offering(price, int(offering.get("quantity") or 0), bool(offering.get("is_enabled", True)),
                                       offering.get("readiness_state_id")))
        products.append({
            "sku": sku,
            "offerings": offerings,
            "property_values": [
                {k: v[k] for k in ("property_id", "property_name", "scale_id", "value_ids", "values") if v.get(k) is not None}
                for v in product.get("property_values") or []
            ],
        })
    return {
        "products": products,
        "price_on_property": list(inventory.get("price_on_property") or []),
        "quantity_on_property": list(inventory.get("quantity_on_property") or []),
        "sku_on_property": list(inventory.get("sku_on_property") or []),
    }


async def update_sku_on_etsy(ctx: dict[str, Any], spec: str) -> str:
    wanted = json.loads(spec)
    async with ctx["sessionmaker"]() as session:
        publication = await session.get(ListingPublication, uuid.UUID(wanted["publication_id"]))
        if publication is None:
            return "no-publication"
        tenant_id = publication.tenant_id
    return await _run_gated(ctx, "update_sku_on_etsy", spec, tenant_id, lambda: _update(ctx, wanted))


async def _update(ctx: dict[str, Any], wanted: dict[str, Any]) -> str:
    settings = get_settings()
    service = _connection_service(settings)
    sku = skus.validate(wanted["sku"])
    async with ctx["sessionmaker"]() as session:
        publication = await session.get(ListingPublication, uuid.UUID(wanted["publication_id"]))
        if publication is None or publication.state == "deleted_on_etsy":
            return "no-publication"
        connection = await _active_shop(session, publication.connection_id)
        if connection is None or connection.tenant_id != publication.tenant_id:
            return "no-connection"
        tenant = await session.get(Tenant, publication.tenant_id)
        token = await service.get_valid_access_token(session, connection)
        async with httpx.AsyncClient(timeout=30.0) as http:
            client = _build_client(ctx, http, settings, shop=connection.id)
            kw = {
                "access_token": token,
                "tenant_id": publication.tenant_id,
                "tenant_limit": limits.ceiling_limit(tenant) if tenant else None,
            }
            await _resolve_shop_id(session, client, connection, kw)
            listing_id = int(publication.etsy_listing_id)
            inventory = await client.get_listing_inventory(listing_id, **kw)
            current = [p.get("sku") for p in inventory.get("products") or []]
            new = skus.variant_skus(current, sku)
            skus.check_lengths(new)
            await client.update_listing_inventory(listing_id, inventory=writable_inventory(inventory, new), **kw)
            back = await client.get_listing_inventory(listing_id, **kw)
            kept = [skus.clean(p.get("sku")) for p in back.get("products") or []]
            if sorted(kept) != sorted(new):
                raise ValueError("Etsy did not keep the SKU as sent; check the listing's inventory in Shop Manager")
        before = publication.sku
        publication.sku = sku
        await versions.sku_changed(session, publication, datetime.now(timezone.utc), sku)
        audit.record(session, "listing.sku_changed", actor=tenant, target_tenant_id=publication.tenant_id,
                     on_etsy=True, publication_id=str(publication.id), shop_id=str(connection.id),
                     etsy_listing_id=listing_id, sku_from=before, sku_to=sku)
        await session.commit()
        return f"sku:{len(new)}"
