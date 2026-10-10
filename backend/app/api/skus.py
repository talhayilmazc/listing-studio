"""Editing a listing's SKU (pipeline/skus.py): on the batch page's group card and on
the review card, prefilled from the file or folder name.

* The SKU is checked against Etsy's rules (422 with the reason). Another listing
  in the same shop with that SKU is a warning, never a refusal.
* Before a draft exists the SKU is simply what drafts will carry, in every shop.
* After a draft exists the response lists each shop that has one, with its
  Etsy request cost; nothing is sent until the seller presses "Update SKU on
  Etsy" (``POST /content/{id}/sku/etsy`` with ``confirm``), which updates only
  the SKU on the inventory there (workers/skus.py).
* Every change is audited (``listing.sku_changed``); a change on Etsy also starts
  a new content version carrying the SKU.

Everything is per account: another account's batch, listing or shop is 404.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session
from app.api.shops import shop_label
from app.core import audit
from app.db.models import (
    Asset,
    GeneratedContent,
    ListingGroupSetting,
    ListingPublication,
    ShopListingCache,
    Tenant,
    UploadBatch,
)
from app.etsy.shops import active_shops
from app.pipeline import grouping, skus
from app.pipeline.group_state import group_states

router = APIRouter(prefix="/api", tags=["skus"])

REQUESTS_PER_SHOP = 3  # workers/skus.py


async def _group(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID, key: str) -> list[Asset]:
    rows = await session.execute(
        select(Asset).where(
            Asset.batch_id == batch_id, Asset.tenant_id == tenant.id,
            Asset.group_key == key if key else or_(Asset.group_key == "", Asset.group_key.is_(None)),
        )
    )
    return list(rows.scalars())


async def apply_group_sku(
    session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID, key: str, raw: str, *, by: str = "seller"
) -> tuple[str, str, str | None]:
    """Set a group's SKU (every photo of it): (new key, SKU, old SKU). A group named
    after its SKU takes the new one as its name, unless another group has it."""
    if grouping.is_unsorted(key):
        raise HTTPException(status_code=422, detail="Unsorted photos have no SKU: move them into a group.")
    try:
        sku = skus.validate(raw)
    except skus.SkuInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    members = await _group(session, tenant, batch_id, key)
    if not members:
        raise HTTPException(status_code=404, detail="group not found")
    old = next((a.parsed_sku for a in members if a.parsed_sku), None)
    for a in members:
        a.parsed_sku = sku
    new_key = key
    if key and old and key == old and sku != key and not sku.startswith("~"):
        taken = await session.scalar(
            select(Asset.id).where(Asset.batch_id == batch_id, Asset.group_key == sku).limit(1)
        )
        if taken is None:
            for a in members:
                a.group_key = sku
            setting = (await session.execute(
                select(ListingGroupSetting).where(ListingGroupSetting.batch_id == batch_id, ListingGroupSetting.group_key == key)
            )).scalar_one_or_none()
            if setting is not None:
                setting.group_key = sku
            new_key = sku
    if old != sku:
        audit.record(session, "listing.sku_changed", actor=tenant, target_tenant_id=tenant.id, on_etsy=False,
                     batch_id=str(batch_id), group_key=new_key, sku_from=old, sku_to=sku, by=by)
    return new_key, sku, old


async def _targets(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID, key: str) -> list[uuid.UUID]:
    state = (await group_states(session, batch_ids={batch_id}, with_removed=True)).get((batch_id, key))
    targets = set(state.targets) if state is not None else set()
    if not targets:
        batch = await session.get(UploadBatch, batch_id)
        if batch is not None and batch.connection_id is not None:
            targets.add(batch.connection_id)
    return list(targets)


async def duplicate_warnings(
    session: AsyncSession, tenant: Tenant, sku: str, shop_ids: list[uuid.UUID], *, batch_id: uuid.UUID, key: str,
    own_listings: set[int],
) -> list[str]:
    """Another listing in the same shop already has this SKU: said, not refused.
    Looks at the shop's listings on Etsy (the 6-hour listing cache), the drafts this
    app made there, and the other groups going there."""
    shops = {c.id: c for c in await active_shops(session, tenant.id)}
    found: list[str] = []
    wanted = sku.lower()
    for shop_id in shop_ids:
        shop = shops.get(shop_id)
        if shop is None:
            continue
        hits: set[str] = set()
        for row in (await session.execute(
            select(ShopListingCache).where(ShopListingCache.connection_id == shop_id, ShopListingCache.tenant_id == tenant.id)
        )).scalars():
            if row.listing_id in own_listings:
                continue
            if any(str(s).strip().lower() == wanted for s in (row.payload or {}).get("skus") or []):
                hits.add(f"listing {row.listing_id}")
        for pub in (await session.execute(
            select(ListingPublication).where(
                ListingPublication.connection_id == shop_id, ListingPublication.tenant_id == tenant.id,
                ListingPublication.state != "deleted_on_etsy",
            )
        )).scalars():
            if pub.etsy_listing_id not in own_listings and (pub.sku or "").strip().lower() == wanted:
                hits.add(f"listing {pub.etsy_listing_id}")
        for other_key in (await session.execute(
            select(Asset.group_key).where(Asset.batch_id == batch_id, Asset.parsed_sku == sku, Asset.group_key != key).distinct()
        )).scalars():
            if other_key and not grouping.is_unsorted(other_key):
                hits.add(f"group {other_key} of this batch")
        if hits:
            found.append(
                f"{shop_label(shop)} already has {sku} on " + ", ".join(sorted(hits)[:3])
                + (f" and {len(hits) - 3} more" if len(hits) > 3 else "") + ". You can keep it; Etsy allows it."
            )
    return found


async def _result(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID, key: str, sku: str) -> schemas.SkuResult:
    members = await _group(session, tenant, batch_id, key)
    contents = list((await session.execute(
        select(GeneratedContent).where(GeneratedContent.asset_id.in_([a.id for a in members]))
    )).scalars()) if members else []
    pubs = list((await session.execute(
        select(ListingPublication).where(
            ListingPublication.content_id.in_([c.id for c in contents]), ListingPublication.state != "deleted_on_etsy",
        )
    )).scalars()) if contents else []
    shops = {c.id: c for c in await active_shops(session, tenant.id)}
    etsy = [
        schemas.EtsySkuUpdate(
            connection_id=p.connection_id, shop_name=shop_label(shops[p.connection_id]), etsy_listing_id=p.etsy_listing_id,
            current_sku=p.sku, requests=REQUESTS_PER_SHOP,
        )
        for p in pubs
        if p.connection_id in shops and (p.sku or "") != sku
    ]
    warnings = await duplicate_warnings(
        session, tenant, sku, await _targets(session, tenant, batch_id, key), batch_id=batch_id, key=key,
        own_listings={int(p.etsy_listing_id) for p in pubs},
    )
    return schemas.SkuResult(group_key=key, sku=sku, warnings=warnings, etsy=etsy)


@router.put("/batches/{batch_id}/groups/sku", response_model=schemas.SkuResult)
async def set_group_sku(
    batch_id: uuid.UUID,
    body: schemas.GroupSku,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.SkuResult:
    """The group card's SKU field."""
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    key, sku, _ = await apply_group_sku(session, tenant, batch_id, body.group_key or "", body.sku)
    await session.commit()
    return await _result(session, tenant, batch_id, key, sku)


