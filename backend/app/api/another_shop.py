"""A batch's listings in more than one shop: where each stands per shop, and "Publish in
another shop" (production report, 2026-10-27).

A seller published a batch in one shop, switched to their second shop, picked that shop's
profile in "Profile for all" and changed SKUs, and nothing went to the second shop: the
listings were already on Etsy in the first shop, so the profile was refused and the SKU
edit offered to change the first shop's drafts. This gives them the way they were looking
for, in a few clicks:

``GET  /api/batches/{id}/shops``                 per shop: how many of the batch's listings
                                                 are not sent, drafted, scheduled, published;
                                                 per listing: its state, SKU and profile in
                                                 each shop.
``POST /api/batches/{id}/another-shop/preview``  what "Publish in another shop" would do: per
                                                 listing its new SKU in that shop, and the
                                                 SKUs refused (already used there, twice in
                                                 this request, or not allowed by Etsy).
``POST /api/batches/{id}/another-shop``          the same, done: drafts in that shop.

The profile there is that shop's own (a profile whose main shop it is) or each listing's own
profile through its link to that shop (its main shop's shipping, return and processing
settings matched or copied, as everywhere). SKUs are kept, given a new prefix (PCS -> ILC)
or set per listing; a SKU already used in that shop is refused, never sent. Title, tags,
description and photos go as they are, and the drafts are made on the normal path (the same
jobs as "Create drafts", with that shop's own ids), so the allowance does not change: it
counts written listings, never drafts. Only approved listings go (CLAUDE.md rule 3); going
live there is the seller's own step, as usual (Where and when, or Scheduled). **Nothing in
any other shop changes.** The caller's own batch, listings, shops and profiles only (404).
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_quota, get_session
from app.api.distribution import _contents, _why_not
from app.api.shops import shop_label
from app.core import audit
from app.db.models import (
    Asset,
    EtsyConnection,
    GeneratedContent,
    ListingProfile,
    ListingPublication,
    ShopListingCache,
    Tenant,
    UploadBatch,
)
from app.etsy.rate_limiter import DailyQuota
from app.etsy.shops import active_shops
from app.pipeline import shop_listing, shop_targets, skus
from app.pipeline.targets import resolve_target

router = APIRouter(prefix="/api", tags=["another-shop"])

#: Words a listing's state in a shop is said in.
STATE_WORDS = {shop_listing.NOT_SENT: "not sent", shop_listing.DRAFTED: "drafted",
               shop_listing.SCHEDULED: "scheduled", shop_listing.PUBLISHED: "published"}


# --- schemas --------------------------------------------------------------------------------------


class ShopStatusOut(BaseModel):
    shop_id: uuid.UUID
    name: str
    #: The batch's written listings.
    total: int
    not_sent: int
    drafted: int
    scheduled: int
    published: int


class ListingShopOut(BaseModel):
    content_id: uuid.UUID
    group_key: str
    #: Shop id -> not_sent / drafted / scheduled / published (a shop not listed: not sent).
    states: dict[str, str] = Field(default_factory=dict)
    #: Shop id -> the SKU it carries there, for every connected shop (its own, else the group's).
    skus: dict[str, str | None] = Field(default_factory=dict)
    #: Shop id -> the profile chosen for it in that shop only (none: its own profile).
    profiles: dict[str, uuid.UUID] = Field(default_factory=dict)


class BatchShopsOut(BaseModel):
    shops: list[ShopStatusOut]
    listings: list[ListingShopOut]


class AnotherShopIn(BaseModel):
    connection_id: uuid.UUID
    #: These listings; omitted: every listing of the batch.
    content_ids: list[uuid.UUID] | None = Field(default=None, max_length=2000)
    #: A profile whose main shop is that shop; omitted: each listing's own profile there (or
    #: the one already chosen for it in that shop on the batch page).
    profile_id: uuid.UUID | None = None
    sku_mode: Literal["keep", "prefix", "each"] = "keep"
    prefix_from: str = Field(default="", max_length=64)
    prefix_to: str = Field(default="", max_length=64)
    #: ``each``: listing id -> its SKU in that shop (a listing not here keeps its SKU).
    skus: dict[uuid.UUID, str] = Field(default_factory=dict)


class ProfileChoice(BaseModel):
    id: uuid.UUID
    name: str


class AnotherShopRow(BaseModel):
    content_id: uuid.UUID
    group_key: str
    title: str
    #: Its SKU now (what it carries in that shop if nothing changes).
    sku_from: str | None
    #: Its SKU in that shop after this.
    sku: str | None
    #: new (a draft is made there) · there (already sent there: left as it is) · cannot (why)
    state: Literal["new", "there", "cannot"]
    reason: str | None = None
    #: Said next to the SKU ("does not start with PCS: kept as it is").
    note: str | None = None


class AnotherShopPreview(BaseModel):
    shop_id: uuid.UUID
    shop_name: str
    #: That shop's own profiles (their main shop is it).
    profiles: list[ProfileChoice]
    #: The listings' own profiles, by name, for "each listing's own profile".
    own_profiles: list[str]
    rows: list[AnotherShopRow]
    #: SKUs refused, one line each; while any is here nothing can be sent.
    refused: list[str]
    total: int
    sentence: str
    button: str
    disabled: str | None


class AnotherShopOut(BaseModel):
    jobs: list[schemas.PublishJobOut] = Field(default_factory=list)
    skipped: list[schemas.PublishSkipped] = Field(default_factory=list)
    message: str


# --- helpers --------------------------------------------------------------------------------------


async def _batch(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID) -> UploadBatch:
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    return batch


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


async def _assets(session: AsyncSession, contents: list[GeneratedContent]) -> dict[uuid.UUID, Asset]:
    ids = [c.asset_id for c in contents]
    return {a.id: a for a in (await session.execute(select(Asset).where(Asset.id.in_(ids)))).scalars()} if ids else {}


async def taken_in_shop(
    session: AsyncSession, tenant: Tenant, shop_id: uuid.UUID, *, mine: set[uuid.UUID]
) -> dict[str, str]:
    """SKUs already used in one shop by another listing (casefolded -> where): its listings on
    Etsy (the 6-hour listing cache), the drafts this app made there, and listings given that
    SKU for that shop that are not drafted yet. ``mine``: the listings being sent (not counted)."""
    out: dict[str, str] = {}
    own_ids = {int(p.etsy_listing_id) for p in (await session.execute(select(ListingPublication).where(
        ListingPublication.connection_id == shop_id, ListingPublication.content_id.in_(mine)))).scalars()} if mine else set()
    for row in (await session.execute(select(ShopListingCache).where(
            ShopListingCache.connection_id == shop_id, ShopListingCache.tenant_id == tenant.id))).scalars():
        if row.listing_id in own_ids:
            continue
        for sku in (row.payload or {}).get("skus") or []:
            if str(sku).strip():
                out.setdefault(str(sku).strip().casefold(), f"listing {row.listing_id}")
    for pub in (await session.execute(select(ListingPublication).where(
            ListingPublication.connection_id == shop_id, ListingPublication.tenant_id == tenant.id,
            ListingPublication.state != "deleted_on_etsy"))).scalars():
        if pub.content_id in mine or not (pub.sku or "").strip():
            continue
        out.setdefault(pub.sku.strip().casefold(), f"listing {pub.etsy_listing_id}")
    for content in (await session.execute(select(GeneratedContent).where(
            GeneratedContent.tenant_id == tenant.id, GeneratedContent.shop_overrides.is_not(None)))).scalars():
        if content.id in mine:
            continue
        sku = shop_listing.sku_override(content, shop_id)
        if sku:
            out.setdefault(sku.casefold(), f"\"{(content.title or '')[:40]}\" (not drafted yet)")
    return out


async def _target_shop(session: AsyncSession, tenant: Tenant, shop_id: uuid.UUID) -> EtsyConnection:
    shop = next((s for s in await active_shops(session, tenant.id) if s.id == shop_id), None)
    if shop is None:
        raise HTTPException(status_code=404, detail="shop not found")
    return shop


async def _shop_profiles(session: AsyncSession, tenant: Tenant, shop: EtsyConnection) -> list[ListingProfile]:
    return list((await session.execute(select(ListingProfile).where(
        ListingProfile.tenant_id == tenant.id, ListingProfile.connection_id == shop.id,
        ListingProfile.confirmed.is_(True)).order_by(ListingProfile.name))).scalars())


def _new_sku(body: AnotherShopIn, content: GeneratedContent, current: str | None) -> tuple[str | None, str | None]:
    """The listing's SKU in the target shop and a note beside it."""
    if body.sku_mode == "prefix":
        if not (body.prefix_from.strip() or body.prefix_to.strip()):
            return current, None
        new, matched = shop_listing.replace_prefix(current, body.prefix_from, body.prefix_to)
        if not matched:
            return current, f"does not start with {body.prefix_from.strip()}: kept as it is"
        return new or None, None
    if body.sku_mode == "each" and content.id in body.skus:
        return skus.clean(body.skus[content.id]) or None, None
    return current, None


