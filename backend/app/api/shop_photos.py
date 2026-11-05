"""A listing's photos in one shop (customer request, 2026-11-03; pipeline/shop_listing.py::arrange).

``GET  /api/batches/{id}/shops/{shop}/photos/{content}``  the listing's photos, and their order, cover and
                                                         left-out ones in that shop.
``PUT  /api/batches/{id}/shops/{shop}/photos/{content}``  that shop's order (its first the cover there) and the
                                                         photos left out there; ``keep``: the listing's own again.
``POST /api/batches/{id}/shops/{shop}/photos/bulk``      for selected listings: "Use photo number N as the cover",
                                                         "Move the cover to the end"; ``apply`` false is the preview.

Stored with the shop's other overrides (``generated_content.shop_overrides``); the draft for that shop sends that
order, every other shop is untouched. Etsy's limit holds: at least one photo, and photos and size charts together at
most :data:`chart_order.MAX_IMAGES` (the charts keep their places). A listing already on Etsy in that shop keeps the
photos it has there: the order is for new drafts only, and the answer says so. Nothing here calls Etsy. The caller's
own batches, shops and listings only (404).
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.another_shop import _assets, _batch, _profile_there, _target_shop
from app.api.deps import active_tenant, get_session
from app.api.distribution import _contents
from app.api.shops import shop_label
from app.core import audit
from app.db.models import Asset, EtsyConnection, GeneratedContent, Tenant
from app.pipeline import chart_order, shop_listing

router = APIRouter(prefix="/api", tags=["shop-photos"])

LIVE_NOTE = "Already on Etsy in {shop}: this order is for new drafts only. The photos on Etsy stay as they are."


class PhotoOut(BaseModel):
    id: uuid.UUID
    filename: str


class ShopPhotosOut(BaseModel):
    content_id: uuid.UUID
    shop_id: uuid.UUID
    shop_name: str
    #: The listing's own photos in its own order, cover first.
    photos: list[PhotoOut]
    #: The order in that shop (its first the cover there) and the photos left out there.
    order: list[uuid.UUID]
    out: list[uuid.UUID]
    #: The shop has an order of its own (else it follows the listing).
    own: bool
    #: Size charts this listing's drafts carry, and so the most photos a draft can take.
    charts: int
    room: int
    #: Said on the row when the listing is already on Etsy in that shop.
    note: str | None = None


class ShopPhotosIn(BaseModel):
    order: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    out: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    #: "Keep as it is": the listing's own order again.
    keep: bool = False


class BulkIn(BaseModel):
    content_ids: list[uuid.UUID] = Field(min_length=1, max_length=2000)
    #: cover: photo number ``number`` (1 = the first of its order there) becomes the cover there;
    #: cover_last: the cover there moves to the end.
    action: Literal["cover", "cover_last"]
    number: int | None = Field(default=None, ge=1, le=200)
    #: False: only the preview; nothing is saved.
    apply: bool = False


class BulkRow(BaseModel):
    content_id: uuid.UUID
    label: str
    before: list[str]
    after: list[str]
    changed: bool
    reason: str | None = None
    note: str | None = None


class BulkOut(BaseModel):
    rows: list[BulkRow]
    changed: int
    applied: bool
    sentence: str


async def _listing(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID, shop_id: uuid.UUID, content_id: uuid.UUID
                   ) -> tuple[EtsyConnection, GeneratedContent, Asset]:
    batch = await _batch(session, tenant, batch_id)
    shop = await _target_shop(session, tenant, shop_id)
    contents = await _contents(session, tenant, batch, [content_id])
    content = contents[0]
    asset = (await _assets(session, contents)).get(content.asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="listing not found")
    return shop, content, asset


async def _photos(session: AsyncSession, cover: Asset) -> list[Asset]:
    from app.workers.publish import group_siblings  # the draft's own query: the same photos it sends

    return [cover, *await group_siblings(session, cover)]


async def _charts(session: AsyncSession, tenant: Tenant, content: GeneratedContent, asset: Asset, shop_id: uuid.UUID) -> int:
    from app.workers.publish import chart_plan

    profile = await _profile_there(session, content, shop_id, None)
    if profile is None:
        return 0
    chart_profile, _ = await chart_plan(session, tenant.id, content, asset, profile)
    return len(chart_profile.fixed_image_ids or [])


async def _live(session: AsyncSession, content: GeneratedContent, shop: EtsyConnection) -> str | None:
    states = await shop_listing.states(session, [content])
    return LIVE_NOTE.format(shop=shop_label(shop)) if shop_listing.sent(states[content.id], shop.id) else None


def _room(charts: int) -> int:
    return max(1, chart_order.MAX_IMAGES - max(0, charts))


async def _out(session: AsyncSession, tenant: Tenant, shop: EtsyConnection, content: GeneratedContent, asset: Asset) -> ShopPhotosOut:
    photos = await _photos(session, asset)
    choice = shop_listing.photos_in(content, shop.id)
    order = shop_listing.arrange(photos, choice)
    charts = await _charts(session, tenant, content, asset, shop.id)
    return ShopPhotosOut(
        content_id=content.id, shop_id=shop.id, shop_name=shop_label(shop),
        photos=[PhotoOut(id=p.id, filename=p.original_filename) for p in photos],
        order=[p.id for p in order], out=[p.id for p in photos if p not in order], own=choice is not None,
        charts=charts, room=_room(charts), note=await _live(session, content, shop),
    )


@router.get("/batches/{batch_id}/shops/{connection_id}/photos/{content_id}", response_model=ShopPhotosOut)
async def shop_photos(batch_id: uuid.UUID, connection_id: uuid.UUID, content_id: uuid.UUID,
                      session: AsyncSession = Depends(get_session, scope="function"),
                      tenant: Tenant = Depends(active_tenant)) -> ShopPhotosOut:
    shop, content, asset = await _listing(session, tenant, batch_id, connection_id, content_id)
    return await _out(session, tenant, shop, content, asset)


def _check(photos: list[Asset], order: list[str], out: list[str], room: int) -> None:
    ids = {str(p.id) for p in photos}
    if not order:
        raise HTTPException(status_code=422, detail="Keep at least one photo: a listing needs a cover.")
    if len(set(order)) != len(order) or len(set(out)) != len(out) or set(order) & set(out):
        raise HTTPException(status_code=422, detail="Each photo once: in the order, or left out.")
    if not set(order) | set(out) <= ids:
        raise HTTPException(status_code=404, detail="photo not found in this listing")
    kept = shop_listing.arrange(photos, (order, out))
    if len(kept) > room:
        raise HTTPException(status_code=422, detail=(
            f"Etsy keeps {chart_order.MAX_IMAGES} images on a listing. With its size charts, this one can have {room} "
            f"photos: leave {len(kept) - room} more out."))


def _save(content: GeneratedContent, shop_id: uuid.UUID, photos: list[Asset], order: list[str], out: list[str]) -> None:
    """The shop's order, or none when it is just the listing's own (it follows the listing again)."""
    if [str(p.id) for p in photos] == order and not out:
        shop_listing.set_override(content, shop_id, photos=None)
    else:
        shop_listing.set_override(content, shop_id, photos=(order, out))


@router.put("/batches/{batch_id}/shops/{connection_id}/photos/{content_id}", response_model=ShopPhotosOut)
async def set_shop_photos(batch_id: uuid.UUID, connection_id: uuid.UUID, content_id: uuid.UUID, body: ShopPhotosIn,
                          session: AsyncSession = Depends(get_session, scope="function"),
                          tenant: Tenant = Depends(active_tenant)) -> ShopPhotosOut:
    shop, content, asset = await _listing(session, tenant, batch_id, connection_id, content_id)
    photos = await _photos(session, asset)
    if body.keep:
        shop_listing.set_override(content, shop.id, photos=None)
    else:
        order, out = [str(i) for i in body.order], [str(i) for i in body.out]
        _check(photos, order, out, _room(await _charts(session, tenant, content, asset, shop.id)))
        _save(content, shop.id, photos, order, out)
    audit.record(session, "listing.shop_photos_changed", actor=tenant, target_tenant_id=tenant.id, content_id=str(content.id),
                 connection_id=str(shop.id), keep=body.keep)
    await session.commit()
    return await _out(session, tenant, shop, content, asset)


def _bulk_order(order: list[Asset], action: str, number: int | None) -> tuple[list[Asset], str | None]:
    if len(order) < 2:
        return order, "it has one photo"
    if action == "cover_last":
        return [*order[1:], order[0]], None
    if number is None:
        raise HTTPException(status_code=422, detail="Say which photo number becomes the cover.")
    if number > len(order):
        return order, f"it has {len(order)} photos"
    if number == 1:
        return order, "that photo is already the cover"
    picked = order[number - 1]
    return [picked, *[p for p in order if p is not picked]], None


@router.post("/batches/{batch_id}/shops/{connection_id}/photos/bulk", response_model=BulkOut)
async def bulk_shop_photos(batch_id: uuid.UUID, connection_id: uuid.UUID, body: BulkIn,
                           session: AsyncSession = Depends(get_session, scope="function"),
                           tenant: Tenant = Depends(active_tenant)) -> BulkOut:
    """A quick way to make shops differ. ``apply`` false: the before and after, nothing saved."""
    batch = await _batch(session, tenant, batch_id)
    shop = await _target_shop(session, tenant, connection_id)
    contents = await _contents(session, tenant, batch, list(dict.fromkeys(body.content_ids)))
    assets = await _assets(session, contents)
    states = await shop_listing.states(session, contents)
    rows: list[BulkRow] = []
    for content in contents:
        asset = assets.get(content.asset_id)
        if asset is None:
            continue
        photos = await _photos(session, asset)
        choice = shop_listing.photos_in(content, shop.id)
        order = shop_listing.arrange(photos, choice)
        after, reason = _bulk_order(order, body.action, body.number)
        live = LIVE_NOTE.format(shop=shop_label(shop)) if shop_listing.sent(states[content.id], shop.id) else None
        rows.append(BulkRow(content_id=content.id, label=asset.group_key or asset.original_filename,
                            before=[p.original_filename for p in order], after=[p.original_filename for p in after],
                            changed=after != order, reason=reason, note=live))
        if body.apply and after != order:
            out = [str(p.id) for p in photos if p not in after]
            _save(content, shop.id, photos, [str(p.id) for p in after], out)
    changed = sum(1 for r in rows if r.changed)
    name = shop_label(shop)
    what = (f"photo number {body.number} becomes the cover" if body.action == "cover" else "the cover moves to the end")
    if body.apply and changed:
        audit.record(session, "listing.shop_photos_changed", actor=tenant, target_tenant_id=tenant.id, connection_id=str(shop.id),
                     listings=changed, change=body.action)
        await session.commit()
    sentence = (f"{changed} listing{'' if changed == 1 else 's'}: {what} in {name}."
                + (" Saved. Other shops are unchanged." if body.apply else " Nothing is saved until you apply it."))
    return BulkOut(rows=rows, changed=changed, applied=body.apply and changed > 0, sentence=sentence)
