"""Editing a listing's SKU (pipeline/skus.py): on the batch page's group card and on
the review card, prefilled from the file or folder name.

* The SKU is checked against Etsy's rules (422 with the reason). Another listing
  in the same shop with that SKU is a warning, never a refusal.
* Before a draft exists the SKU is simply what drafts will carry, in every shop.
* After a draft exists the response lists each shop that has one; nothing is sent until
  the seller presses "Update SKU on Etsy" and then "Update it" in the confirmation that
  names the shops and the number of listings (``POST /content/{id}/sku/etsy`` with
  ``confirm`` and the shops named; anything else is 422), which updates only the SKU on
  the inventory there (workers/skus.py). Which drafts differ is one read for the whole
  page, ``GET /batches/{id}/sku-etsy``: never a POST, never one request per row (a page
  once sent 15 of those POSTs on each load; they wrote nothing, but looked like writes).
* Every change is audited (``listing.sku_changed``); a change on Etsy also starts
  a new content version carrying the SKU.
* A SKU belongs to a listing in a shop: saved while a shop is shown on the page
  (``connection_id``), once the listing is on Etsy in another shop, it is that shop's
  SKU only (pipeline/shop_listing.py) and "Update SKU on Etsy" offers that shop's draft
  only, by name. Its drafts elsewhere keep theirs.

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
from app.etsy.shops import active_shops, owned_shop
from app.pipeline import grouping, shop_listing, skus
from app.pipeline.group_state import group_states

router = APIRouter(prefix="/api", tags=["skus"])

REQUESTS_PER_SHOP = 3  # workers/skus.py
#: Who asked for a SKU write on Etsy (audit ``trigger``): the seller's "Update it" click.
TRIGGER_CLICK = "seller_click"


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
    # The group's targets (its profile's main shop unless the seller chose others; B3: no
    # batch shop decides it any more).
    targets = set(state.targets) if state is not None else set()
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


async def _contents_of(session: AsyncSession, members: list[Asset]) -> list[GeneratedContent]:
    return list((await session.execute(
        select(GeneratedContent).where(GeneratedContent.asset_id.in_([a.id for a in members]))
    )).scalars()) if members else []


async def _result(
    session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID, key: str, sku: str, shop: uuid.UUID | None = None
) -> schemas.SkuResult:
    """The SKU saved, its warnings, and the drafts on Etsy carrying another SKU than the one
    they should have in their shop (``shop``: that shop only)."""
    members = await _group(session, tenant, batch_id, key)
    contents = await _contents_of(session, members)
    base = next((a.parsed_sku for a in members if a.parsed_sku), None)
    pubs = list((await session.execute(
        select(ListingPublication).where(
            ListingPublication.content_id.in_([c.id for c in contents]), ListingPublication.state != "deleted_on_etsy",
        )
    )).scalars()) if contents else []
    by_id = {c.id: c for c in contents}
    shops = {c.id: c for c in await active_shops(session, tenant.id)}
    etsy = [
        schemas.EtsySkuUpdate(
            connection_id=p.connection_id, shop_name=shop_label(shops[p.connection_id]), etsy_listing_id=p.etsy_listing_id,
            current_sku=p.sku, sku=shop_listing.sku_in(by_id[p.content_id], base, p.connection_id),
            requests=REQUESTS_PER_SHOP,
        )
        for p in pubs
        if p.connection_id in shops and (shop is None or p.connection_id == shop)
        and (p.sku or "") != (shop_listing.sku_in(by_id[p.content_id], base, p.connection_id) or "")
    ]
    targets = [shop] if shop is not None else await _targets(session, tenant, batch_id, key)
    warnings = await duplicate_warnings(
        session, tenant, sku, targets, batch_id=batch_id, key=key,
        own_listings={int(p.etsy_listing_id) for p in pubs},
    )
    return schemas.SkuResult(group_key=key, sku=sku, warnings=warnings, etsy=etsy, connection_id=shop)


async def _shop_sku(
    session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID, key: str, raw: str, shop_id: uuid.UUID | None
) -> tuple[str, str, uuid.UUID | None]:
    """Save a SKU from the batch or review page: (group key, SKU, the shop it is for only).

    The shop shown on the page (``shop_id``): once the group's listing is on Etsy in another
    shop, or already has its own SKU in this one, the SKU is this shop's only (its drafts
    elsewhere keep theirs; pipeline/shop_listing.py). Otherwise it is the group's SKU, for
    every shop without its own, as before."""
    if shop_id is not None and await owned_shop(session, tenant.id, shop_id) is None:
        raise HTTPException(status_code=404, detail="shop not found")
    if shop_id is not None:
        members = await _group(session, tenant, batch_id, key)
        contents = await _contents_of(session, members)
        states = await shop_listing.states(session, contents)
        if contents and any(shop_listing.sku_override(c, shop_id) or any(s != shop_id for s in states[c.id])
                            for c in contents):
            if grouping.is_unsorted(key):
                raise HTTPException(status_code=422, detail="Unsorted photos have no SKU: move them into a group.")
            try:
                sku = skus.validate(raw)
            except skus.SkuInvalid as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            base = next((a.parsed_sku for a in members if a.parsed_sku), None)
            for content in contents:
                old = shop_listing.sku_in(content, base, shop_id)
                shop_listing.set_override(content, shop_id, sku=None if sku == base else sku)
                if old != sku:
                    audit.record(session, "listing.sku_changed", actor=tenant, target_tenant_id=tenant.id, on_etsy=False,
                                 batch_id=str(batch_id), group_key=key, sku_from=old, sku_to=sku, by="seller",
                                 shop_id=str(shop_id))
            return key, sku, shop_id
    key, sku, _ = await apply_group_sku(session, tenant, batch_id, key, raw)
    return key, sku, None


@router.put("/batches/{batch_id}/groups/sku", response_model=schemas.SkuResult)
async def set_group_sku(
    batch_id: uuid.UUID,
    body: schemas.GroupSku,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.SkuResult:
    """The group card's SKU field."""
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id or batch.deleted_at is not None:
        raise HTTPException(status_code=404, detail="batch not found")
    key, sku, shop = await _shop_sku(session, tenant, batch_id, body.group_key or "", body.sku, body.connection_id)
    await session.commit()
    return await _result(session, tenant, batch_id, key, sku, shop)


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
    key, sku, shop = await _shop_sku(session, tenant, content.batch_id, asset.group_key or "", body.sku, body.connection_id)
    await session.commit()
    return await _result(session, tenant, content.batch_id, key, sku, shop)


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
    if batch is None or batch.tenant_id != tenant.id or batch.deleted_at is not None:
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


