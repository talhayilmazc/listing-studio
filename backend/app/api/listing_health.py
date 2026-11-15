"""Listing health: what sells is left alone, real problems first, and fixes the seller approves (docs/listing-health.md).

``GET  /listing-health``           the shop's listings in buckets (Fix now, Worth improving, Selling, ...), from the
                                   six-hour copy and the shop's own sales. No score.
``POST /listing-health/rewrite-check``  what "Rewrite" would use of the allowance, said before it runs.
``POST /listing-health/preview``   makes a before and an after per listing. Nothing goes to Etsy. Only the fixes the
                                   listing's own problems offer: never a title or tag change for a selling listing,
                                   unless a Fix now problem asks for it.
``GET  /listing-health/fixes``     previews and what was done, with Undo while it lasts.
``POST /listing-health/apply``     the seller's approval of selected previews. At most 50 live listings.
``POST /listing-health/undo``      writes back the copy kept when the fix was made, for 7 days.
``POST /listing-health/discard``   drops previews nobody approved.
``GET  /listing-health/sales``     each of the account's shops: whether its sales were read, and how far a read is.
``POST /listing-health/sales/read`` "Read sales" / "Read sales for all N shops": queued, read one after another
                                   (workers/sales.py::advance_queue), the same estimate and first read as Reports.
``POST /listing-health/brands/keep``  "Keep it, I know the risk": the listing's brand names stop being Fix now.
``POST /listing-health/brands/warn``  "Show the warning again".

Only the seller's own shops, read from their own shop copy and their own sales: another account's shop, listing or
fix is 404, and nothing here reads any other seller's data. Every write to Etsy is a job that a click approved.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from redis.asyncio import Redis

from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_redis, get_session
from app.api.shop import _is_stale, _shop_rows, selected_shop, sync_if_stale
from app.api.shops import shop_label
from app.core import allowance, audit, usage
from app.core.config import get_settings
from app.db.models import (
    AllowanceUse,
    Asset,
    BrandKept,
    EtsyConnection,
    GeneratedContent,
    ListingFix,
    ListingProfile,
    ListingPublication,
    ProfileShopLink,
    SalesDaily,
    SalesSync,
    ShopListingCache,
    Tenant,
)
from app.etsy.shops import active_shops, owned_shop
from app.pipeline import listing_fixes as F
from app.pipeline import listing_health as H
from app.workers import sales as sales_worker
from app.workers.listing_fixes import READ_AT_ONCE, read_spec

router = APIRouter(prefix="/api", tags=["listing-health"])

PAGE_SIZE = 25
NOT_CONFIRMED = "Nothing was changed: the approval was not confirmed."
TOO_MANY = f"At most {F.MAX_PER_APPROVAL} live listings can be changed in one approval, so that a mistake reaches no further. Choose fewer."
OLD_REWRITE_RULE = (
    f"A rewrite follows the allowance's rewrite rule: the first {allowance.FREE_REWRITES} of each design this period are "
    "free, then each counts as one listing generated. A listing the app did not write counts once, the first time."
)
LIVE_REWRITE_RULE = (
    "Each live listing our AI rewrites counts as one listing, once per allowance period, however many times it is "
    "rewritten in that period. A listing already counted this period adds nothing, and a rewrite that fails counts nothing."
)
SELLING_LEFT_ALONE = "It is selling: its title and tags are left as they are."
#: How long an approved job may sit before the page's next read queues it again (Redis may have been emptied).
STUCK_AFTER = timedelta(minutes=30)
UNDOABLE = ("applied", "failed", "undo_failed")
#: What the page can be asked to show: a bucket, the two that ask something of the seller, or everything.
TO_DO = "to_do"
SHOWS = (TO_DO, "all", *H.BUCKETS)


def rewrite_rule() -> str:
    return LIVE_REWRITE_RULE if usage.mode() == "on" else OLD_REWRITE_RULE


def accept_rule() -> str:
    return "Rewriting uses your allowance, so it has to be asked for: " + rewrite_rule()


def windows() -> H.Windows:
    settings = get_settings()
    return H.Windows(selling_days=settings.listing_health_selling_days, new_days=settings.listing_health_new_days)


class ProblemOut(BaseModel):
    id: str
    kind: str
    level: str
    sentence: str
    fixes: list[str]


class ListingHealthOut(BaseModel):
    listing_id: int
    title: str
    url: str
    image: str | None = None
    tags: list[str]
    state: str = "active"
    bucket: str
    problems: list[ProblemOut]
    suggestions: list[ProblemOut] = []
    note: str | None = None
    brands: list[str]
    #: Brand names the seller kept on this listing ("Keep it, I know the risk"): no longer Fix now.
    kept_brands: list[str] = []
    #: Units sold in the selling window, and in the history kept (13 months); favourites from Etsy's listing data.
    recent_sales: int = 0
    past_sales: int = 0
    favourites: int = 0
    #: Attributes are read for the listings on screen; until then those problems cannot be judged.
    attributes_read: bool
    profile_id: uuid.UUID | None = None
    profile_name: str | None = None
    #: More than one profile fits this category, so the seller says which.
    profile_unclear: bool = False


class HealthPageOut(BaseModel):
    connection_id: uuid.UUID | None = None
    shop_name: str | None = None
    #: The shop's copy is being read: nothing to show until it is.
    reading: bool = False
    #: A sync of the copy is queued or running (False while reading: the last try failed, it is tried again soon).
    syncing: bool = False
    total: int = 0
    #: Listings per bucket (H.BUCKETS); the page's three figures are fix_now, worth_improving and selling.
    counts: dict[str, int] = {}
    #: The shop's sales have been read, so "selling" means something.
    sales_known: bool = False
    selling_days: int = 60
    new_days: int = 30
    show: str = TO_DO
    page: int = 1
    pages: int = 1
    listings: list[ListingHealthOut] = []
    max_per_approval: int = F.MAX_PER_APPROVAL
    undo_days: int = F.UNDO_DAYS
    rewrite_rule: str = ""


class FixOut(BaseModel):
    id: uuid.UUID
    connection_id: uuid.UUID
    listing_id: int
    url: str
    state: str
    note: str | None = None
    kinds: list[str]
    before: dict[str, Any]
    after: dict[str, Any]
    #: A rewrite that counted as one listing.
    counted: bool = False
    created_at: datetime
    applied_at: datetime | None = None
    undo_until: datetime | None = None
    can_undo: bool = False


class FixesOut(BaseModel):
    fixes: list[FixOut]


class SkippedOut(BaseModel):
    listing_id: int
    title: str
    reason: str


class PreviewIn(BaseModel):
    connection_id: uuid.UUID
    listing_ids: list[int] = Field(min_length=1)
    kinds: list[str] = Field(min_length=1)
    #: A profile the seller chose, when the listing's own is not clear.
    profile_id: uuid.UUID | None = None
    #: "Rewrite" uses the allowance: the page said so and the seller pressed it.
    accept_rewrite: bool = False


class PreviewOut(BaseModel):
    fixes: list[FixOut]
    skipped: list[SkippedOut]
    #: Attributes still being read for these listings: preview again in a moment.
    still_reading: list[int] = []


class RewriteCheckIn(BaseModel):
    connection_id: uuid.UUID
    listing_ids: list[int] = Field(min_length=1)


class RewriteCheckOut(BaseModel):
    rule: str
    free: int
    counted: int
    remaining: int
    fits: bool


class ApproveIn(BaseModel):
    fix_ids: list[uuid.UUID] = Field(min_length=1)
    confirm: bool = False


class ApproveOut(BaseModel):
    started: int
    #: Previews too old to apply (they show listing content, kept six hours).
    expired: int = 0
    message: str


def _utc(at: datetime | None) -> datetime | None:
    if at is None:
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


def _url(listing_id: int, payload: dict[str, Any] | None = None) -> str:
    return str((payload or {}).get("url") or f"https://www.etsy.com/listing/{listing_id}")


def _image(payload: dict[str, Any]) -> str | None:
    images = payload.get("images")
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        return None
    return images[0].get("url_170x135") or images[0].get("url_75x75") or images[0].get("url_fullxfull")


def _fix_out(fix: ListingFix, now: datetime) -> FixOut:
    undo_until = _utc(fix.undo_until)
    return FixOut(
        id=fix.id, connection_id=fix.connection_id, listing_id=fix.listing_id, url=_url(fix.listing_id), state=fix.state, note=fix.note,
        kinds=list(fix.kinds or []), before=fix.before or {}, after=fix.after or {},
        counted=fix.allowance_kind in (allowance.GENERATION, usage.LIVE_COUNTED),
        created_at=fix.created_at, applied_at=fix.applied_at, undo_until=undo_until,
        can_undo=fix.state in UNDOABLE and fix.written_at is not None and undo_until is not None and undo_until > now,
    )


class _Profiles:
    """Which of the account's profiles a listing belongs to: the one that wrote it, else the only one that
    fits its category in this shop. Two that fit is the seller's choice, not ours to guess."""

    def __init__(self, session: AsyncSession, tenant: Tenant, connection_id: uuid.UUID) -> None:
        self.session, self.tenant, self.connection_id = session, tenant, connection_id
        self.profiles: list[ListingProfile] = []
        self.written: dict[int, tuple[uuid.UUID | None, GeneratedContent | None]] = {}
        self.tags: dict[uuid.UUID, list[str]] = {}

    async def load(self) -> "_Profiles":
        linked = select(ProfileShopLink.profile_id).where(ProfileShopLink.connection_id == self.connection_id)
        self.profiles = list((await self.session.execute(select(ListingProfile).where(
            ListingProfile.tenant_id == self.tenant.id,
            (ListingProfile.connection_id == self.connection_id) | ListingProfile.id.in_(linked),
        ))).scalars())
        rows = await self.session.execute(
            select(ListingPublication.etsy_listing_id, GeneratedContent)
            .join(GeneratedContent, GeneratedContent.id == ListingPublication.content_id, isouter=True)
            .where(ListingPublication.tenant_id == self.tenant.id, ListingPublication.connection_id == self.connection_id)
        )
        for listing_id, content in rows.all():
            if listing_id is not None:
                self.written[int(listing_id)] = (content.listing_profile_id if content else None, content)
        for profile in self.profiles:
            self.tags[profile.id] = await _reference_tags(self.session, self.tenant, profile)
        return self

    def for_listing(self, listing: dict[str, Any], chosen: uuid.UUID | None = None) -> tuple[ListingProfile | None, bool]:
        by_id = {p.id: p for p in self.profiles}
        own = self.written.get(int(listing.get("listing_id") or 0), (None, None))[0]
        if own in by_id:
            return by_id[own], False
        taxonomy = listing.get("taxonomy_id")
        fits = [p for p in self.profiles if taxonomy is not None and (p.cached_payload or {}).get("taxonomy_id") == taxonomy]
        if len(fits) == 1:
            return fits[0], False
        # The seller's choice settles only what was unclear, and only among those that fit the category.
        picked = next((p for p in fits if p.id == chosen), None)
        return picked, picked is None and len(fits) > 1

    @staticmethod
    def attributes_of(profile: ListingProfile | None, listing: dict[str, Any]) -> list[dict[str, Any]] | None:
        """A property id means nothing outside its category, so only from the same category."""
        payload = (profile.cached_payload or {}) if profile is not None else {}
        if not payload or payload.get("taxonomy_id") != listing.get("taxonomy_id"):
            return None
        return list(payload.get("attributes") or [])

    def required_for(self, listing: dict[str, Any]) -> list[dict[str, Any]] | None:
        """The category's required attributes, from any of the account's profiles in the same category (Etsy's
        getPropertiesByTaxonomyId, read at the profile's refresh). None: no profile knows this category."""
        taxonomy = listing.get("taxonomy_id")
        found: dict[int, dict[str, Any]] = {}
        known = False
        for profile in self.profiles:
            payload = profile.cached_payload or {}
            if taxonomy is None or payload.get("taxonomy_id") != taxonomy or not isinstance(payload.get("category_properties"), list):
                continue
            known = True
            for prop in payload["category_properties"]:
                if prop.get("required") and prop.get("id") is not None:
                    found[int(prop["id"])] = {"id": int(prop["id"]), "name": prop.get("name")}
        return list(found.values()) if known else None

    def short_style(self, listing: dict[str, Any], profile: ListingProfile | None) -> bool:
        """A listing the app wrote in the long keyword style is not told off for being in it."""
        if int(listing.get("listing_id") or 0) not in self.written or profile is None:
            return True
        return (profile.listing_style or "classic") == "search"

    def tags_to_add(self, listing: dict[str, Any], profile: ListingProfile | None, unclear: bool) -> list[str]:
        if profile is None or unclear:
            return []
        text = F.text_of(listing)
        return F.added_tags(text["title"], text["tags"], self.tags.get(profile.id, []))