async def _work(
    session: AsyncSession, tenant: Tenant, batch: UploadBatch, body: AnotherShopIn
) -> tuple[AnotherShopPreview, list[tuple[GeneratedContent, str | None]], EtsyConnection]:
    """What sending would do. Sets the new profile and SKUs on the listings in the session
    (never committed here) so each listing is checked as it would be drafted."""
    shop = await _target_shop(session, tenant, body.connection_id)
    choices = await _shop_profiles(session, tenant, shop)
    if body.profile_id is not None and all(p.id != body.profile_id for p in choices):
        raise HTTPException(status_code=404, detail="profile not found in that shop")
    contents = await _contents(session, tenant, batch, body.content_ids)
    assets = await _assets(session, contents)
    states = await shop_listing.states(session, contents)
    taken = await taken_in_shop(session, tenant, shop.id, mine={c.id for c in contents})
    own_names = sorted({p.name for p in (await session.execute(select(ListingProfile).where(
        ListingProfile.id.in_([c.listing_profile_id for c in contents if c.listing_profile_id])))).scalars()})
    rows: list[AnotherShopRow] = []
    going: list[tuple[GeneratedContent, str | None]] = []
    refused: list[str] = []
    given: dict[str, list[str]] = {}
    for content in contents:
        asset = assets.get(content.asset_id)
        key = (asset.group_key if asset else None) or ""
        label = key or (asset.original_filename if asset else "a listing")
        current = shop_listing.sku_in(content, asset.parsed_sku if asset else None, shop.id)
        state = states.get(content.id, {}).get(shop.id, shop_listing.NOT_SENT)
        row = AnotherShopRow(content_id=content.id, group_key=key, title=content.title or "", sku_from=current,
                             sku=current, state="new")
        rows.append(row)
        if state != shop_listing.NOT_SENT:
            row.state, row.reason = "there", f"already in {shop_label(shop)} ({STATE_WORDS[state]}): left as it is"
            continue
        why = await _why_not(session, content)
        if why:
            row.state, row.reason = "cannot", why
            continue
        sku, row.note = _new_sku(body, content, current)
        row.sku = sku
        if sku:
            try:
                skus.validate(sku)
            except skus.SkuInvalid as exc:
                refused.append(f"{label}: {exc}")
            else:
                where = taken.get(sku.casefold())
                if where:
                    refused.append(f"{sku} ({label}) is already used in {shop_label(shop)} by {where}.")
                given.setdefault(sku.casefold(), []).append(label)
        # Checked as it will be drafted: this shop's profile (or its own) and this SKU.
        if body.profile_id is not None:
            shop_listing.set_override(content, shop.id,
                                      profile_id=None if body.profile_id == content.listing_profile_id else body.profile_id)
        shop_listing.set_override(content, shop.id, sku=sku if sku != (asset.parsed_sku if asset else None) else None)
        target = await resolve_target(session, content, shop)
        from app.api.publish import auto_setup

        if not target.ok and not auto_setup(target):
            row.state, row.reason = "cannot", target.reason or "no profile for this shop"
            continue
        going.append((content, sku))
    for sku_key, labels in given.items():
        if len(labels) > 1:
            shown = next(r.sku for r in rows if r.sku and r.sku.casefold() == sku_key)
            refused.append(f"{shown} is given to {len(labels)} listings here: {', '.join(labels)}.")
    total = len(going)
    name = shop_label(shop)
    disabled = None
    if refused:
        disabled = f"Change the SKUs listed above first: each SKU can be used once in {name}."
    elif not rows:
        disabled = "No listings yet."
    elif total == 0:
        disabled = (f"Nothing new to send: these listings are already in {name}." if all(r.state == "there" for r in rows)
                    else "Nothing can be sent yet: the list says why.")
    preview = AnotherShopPreview(
        shop_id=shop.id, shop_name=name, profiles=[ProfileChoice(id=p.id, name=p.name) for p in choices],
        own_profiles=own_names, rows=rows, refused=refused, total=total,
        sentence=f"{_plural(total, 'listing')} → {name}: {_plural(total, 'draft')}. Nothing changes in your other shops.",
        button=f"Create {_plural(total, 'draft')} in {name}", disabled=disabled,
    )
    return preview, going, shop