@router.get("/batches/{batch_id}/sku-etsy", response_model=schemas.SkuOnEtsyOut)
async def sku_on_etsy(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.SkuOnEtsyOut:
    """Each listing of the batch whose drafts on Etsy carry another SKU than the one it
    should have in their shop. Reads only our records: no Etsy call, nothing written."""
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id or batch.deleted_at is not None:
        raise HTTPException(status_code=404, detail="batch not found")
    contents = list((await session.execute(select(GeneratedContent).where(
        GeneratedContent.batch_id == batch_id, GeneratedContent.tenant_id == tenant.id))).scalars())
    if not contents:
        return schemas.SkuOnEtsyOut()
    assets = list((await session.execute(select(Asset).where(Asset.batch_id == batch_id))).scalars())
    by_asset = {a.id: a for a in assets}
    base_of: dict[str, str | None] = {}
    for a in sorted(assets, key=lambda a: a.rank or 0):
        if a.parsed_sku and not base_of.get(a.group_key or ""):
            base_of[a.group_key or ""] = a.parsed_sku
    pubs = list((await session.execute(select(ListingPublication).where(
        ListingPublication.content_id.in_([c.id for c in contents]), ListingPublication.state != "deleted_on_etsy",
    ))).scalars())
    shops = {c.id: c for c in await active_shops(session, tenant.id)}
    by_id = {c.id: c for c in contents}
    out: dict[uuid.UUID, list[schemas.EtsySkuUpdate]] = {}
    for p in pubs:
        content = by_id[p.content_id]
        asset = by_asset.get(content.asset_id)
        base = base_of.get((asset.group_key or "") if asset else "")
        wanted = shop_listing.sku_in(content, base, p.connection_id)
        if p.connection_id not in shops or not wanted or (p.sku or "") == wanted:
            continue
        out.setdefault(content.id, []).append(schemas.EtsySkuUpdate(
            connection_id=p.connection_id, shop_name=shop_label(shops[p.connection_id]),
            etsy_listing_id=p.etsy_listing_id, current_sku=p.sku, sku=wanted, requests=REQUESTS_PER_SHOP,
        ))
    return schemas.SkuOnEtsyOut(listings=[schemas.SkuOnEtsyListing(content_id=k, shops=v) for k, v in out.items()])


NOT_CONFIRMED = ("Nothing was sent to Etsy: press \"Update SKU on Etsy\", check the shops it names, "
                 "then press \"Update it\".")


@router.post("/content/{content_id}/sku/etsy", response_model=schemas.SkuEtsyOut)
async def update_sku_on_etsy(
    content_id: uuid.UUID,
    body: schemas.SkuEtsyRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.SkuEtsyOut:
    """"Update it" in the "Update SKU on Etsy" confirmation: one job per named shop updates
    only the SKU on that draft's inventory. Refused (422, nothing sent) without ``confirm``
    or without the shops the confirmation named; a shop whose draft already has the SKU is
    left alone."""
    if not body.confirm or not body.connection_ids:
        raise HTTPException(status_code=422, detail=NOT_CONFIRMED)
    content, asset = await _own_content(session, tenant, content_id)
    base = skus.clean(asset.parsed_sku)
    # Each draft gets the SKU the listing has in its own shop (its own there, else the group's).
    result = await _result(session, tenant, content.batch_id, asset.group_key or "", base)
    shops = [u for u in result.etsy if u.connection_id in body.connection_ids
             and shop_listing.sku_in(content, base, u.connection_id)]
    wanted = {u.connection_id: shop_listing.sku_in(content, base, u.connection_id) or "" for u in shops}
    sku = next(iter(wanted.values())) if len(set(wanted.values())) == 1 else (base or "")
    if not shops:
        return schemas.SkuEtsyOut(queued=False, sku=sku, shops=[], requests=0)
    from app.workers.skus import update_spec

    pubs = {
        p.connection_id: p for p in (await session.execute(
            select(ListingPublication).where(ListingPublication.content_id == content.id, ListingPublication.state != "deleted_on_etsy")
        )).scalars()
    }
    for shop in shops:
        pub = pubs.get(shop.connection_id)
        if pub is not None:
            mine = wanted[shop.connection_id]
            audit.record(session, "listing.sku_etsy_requested", actor=tenant, target_tenant_id=tenant.id,
                         trigger=TRIGGER_CLICK, publication_id=str(pub.id), shop_id=str(pub.connection_id),
                         etsy_listing_id=int(pub.etsy_listing_id), sku_from=pub.sku, sku_to=mine)
            await enqueuer.enqueue("update_sku_on_etsy", update_spec(pub.id, mine, TRIGGER_CLICK),
                                   _job_id=f"sku:{pub.id}:{mine}")
    await session.commit()
    return schemas.SkuEtsyOut(queued=True, sku=sku, shops=shops, requests=REQUESTS_PER_SHOP * len(shops))
