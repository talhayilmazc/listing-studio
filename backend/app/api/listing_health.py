"""Listing health: how a seller's own active listings do against the app's rules, and fixes they approve.

``GET  /listing-health``           the shop's active listings, worst first, from the six-hour copy.
``POST /listing-health/rewrite-check``  what "Rewrite" would use of the allowance, said before it runs.
``POST /listing-health/preview``   makes a before and an after per listing. Nothing goes to Etsy.
``GET  /listing-health/fixes``     previews and what was done, with Undo while it lasts.
``POST /listing-health/apply``     the seller's approval of selected previews. At most 50 live listings.
``POST /listing-health/undo``      writes back the copy kept when the fix was made, for 7 days.
``POST /listing-health/discard``   drops previews nobody approved.

Only the seller's own shops, read from their own shop copy: another account's shop, listing or fix is 404,
and nothing here reads any other seller's data. Every write to Etsy is a job that a click approved.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session
from app.api.shop import _is_stale, _shop_rows, selected_shop
from app.api.shops import shop_label
from app.core import allowance, audit
from app.db.models import (
    AllowanceUse,
    Asset,
    GeneratedContent,
    ListingFix,
    ListingProfile,
    ListingPublication,
    ProfileShopLink,
    ShopListingCache,
    Tenant,
)
from app.etsy.shops import owned_shop
from app.pipeline import listing_fixes as F
from app.pipeline import listing_health as H
from app.workers.listing_fixes import READ_AT_ONCE, read_spec

router = APIRouter(prefix="/api", tags=["listing-health"])

PAGE_SIZE = 25
NOT_CONFIRMED = "Nothing was changed: the approval was not confirmed."
TOO_MANY = f"At most {F.MAX_PER_APPROVAL} live listings can be changed in one approval, so that a mistake reaches no further. Choose fewer."
REWRITE_RULE = (
    f"A rewrite follows the allowance's rewrite rule: the first {allowance.FREE_REWRITES} of each design this period are "
    "free, then each counts as one listing generated. A listing the app did not write counts once, the first time."
)
ACCEPT_RULE = "Rewriting uses your allowance, so it has to be asked for: " + REWRITE_RULE
#: How long an approved job may sit before the page's next read queues it again (Redis may have been emptied).
STUCK_AFTER = timedelta(minutes=30)
UNDOABLE = ("applied", "failed", "undo_failed")


class CheckOut(BaseModel):
    id: str
    kind: str
    verdict: str
    detail: str


class ListingHealthOut(BaseModel):
    listing_id: int
    title: str
    url: str
    image: str | None = None
    tags: list[str]
    score: int
    to_fix: int
    checks: list[CheckOut]
    brands: list[str]
    #: Attributes are read for the listings on screen; until then that check is left out.
    attributes_read: bool
    profile_id: uuid.UUID | None = None
    profile_name: str | None = None
    #: More than one profile fits this category, so the seller says which.
    profile_unclear: bool = False


class HealthPageOut(BaseModel):
    connection_id: uuid.UUID | None = None
    shop_name: str | None = None
    #: The shop's copy is being read: no figures until it is.
    reading: bool = False
    total: int = 0
    average: int | None = None
    needs_work: int = 0
    page: int = 1
    pages: int = 1
    listings: list[ListingHealthOut] = []
    max_per_approval: int = F.MAX_PER_APPROVAL
    undo_days: int = F.UNDO_DAYS
    rewrite_rule: str = REWRITE_RULE


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
    #: A rewrite that counted as one listing generated.
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
        kinds=list(fix.kinds or []), before=fix.before or {}, after=fix.after or {}, counted=fix.allowance_kind == allowance.GENERATION,
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

    def short_style(self, listing: dict[str, Any], profile: ListingProfile | None) -> bool:
        """A listing the app wrote in the long keyword style is not told off for being in it."""
        if int(listing.get("listing_id") or 0) not in self.written or profile is None:
            return True
        return (profile.listing_style or "classic") == "search"


def _score_rows(rows: list[ShopListingCache], profiles: _Profiles) -> list[tuple[ShopListingCache, H.Health, ListingProfile | None, bool]]:
    out = []
    for row in rows:
        profile, unclear = profiles.for_listing(row.payload)
        health = H.score(
            row.payload, properties=row.payload.get("_properties"), reference_attributes=profiles.attributes_of(profile, row.payload),
            short_style=profiles.short_style(row.payload, profile),
        )
        out.append((row, health, profile, unclear))
    return out


def _active(rows: list[ShopListingCache]) -> list[ShopListingCache]:
    return [r for r in rows if not _is_stale(r.fetched_at) and str(r.payload.get("state") or "") == "active"]


@router.get("/listing-health", response_model=HealthPageOut)
async def listing_health(
    shop: uuid.UUID | None = None,
    page: int = 1,
    show: str = "needs_work",
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> HealthPageOut:
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        return HealthPageOut()
    rows = await _shop_rows(session, tenant, connection)
    if not rows or _is_stale(max(r.fetched_at for r in rows)):
        await enqueuer.enqueue("sync_shop_listings", str(connection.id), _job_id=f"sync:{connection.id}")
        return HealthPageOut(connection_id=connection.id, shop_name=shop_label(connection), reading=True)
    profiles = await _Profiles(session, tenant, connection.id).load()
    # Regular expressions over a whole shop: off the event loop.
    scored = await asyncio.to_thread(_score_rows, _active(rows), profiles)
    scored.sort(key=lambda item: (item[1].score, int(item[0].listing_id)))
    needing = [item for item in scored if item[1].to_fix]
    shown = needing if show == "needs_work" else scored
    pages = max(1, (len(shown) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(1, page), pages)
    window = shown[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    unread = [int(r.listing_id) for r, _, _, _ in window if "_properties" not in r.payload]
    if unread:
        await enqueuer.enqueue("read_listing_properties", read_spec(connection.id, unread[:READ_AT_ONCE]),
                               _job_id=f"props:{connection.id}:{page}:{show}")
    listings = []
    for row, health, profile, unclear in window:
        text = F.text_of(row.payload)
        listings.append(ListingHealthOut(
            listing_id=int(row.listing_id), title=text["title"], url=_url(int(row.listing_id), row.payload), image=_image(row.payload),
            tags=text["tags"], score=health.score, to_fix=health.to_fix,
            checks=[CheckOut(id=c.id, kind=c.kind, verdict=c.verdict, detail=c.detail) for c in health.checks], brands=health.brands,
            attributes_read="_properties" in row.payload, profile_id=profile.id if profile else None,
            profile_name=profile.name if profile else None, profile_unclear=unclear,
        ))
    average = round(sum(h.score for _, h, _, _ in scored) / len(scored)) if scored else None
    return HealthPageOut(
        connection_id=connection.id, shop_name=shop_label(connection), total=len(scored), average=average, needs_work=len(needing),
        page=page, pages=pages, listings=listings,
    )


async def _own_rows(session: AsyncSession, tenant: Tenant, connection_id: uuid.UUID, listing_ids: list[int]) -> list[ShopListingCache]:
    """Only this account's listings in this shop, still within the copy's six hours. Others are not there."""
    rows = await session.execute(select(ShopListingCache).where(
        ShopListingCache.tenant_id == tenant.id, ShopListingCache.connection_id == connection_id,
        ShopListingCache.listing_id.in_(listing_ids),
    ))
    return _active(list(rows.scalars()))


async def _rewrite_kinds(session: AsyncSession, tenant: Tenant, rows: list[ShopListingCache], profiles: _Profiles) -> dict[int, tuple[str, str]]:
    """(design, kind) per listing: a listing the app wrote is a rewrite of its design; one it never wrote
    counts once, the first time (the rule Replace images has)."""
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
    kinds = await _rewrite_kinds(session, tenant, rows, profiles)
    counted = sum(1 for _, kind in kinds.values() if kind == allowance.GENERATION)
    current = await allowance.status(session, tenant)
    return RewriteCheckOut(rule=REWRITE_RULE, free=len(kinds) - counted, counted=counted, remaining=current.remaining, fits=counted <= current.remaining)


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
        raise HTTPException(status_code=422, detail=ACCEPT_RULE)
    profiles = await _Profiles(session, tenant, connection.id).load()
    if body.profile_id is not None and body.profile_id not in {p.id for p in profiles.profiles}:
        raise HTTPException(status_code=404, detail="Profile not found")
    rows = await _own_rows(session, tenant, connection.id, wanted_ids)
    rewrites: dict[int, tuple[str, str]] = {}
    if F.REWRITE in kinds:
        rewrites = await _rewrite_kinds(session, tenant, rows, profiles)
        counted = sum(1 for _, kind in rewrites.values() if kind == allowance.GENERATION)
        if counted:
            try:
                await allowance.check(session, tenant, counted)
            except allowance.AllowanceExceeded as exc:
                raise HTTPException(status_code=429, detail=str(exc)) from None
    fixes: list[ListingFix] = []
    skipped: list[SkippedOut] = []
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
        if F.REWRITE in kinds:
            before.update(text)
            after.update(text)  # the model's words replace these before anything can be approved
            done.append(F.REWRITE)
        elif F.TAGS in kinds:
            candidates = await _reference_tags(session, tenant, profile) if not unclear else []
            added = F.added_tags(text["title"], text["tags"], candidates)
            if added:
                before["tags"] = text["tags"]
                after["tags"] = [*text["tags"], *added]
                done.append(F.TAGS)
            elif len(text["tags"]) >= 13:
                reasons.append("it already has 13 tags")
            else:
                reasons.append("the profile's reference listing has no tag to add" if profile else "no profile to take tags from")
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
    missing_rows = set(wanted_ids) - {int(r.listing_id) for r in rows}
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