class _Sales:
    """The shop's own sales, per listing: within the selling window, and in all the history kept. ``known`` is
    False until the first sales read has finished (or while the seller's "Delete my sales history" is younger than
    the window): then nobody can say a listing is not selling."""

    def __init__(self, known: bool, recent: dict[int, int], past: dict[int, int]) -> None:
        self.known, self.recent, self.past = known, recent, past


async def _sales(session: AsyncSession, tenant: Tenant, connection: EtsyConnection, w: H.Windows) -> _Sales:
    today = datetime.now(timezone.utc).date()
    since = today - timedelta(days=w.selling_days)
    sync = await session.get(SalesSync, connection.id)
    history_from: date | None = connection.sales_history_from
    known = sync is not None and sync.state == "complete" and (history_from is None or history_from <= since)
    rows = (await session.execute(
        select(SalesDaily.listing_id, func.sum(SalesDaily.units),
               func.sum(case((SalesDaily.day >= since, SalesDaily.units), else_=0)))
        .where(SalesDaily.tenant_id == tenant.id, SalesDaily.connection_id == connection.id)
        .group_by(SalesDaily.listing_id)
    )).all() if sync is not None else []
    past = {int(lid): int(total or 0) for lid, total, _ in rows}
    recent = {int(lid): int(r or 0) for lid, _, r in rows if r}
    return _Sales(known, recent, past)


