"""Analytics and profit over the seller's own shop (docs/duzeltmeler-v7.md §C).

Everything here reads the seller's own data only: ``sales_daily`` (daily
totals derived from their own sales), ``ad_spend`` (the Etsy Ads CSV they
uploaded), their own listing cache for titles, and the fee rates and costs they
entered. No endpoint here calls Etsy directly; a sales read is a queued job.

Listing titles, images and links come from the six-hour listing cache and are
shown only while fresh (CLAUDE.md); past that a listing is shown by its id with
its Etsy link, and a sync is queued. Recommendations point at Shop Manager: the
app has no Ads endpoint and changes nothing on Etsy from here.
"""

from __future__ import annotations

import csv
import io
import json
import math
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session
from app.api import sales_reread
from app.api.shops import missing_scopes, selected_shop
from app.db.models import (
    AdSpend,
    Asset,
    EtsyConnection,
    GeneratedContent,
    ListingProfile,
    ListingPublication,
    SalesDaily,
    LedgerDaily,
    LedgerSync,
    SalesSync,
    ShopListingCache,
    Tenant,
)
from app.pipeline import finance, profit
from app.pipeline.reference import decode_etsy_text
from app.workers import ledger as ledger_worker
from app.workers import sales as sales_worker

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

PERIODS = (7, 30, 90)
SALES_SCOPE = "transactions_r"
#: Weeks in a listing's chart: the whole 13 months kept.
CHART_WEEKS = 56
#: Same weeks last year, weekday-aligned.
YEAR_DAYS = 364


# --- Cost settings (C2) --------------------------------------------------------------


class CostsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listing_fee: str | int | float = profit.DEFAULT_COSTS["listing_fee"]
    transaction_pct: str | int | float = profit.DEFAULT_COSTS["transaction_pct"]
    payment_pct: str | int | float = profit.DEFAULT_COSTS["payment_pct"]
    payment_fixed: str | int | float = profit.DEFAULT_COSTS["payment_fixed"]
    shipping_cost: str | int | float = profit.DEFAULT_COSTS["shipping_cost"]
    monthly_fixed: str | int | float = profit.DEFAULT_COSTS["monthly_fixed"]
    product_cost: str | int | float = profit.DEFAULT_COSTS["product_cost"]
    product_cost_by_profile: dict[str, str | int | float | None] = {}
    product_cost_by_sku: dict[str, str | int | float | None] = {}


class CostsOut(BaseModel):
    listing_fee: str
    transaction_pct: str
    payment_pct: str
    payment_fixed: str
    shipping_cost: str
    monthly_fixed: str
    product_cost: str
    product_cost_by_profile: dict[str, str]
    product_cost_by_sku: dict[str, str]
    defaults: dict[str, str]


def _costs_out(stored: dict[str, Any] | None) -> CostsOut:
    c = profit.CostSettings.from_stored(stored)
    return CostsOut(
        listing_fee=str(c.listing_fee),
        transaction_pct=str(c.transaction_pct),
        payment_pct=str(c.payment_pct),
        payment_fixed=str(c.payment_fixed),
        shipping_cost=str(c.shipping_cost),
        monthly_fixed=str(c.monthly_fixed),
        product_cost=str(c.product_cost),
        product_cost_by_profile={k: str(v) for k, v in c.product_cost_by_profile.items()},
        product_cost_by_sku={k: str(v) for k, v in c.product_cost_by_sku.items()},
        defaults={k: v for k, v in profit.DEFAULT_COSTS.items() if isinstance(v, str)},
    )


@router.get("/costs", response_model=CostsOut)
async def get_costs(tenant: Tenant = Depends(active_tenant)) -> CostsOut:
    return _costs_out(tenant.cost_settings)