async def _own_content(session: AsyncSession, tenant: Tenant, content_id: uuid.UUID) -> tuple[GeneratedContent, Asset]:
    content = await session.get(GeneratedContent, content_id)
    asset = await session.get(Asset, content.asset_id) if content is not None else None
    if content is None or asset is None or content.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="listing not found")
    return content, asset


@router.put("/content/{content_id}/sku", response_model=schemas.SkuResult)
async def set_listing_sku(
    content_id: uuid.UUID,
    body: schemas.ListingSku,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.SkuResult:
    """The review card's SKU field."""
    content, asset = await _own_content(session, tenant, content_id)
    key, sku, _ = await apply_group_sku(session, tenant, content.batch_id, asset.group_key or "", body.sku)
    await session.commit()
    return await _result(session, tenant, content.batch_id, key, sku)


@router.post("/batches/{batch_id}/sku", response_model=schemas.SkuBulkOut)
async def set_sku_for_selected(
    batch_id: uuid.UUID,
    body: schemas.SkuBulk,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.SkuBulkOut:
    """"Set SKU for selected": one SKU for each, or each listing's own with a
    prefix and/or suffix added ("-CC" on all). A SKU Etsy would refuse is left
    as it was and said why."""
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    if not (body.sku or body.prefix or body.suffix):
        raise HTTPException(status_code=422, detail="Enter a SKU, a prefix or a suffix.")
    results: list[schemas.SkuResult] = []
    skipped: list[schemas.BulkSkipped] = []
    seen: set[tuple[uuid.UUID, str]] = set()
    for content_id in dict.fromkeys(body.content_ids):
        content = await session.get(GeneratedContent, content_id)
        asset = await session.get(Asset, content.asset_id) if content is not None else None
        if content is None or asset is None or content.tenant_id != tenant.id or content.batch_id != batch_id:
            skipped.append(schemas.BulkSkipped(content_id=content_id, reason="not a listing of this batch"))
            continue
        key = asset.group_key or ""
        if (batch_id, key) in seen:
            continue
        seen.add((batch_id, key))
        base = skus.clean(body.sku) or skus.clean(asset.parsed_sku)
        if not base and not body.sku:
            skipped.append(schemas.BulkSkipped(content_id=content_id, reason="it has no SKU to add to"))
            continue
        try:
            key, sku, _ = await apply_group_sku(session, tenant, batch_id, key, f"{body.prefix or ''}{base}{body.suffix or ''}")
        except HTTPException as exc:
            skipped.append(schemas.BulkSkipped(content_id=content_id, reason=str(exc.detail)))
            continue
        await session.flush()
        results.append(await _result(session, tenant, batch_id, key, sku))
    await session.commit()
    return schemas.SkuBulkOut(updated=len(results), results=results, skipped=skipped)


@router.post("/content/{content_id}/sku/etsy", response_model=schemas.SkuEtsyOut)
async def update_sku_on_etsy(
    content_id: uuid.UUID,
    body: schemas.SkuEtsyRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.SkuEtsyOut:
    """"Update SKU on Etsy": the drafts of this listing whose SKU differs, each with
    its cost. Nothing is sent without ``confirm``; then one job per shop updates
    only the SKU on that draft's inventory."""
    content, asset = await _own_content(session, tenant, content_id)
    sku = skus.clean(asset.parsed_sku)
    result = await _result(session, tenant, content.batch_id, asset.group_key or "", sku) if sku else None
    shops = [u for u in (result.etsy if result else []) if not body.connection_ids or u.connection_id in body.connection_ids]
    if not body.confirm or not shops:
        return schemas.SkuEtsyOut(queued=False, sku=sku, shops=shops, requests=REQUESTS_PER_SHOP * len(shops))
    from app.workers.skus import update_spec

    pubs = {
        p.connection_id: p for p in (await session.execute(
            select(ListingPublication).where(ListingPublication.content_id == content.id, ListingPublication.state != "deleted_on_etsy")
        )).scalars()
    }
    for shop in shops:
        pub = pubs.get(shop.connection_id)
        if pub is not None:
            await enqueuer.enqueue("update_sku_on_etsy", update_spec(pub.id, sku), _job_id=f"sku:{pub.id}:{sku}")
    return schemas.SkuEtsyOut(queued=True, sku=sku, shops=shops, requests=REQUESTS_PER_SHOP * len(shops))