# --- endpoints ------------------------------------------------------------------------------------


@router.get("/batches/{batch_id}/shops", response_model=BatchShopsOut)
async def batch_shops(batch_id: uuid.UUID, session: AsyncSession = Depends(get_session),
                      tenant: Tenant = Depends(active_tenant)) -> BatchShopsOut:
    """Where the batch's listings stand in each connected shop."""
    batch = await _batch(session, tenant, batch_id)
    contents = await _contents(session, tenant, batch, None)
    assets = await _assets(session, contents)
    shops = await active_shops(session, tenant.id)
    states = await shop_listing.states(session, contents)
    out_shops: list[ShopStatusOut] = []
    for shop in shops:
        counts = {s: 0 for s in shop_listing.STATES}
        for content in contents:
            counts[states[content.id].get(shop.id, shop_listing.NOT_SENT)] += 1
        out_shops.append(ShopStatusOut(shop_id=shop.id, name=shop_label(shop), total=len(contents), **counts))
    listings = []
    for content in contents:
        asset = assets.get(content.asset_id)
        base = asset.parsed_sku if asset else None
        listings.append(ListingShopOut(
            content_id=content.id, group_key=(asset.group_key if asset else None) or "",
            states={str(k): v for k, v in states[content.id].items()},
            skus={str(s.id): shop_listing.sku_in(content, base, s.id) for s in shops},
            profiles={str(s.id): p for s in shops if (p := shop_listing.profile_in(content, s.id)) is not None},
        ))
    return BatchShopsOut(shops=out_shops, listings=listings)