def _fresh(rows: list[ShopListingCache]) -> list[ShopListingCache]:
    """Within the copy's six hours, active or stopped (an inactive, expired or sold-out listing may have sold before)."""
    return [r for r in rows if not _is_stale(r.fetched_at) and str(r.payload.get("state") or "") in ("active", *H.STOPPED)]


def _active(rows: list[ShopListingCache]) -> list[ShopListingCache]:
    return [r for r in rows if not _is_stale(r.fetched_at) and str(r.payload.get("state") or "") == "active"]


Judged = tuple[ShopListingCache, H.Verdict, ListingProfile | None, bool]


async def _kept(session: AsyncSession, tenant: Tenant, connection_id: uuid.UUID) -> dict[int, set[str]]:
    """The brand names the seller kept, per listing in this shop (folded)."""
    rows = await session.execute(select(BrandKept.listing_id, BrandKept.brand).where(
        BrandKept.tenant_id == tenant.id, BrandKept.connection_id == connection_id))
    out: dict[int, set[str]] = {}
    for listing_id, brand in rows.all():
        out.setdefault(int(listing_id), set()).add(brand.casefold())
    return out


def _judge_rows(rows: list[ShopListingCache], profiles: _Profiles, sales: _Sales, w: H.Windows,
                chosen: uuid.UUID | None = None, kept: dict[int, set[str]] | None = None) -> list[Judged]:
    now = datetime.now(timezone.utc)
    out: list[Judged] = []
    for row in rows:
        profile, unclear = profiles.for_listing(row.payload, chosen)
        lid = int(row.listing_id)
        verdict = H.classify(
            row.payload, recent_units=sales.recent.get(lid, 0), past_units=sales.past.get(lid, 0), sales_known=sales.known,
            properties=row.payload.get("_properties"), required=profiles.required_for(row.payload),
            reference_attributes=profiles.attributes_of(profile, row.payload),
            tags_to_add=profiles.tags_to_add(row.payload, profile, unclear),
            short_style=profiles.short_style(row.payload, profile), now=now, windows=w,
            kept_brands=(kept or {}).get(lid, set()),
        )
        if verdict is not None:
            out.append((row, verdict, profile, unclear))
    return out


