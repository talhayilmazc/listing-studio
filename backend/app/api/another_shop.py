"""A batch's listings in more than one shop: where each stands per shop, and the batch page's
shop workspace (production report and seller feedback, 2026-10-27).

**The shop workspace** (batch page, "Working on: <shop>"; it replaced the "Publish in another
shop" dialog): one row per listing for the shop in the bar, its state there, its profile, SKU
and text for that shop saved as they are changed (``PUT .../shops/{shop}/profile|skus|texts``;
a field left alone follows the listing and says so), bulk actions for the selected rows, and
"Send N to <shop>" (the preview below is its summary; the send below makes the drafts). Rows
already in that shop are locked (edited on Etsy). The rows are the preview's.

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
``POST /api/batches/{id}/another-shop/rewrite``  "Rewrite for this shop": new title, tags and
                                                 description in that shop's profile style,
                                                 kept as that shop's own text; a rewrite of the
                                                 design under the allowance's rewrite rule.

**Text for the shop** (2026-10-27): each listing's title, tags and description can be edited
for that shop only, in the same step (``texts``; "Keep as it is" is ``reset``); they are held
to the same rules as every listing's text (Etsy's refusals and the trademark filter stop it,
the app's quality rules are notes), and a listing whose text Etsy would refuse is not sent.

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
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_quota, get_session, get_storage
from app.api.distribution import _contents, _why_not
from app.api.shops import shop_label
from app.core import allowance, audit
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
from app.etsy.publisher import link_for
from app.etsy.rate_limiter import DailyQuota
from app.etsy.shops import active_shops
from app.pipeline import shop_listing, shop_targets, skus
from app.pipeline.storage import Storage
from app.pipeline.targets import check_text, resolve_target

router = APIRouter(prefix="/api", tags=["another-shop"])

#: Said before "Rewrite for this shop" runs (core/allowance.py).
REWRITE_RULE = (f"Rewriting is a rewrite of the design: the first {allowance.FREE_REWRITES} of each design this "
                "period are free, then each counts as one listing generated.")

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


class ShopTextIn(BaseModel):
    """One listing's text for the target shop: only the fields sent change; ``reset`` drops
    its own text there ("Keep as it is": the listing's text goes)."""

    title: str | None = Field(default=None, max_length=1000)
    tags: list[str] | None = Field(default=None, max_length=40)
    description: str | None = Field(default=None, max_length=100_000)
    reset: bool = False


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
    #: Listing id -> its text for that shop (a listing not here: its own text there, if any,
    #: else the listing's).
    texts: dict[uuid.UUID, ShopTextIn] = Field(default_factory=dict)


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
    #: Its text as it goes to that shop, and whether any of it is that shop's own.
    title: str = ""
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    own_text: bool = False
    #: What Etsy would refuse (or the trademark filter stops): it is not sent until fixed.
    errors: list[str] = Field(default_factory=list)
    #: The app's quality notes: they never stop it.
    warnings: list[str] = Field(default_factory=list)
    #: Free rewrites of this design left this period ("Rewrite for this shop").
    free_rewrites_left: int = 0
    #: The profile chosen for it in that shop only (None: its own, through the shop link), and
    #: the name of the one it goes with.
    profile_id: uuid.UUID | None = None
    profile_name: str | None = None
    #: Its SKU there is that shop's own; which text fields are.
    sku_own: bool = False
    text_own: list[str] = Field(default_factory=list)
    approved: bool = False
    #: Where it stands in that shop (not_sent / drafted / scheduled / published) and, when it is
    #: there, its listing on Etsy.
    shop_state: str = shop_listing.NOT_SENT
    listing_link: str | None = None


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
    #: Listings whose text Etsy would refuse (their rows say why).
    text_problems: int = 0
    #: Said before "Rewrite for this shop" (the allowance's rewrite rule).
    rewrite_rule: str = REWRITE_RULE
    total: int
    sentence: str
    button: str
    disabled: str | None


class RewriteIn(BaseModel):
    connection_id: uuid.UUID
    content_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    #: The profile whose style it is written in (that shop's); omitted: the one it goes there with.
    profile_id: uuid.UUID | None = None


class RewriteRow(BaseModel):
    content_id: uuid.UUID
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    #: It counted as a listing generated (no free rewrite of the design was left).
    counted: bool = False
    #: Why it was not rewritten.
    reason: str | None = None


class RewriteOut(BaseModel):
    rows: list[RewriteRow]
    message: str


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


def _apply_text(content: GeneratedContent, shop_id: uuid.UUID, text: ShopTextIn | None) -> None:
    """The text the dialog holds for this listing in that shop: a field equal to the
    listing's own is not kept as the shop's (it follows the listing); ``reset`` drops what the
    shop had of its own first."""
    if text is None:
        return
    if text.reset:  # back to the listing's text, then whatever was typed after
        shop_listing.set_override(content, shop_id, title=None, tags=None, description=None)
    own = {"title": content.title or "", "tags": list(content.tags or []), "description": content.description or ""}
    for name in shop_listing.TEXT_FIELDS:
        value = getattr(text, name)
        if value is None:
            continue
        if name == "tags":
            value = [t.strip() for t in value if t and t.strip()]
        shop_listing.set_override(content, shop_id, **{name: None if value == own[name] else value})


async def _profile_there(
    session: AsyncSession, content: GeneratedContent, shop_id: uuid.UUID, chosen: ListingProfile | None
) -> ListingProfile | None:
    """The profile this listing goes to that shop with: the one being chosen, else its own there,
    else its own."""
    profile_id = shop_listing.profile_in(content, shop_id) or content.listing_profile_id
    return chosen or (await session.get(ListingProfile, profile_id) if profile_id else None)


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
    text_problems = 0
    chosen_profile = await session.get(ListingProfile, body.profile_id) if body.profile_id else None
    period = await allowance.status(session, tenant)
    publications = {p.content_id: p for p in (await session.execute(select(ListingPublication).where(
        ListingPublication.connection_id == shop.id, ListingPublication.content_id.in_([c.id for c in contents]),
        ListingPublication.state != "deleted_on_etsy"))).scalars()} if contents else {}
    for content in contents:
        asset = assets.get(content.asset_id)
        key = (asset.group_key if asset else None) or ""
        label = key or (asset.original_filename if asset else "a listing")
        current = shop_listing.sku_in(content, asset.parsed_sku if asset else None, shop.id)
        state = states.get(content.id, {}).get(shop.id, shop_listing.NOT_SENT)
        row = AnotherShopRow(content_id=content.id, group_key=key, title=content.title or "", sku_from=current,
                             sku=current, state="new")
        rows.append(row)
        row.title, row.tags, row.description = shop_listing.text_for(content, shop.id)
        row.approved, row.shop_state = bool(content.approved), state
        row.profile_id = shop_listing.profile_in(content, shop.id)
        row.sku_own = shop_listing.sku_override(content, shop.id) is not None
        row.text_own = sorted(shop_listing.text_in(content, shop.id))
        if state != shop_listing.NOT_SENT:
            row.state, row.reason = "there", f"already in {shop_label(shop)} ({STATE_WORDS[state]}): edited on Etsy"
            pub = publications.get(content.id)
            row.listing_link = link_for(pub.etsy_listing_id, pub.state) if pub is not None else None
            continue
        _apply_text(content, shop.id, body.texts.get(content.id))
        row.title, row.tags, row.description = shop_listing.text_for(content, shop.id)
        row.own_text = bool(shop_listing.text_in(content, shop.id))
        row.text_own = sorted(shop_listing.text_in(content, shop.id))
        row.free_rewrites_left = await allowance.free_rewrites_left(
            session, tenant, allowance.design_key(content.batch_id, key), period)
        profile = await _profile_there(session, content, shop.id, chosen_profile)
        row.profile_name = profile.name if profile is not None else None
        check = await check_text(session, content, profile, row.title, row.tags, row.description)
        row.errors, row.warnings = list(check.errors), list(check.warnings)
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
        row.profile_id = shop_listing.profile_in(content, shop.id)
        row.sku_own = shop_listing.sku_override(content, shop.id) is not None
        if row.errors:
            text_problems += 1
            continue
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
    elif text_problems:
        disabled = f"Fix the text marked below first: Etsy would refuse {_plural(text_problems, 'listing')} as written."
    elif not rows:
        disabled = "No listings yet."
    elif total == 0:
        disabled = (f"Nothing new to send: these listings are already in {name}." if all(r.state == "there" for r in rows)
                    else "Nothing can be sent yet: the list says why.")
    preview = AnotherShopPreview(
        shop_id=shop.id, shop_name=name, profiles=[ProfileChoice(id=p.id, name=p.name) for p in choices],
        own_profiles=own_names, rows=rows, refused=refused, total=total, text_problems=text_problems,
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


async def _write(
    session: AsyncSession, tenant: Tenant, content: GeneratedContent, profile: ListingProfile, storage: Storage
) -> tuple[str, list[str], str]:
    """New title, tags and description for ``profile`` from the listing's cover: the writer
    every listing is written with (its style, its prefix, the account's trademark filter),
    one vision and one content call, metered; the description is the profile's own body under
    the new opening (short style) or title (long style)."""
    from app.compliance.trademarks import blocklist_for_tenant
    from app.core import ai_meter
    from app.core.config import get_settings
    from app.pipeline.content import AnthropicContentGenerator, content_template_for, policy_for, search_style
    from app.pipeline.llm import client_for
    from app.pipeline.templates import load_template
    from app.pipeline.reference import replace_title_block, with_opening
    from app.pipeline.vision import AnthropicVisionAnalyzer

    asset = await session.get(Asset, content.asset_id)
    if asset is None or asset.files_removed_at is not None or not asset.processed_key:
        raise ValueError("its image files were deleted after the retention period")
    settings = get_settings()
    generator = AnthropicContentGenerator(
        client_for(settings, "content"), template=load_template(content_template_for(profile)),
        policy=policy_for(profile.content_template), title_prefix=profile.title_prefix or "",
        trademarks=blocklist_for_tenant(tenant), **search_style(profile))
    analyzer = AnthropicVisionAnalyzer(client_for(settings, "vision"))
    async with ai_meter.scope(tenant.id, session) as meter:
        vision = await analyzer.analyze(storage.get(asset.processed_key), asset.mime_type or "image/jpeg")
        result = await generator.generate(vision.analysis, asset.parsed_sku)
        meter.listing_written()
    body = str((profile.cached_payload or {}).get("description", ""))
    listing = result.listing
    description = with_opening(body, listing.opening) if listing.opening else replace_title_block(body, listing.title)
    return listing.title, list(listing.tags), description


@router.post("/batches/{batch_id}/another-shop/rewrite", response_model=RewriteOut)
async def rewrite_for_shop(batch_id: uuid.UUID, body: RewriteIn, session: AsyncSession = Depends(get_session),
                           tenant: Tenant = Depends(active_tenant),
                           storage: Storage = Depends(get_storage)) -> RewriteOut:
    """"Rewrite for this shop": each listing written again in that shop's profile style and
    kept as that shop's own text (the listing and every shop it is in stay as they are). A
    rewrite of the design: free while the design has free rewrites left this period, else it
    counts as a listing generated; refused (with the reset date) when none would fit."""
    from app.core.llm_status import LLMUnavailable

    batch = await _batch(session, tenant, batch_id)
    shop = await _target_shop(session, tenant, body.connection_id)
    if body.profile_id is not None and all(p.id != body.profile_id for p in await _shop_profiles(session, tenant, shop)):
        raise HTTPException(status_code=404, detail="profile not found in that shop")
    contents = await _contents(session, tenant, batch, body.content_ids)
    assets = await _assets(session, contents)
    states = await shop_listing.states(session, contents)
    rows: list[RewriteRow] = []
    for content in contents:
        row = RewriteRow(content_id=content.id)
        rows.append(row)
        if shop_listing.sent(states[content.id], shop.id):
            row.reason = f"already in {shop_label(shop)}: what is there is edited on Etsy"
            continue
        profile_id = body.profile_id or shop_listing.profile_in(content, shop.id) or content.listing_profile_id
        profile = await session.get(ListingProfile, profile_id) if profile_id else None
        if profile is None or profile.tenant_id != tenant.id or not profile.cached_payload:
            row.reason = "its profile has no reference read yet: refresh the profile first"
            continue
        asset = assets.get(content.asset_id)
        design = allowance.design_key(content.batch_id, (asset.group_key if asset else None) or "")
        current = await allowance.status(session, tenant)
        kind = await allowance.rewrite_kind(session, tenant, design, current)
        if kind == allowance.GENERATION and current.remaining <= 0:
            row.reason = current.message()
            continue
        try:
            title, tags, description = await _write(session, tenant, content, profile, storage)
        except LLMUnavailable:
            await session.rollback()
            row.reason = "writing is paused for a few minutes; try again shortly"
            break
        except ValueError as exc:
            row.reason = str(exc)
            continue
        except Exception:  # noqa: BLE001 - the provider's words are never shown to sellers
            row.reason = "it could not be rewritten this time; try again"
            continue
        if body.profile_id is not None:
            shop_listing.set_override(content, shop.id,
                                      profile_id=None if body.profile_id == content.listing_profile_id else body.profile_id)
        shop_listing.set_override(content, shop.id, title=title, tags=tags, description=description)
        allowance.record(session, tenant.id, kind, design=design)
        await session.commit()
        row.title, row.tags, row.description = title, tags, description
        row.counted = kind == allowance.GENERATION
    done = [r for r in rows if r.reason is None]
    counted = sum(1 for r in done if r.counted)
    message = (f"Rewrote {_plural(len(done), 'listing')} for {shop_label(shop)}"
               + (f"; {counted} counted as {'a listing' if counted == 1 else 'listings'} generated" if counted else ", all free rewrites")
               + "." + (f" {_plural(len(rows) - len(done), 'listing')} not rewritten: the list says why." if len(done) < len(rows) else ""))
    return RewriteOut(rows=rows, message=message)


# --- the shop workspace: a listing's profile, SKU and text for one shop, saved as changed -------


class WorkspaceProfileIn(BaseModel):
    content_ids: list[uuid.UUID] = Field(min_length=1, max_length=2000)
    #: A profile whose main shop is that shop; None: each listing's own (through its shop link).
    profile_id: uuid.UUID | None = None


class WorkspaceSkusIn(BaseModel):
    #: Listing id -> its SKU in that shop ("" or the listing's own SKU: follows the listing).
    skus: dict[uuid.UUID, str] = Field(min_length=1, max_length=2000)


class WorkspaceSkusOut(BaseModel):
    saved: int = 0
    #: One line each: already used in that shop, given twice, or not allowed by Etsy.
    refused: list[str] = Field(default_factory=list)


class WorkspaceTextsIn(BaseModel):
    texts: dict[uuid.UUID, ShopTextIn] = Field(min_length=1, max_length=2000)


class WorkspaceTextRow(BaseModel):
    content_id: uuid.UUID
    title: str
    tags: list[str]
    description: str
    text_own: list[str]
    errors: list[str]
    warnings: list[str]


async def _open_rows(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID, shop_id: uuid.UUID,
                     ids: list[uuid.UUID]) -> tuple[EtsyConnection, list[GeneratedContent], dict[uuid.UUID, Asset]]:
    """These listings of the caller's batch, none of them already in that shop (409: locked rows
    are edited on Etsy)."""
    batch = await _batch(session, tenant, batch_id)
    shop = await _target_shop(session, tenant, shop_id)
    contents = await _contents(session, tenant, batch, list(dict.fromkeys(ids)))
    states = await shop_listing.states(session, contents)
    there = [c for c in contents if shop_listing.sent(states[c.id], shop.id)]
    if there:
        raise HTTPException(status_code=409, detail=(
            f"{_plural(len(there), 'listing is', 'listings are')} already in {shop_label(shop)}: what is there is "
            "edited on Etsy."))
    return shop, contents, await _assets(session, contents)


@router.put("/batches/{batch_id}/shops/{connection_id}/profile", response_model=AnotherShopPreview)
async def workspace_profile(batch_id: uuid.UUID, connection_id: uuid.UUID, body: WorkspaceProfileIn,
                            session: AsyncSession = Depends(get_session),
                            tenant: Tenant = Depends(active_tenant)) -> AnotherShopPreview:
    """The profile these listings go to that shop with (that shop's own, or each listing's own
    through the shop link). Nothing else changes; the listing's own profile stays."""
    shop, contents, _ = await _open_rows(session, tenant, batch_id, connection_id, body.content_ids)
    if body.profile_id is not None and all(p.id != body.profile_id for p in await _shop_profiles(session, tenant, shop)):
        raise HTTPException(status_code=404, detail="profile not found in that shop")
    for content in contents:
        shop_listing.set_override(content, shop.id, profile_id=None if body.profile_id in (None, content.listing_profile_id)
                                  else body.profile_id)
    await session.commit()
    return await _rows(session, tenant, batch_id, shop.id)


async def _sku_refusals(session: AsyncSession, tenant: Tenant, shop: EtsyConnection, contents: list[GeneratedContent],
                        assets: dict[uuid.UUID, Asset], wanted: dict[uuid.UUID, str]) -> tuple[list[str], dict[uuid.UUID, str | None]]:
    """What would be refused if these SKUs were saved for that shop, and each listing's new SKU
    there (None: follows the listing). Another listing given that SKU for that shop counts
    (``taken_in_shop``)."""
    taken = await taken_in_shop(session, tenant, shop.id, mine=set(wanted))
    refused: list[str] = []
    new: dict[uuid.UUID, str | None] = {}
    given: dict[str, list[str]] = {}
    for content in contents:
        asset = assets.get(content.asset_id)
        label = (asset.group_key if asset else None) or "a listing"
        raw = skus.clean(wanted[content.id])
        base = asset.parsed_sku if asset else None
        if not raw or raw == base:
            new[content.id] = None
            raw = base or ""
        else:
            try:
                skus.validate(raw)
            except skus.SkuInvalid as exc:
                refused.append(f"{label}: {exc}")
                continue
            new[content.id] = raw
        if raw:
            where = taken.get(raw.casefold())
            if where:
                refused.append(f"{raw} ({label}) is already used in {shop_label(shop)} by {where}.")
            given.setdefault(raw.casefold(), []).append(label)
    for labels in given.values():
        if len(labels) > 1:
            refused.append(f"The same SKU is given to {len(labels)} listings here: {', '.join(labels)}.")
    return refused, new


@router.post("/batches/{batch_id}/shops/{connection_id}/skus/check", response_model=WorkspaceSkusOut)
async def workspace_skus_check(batch_id: uuid.UUID, connection_id: uuid.UUID, body: WorkspaceSkusIn,
                               session: AsyncSession = Depends(get_session),
                               tenant: Tenant = Depends(active_tenant)) -> WorkspaceSkusOut:
    """As the seller types: what saving these SKUs for that shop would refuse. Nothing is saved."""
    shop, contents, assets = await _open_rows(session, tenant, batch_id, connection_id, list(body.skus))
    refused, _ = await _sku_refusals(session, tenant, shop, contents, assets, body.skus)
    return WorkspaceSkusOut(refused=refused)


@router.put("/batches/{batch_id}/shops/{connection_id}/skus", response_model=WorkspaceSkusOut)
async def workspace_skus(batch_id: uuid.UUID, connection_id: uuid.UUID, body: WorkspaceSkusIn,
                         session: AsyncSession = Depends(get_session),
                         tenant: Tenant = Depends(active_tenant)) -> WorkspaceSkusOut:
    """These listings' SKUs in that shop, saved at once, or none of them (409 with the list) when
    one is already used there, given twice, or not allowed by Etsy. Other shops keep theirs."""
    shop, contents, assets = await _open_rows(session, tenant, batch_id, connection_id, list(body.skus))
    refused, new = await _sku_refusals(session, tenant, shop, contents, assets, body.skus)
    if refused:
        raise HTTPException(status_code=409, detail=" ".join(refused))
    for content in contents:
        before = shop_listing.sku_override(content, shop.id)
        shop_listing.set_override(content, shop.id, sku=new[content.id])
        if before != new[content.id]:
            audit.record(session, "listing.sku_changed", actor=tenant, target_tenant_id=tenant.id, on_etsy=False,
                         batch_id=str(batch_id), sku_from=before, sku_to=new[content.id], by="seller", shop_id=str(shop.id))
    await session.commit()
    return WorkspaceSkusOut(saved=len(contents))


@router.put("/batches/{batch_id}/shops/{connection_id}/texts", response_model=list[WorkspaceTextRow])
async def workspace_texts(batch_id: uuid.UUID, connection_id: uuid.UUID, body: WorkspaceTextsIn,
                          session: AsyncSession = Depends(get_session),
                          tenant: Tenant = Depends(active_tenant)) -> list[WorkspaceTextRow]:
    """These listings' title, tags and description for that shop (a field equal to the
    listing's follows it; ``reset``: the listing's text again), with what Etsy would refuse and
    the notes. Saved as typed; a listing whose text Etsy would refuse is not sent."""
    shop, contents, _ = await _open_rows(session, tenant, batch_id, connection_id, list(body.texts))
    for content in contents:
        _apply_text(content, shop.id, body.texts[content.id])
    await session.commit()
    out: list[WorkspaceTextRow] = []
    for content in contents:
        title, tags, description = shop_listing.text_for(content, shop.id)
        check = await check_text(session, content, await _profile_there(session, content, shop.id, None), title, tags, description)
        out.append(WorkspaceTextRow(content_id=content.id, title=title, tags=tags, description=description,
                                    text_own=sorted(shop_listing.text_in(content, shop.id)),
                                    errors=list(check.errors), warnings=list(check.warnings)))
    return out


async def _rows(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID, shop_id: uuid.UUID) -> AnotherShopPreview:
    preview, _, _ = await _work(session, tenant, await _batch(session, tenant, batch_id), AnotherShopIn(connection_id=shop_id))
    await session.rollback()
    return preview