@router.post("/batches/{batch_id}/another-shop/preview", response_model=AnotherShopPreview)
async def another_shop_preview(batch_id: uuid.UUID, body: AnotherShopIn, session: AsyncSession = Depends(get_session),
                               tenant: Tenant = Depends(active_tenant)) -> AnotherShopPreview:
    preview, _, _ = await _work(session, tenant, await _batch(session, tenant, batch_id), body)
    await session.rollback()  # the preview never keeps the SKUs or profile it tried
    return preview


@router.post("/batches/{batch_id}/another-shop", response_model=AnotherShopOut)
async def another_shop(batch_id: uuid.UUID, body: AnotherShopIn, session: AsyncSession = Depends(get_session),
                       tenant: Tenant = Depends(active_tenant), quota: DailyQuota = Depends(get_quota),
                       enqueuer: Enqueuer = Depends(get_enqueuer)) -> AnotherShopOut:
    """Drafts of these listings in that shop, with its profile and these SKUs. Listings
    already sent there, and every other shop, are left as they are."""
    from app.api.publish import _publish

    batch = await _batch(session, tenant, batch_id)
    preview, going, shop = await _work(session, tenant, batch, body)
    if preview.disabled:
        await session.rollback()
        detail = preview.disabled + (" " + " ".join(preview.refused) if preview.refused else "")
        raise HTTPException(status_code=409, detail=detail)
    connected = await active_shops(session, tenant.id)
    for content, _sku in going:
        await shop_targets.set_shop(session, content, connected, shop.id, True)  # it goes there too from now on
    audit.record(session, "listing.sent_to_another_shop", actor=tenant, target_tenant_id=tenant.id,
                 batch_id=str(batch.id), shop_id=str(shop.id), listings=len(going), sku_mode=body.sku_mode,
                 profile=str(body.profile_id) if body.profile_id else "own")
    await session.flush()
    pairs = [schemas.PublishPair.model_construct(content_id=c.id, connection_id=shop.id, profile_id=None) for c, _ in going]
    request = schemas.PublishRequest.model_construct(targets=None, content_ids=None, pairs=pairs)
    result = await _publish(session, tenant, quota, enqueuer, [c for c, _ in going], request)
    await session.commit()
    message = f"Creating {_plural(len(result.jobs), 'draft')} in {shop_label(shop)}. Nothing changed in your other shops."
    return AnotherShopOut(jobs=result.jobs, skipped=result.skipped, message=message)