@router.put("/costs", response_model=CostsOut)
async def put_costs(
    body: CostsIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> CostsOut:
    """The seller's fee rates and costs; every rate is theirs to change (Etsy's change)."""
    try:
        stored = profit.validate_costs(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    tenant.cost_settings = stored
    await session.commit()
    return _costs_out(stored)


# --- Reading sales now ---------------------------------------------------------------


class LedgerOut(BaseModel):
    """Where reading the shop's payment ledger (fees and ad spend) stands."""

    #: "none" | "estimating" | "estimated" | "reading" | "waiting" | "complete" | "failed"
    state: str
    #: Entries in the first window (the last LedgerSync.FIRST_DAYS days) and the requests they take.
    total_count: int | None = None
    pages_estimate: int | None = None
    first_days: int = LedgerSync.FIRST_DAYS
    read_count: int = 0
    requests_used: int = 0
    resumes_at: datetime | None = None
    note: str | None = None
    #: The day the figures reach back to, and the day they reach.
    covers_from: date | None = None
    covers_to: date | None = None
    #: Filling in the 13 months before the first read: "none" | "reading" | "waiting" | "complete" | "failed".
    history_state: str = "none"
    #: The day the history is to reach back to, and the requests it has used.
    history_target: date | None = None
    history_requests: int = 0
    #: Requests still to go, from what the days read so far have cost; None until there is something to go on.
    history_requests_left: int | None = None
    history_note: str | None = None


def _first_whole_day(stamp: int | None) -> date | None:
    """The first day wholly at or after an epoch second (a day read from noon on isn't covered)."""
    if stamp is None:
        return None
    return datetime.fromtimestamp(stamp + (-stamp % 86400), tz=timezone.utc).date()


def _ledger_out(sync: LedgerSync | None) -> LedgerOut:
    if sync is None:
        return LedgerOut(state="none")
    day = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).date() if t else None  # noqa: E731
    reached = ledger_worker.covered_from(sync)
    left = None
    if sync.backfill_target is not None and reached is not None and sync.window_start is not None:
        done_days = (sync.window_start - reached) / 86400
        left_days = max(0.0, (reached - sync.backfill_target) / 86400)
        if sync.backfill_state == "complete" or left_days == 0:
            left = 0
        elif done_days >= 1:
            left = math.ceil(sync.backfill_requests / done_days * left_days)
    return LedgerOut(
        history_state=sync.backfill_state,
        history_target=day(sync.backfill_target),
        history_requests=sync.backfill_requests,
        history_requests_left=left,
        history_note=sync.backfill_note,
        state=sync.state,
        total_count=sync.total_count,
        pages_estimate=ledger_worker.pages_for(sync.total_count) if sync.total_count is not None else None,
        read_count=sync.read_count,
        requests_used=sync.requests_used,
        resumes_at=sync.resumes_at,
        note=sync.note,
        covers_from=_first_whole_day(reached),
        covers_to=day(sync.synced_until),
    )


class SalesSyncOut(BaseModel):
    """Where reading the shop's sales stands, and what it costs."""

    can_read: bool
    #: "none" | "estimating" | "estimated" | "reading" | "waiting" | "complete" | "failed"
    state: str
    total_count: int | None = None
    #: Sales in the 13 months read, and the requests reading them takes.
    window_count: int | None = None
    pages_estimate: int | None = None
    #: Days the first read takes at the daily pace (from today's remaining share).
    days_estimate: int | None = None
    daily_requests: int = SalesSync.DAILY_REQUESTS
    read_count: int = 0
    requests_used: int = 0
    requests_today: int = 0
    last_update_requests: int | None = None
    resumes_at: datetime | None = None
    note: str | None = None
    #: The one-time second read (order lines): None | "reading" | "done".
    reread: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    synced_at: datetime | None = None
    #: The ledger read runs beside the sales read (same scope, same start).
    ledger: LedgerOut = LedgerOut(state="none")


def _sync_out(connection: EtsyConnection, sync: SalesSync | None, ledger: LedgerSync | None = None) -> SalesSyncOut:
    can = SALES_SCOPE not in missing_scopes(connection)
    if sync is None:
        return SalesSyncOut(can_read=can, state="none", synced_at=connection.sales_synced_at, ledger=_ledger_out(ledger))
    today = _today()
    used_today = sync.requests_today if sync.requests_day == today else 0
    # Requests still to go: the whole estimate before starting, else what is unread.
    remaining = None
    if sync.state == "estimated":
        remaining = sync.pages_estimate
    elif sync.state in ("reading", "waiting") and sync.window_count is not None:
        remaining = math.ceil(max(0, sync.window_count - sync.read_count) / sales_worker.PAGE_SIZE)
    return SalesSyncOut(
        can_read=can,
        state=sync.state,
        total_count=sync.total_count,
        window_count=sync.window_count,
        pages_estimate=sync.pages_estimate,
        days_estimate=sales_worker.days_needed(remaining, used_today) if remaining is not None else None,
        read_count=sync.read_count,
        requests_used=sync.requests_used,
        requests_today=used_today,
        last_update_requests=sync.last_update_requests,
        resumes_at=sync.resumes_at,
        note=sync.note,
        reread=sync.reread,
        started_at=sync.started_at,
        finished_at=sync.finished_at,
        synced_at=connection.sales_synced_at,
        ledger=_ledger_out(ledger),
    )


async def _sales_shop(session: AsyncSession, tenant: Tenant, shop: uuid.UUID | None) -> EtsyConnection:
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")
    if SALES_SCOPE in missing_scopes(connection):
        raise HTTPException(status_code=409, detail="reconnect your shop to allow reading its sales")
    return connection


@router.get("/sales/status", response_model=SalesSyncOut)
async def sales_status(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> SalesSyncOut:
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")
    return _sync_out(connection, await session.get(SalesSync, connection.id), await session.get(LedgerSync, connection.id))


@router.post("/sales/estimate", response_model=SalesSyncOut, status_code=202)
async def estimate_sales(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> SalesSyncOut:
    """Work out what reading the shop's sales costs, before starting (a few requests)."""
    connection = await _sales_shop(session, tenant, shop)
    sync = await session.get(SalesSync, connection.id)
    if sync is not None and sync.state in ("reading", "waiting"):
        raise HTTPException(status_code=409, detail="your sales are being read already")
    if sync is None:
        sync = SalesSync(connection_id=connection.id, tenant_id=tenant.id)
        session.add(sync)
    sync.state = "estimating"
    sync.requests_used = 0
    sync.note = None
    ledger = await session.get(LedgerSync, connection.id)
    if ledger is None:
        ledger = LedgerSync(connection_id=connection.id, tenant_id=tenant.id)
        session.add(ledger)
    if ledger.state not in ("reading", "waiting", "complete"):
        ledger.state, ledger.requests_used, ledger.note = "estimating", 0, None
    await session.commit()
    await enqueuer.enqueue("estimate_sales", str(connection.id), _job_id=f"sales-estimate:{connection.id}:{_today()}")
    if ledger.state == "estimating":
        await enqueuer.enqueue("estimate_ledger", str(connection.id), _job_id=f"ledger-estimate:{connection.id}:{_today()}")
    return _sync_out(connection, sync, ledger)


@router.post("/sales/start", response_model=SalesSyncOut, status_code=202)
async def start_sales_read(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> SalesSyncOut:
    """The seller saw the estimate and starts the read. It runs in the background."""
    connection = await _sales_shop(session, tenant, shop)
    sync = await session.get(SalesSync, connection.id)
    if sync is None or sync.state not in ("estimated", "complete", "failed") or sync.pages_estimate is None:
        raise HTTPException(status_code=409, detail="see what reading your sales costs first")
    await sales_worker.begin_first_read(session, sync)
    ledger = await session.get(LedgerSync, connection.id)
    start_ledger = ledger is not None and ledger.state in ("estimated", "failed") and ledger.window_start is not None
    if start_ledger:
        await ledger_worker.begin(session, ledger)
    await session.commit()
    await enqueuer.enqueue("sync_sales", str(connection.id), _job_id=f"sales-read:{connection.id}:start:{sync.started_at}")
    if start_ledger:
        await enqueuer.enqueue("sync_ledger", str(connection.id), _job_id=f"ledger-read:{connection.id}:start:{ledger.started_at}")
    return _sync_out(connection, sync, ledger)


@router.post("/sales/resume", response_model=SalesSyncOut, status_code=202)
async def resume_sales_read(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> SalesSyncOut:
    """After a failure (e.g. once the shop is reconnected): carry on where it stopped."""
    connection = await _sales_shop(session, tenant, shop)
    sync = await session.get(SalesSync, connection.id)
    ledger = await session.get(LedgerSync, connection.id)
    sales_failed = sync is not None and sync.state == "failed"
    ledger_failed = ledger is not None and ledger.state == "failed"
    history_failed = ledger is not None and ledger.backfill_state == "failed"
    if not (sales_failed or ledger_failed or history_failed):
        raise HTTPException(status_code=409, detail="nothing to resume")
    if history_failed:
        ledger_worker.resume_backfill(ledger)
    if sales_failed:
        await sales_worker.resume(session, sync)
    if ledger_failed:
        ledger_worker.resume(ledger)
    await session.commit()
    if sales_failed and sync.state in ("reading", "complete"):
        await enqueuer.enqueue("sync_sales", str(connection.id), _job_id=f"sales-resume:{connection.id}:{_now_stamp()}")
    if ledger_failed and ledger.state in ("reading", "complete"):
        await enqueuer.enqueue("sync_ledger", str(connection.id), _job_id=f"ledger-resume:{connection.id}:{_now_stamp()}")
    if history_failed:
        await enqueuer.enqueue("backfill_ledger", str(connection.id), _job_id=f"ledger-backfill:{connection.id}:{_now_stamp()}")
    return _sync_out(connection, sync, ledger)


@router.post("/ledger/estimate", response_model=SalesSyncOut, status_code=202)
async def estimate_ledger(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> SalesSyncOut:
    """Count the ledger entries of the first window (one request), without
    touching the sales read: for a shop whose sales are read already."""
    connection = await _sales_shop(session, tenant, shop)
    ledger = await session.get(LedgerSync, connection.id)
    if ledger is not None and ledger.state in ("reading", "waiting", "complete"):
        raise HTTPException(status_code=409, detail="Etsy's ledger is being read already")
    if ledger is None:
        ledger = LedgerSync(connection_id=connection.id, tenant_id=tenant.id)
        session.add(ledger)
    ledger.state, ledger.requests_used, ledger.note = "estimating", 0, None
    await session.commit()
    await enqueuer.enqueue("estimate_ledger", str(connection.id), _job_id=f"ledger-estimate:{connection.id}:{_now_stamp()}")
    return _sync_out(connection, await session.get(SalesSync, connection.id), ledger)


@router.post("/ledger/start", response_model=SalesSyncOut, status_code=202)
async def start_ledger_read(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> SalesSyncOut:
    """The seller saw the ledger's cost and starts it on its own."""
    connection = await _sales_shop(session, tenant, shop)
    ledger = await session.get(LedgerSync, connection.id)
    if ledger is None or ledger.state not in ("estimated", "failed") or ledger.window_start is None:
        raise HTTPException(status_code=409, detail="see what reading Etsy's ledger costs first")
    await ledger_worker.begin(session, ledger)
    await session.commit()
    await enqueuer.enqueue("sync_ledger", str(connection.id), _job_id=f"ledger-read:{connection.id}:start:{ledger.started_at}")
    return _sync_out(connection, await session.get(SalesSync, connection.id), ledger)


@router.get("/sales/reread", response_model=sales_reread.RereadReport)
async def sales_reread_status(
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> sales_reread.RereadReport:
    """Where the one-time second read of the sales stands, for each of the
    account's shops: it adds the order lines that tie an order to its listings."""
    return await sales_reread.for_account(session, tenant)


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M")


@router.post("/sales/refresh", status_code=202)
async def refresh_sales(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> dict[str, bool]:
    """Take the sales made since the last read now, instead of at the nightly run.

    Only after the first read; usually one request. One queued read per shop
    however often it is pressed; it is upkeep, not the seller's own quota.
    """
    connection = await _sales_shop(session, tenant, shop)
    sync = await session.get(SalesSync, connection.id)
    if sync is None or sync.state != "complete":
        raise HTTPException(status_code=409, detail="read your sales once first")
    await enqueuer.enqueue("sync_sales", str(connection.id), _job_id=f"manual-sales:{connection.id}")
    ledger = await session.get(LedgerSync, connection.id)
    if ledger is not None and ledger.state == "complete":
        await enqueuer.enqueue("sync_ledger", str(connection.id), _job_id=f"manual-ledger:{connection.id}")
    return {"queued": True}


# --- The report (v7 §C, reworked; the figures are pipeline/finance.py's) --------------


def _fresh(fetched_at: datetime) -> bool:
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fetched_at).total_seconds() < ShopListingCache.STALE_SECONDS


def _stamp_day(value: Any) -> date | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


@dataclass
class _Loaded:
    shop: finance.Shop
    connection: EtsyConnection
    sales_sync: SalesSync | None
    ledger_sync: LedgerSync | None

    # The Ads import reads these directly.
    @property
    def sales(self) -> dict[int, list[profit.DaySales]]:
        return self.shop.sales

    @property
    def ads(self) -> dict[int, list[profit.AdRow]]:
        return self.shop.ads

    @property
    def info(self) -> dict[int, finance.ListingInfo]:
        return self.shop.info

    @property
    def cache_fresh(self) -> bool:
        return self.shop.cache_fresh


async def _load(session: AsyncSession, tenant: Tenant, connection: EtsyConnection, enqueuer: Enqueuer) -> _Loaded:
    sales: dict[int, list[profit.DaySales]] = defaultdict(list)
    currencies: Counter[str] = Counter()
    for s in (await session.execute(select(SalesDaily).where(SalesDaily.connection_id == connection.id))).scalars():
        sales[s.listing_id].append(profit.DaySales(s.listing_id, s.day, s.units, s.orders, s.revenue_minor))
        if s.currency:
            currencies[s.currency] += 1
    ads: dict[int, list[profit.AdRow]] = defaultdict(list)
    for a in (await session.execute(select(AdSpend).where(AdSpend.connection_id == connection.id))).scalars():
        ads[a.listing_id].append(profit.AdRow(
            a.listing_id, a.period_start, a.period_end, a.spend_minor, a.ad_orders, a.ad_revenue_minor, a.ad_views))
    ledger = [
        finance.LedgerDay(r.day, r.ledger_type, r.amount_minor, r.entries)
        for r in (await session.execute(select(LedgerDaily).where(LedgerDaily.connection_id == connection.id))).scalars()
    ]
    for r in (await session.execute(select(LedgerDaily.currency).where(LedgerDaily.connection_id == connection.id).limit(1))).scalars():
        if r:
            currencies[r] += 1

    info: dict[int, finance.ListingInfo] = defaultdict(finance.ListingInfo)
    profiles = {p.id: p for p in (await session.execute(
        select(ListingProfile).where(ListingProfile.connection_id == connection.id))).scalars()}
    for p in profiles.values():
        i = info[p.reference_listing_id]
        i.profile_id, i.profile_name = str(p.id), p.name
    for listing_id, profile_id, sku in (await session.execute(
        # The publication's own SKU: its batch (and so its asset) may be deleted.
        select(ListingPublication.etsy_listing_id, ListingPublication.profile_id,
               func.coalesce(ListingPublication.sku, Asset.parsed_sku))
        .outerjoin(GeneratedContent, GeneratedContent.id == ListingPublication.content_id)
        .outerjoin(Asset, Asset.id == GeneratedContent.asset_id)
        .where(ListingPublication.connection_id == connection.id)
    )).all():
        i = info[listing_id]
        if profile_id in profiles:
            i.profile_id, i.profile_name = str(profile_id), profiles[profile_id].name
        i.sku = i.sku or sku

    cached = (await session.execute(select(ShopListingCache).where(
        ShopListingCache.tenant_id == tenant.id, ShopListingCache.connection_id == connection.id))).scalars().all()
    fresh = [c for c in cached if _fresh(c.fetched_at)]
    cache_fresh = bool(fresh) and len(fresh) == len(cached)
    if not cache_fresh:
        await enqueuer.enqueue("sync_shop_listings", str(connection.id), _job_id=f"manual-sync:{connection.id}")
    for c in fresh:  # expired listing content is never shown
        row = c.payload or {}
        i = info[c.listing_id]
        i.title = decode_etsy_text(row.get("title")) or None
        i.state = row.get("state")
        i.url = row.get("url")
        images = row.get("images") or []
        if images:
            i.thumbnail_url = images[0].get("url_170x135") or images[0].get("url_570xN")
        skus = [s for s in row.get("skus") or [] if s]
        if skus:
            i.sku = skus[0]
        i.launched = _stamp_day(row.get("original_creation_timestamp") or row.get("created_timestamp"))

    sales_sync = await session.get(SalesSync, connection.id)
    ledger_sync = await session.get(LedgerSync, connection.id)
    # What the sales rows cover: a finished read, its whole window; a read in
    # progress (newest first), from the oldest sale counted so far.
    sales_from: date | None = None
    if sales_sync is not None and sales_sync.state == "complete":
        # Finished, even with no sale in it: the figures are real zeros, not blanks.
        sales_from = (sales_sync.window_start
                      or (min(s.day for rows in sales.values() for s in rows) if sales else sales_worker.window_start(_today())))
    elif sales_sync is not None and sales:
        if sales_sync.oldest_ts is not None:
            sales_from = _stamp_day(sales_sync.oldest_ts)
    elif sales:  # rows from before the resumable read
        sales_from = min(s.day for rows in sales.values() for s in rows)
    ledger_from = ledger_to = None
    if ledger_sync is not None and ledger_sync.synced_until is not None and ledger_sync.window_start is not None:
        # Back to wherever the history has reached; periods before it stay estimates.
        ledger_from = _first_whole_day(ledger_worker.covered_from(ledger_sync))
        ledger_to = _stamp_day(ledger_sync.synced_until)

    shop = finance.Shop(
        sales=sales, ads=ads, ledger=ledger, info=info,
        costs=profit.CostSettings.from_stored(tenant.cost_settings),
        entered=finance.entered_costs(tenant.cost_settings),
        currency=currencies.most_common(1)[0][0] if currencies else None,
        sales_from=sales_from, ledger_from=ledger_from, ledger_to=ledger_to, cache_fresh=cache_fresh,
        ledger_filling=ledger_sync is not None and ledger_sync.state == "complete" and ledger_sync.backfill_state != "complete",
    )
    return _Loaded(shop, connection, sales_sync, ledger_sync)


def _link(listing_id: int, info: finance.ListingInfo) -> str:
    """The ToU back link: the public page for an active listing, else Shop Manager."""
    if info.state == "draft":
        return profit.editor_url(listing_id)
    return info.url or f"https://www.etsy.com/listing/{listing_id}"


def _ref(shop: finance.Shop, lid: int) -> dict[str, Any]:
    i = shop.info.get(lid) or finance.ListingInfo()
    return {
        "listing_id": lid, "title": i.title, "state": i.state, "url": _link(lid, i),
        "thumbnail_url": i.thumbnail_url, "sku": i.sku, "profile_name": i.profile_name,
        "launched": i.launched.isoformat() if i.launched else None,
    }


def _data(loaded: _Loaded) -> dict[str, Any]:
    """Where each source stands: what the figures can and can't include."""
    c, s, l, shop = loaded.connection, loaded.sales_sync, loaded.ledger_sync, loaded.shop
    ends = [a.period_end for rows in shop.ads.values() for a in rows]
    return {
        "connected": True,
        "can_read_sales": SALES_SCOPE not in missing_scopes(c),
        "currency": shop.currency,
        "sales": {"state": s.state if s else "none", "from": shop.sales_from, "synced_at": c.sales_synced_at,
                  "read": s.read_count if s else 0, "of": s.window_count if s else None, "note": s.note if s else None},
        "ledger": {"state": l.state if l else "none", "from": shop.ledger_from, "to": shop.ledger_to,
                   "history": l.backfill_state if l else "none",
                   "read": l.read_count if l else 0, "of": l.total_count if l else None, "note": l.note if l else None},
        "reports_until": max(ends) if ends else None,
        "titles_refreshing": not shop.cache_fresh,
        "listing_counts": c.listing_counts,
        "costs_entered": shop.entered,
    }


def _days(days: int) -> int:
    if days not in PERIODS:
        raise HTTPException(status_code=422, detail="period must be 7, 30 or 90 days")
    return days


def _compare(compare: str) -> str:
    if compare not in ("previous", "year"):
        raise HTTPException(status_code=422, detail="compare must be previous or year")
    return compare


def _today() -> date:
    return datetime.now(timezone.utc).date()


async def _shop_or_none(session: AsyncSession, tenant: Tenant, shop: uuid.UUID | None, enqueuer: Enqueuer) -> _Loaded | None:
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        return None
    return await _load(session, tenant, connection, enqueuer)


NO_SHOP = {"data": {"connected": False, "can_read_sales": False}}


def _status_of(lid: int, e: finance.Economics | None, info: finance.ListingInfo, action: dict[str, Any] | None,
               sold_90: int, winners: set[int], today: date) -> str:
    """A listing's badge, from what's worth doing about it."""
    if action is not None:
        return {"ad_sink": "ad_sink", "ads_above_break_even": "ad_sink", "selling_at_loss": "loser",
                "fading": "fading", "turned_down": "fading", "room_to_advertise": "winner"}[action["kind"]]
    if lid in winners:
        return "winner"
    if sold_90 == 0:
        if info.launched and (today - info.launched).days < profit.NEW_FAIR_DAYS:
            return "new"
        return "loser"
    return "steady"


def _listing_rows(loaded: _Loaded, window: profit.Window, other: profit.Window | None, today: date) -> list[dict[str, Any]]:
    shop = loaded.shop
    have_sales = shop.sales_from is not None
    econ = finance.economics(shop, window)
    cmp_econ = finance.economics(shop, other) if other else {}
    money = profit.MoneyFormat(shop.currency)
    acts = {a["listing_id"]: a for a in finance.actions(shop, econ, finance.economics(shop, window.previous()), window, today, money)}
    earning = sorted((e for e in econ.values() if e.net > 0 and e.units >= profit.WINNER_MIN_UNITS), key=lambda e: e.net, reverse=True)
    winners = {e.lid for e in earning[: max(1, int(sum(1 for e in econ.values() if e.units) * profit.WINNER_TOP_SHARE))]} if earning else set()
    long = profit.Window.last(90, today)
    # Listings with sales now or in the comparison period, and every active one.
    universe = set(econ) | set(cmp_econ) | set(finance.economics(shop, window.previous())) | {
        lid for lid, i in shop.info.items() if i.state == "active"}
    rows = []
    for lid in universe:
        info = shop.info.get(lid) or finance.ListingInfo()
        e = econ.get(lid)
        c = cmp_econ.get(lid)
        sold_90 = sum(s.units for s in shop.sales.get(lid, []) if s.day in long)
        rows.append({
            **_ref(shop, lid),
            # With no sales read, a listing's figures are unknown, not zero.
            "economics": (e.out() if e else finance.Economics(lid, 0, 0, 0, {k: 0 for k in finance.FEES}, "rates", None, None, None, None, None, None, None, 0).out()) if have_sales else None,
            "comparison": ({"revenue": c.revenue, "units": c.units, "net": c.net} if c else {"revenue": 0, "units": 0, "net": 0}) if (have_sales and other) else None,
            "trend": finance.trend(shop.sales.get(lid, []), today)["signal"] if have_sales else None,
            "action": acts.get(lid),
            "status": _status_of(lid, e, info, acts.get(lid), sold_90, winners, today) if have_sales else None,
        })
    rows.sort(key=lambda r: (-(r["action"]["stake"] if r["action"] else -1), -((r["economics"] or {}).get("revenue") or 0), r["listing_id"]))
    return rows


@router.get("/summary")
async def summary(
    shop: uuid.UUID | None = None,
    days: int = 30,
    compare: str = "previous",
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> dict[str, Any]:
    """The shop's income statement for the period against the comparison, with
    what to do today, the trend, concentration and launch cohorts."""
    days, compare = _days(days), _compare(compare)
    loaded = await _shop_or_none(session, tenant, shop, enqueuer)
    if loaded is None:
        return NO_SHOP
    s, today = loaded.shop, _today()
    window = profit.Window.last(days, today)
    other, label, why = finance.comparison(s, window, compare)
    econ = finance.economics(s, window)
    money = profit.MoneyFormat(s.currency)
    acts = finance.actions(s, econ, finance.economics(s, window.previous()), window, today, money)
    conc = finance.concentration(econ)
    return {
        "data": _data(loaded),
        "period": {"days": days, "start": window.start, "end": window.end},
        "comparison": {"mode": compare, "label": label, "start": other.start if other else None,
                       "end": other.end if other else None, "unavailable": why,
                       "year_available": finance.comparison(s, window, "year")[2] is None},
        "totals": finance.shop_totals(s, window),
        "compared": finance.shop_totals(s, other) if other else None,
        "actions": [{**a, "listing": _ref(s, a["listing_id"])} for a in acts[:15]],
        "actions_total": len(acts),
        "concentration": {**conc, "top": [{**_ref(s, lid), "revenue": econ[lid].revenue} for lid in conc["top_listings"]]},
        "cohorts": finance.cohorts(s, econ, window, today),
        "series": finance.daily_series(s, window, other),
    }


@router.get("/listings")
async def listings(
    shop: uuid.UUID | None = None,
    days: int = 30,
    compare: str = "previous",
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> dict[str, Any]:
    """Every listing's unit economics, most money at stake first."""
    days, compare = _days(days), _compare(compare)
    loaded = await _shop_or_none(session, tenant, shop, enqueuer)
    if loaded is None:
        return {**NO_SHOP, "listings": []}
    s, today = loaded.shop, _today()
    window = profit.Window.last(days, today)
    other, label, why = finance.comparison(s, window, compare)
    return {
        "data": _data(loaded),
        "period": {"days": days, "start": window.start, "end": window.end},
        "comparison": {"mode": compare, "label": label, "unavailable": why},
        "listings": _listing_rows(loaded, window, other, today),
    }


CHART_WEEKS = 56


@router.get("/listings/{listing_id}")
async def listing_detail(
    listing_id: int,
    shop: uuid.UUID | None = None,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> dict[str, Any]:
    """One listing: 13 months of weekly revenue with a 4-week average and last
    year's same weeks, its unit economics against the previous period, and why."""
    days = _days(days)
    loaded = await _shop_or_none(session, tenant, shop, enqueuer)
    if loaded is None:
        raise HTTPException(status_code=404, detail="not found")
    s, today = loaded.shop, _today()
    known = listing_id in s.sales or listing_id in s.ads or listing_id in s.info
    if not known:
        raise HTTPException(status_code=404, detail="not found")
    window = profit.Window.last(days, today)
    econ = finance.economics(s, window)
    prev = finance.economics(s, window.previous())
    rows = s.sales.get(listing_id, [])
    units = finance.weekly(rows, today, CHART_WEEKS)
    revenue = finance.weekly(rows, today, CHART_WEEKS, "revenue_minor")
    first = today - timedelta(days=7 * CHART_WEEKS - 1)
    weeks = []
    for i in range(CHART_WEEKS):
        avg = sum(revenue[max(0, i - 3): i + 1]) / len(revenue[max(0, i - 3): i + 1])
        weeks.append({
            "start": (first + timedelta(weeks=i)).isoformat(), "units": units[i], "revenue": revenue[i],
            "avg4": round(avg), "last_year": revenue[i - 52] if i >= 52 else None,
        })
    money = profit.MoneyFormat(s.currency)
    action = next((a for a in finance.actions(s, econ, prev, window, today, money) if a["listing_id"] == listing_id), None)
    unit_cost, unit_src = finance._unit_cost(s, listing_id) if s.entered["product"] else (None, None)
    return {
        "data": _data(loaded),
        "listing": _ref(s, listing_id),
        "period": {"days": days, "start": window.start, "end": window.end},
        "comparison_label": f"Last {days} days vs the previous {days}",
        "economics": econ[listing_id].out() if listing_id in econ else None,
        "previous": prev[listing_id].out() if listing_id in prev else None,
        "unit_cost": None if unit_cost is None else str(unit_cost),
        "unit_cost_source": unit_src,
        "trend": finance.trend(rows, today),
        "action": action,
        "weeks": weeks,
        "ads": [
            {"period_start": a.period_start, "period_end": a.period_end, "spend": a.spend_minor,
             "ad_orders": a.ad_orders, "ad_revenue": a.ad_revenue_minor, "ad_views": a.ad_views}
            for a in sorted(s.ads.get(listing_id, []), key=lambda a: a.period_start, reverse=True)
        ],
    }


METRICS = {
    "revenue": "Revenue", "units": "Items sold", "orders": "Order lines",
    "listing_fees": "Listing fees", "transaction_fees": "Transaction fees", "processing_fees": "Payment processing",
    "ads": "Ad spend", "shipping": "Shipping", "product": "Product cost", "net": "Net profit",
}


def _breakdown(loaded: _Loaded, metric: str, window: profit.Window) -> dict[str, Any]:
    s = loaded.shop
    econ = finance.economics(s, window)
    totals = finance.shop_totals(s, window)
    field_of = {
        "revenue": lambda e: e.revenue, "units": lambda e: e.units, "orders": lambda e: e.orders,
        "listing_fees": lambda e: e.fees["listing_fees"], "transaction_fees": lambda e: e.fees["transaction_fees"],
        "processing_fees": lambda e: e.fees["processing_fees"], "ads": lambda e: e.ads,
        "shipping": lambda e: e.shipping, "product": lambda e: e.product, "net": lambda e: e.net,
    }[metric]
    rows = [(lid, field_of(e)) for lid, e in econ.items()]
    rows = [(lid, v) for lid, v in rows if v]
    total = sum(v for _, v in rows) or 0
    rows.sort(key=lambda r: abs(r[1]), reverse=True)
    if metric in ("revenue", "units", "orders", "net"):
        figure = {"value": totals["net"]["value"] if metric == "net" else (totals["revenue"]["value"] if metric == "revenue" else totals[metric]),
                  "source": "computed" if metric == "net" else "sales"}
    else:
        figure = totals["lines"][metric]
    notes = []
    if metric in ("revenue", "units", "orders"):
        notes.append("Individual orders aren't kept, by design: the app holds daily totals per listing, never the "
                     "orders themselves or anything about buyers. Each listing's days are on its own page.")
    if figure.get("source") == "ledger":
        notes.append("The shop total is what Etsy's ledger charged; the listings' shares are allocated from it "
                     "(listing fees by items sold, other fees by revenue).")
    if figure.get("source") == "rates":
        notes.append("Estimated from your fee rates for each listing's sales.")
    if metric == "ads":
        if totals.get("ads_unattributed"):
            notes.append("Etsy's ledger has the shop's ad spend per day but not per listing; per-listing spend comes "
                         "from uploaded Ads reports, and the rest shows as not attributed.")
    daily: dict[str, int] = defaultdict(int)
    if metric in ("revenue", "units", "orders"):
        key = {"revenue": "revenue_minor", "units": "units", "orders": "orders"}[metric]
        for lrows in s.sales.values():
            for d in lrows:
                if d.day in window:
                    daily[d.day.isoformat()] += getattr(d, key)
    types = []
    if metric in finance.FEES or metric in ("ads", "shipping"):
        cat = {"shipping": "shipping_labels"}.get(metric, metric)
        for e in s.ledger:
            if e.day in window and finance.category(e.ledger_type) == cat:
                daily[e.day.isoformat()] += finance.cost_of(e.amount_minor)
        types = [t for t in finance.ledger_by_category(s, window)[1] if t["category"] == cat]
    return {
        "metric": metric, "label": METRICS[metric], "figure": figure, "listed_total": total,
        "unattributed": totals.get("ads_unattributed") if metric == "ads" else None,
        "rows": [{**_ref(s, lid), "value": v, "share": (v / total) if total else None} for lid, v in rows],
        "ledger_types": types,
        "daily": [{"day": d, "value": v} for d, v in sorted(daily.items())],
        "notes": notes,
    }


@router.get("/breakdown")
async def breakdown(
    metric: str,
    shop: uuid.UUID | None = None,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> dict[str, Any]:
    """What a total is made of: which listings, which fee entries, which days."""
    if metric not in METRICS:
        raise HTTPException(status_code=422, detail="unknown figure")
    days = _days(days)
    loaded = await _shop_or_none(session, tenant, shop, enqueuer)
    if loaded is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")
    window = profit.Window.last(days, _today())
    return {"period": {"days": days, "start": window.start, "end": window.end}, **_breakdown(loaded, metric, window)}


def _minor_str(v: Any) -> str:
    return "" if v is None else f"{v / 100:.2f}"


@router.get("/export")
async def export(
    view: str,
    shop: uuid.UUID | None = None,
    days: int = 30,
    compare: str = "previous",
    metric: str | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> Response:
    """The seller's figures as CSV, for their own spreadsheet. Amounts in the
    shop's currency (major units); blanks are figures that aren't available."""
    days, compare = _days(days), _compare(compare)
    loaded = await _shop_or_none(session, tenant, shop, enqueuer)
    if loaded is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")
    s, today = loaded.shop, _today()
    window = profit.Window.last(days, today)
    buf = io.StringIO()
    w = csv.writer(buf)
    if view == "listings":
        other = finance.comparison(s, window, compare)[0]
        w.writerow(["listing_id", "title", "state", "sku", "launched", "status", "trend", "units", "orders", "revenue",
                    "listing_fees", "transaction_fees", "processing_fees", "fees_source", "product_cost", "shipping",
                    "ad_spend", "net", "margin", "net_per_unit", "acos", "break_even_acos", "spend_per_sale",
                    "compared_revenue", "compared_net", "action", "stake_per_30_days", "etsy_link"])
        for r in _listing_rows(loaded, window, other, today):
            e, c, a = r["economics"] or {}, r["comparison"] or {}, r["action"] or {}
            fees = e.get("fees") or {}
            w.writerow([r["listing_id"], r["title"] or "", r["state"] or "", r["sku"] or "", r["launched"] or "",
                        r["status"] or "", r["trend"] or "", e.get("units", ""), e.get("orders", ""), _minor_str(e.get("revenue")),
                        _minor_str(fees.get("listing_fees")), _minor_str(fees.get("transaction_fees")),
                        _minor_str(fees.get("processing_fees")), e.get("fees_source", ""), _minor_str(e.get("product")),
                        _minor_str(e.get("shipping")), _minor_str(e.get("ads")), _minor_str(e.get("net")),
                        "" if e.get("margin") is None else f"{e['margin']:.4f}", _minor_str(e.get("net_per_unit")),
                        "" if e.get("acos") is None else f"{e['acos']:.4f}",
                        "" if e.get("break_even_acos") is None else f"{e['break_even_acos']:.4f}",
                        _minor_str(e.get("spend_per_sale")), _minor_str(c.get("revenue")), _minor_str(c.get("net")),
                        a.get("kind", ""), _minor_str(a.get("stake")), r["url"]])
    elif view == "daily":
        w.writerow(["day", "revenue", "rolling_7_day_avg", "rolling_28_day_avg"])
        for d in finance.daily_series(s, window, None)["current"]:
            w.writerow([d["day"], _minor_str(d["revenue"]), _minor_str(d["avg7"]), _minor_str(d["avg28"])])
    elif view == "ledger":
        w.writerow(["ledger_type", "category", "counted", "entries", "amount"])
        for t in finance.ledger_by_category(s, window)[1]:
            w.writerow([t["ledger_type"], t["category"] or "", "yes" if t["counted"] else "no", t["entries"], _minor_str(t["amount"])])
    elif view == "breakdown" and metric in METRICS:
        b = _breakdown(loaded, metric, window)
        money = metric not in ("units", "orders")
        w.writerow(["listing_id", "title", metric, "share", "etsy_link"])
        for r in b["rows"]:
            w.writerow([r["listing_id"], r["title"] or "", _minor_str(r["value"]) if money else r["value"],
                        "" if r["share"] is None else f"{r['share']:.4f}", r["url"]])
    else:
        raise HTTPException(status_code=422, detail="unknown export")
    name = f"{view}{'-' + metric if metric and view == 'breakdown' else ''}-{window.start}-to-{window.end}.csv"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