def _problem_out(p: H.Problem) -> ProblemOut:
    return ProblemOut(id=p.id, kind=p.kind, level=p.level, sentence=p.sentence, fixes=list(p.fixes))


@router.get("/listing-health", response_model=HealthPageOut)
async def listing_health(
    shop: uuid.UUID | None = None,
    page: int = 1,
    show: str = TO_DO,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
    redis: Redis = Depends(get_redis),
) -> HealthPageOut:
    w = windows()
    show = show if show in SHOWS else TO_DO
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        return HealthPageOut(selling_days=w.selling_days, new_days=w.new_days, rewrite_rule=rewrite_rule())
    rows = await _shop_rows(session, tenant, connection)
    # The shop's own listing sync, as every page that shows its listings asks for it: once per shop, at most
    # one try per ten minutes (etsy/shop_sync.py). A shop read in the last six hours with no listings is current.
    stale, state = await sync_if_stale(redis, enqueuer, connection, rows)
    if stale:
        return HealthPageOut(connection_id=connection.id, shop_name=shop_label(connection), reading=True, syncing=state.syncing,
                             selling_days=w.selling_days, new_days=w.new_days, rewrite_rule=rewrite_rule())
    profiles = await _Profiles(session, tenant, connection.id).load()
    sales = await _sales(session, tenant, connection, w)
    kept = await _kept(session, tenant, connection.id)
    # Regular expressions over a whole shop: off the event loop.
    judged = await asyncio.to_thread(_judge_rows, _fresh(rows), profiles, sales, w, None, kept)
    judged.sort(key=lambda item: (*item[1].sort_key(), int(item[0].listing_id)))
    counts = {b: sum(1 for _, v, _, _ in judged if v.bucket == b) for b in H.BUCKETS}
    if show == "all":
        shown = judged
    elif show == TO_DO:
        shown = [item for item in judged if item[1].bucket in (H.FIX_NOW, H.IMPROVE)]
    else:
        shown = [item for item in judged if item[1].bucket == show]
    pages = max(1, (len(shown) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(1, page), pages)
    window = shown[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    unread = [int(r.listing_id) for r, _, _, _ in window if "_properties" not in r.payload and r.payload.get("state") == "active"]
    if unread:
        await enqueuer.enqueue("read_listing_properties", read_spec(connection.id, unread[:READ_AT_ONCE]),
                               _job_id=f"props:{connection.id}:{page}:{show}")
    listings = []
    for row, verdict, profile, unclear in window:
        text = F.text_of(row.payload)
        listings.append(ListingHealthOut(
            listing_id=int(row.listing_id), title=text["title"], url=_url(int(row.listing_id), row.payload), image=_image(row.payload),
            tags=text["tags"], state=str(row.payload.get("state") or "active"), bucket=verdict.bucket,
            problems=[_problem_out(p) for p in verdict.problems], suggestions=[_problem_out(p) for p in verdict.suggestions],
            note=verdict.note, brands=verdict.brands, kept_brands=verdict.kept_brands, recent_sales=verdict.recent_units, past_sales=verdict.past_units,
            favourites=verdict.favourites, attributes_read="_properties" in row.payload, profile_id=profile.id if profile else None,
            profile_name=profile.name if profile else None, profile_unclear=unclear,
        ))
    return HealthPageOut(
        connection_id=connection.id, shop_name=shop_label(connection), total=len(judged), counts=counts, sales_known=sales.known,
        selling_days=w.selling_days, new_days=w.new_days, show=show, page=page, pages=pages, listings=listings,
        rewrite_rule=rewrite_rule(),
    )


async def _own_rows(session: AsyncSession, tenant: Tenant, connection_id: uuid.UUID, listing_ids: list[int]) -> list[ShopListingCache]:
    """Only this account's listings in this shop, still within the copy's six hours. Others are not there."""
    rows = await session.execute(select(ShopListingCache).where(
        ShopListingCache.tenant_id == tenant.id, ShopListingCache.connection_id == connection_id,
        ShopListingCache.listing_id.in_(listing_ids),
    ))
    return _active(list(rows.scalars()))


async def _rewrite_kinds(session: AsyncSession, tenant: Tenant, rows: list[ShopListingCache], profiles: _Profiles) -> dict[int, tuple[str, str]]:
    """The old rule, kept while USAGE_COUNTER is shadow: (design, kind) per listing. A listing the app wrote is a
    rewrite of its design; one it never wrote counts once, the first time."""
    current = await allowance.status(session, tenant)
    out: dict[int, tuple[str, str]] = {}
    for row in rows:
        content = profiles.written.get(int(row.listing_id), (None, None))[1]
        if content is not None:
            asset = await session.get(Asset, content.asset_id) if content.asset_id else None
            design = allowance.design_key(content.batch_id, (asset.group_key if asset else None) or "")
            out[int(row.listing_id)] = (design, await allowance.rewrite_kind(session, tenant, design, current))
            continue
        design = allowance.listing_key(int(row.listing_id))
        made = await session.scalar(select(func.count()).select_from(AllowanceUse).where(
            AllowanceUse.tenant_id == tenant.id, AllowanceUse.design == design))
        out[int(row.listing_id)] = (design, await allowance.rewrite_kind(session, tenant, design, current) if made else allowance.GENERATION)
    return out


async def _would_count(session: AsyncSession, tenant: Tenant, connection_id: uuid.UUID, rows: list[ShopListingCache],
                       old: dict[int, tuple[str, str]]) -> set[int]:
    """The listings a rewrite now would add to the count: under the listing counter, those not counted this period;
    in shadow, the old rule's."""
    if usage.mode() != "on":
        return {lid for lid, (_, kind) in old.items() if kind == allowance.GENERATION}
    current = await allowance.status(session, tenant)
    ids = [int(r.listing_id) for r in rows]
    done = await usage.live_counted_already(session, tenant.id, connection_id, ids, current.period_start, current.resets_at)
    return set(ids) - done


@router.post("/listing-health/rewrite-check", response_model=RewriteCheckOut)
async def rewrite_check(
    body: RewriteCheckIn,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
) -> RewriteCheckOut:
    """Said before "Rewrite" runs: nothing is written and nothing is counted by asking."""
    if await owned_shop(session, tenant.id, body.connection_id) is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    if len(set(body.listing_ids)) > F.MAX_PER_APPROVAL:
        raise HTTPException(status_code=422, detail=TOO_MANY)
    rows = await _own_rows(session, tenant, body.connection_id, body.listing_ids)
    profiles = await _Profiles(session, tenant, body.connection_id).load()
    counted = len(await _would_count(session, tenant, body.connection_id, rows, await _rewrite_kinds(session, tenant, rows, profiles)))
    current = await allowance.status(session, tenant)
    return RewriteCheckOut(rule=rewrite_rule(), free=len(rows) - counted, counted=counted, remaining=current.remaining,
                           fits=counted <= current.remaining)


def _refused(verdict: H.Verdict | None, kind: str) -> str | None:
    """Why this fix is not offered for this listing, or None when one of its problems offers it."""
    if verdict is None:
        return "it is not one of the listings Listing health looks at"
    if kind in verdict.fixes:
        return None
    if kind == F.TAGS and any(p.id == "tags_missing" for p in verdict.problems):
        return None  # tags to add depend on the profile, which the seller may be choosing now
    if verdict.recent_units > 0 and kind in F.TITLE_OR_TAGS:
        return SELLING_LEFT_ALONE[0].lower() + SELLING_LEFT_ALONE[1:].rstrip(".")
    if verdict.bucket == H.NOT_JUDGED and kind in F.TITLE_OR_TAGS:
        return "its sales have not been read yet, so its title and tags are left as they are"
    if verdict.bucket == H.TOO_NEW and kind in F.TITLE_OR_TAGS:
        return "it is too new to judge"
    return None if kind in (F.CLEAN, F.ATTRIBUTES, F.FIX_TAGS) else "none of its problems asks for this"


@router.post("/listing-health/preview", response_model=PreviewOut)
async def preview(
    body: PreviewIn,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> PreviewOut:
    """A before and an after for each listing. Nothing goes to Etsy from here."""
    connection = await owned_shop(session, tenant.id, body.connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    kinds = [k for k in F.KINDS if k in body.kinds]
    if not kinds:
        raise HTTPException(status_code=422, detail="Choose what to fix.")
    wanted_ids = list(dict.fromkeys(body.listing_ids))
    if len(wanted_ids) > F.MAX_PER_APPROVAL:
        raise HTTPException(status_code=422, detail=TOO_MANY)
    if F.REWRITE in kinds and not body.accept_rewrite:
        raise HTTPException(status_code=422, detail=accept_rule())
    profiles = await _Profiles(session, tenant, connection.id).load()
    if body.profile_id is not None and body.profile_id not in {p.id for p in profiles.profiles}:
        raise HTTPException(status_code=404, detail="Profile not found")
    rows = await _own_rows(session, tenant, connection.id, wanted_ids)
    # The listing's own problems decide which fixes it may get: a selling listing's title and tags are left alone.
    w = windows()
    sales = await _sales(session, tenant, connection, w)
    kept = await _kept(session, tenant, connection.id)
    verdicts = {int(r.listing_id): v for r, v, _, _ in _judge_rows(rows, profiles, sales, w, body.profile_id, kept)}
    skipped: list[SkippedOut] = []
    allowed_rows = []
    for row in rows:
        why = next((r for r in (_refused(verdicts.get(int(row.listing_id)), k) for k in kinds) if r), None)
        if why:
            skipped.append(SkippedOut(listing_id=int(row.listing_id), title=F.text_of(row.payload)["title"], reason=why))
        else:
            allowed_rows.append(row)
    rows = allowed_rows
    rewrites: dict[int, tuple[str, str]] = {}
    if F.REWRITE in kinds and rows:
        rewrites = await _rewrite_kinds(session, tenant, rows, profiles)
        counted = len(await _would_count(session, tenant, connection.id, rows, rewrites))
        if counted:
            try:
                await allowance.check(session, tenant, counted)
            except allowance.AllowanceExceeded as exc:
                raise HTTPException(status_code=429, detail=str(exc)) from None
    fixes: list[ListingFix] = []
    reading: list[int] = []
    for row in rows:
        listing = row.payload
        text = F.text_of(listing)
        listing_id = int(row.listing_id)
        profile, unclear = profiles.for_listing(listing, body.profile_id)
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        done: list[str] = []
        reasons: list[str] = []
        if F.ATTRIBUTES in kinds:
            properties = listing.get("_properties")
            if properties is None:
                reading.append(listing_id)
            elif unclear:
                reasons.append("more than one profile fits its category: choose which")
            else:
                missing = F.missing_attributes(properties, profiles.attributes_of(profile, listing))
                if missing:
                    current = F.attributes_by_id(properties)
                    before["attributes"] = [current.get(int(m["property_id"])) or {**m, "value_ids": [], "values": []} for m in missing]
                    after["attributes"] = missing
                    done.append(F.ATTRIBUTES)
                else:
                    reasons.append("no attribute to fill from a profile in the same category")
        if F.CLEAN in kinds:
            found = F.stray(listing)
            if found:
                # Only the fields with stray characters, cleaned; the seller sees both before approving.
                before.update(found[0])
                after.update(found[1])
                done.append(F.CLEAN)
            else:
                reasons.append("no stray characters in its title, tags or description")
        if F.REWRITE in kinds:
            before.update(text)
            after.update(text)  # the model's words replace these before anything can be approved
            done.append(F.REWRITE)
        elif F.TAGS in kinds:
            added = profiles.tags_to_add(listing, profile, unclear)
            if added:
                before["tags"] = text["tags"]
                after["tags"] = [*text["tags"], *added]
                done.append(F.TAGS)
            elif len(text["tags"]) >= 13:
                reasons.append("it already has 13 tags")
            else:
                reasons.append("the profile's reference listing has no tag to add" if profile else "no profile to take tags from")
        elif F.FIX_TAGS in kinds:
            cleaned = F.fixed_tags(text["tags"])
            if cleaned is not None:
                before["tags"], after["tags"] = text["tags"], cleaned
                done.append(F.FIX_TAGS)
            else:
                reasons.append("Etsy accepts all of its tags")
        if not done:
            if reasons:
                skipped.append(SkippedOut(listing_id=listing_id, title=text["title"], reason="; ".join(reasons)))
            continue
        await session.execute(delete(ListingFix).where(
            ListingFix.tenant_id == tenant.id, ListingFix.listing_id == listing_id,
            ListingFix.state.in_(("proposed", "writing", "could_not_write")),
        ))
        design, kind = rewrites.get(listing_id, (None, None))
        fix = ListingFix(
            id=uuid.uuid4(), tenant_id=tenant.id, connection_id=connection.id, listing_id=listing_id, kinds=done, before=before, after=after,
            state="writing" if F.REWRITE in done else "proposed", design=design, allowance_kind=kind,
        )
        session.add(fix)
        fixes.append(fix)
    missing_rows = set(wanted_ids) - {int(r.listing_id) for r in rows} - {s.listing_id for s in skipped}
    skipped += [SkippedOut(listing_id=i, title="", reason="not one of this shop's active listings, or its copy is out of date") for i in missing_rows]
    await session.commit()
    if reading:
        await enqueuer.enqueue("read_listing_properties", read_spec(connection.id, reading[:READ_AT_ONCE]), _job_id=f"props:{connection.id}:preview")
    for fix in fixes:
        if fix.state == "writing":
            await enqueuer.enqueue("write_listing_fix_text", str(fix.id))
    now = datetime.now(timezone.utc)
    return PreviewOut(fixes=[_fix_out(f, now) for f in fixes], skipped=skipped, still_reading=reading)


async def _reference_tags(session: AsyncSession, tenant: Tenant, profile: ListingProfile | None) -> list[str]:
    """The tags on the seller's own reference listing, while its copy is within its six hours."""
    if profile is None or profile.reference_listing_id is None:
        return []
    row = await session.get(ShopListingCache, (tenant.id, int(profile.reference_listing_id)))
    if row is None or _is_stale(row.fetched_at):
        return []
    return [str(t) for t in row.payload.get("tags") or []]


async def _own_fixes(session: AsyncSession, tenant: Tenant, ids: list[uuid.UUID]) -> list[ListingFix]:
    wanted = list(dict.fromkeys(ids))
    fixes = list((await session.execute(select(ListingFix).where(ListingFix.tenant_id == tenant.id, ListingFix.id.in_(wanted)))).scalars())
    if len(fixes) != len(wanted):
        raise HTTPException(status_code=404, detail="Fix not found")
    return fixes


@router.get("/listing-health/fixes", response_model=FixesOut)
async def list_fixes(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> FixesOut:
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        return FixesOut(fixes=[])
    fixes = list((await session.execute(select(ListingFix).where(
        ListingFix.tenant_id == tenant.id, ListingFix.connection_id == connection.id,
    ).order_by(ListingFix.created_at.desc()).limit(200))).scalars())
    now = datetime.now(timezone.utc)
    # A job lost from the queue (Redis emptied) goes again. It repeats safely: it reads the listing first.
    for fix in fixes:
        started = _utc(fix.approved_at)
        if fix.state in ("approved", "undoing") and started is not None and now - started > STUCK_AFTER:
            await enqueuer.enqueue("apply_listing_fix" if fix.state == "approved" else "undo_listing_fix", str(fix.id))
    return FixesOut(fixes=[_fix_out(f, now) for f in fixes])


@router.post("/listing-health/apply", response_model=ApproveOut)
async def apply_fixes(
    body: ApproveIn,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> ApproveOut:
    """The seller's approval. Only this starts a write to a live listing, one job per listing."""
    if not body.confirm:
        raise HTTPException(status_code=422, detail=NOT_CONFIRMED)
    fixes = await _own_fixes(session, tenant, body.fix_ids)
    if len({f.listing_id for f in fixes}) > F.MAX_PER_APPROVAL:
        raise HTTPException(status_code=422, detail=TOO_MANY)
    now = datetime.now(timezone.utc)
    too_old = now - timedelta(hours=F.PREVIEW_HOURS)
    approval = uuid.uuid4()
    started: list[ListingFix] = []
    expired = 0
    for fix in fixes:
        if fix.state != "proposed":
            continue
        if _utc(fix.created_at) < too_old:
            expired += 1
            continue
        fix.state, fix.approved_at, fix.approval_id = "approved", now, approval
        started.append(fix)
    if not started and not expired:
        raise HTTPException(status_code=409, detail="None of these previews is waiting for approval.")
    if started:
        audit.record(session, "listing.health_fix_approved", actor=tenant, target_tenant_id=tenant.id, approval_id=str(approval),
                     listings=len(started), trigger="seller_click")
    await session.commit()
    for fix in started:
        await enqueuer.enqueue("apply_listing_fix", str(fix.id))
    return ApproveOut(started=len(started), expired=expired, message=_said(len(started), expired))


def _said(started: int, expired: int) -> str:
    parts = [f"Changing {started} live listing{'' if started == 1 else 's'}." if started else ""]
    if expired:
        parts.append(f"{expired} preview{' is' if expired == 1 else 's are'} over {F.PREVIEW_HOURS} hours old, so nothing was changed there: preview again.")
    return " ".join(p for p in parts if p)


@router.post("/listing-health/undo", response_model=ApproveOut)
async def undo_fixes(
    body: ApproveIn,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> ApproveOut:
    if not body.confirm:
        raise HTTPException(status_code=422, detail=NOT_CONFIRMED)
    fixes = await _own_fixes(session, tenant, body.fix_ids)
    if len({f.listing_id for f in fixes}) > F.MAX_PER_APPROVAL:
        raise HTTPException(status_code=422, detail=TOO_MANY)
    now = datetime.now(timezone.utc)
    started: list[ListingFix] = []
    for fix in fixes:
        if _fix_out(fix, now).can_undo:
            fix.state, fix.approved_at, fix.note = "undoing", now, None
            started.append(fix)
    if not started:
        raise HTTPException(status_code=409, detail=f"Undo lasts {F.UNDO_DAYS} days after a fix, and none of these can be undone now.")
    await session.commit()
    for fix in started:
        await enqueuer.enqueue("undo_listing_fix", str(fix.id))
    return ApproveOut(started=len(started), message=f"Putting {len(started)} listing{'' if len(started) == 1 else 's'} back.")


@router.post("/listing-health/discard", response_model=ApproveOut)
async def discard_fixes(
    body: ApproveIn,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
) -> ApproveOut:
    """Drops previews nobody approved. Nothing was sent for them."""
    fixes = await _own_fixes(session, tenant, body.fix_ids)
    gone = [f for f in fixes if f.state in ("proposed", "could_not_write")]
    for fix in gone:
        await session.delete(fix)
    await session.commit()
    return ApproveOut(started=0, message=f"Dropped {len(gone)} preview{'' if len(gone) == 1 else 's'}.")


# --- sales: read them from here -------------------------------------------------------------------------

READ_LINE = "To see which listings sell, read this shop's sales first. It runs in the background."
NO_PERMISSION = "This shop was connected before sales could be read. Connect it again in Shops to allow it."


class ShopSalesOut(BaseModel):
    connection_id: uuid.UUID
    shop_name: str
    #: never | counted | queued | counting | reading | waiting | complete | failed | no_permission
    state: str
    #: Listing health can tell which of its listings sell.
    known: bool
    #: Its sales were never read, and "Read sales" can do it (a stopped read included).
    needs_read: bool
    #: Waiting behind another of the account's shops.
    waiting_turn: bool = False
    #: Reports' own estimate (sales in the 13 months read, days the read takes), once counted.
    window_count: int | None = None
    days_estimate: int | None = None
    read_count: int = 0
    resumes_at: datetime | None = None
    note: str | None = None


class HealthSalesOut(BaseModel):
    shops: list[ShopSalesOut]
    #: Shops "Read sales for all" would start.
    to_read: int
    read_line: str = READ_LINE


class ReadSalesIn(BaseModel):
    #: The shops to read; empty: every shop whose sales were never read.
    connection_ids: list[uuid.UUID] = []


def _shop_sales(connection: EtsyConnection, sync: SalesSync | None, turn: uuid.UUID | None, w: H.Windows) -> ShopSalesOut:
    from app.api.analytics import _sync_out  # Reports' figures, so both pages say the same

    out = _sync_out(connection, sync)
    since = datetime.now(timezone.utc).date() - timedelta(days=w.selling_days)
    known = sync is not None and sync.state == "complete" and (connection.sales_history_from is None or connection.sales_history_from <= since)
    queued = sync is not None and sync.queued_at is not None
    if not out.can_read:
        state = "no_permission"
    elif sync is None:
        state = "never"
    elif sync.state == "estimated":
        state = "queued" if queued else "counted"
    elif sync.state == "estimating":
        state = "counting"
    else:
        state = sync.state  # queued, reading, waiting, complete, failed
    return ShopSalesOut(
        connection_id=connection.id, shop_name=shop_label(connection), state=state, known=known,
        needs_read=state in ("never", "counted", "failed"),
        waiting_turn=state == "queued" and turn != connection.id,
        window_count=out.window_count, days_estimate=out.days_estimate, read_count=out.read_count, resumes_at=out.resumes_at,
        note=NO_PERMISSION if state == "no_permission" else (sales_worker.QUEUE_WAITING if state == "queued" else out.note),
    )


async def _sales_page(session: AsyncSession, tenant: Tenant, turn: uuid.UUID | None = None) -> HealthSalesOut:
    w = windows()
    shops = await active_shops(session, tenant.id)
    syncs = {s.connection_id: s for s in (await session.execute(select(SalesSync).where(SalesSync.tenant_id == tenant.id))).scalars()}
    if turn is None:
        turn = next((s.connection_id for s in sorted((s for s in syncs.values() if s.queued_at is not None), key=lambda s: s.queued_at)
                     if s.state in ("estimating", "reading")), None)
    out = [_shop_sales(c, syncs.get(c.id), turn, w) for c in shops]
    return HealthSalesOut(shops=out, to_read=sum(1 for s in out if s.needs_read))


@router.get("/listing-health/sales", response_model=HealthSalesOut)
async def health_sales(
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
) -> HealthSalesOut:
    """Every shop of the account: whether its sales were read. No Etsy call; Home's card reads it too."""
    return await _sales_page(session, tenant)


@router.post("/listing-health/sales/read", response_model=HealthSalesOut, status_code=202)
async def read_sales(
    body: ReadSalesIn,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> HealthSalesOut:
    """"Read sales" for one shop, or for every shop never read: they are counted and read one after another, each
    with Reports' estimate and first read, as upkeep inside the app's Etsy budget and each shop's daily share."""
    shops = await active_shops(session, tenant.id)
    if body.connection_ids:
        mine = {c.id: c for c in shops}
        if any(cid not in mine for cid in body.connection_ids):
            raise HTTPException(status_code=404, detail="Shop not found")
        wanted = [mine[cid] for cid in dict.fromkeys(body.connection_ids)]
    else:
        page = await _sales_page(session, tenant)
        needing = {s.connection_id for s in page.shops if s.needs_read}
        wanted = [c for c in shops if c.id in needing]
    if not wanted:
        raise HTTPException(status_code=409, detail="Every shop's sales are read or being read already.")
    queued = await sales_worker.queue_reads(session, wanted)
    await session.commit()
    for sync in queued:
        if sync.state == "reading":  # a stopped read that carries on where it stopped
            await enqueuer.enqueue("sync_sales", str(sync.connection_id),
                                   _job_id=f"sales-resume:{sync.connection_id}:{datetime.now(timezone.utc):%Y%m%d%H%M}")
    turn = await sales_worker.advance_queue(enqueuer.enqueue, session, tenant.id)
    return await _sales_page(session, tenant, turn)


# --- brand names the seller keeps -------------------------------------------------------------------------


class KeepBrandIn(BaseModel):
    connection_id: uuid.UUID
    listing_id: int


class KeepBrandOut(BaseModel):
    listing_id: int
    kept_brands: list[str]
    message: str


async def _listing_for_brand(session: AsyncSession, tenant: Tenant, body: KeepBrandIn) -> ShopListingCache:
    if await owned_shop(session, tenant.id, body.connection_id) is None:
        raise HTTPException(status_code=404, detail="Shop not found")
    row = await session.get(ShopListingCache, (tenant.id, body.listing_id))
    if row is None or row.connection_id != body.connection_id or not _fresh([row]):
        raise HTTPException(status_code=404, detail="Listing not found")
    return row


@router.post("/listing-health/brands/keep", response_model=KeepBrandOut)
async def keep_brand(
    body: KeepBrandIn,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
) -> KeepBrandOut:
    """"Keep it, I know the risk": every brand name on the listing now stops being Fix now there. Only brand names:
    nothing else Listing health says is kept. A brand name added later is warned about again. Nothing goes to Etsy."""
    row = await _listing_for_brand(session, tenant, body)
    brands = H.brands_in(row.payload)
    if not brands:
        raise HTTPException(status_code=409, detail="This listing has no brand name to keep.")
    have = (await _kept(session, tenant, body.connection_id)).get(body.listing_id, set())
    new = [b for b in brands if b.casefold() not in have]
    for brand in new:
        session.add(BrandKept(tenant_id=tenant.id, connection_id=body.connection_id, listing_id=body.listing_id, brand=brand))
    if new:
        audit.record(session, "listing.brand_kept", actor=tenant, target_tenant_id=tenant.id, connection_id=str(body.connection_id),
                     listing_id=body.listing_id, brands=new, trigger="seller_click")
    await session.commit()
    return KeepBrandOut(listing_id=body.listing_id, kept_brands=brands,
                        message="Kept. Listing health no longer counts this brand name as Fix now on this listing.")


@router.post("/listing-health/brands/warn", response_model=KeepBrandOut)
async def warn_again(
    body: KeepBrandIn,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
) -> KeepBrandOut:
    """"Show the warning again": the listing's kept brand names are Fix now again."""
    await _listing_for_brand(session, tenant, body)
    gone = list((await session.execute(select(BrandKept).where(
        BrandKept.tenant_id == tenant.id, BrandKept.connection_id == body.connection_id, BrandKept.listing_id == body.listing_id,
    ))).scalars())
    if not gone:
        raise HTTPException(status_code=409, detail="No brand name is kept on this listing.")
    for kept in gone:
        await session.delete(kept)
    audit.record(session, "listing.brand_warning_restored", actor=tenant, target_tenant_id=tenant.id, connection_id=str(body.connection_id),
                 listing_id=body.listing_id, brands=[k.brand for k in gone], trigger="seller_click")
    await session.commit()
    return KeepBrandOut(listing_id=body.listing_id, kept_brands=[], message="The warning is shown again.")
