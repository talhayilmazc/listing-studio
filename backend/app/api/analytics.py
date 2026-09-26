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

import json
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session
from app.api.shops import missing_scopes, selected_shop
from app.db.models import (
    AdSpend,
    Asset,
    EtsyConnection,
    GeneratedContent,
    ListingProfile,
    ListingPublication,
    SalesDaily,
    ShopListingCache,
    Tenant,
)
from app.pipeline import ads_csv, profit
from app.pipeline.reference import decode_etsy_text

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


@router.post("/sales/refresh", status_code=202)
async def refresh_sales(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> dict[str, bool]:
    """Read the shop's latest sales now instead of at the nightly run.

    One queued read per shop however often it is pressed; it is upkeep, not the
    seller's own quota.
    """
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")
    if SALES_SCOPE in missing_scopes(connection):
        raise HTTPException(status_code=409, detail="reconnect your shop to allow reading its sales")
    await enqueuer.enqueue("sync_sales", str(connection.id), _job_id=f"manual-sales:{connection.id}")
    return {"queued": True}


# --- The report (C3, C4) ----------------------------------------------------------------


@dataclass
class _ListingInfo:
    title: str | None = None
    state: str | None = None
    url: str | None = None
    thumbnail_url: str | None = None
    sku: str | None = None
    profile_id: str | None = None
    profile_name: str | None = None
    created: date | None = None


@dataclass
class _ShopData:
    connection: EtsyConnection
    sales: dict[int, list[profit.DaySales]] = field(default_factory=lambda: defaultdict(list))
    ads: dict[int, list[profit.AdRow]] = field(default_factory=lambda: defaultdict(list))
    info: dict[int, _ListingInfo] = field(default_factory=lambda: defaultdict(_ListingInfo))
    currency: str | None = None
    #: The listing cache is fresh, so a listing missing from it is not active.
    cache_fresh: bool = False
    has_sales: bool = False


def _fresh(fetched_at: datetime) -> bool:
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - fetched_at).total_seconds() < ShopListingCache.STALE_SECONDS


def _stamp_day(value: Any) -> date | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


async def _load(session: AsyncSession, tenant: Tenant, connection: EtsyConnection, enqueuer: Enqueuer) -> _ShopData:
    data = _ShopData(connection=connection)
    currencies: Counter[str] = Counter()
    rows = await session.execute(select(SalesDaily).where(SalesDaily.connection_id == connection.id))
    for s in rows.scalars():
        data.sales[s.listing_id].append(profit.DaySales(s.listing_id, s.day, s.units, s.orders, s.revenue_minor))
        if s.currency:
            currencies[s.currency] += 1
        data.has_sales = True
    data.currency = currencies.most_common(1)[0][0] if currencies else None

    ads = await session.execute(select(AdSpend).where(AdSpend.connection_id == connection.id))
    for a in ads.scalars():
        data.ads[a.listing_id].append(
            profit.AdRow(a.listing_id, a.period_start, a.period_end, a.spend_minor, a.ad_orders, a.ad_revenue_minor)
        )

    # Which profile each listing belongs to, for product cost: listings the app
    # drafted from a profile, and the profiles' own reference listings.
    profiles = {
        p.id: p
        for p in (
            await session.execute(select(ListingProfile).where(ListingProfile.connection_id == connection.id))
        ).scalars()
    }
    for p in profiles.values():
        info = data.info[p.reference_listing_id]
        info.profile_id, info.profile_name = str(p.id), p.name
    pubs = await session.execute(
        select(ListingPublication.etsy_listing_id, ListingPublication.profile_id, Asset.parsed_sku)
        .join(GeneratedContent, GeneratedContent.id == ListingPublication.content_id)
        .join(Asset, Asset.id == GeneratedContent.asset_id)
        .where(ListingPublication.connection_id == connection.id)
    )
    for listing_id, profile_id, sku in pubs.all():
        info = data.info[listing_id]
        if profile_id in profiles:
            info.profile_id, info.profile_name = str(profile_id), profiles[profile_id].name
        info.sku = info.sku or sku

    cached = (
        await session.execute(
            select(ShopListingCache).where(
                ShopListingCache.tenant_id == tenant.id, ShopListingCache.connection_id == connection.id
            )
        )
    ).scalars().all()
    fresh = [c for c in cached if _fresh(c.fetched_at)]
    data.cache_fresh = bool(fresh) and len(fresh) == len(cached)
    if not data.cache_fresh:
        # One queued sync however often the page asks (it polls while sales are read).
        await enqueuer.enqueue("sync_shop_listings", str(connection.id), _job_id=f"manual-sync:{connection.id}")
    for c in fresh:  # expired listing content is never shown
        row = c.payload or {}
        info = data.info[c.listing_id]
        info.title = decode_etsy_text(row.get("title")) or None
        info.state = row.get("state")
        info.url = row.get("url")
        images = row.get("images") or []
        if images:
            info.thumbnail_url = images[0].get("url_170x135") or images[0].get("url_570xN")
        skus = [s for s in row.get("skus") or [] if s]
        if skus:
            info.sku = skus[0]
        info.created = _stamp_day(row.get("original_creation_timestamp") or row.get("created_timestamp"))
    return data


def _link(listing_id: int, info: _ListingInfo) -> str:
    """The ToU back link: the public page for an active listing, else Shop Manager."""
    if info.state == "draft":
        return profit.editor_url(listing_id)
    return info.url or f"https://www.etsy.com/listing/{listing_id}"


def _units(sales: list[profit.DaySales], window: profit.Window) -> int:
    return sum(s.units for s in sales if s.day in window)


@dataclass
class _Row:
    listing_id: int
    info: _ListingInfo
    facts: profit.ListingFacts
    verdict: profit.Verdict


def _report(data: _ShopData, costs: profit.CostSettings, days: int, today: date) -> list[_Row]:
    window = profit.Window.last(days, today)
    previous = window.previous()
    long = profit.Window.last(profit.LOSER_DAYS, today)
    active = {lid for lid, i in data.info.items() if i.state == "active"}
    ids = set(data.sales) | set(data.ads) | active

    facts: list[tuple[int, _ListingInfo, profit.ListingFacts]] = []
    for lid in ids:
        info = data.info.get(lid) or _ListingInfo()
        sales, ads = data.sales.get(lid, []), data.ads.get(lid, [])
        unit_cost = costs.product_cost_for(info.sku, info.profile_id)
        cur = profit.listing_metrics(sales, ads, window, costs, unit_cost)
        prev = profit.listing_metrics(sales, ads, previous, costs, unit_cost)
        # A listing no longer active (sold out, deactivated, deleted) with nothing
        # in either period is history, not something to act on.
        if data.cache_fresh and lid not in active and not (cur.units or prev.units or cur.ad_spend):
            continue
        facts.append(
            (
                lid,
                info,
                profit.ListingFacts(
                    listing_id=lid,
                    current=cur,
                    previous=prev,
                    units_long=_units(sales, long),
                    age_days=(today - info.created).days if info.created else None,
                    last_year_current=_units(sales, window.shifted(YEAR_DAYS)),
                    last_year_previous=_units(sales, previous.shifted(YEAR_DAYS)),
                ),
            )
        )
    top = profit.winners([f for _, _, f in facts])
    money = profit.MoneyFormat(data.currency)
    rows = [_Row(lid, info, f, profit.classify(f, top, days, money)) for lid, info, f in facts]
    rows.sort(key=lambda r: (-r.verdict.priority, r.listing_id))
    return rows


class VerdictOut(BaseModel):
    klass: str
    reason: str
    action: str
    links: list[dict[str, str]]


class ListingRowOut(BaseModel):
    listing_id: int
    title: str | None
    state: str | None
    url: str
    thumbnail_url: str | None
    sku: str | None
    profile_name: str | None
    current: dict[str, Any]
    previous: dict[str, Any]
    revenue_change: float | None
    net_change: float | None
    units_change: float | None
    verdict: VerdictOut


def _row_out(r: _Row) -> ListingRowOut:
    cur, prev = r.facts.current, r.facts.previous
    return ListingRowOut(
        listing_id=r.listing_id,
        title=r.info.title,
        state=r.info.state,
        url=_link(r.listing_id, r.info),
        thumbnail_url=r.info.thumbnail_url,
        sku=r.info.sku,
        profile_name=r.info.profile_name,
        current=cur.as_dict(),
        previous=prev.as_dict(),
        revenue_change=profit.change(cur.revenue, prev.revenue),
        net_change=profit.change(cur.net, prev.net),
        units_change=profit.change(cur.units, prev.units),
        verdict=VerdictOut(
            klass=r.verdict.klass, reason=r.verdict.reason, action=r.verdict.action, links=r.verdict.links
        ),
    )


class StatusOut(BaseModel):
    connected: bool
    can_read_sales: bool
    synced_at: datetime | None
    has_sales: bool
    currency: str | None
    titles_refreshing: bool
    ads_until: date | None


class TotalsOut(BaseModel):
    revenue: int
    units: int
    orders: int
    fees: int
    product_cost: int
    shipping_cost: int
    ad_spend: int
    ad_revenue: int
    fixed_costs: int
    costs: int
    net: int
    margin: float | None
    aov: int | None
    acos: float | None
    roas: float | None


class OverviewOut(BaseModel):
    status: StatusOut
    days: int
    start: date | None = None
    end: date | None = None
    current: TotalsOut | None = None
    previous: TotalsOut | None = None
    classes: dict[str, int] = {}
    attention: list[ListingRowOut] = []
    best: list[ListingRowOut] = []
    worst: list[ListingRowOut] = []


def _totals(metrics: list[profit.Metrics], fixed: int) -> TotalsOut:
    t = profit.Metrics()
    for m in metrics:
        for k in ("units", "orders", "revenue", "transaction_fee", "payment_fee", "listing_fee",
                  "product_cost", "shipping_cost", "ad_spend", "ad_orders", "ad_revenue"):
            setattr(t, k, getattr(t, k) + getattr(m, k))
    net = t.net - fixed
    return TotalsOut(
        revenue=t.revenue,
        units=t.units,
        orders=t.orders,
        fees=t.fees,
        product_cost=t.product_cost,
        shipping_cost=t.shipping_cost,
        ad_spend=t.ad_spend,
        ad_revenue=t.ad_revenue,
        fixed_costs=fixed,
        costs=t.costs + fixed,
        net=net,
        margin=net / t.revenue if t.revenue else None,
        aov=t.aov,
        acos=t.acos,
        roas=t.ad_revenue / t.ad_spend if t.ad_spend else None,
    )


def _status(connection: EtsyConnection | None, data: _ShopData | None) -> StatusOut:
    ads_until = None
    if data:
        ends = [a.period_end for rows in data.ads.values() for a in rows]
        ads_until = max(ends) if ends else None
    return StatusOut(
        connected=connection is not None,
        can_read_sales=connection is not None and SALES_SCOPE not in missing_scopes(connection),
        synced_at=connection.sales_synced_at if connection else None,
        has_sales=bool(data and data.has_sales),
        currency=data.currency if data else None,
        titles_refreshing=bool(data and not data.cache_fresh),
        ads_until=ads_until,
    )


def _days(days: int) -> int:
    if days not in PERIODS:
        raise HTTPException(status_code=422, detail="period must be 7, 30 or 90 days")
    return days


def _today() -> date:
    return datetime.now(timezone.utc).date()


@router.get("/overview", response_model=OverviewOut)
async def overview(
    shop: uuid.UUID | None = None,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> OverviewOut:
    """The shop's profit for the period against the one before, and where to look first."""
    days = _days(days)
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        return OverviewOut(status=_status(None, None), days=days)
    data = await _load(session, tenant, connection, enqueuer)
    costs = profit.CostSettings.from_stored(tenant.cost_settings)
    today = _today()
    window = profit.Window.last(days, today)
    rows = _report(data, costs, days, today)

    selling = [r for r in rows if r.facts.current.units > 0]
    by_net = sorted(selling, key=lambda r: r.facts.current.net, reverse=True)
    worst = sorted(
        [r for r in rows if r.facts.current.net < 0 or r.facts.current.ad_spend > 0],
        key=lambda r: r.facts.current.net,
    )
    attention = [r for r in rows if r.verdict.klass in (profit.AD_SINK, profit.FADING)]
    attention += [r for r in rows if r.verdict.klass == profit.LOSER and r.facts.current.units > 0]
    return OverviewOut(
        status=_status(connection, data),
        days=days,
        start=window.start,
        end=window.end,
        current=_totals([r.facts.current for r in rows], profit.fixed_costs(costs, window)),
        previous=_totals([r.facts.previous for r in rows], profit.fixed_costs(costs, window.previous())),
        classes=dict(Counter(r.verdict.klass for r in rows)),
        attention=[_row_out(r) for r in attention[:8]],
        best=[_row_out(r) for r in by_net[:5]],
        worst=[_row_out(r) for r in (worst or by_net[::-1])[:5]],
    )


class ListingsOut(BaseModel):
    status: StatusOut
    days: int
    listings: list[ListingRowOut]


@router.get("/listings", response_model=ListingsOut)
async def listings(
    shop: uuid.UUID | None = None,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> ListingsOut:
    """Every listing with its figures and class, most in need of attention first."""
    days = _days(days)
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        return ListingsOut(status=_status(None, None), days=days, listings=[])
    data = await _load(session, tenant, connection, enqueuer)
    rows = _report(data, profit.CostSettings.from_stored(tenant.cost_settings), days, _today())
    return ListingsOut(status=_status(connection, data), days=days, listings=[_row_out(r) for r in rows])


class WeekOut(BaseModel):
    start: date
    units: int
    revenue: int


class AdPeriodOut(BaseModel):
    period_start: date
    period_end: date
    spend: int
    ad_orders: int
    ad_revenue: int


class ListingDetailOut(BaseModel):
    status: StatusOut
    days: int
    listing: ListingRowOut
    unit_cost: str
    unit_cost_source: str
    weeks: list[WeekOut]
    ads: list[AdPeriodOut]


@router.get("/listings/{listing_id}", response_model=ListingDetailOut)
async def listing_detail(
    listing_id: int,
    shop: uuid.UUID | None = None,
    days: int = 30,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> ListingDetailOut:
    """One listing: its weekly sales over 13 months, its cost breakdown and the advice."""
    days = _days(days)
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        raise HTTPException(status_code=404, detail="not found")
    data = await _load(session, tenant, connection, enqueuer)
    costs = profit.CostSettings.from_stored(tenant.cost_settings)
    today = _today()
    row = next((r for r in _report(data, costs, days, today) if r.listing_id == listing_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")

    first = today - timedelta(days=today.weekday()) - timedelta(weeks=CHART_WEEKS - 1)
    weeks = [WeekOut(start=first + timedelta(weeks=i), units=0, revenue=0) for i in range(CHART_WEEKS)]
    for s in data.sales.get(listing_id, []):
        i = (s.day - first).days // 7
        if 0 <= i < CHART_WEEKS:
            weeks[i].units += s.units
            weeks[i].revenue += s.revenue_minor

    info = row.info
    sku_cost = info.sku and info.sku.strip().casefold() in {
        k.strip().casefold() for k in costs.product_cost_by_sku
    }
    source = (
        f"SKU {info.sku}" if sku_cost
        else f"profile {info.profile_name}" if info.profile_id in costs.product_cost_by_profile
        else "default"
    )
    return ListingDetailOut(
        status=_status(connection, data),
        days=days,
        listing=_row_out(row),
        unit_cost=str(costs.product_cost_for(info.sku, info.profile_id)),
        unit_cost_source=source,
        weeks=weeks,
        ads=[
            AdPeriodOut(
                period_start=a.period_start, period_end=a.period_end, spend=a.spend_minor,
                ad_orders=a.ad_orders, ad_revenue=a.ad_revenue_minor,
            )
            for a in sorted(data.ads.get(listing_id, []), key=lambda a: a.period_start, reverse=True)
        ],
    )


# --- Etsy Ads CSV (C1) ---------------------------------------------------------------


class AdsPreviewOut(BaseModel):
    headers: list[str]
    sample: list[list[str]]
    rows: int
    mapping: dict[str, str | None]


async def _read_upload(file: UploadFile) -> ads_csv.Table:
    data = await file.read(ads_csv.MAX_BYTES + 1)
    try:
        return ads_csv.read_table(data)
    except ads_csv.CsvError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    finally:
        del data  # the file itself is never kept


@router.post("/ads/preview", response_model=AdsPreviewOut)
async def ads_preview(file: UploadFile = File(...), tenant: Tenant = Depends(active_tenant)) -> AdsPreviewOut:
    """The report's columns and first rows, for the mapping screen. Nothing is stored."""
    table = await _read_upload(file)
    return AdsPreviewOut(
        headers=table.headers,
        sample=table.rows[: ads_csv.PREVIEW_ROWS],
        rows=len(table.rows),
        mapping=ads_csv.guess_mapping(table.headers),
    )


class UnmatchedOut(BaseModel):
    line: int
    label: str
    why: str


class AdsImportOut(BaseModel):
    upload_id: uuid.UUID | None
    matched: int
    replaced: int
    skipped: int
    unmatched: list[UnmatchedOut]
    unmatched_total: int
    spend: int
    titles_refreshing: bool


@router.post("/ads/import", response_model=AdsImportOut)
async def ads_import(
    file: UploadFile = File(...),
    mapping: str = Form(...),
    period_start: date | None = Form(None),
    period_end: date | None = Form(None),
    shop: uuid.UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> AdsImportOut:
    """Match the report's rows to the seller's own listings and keep their spend.

    Only this shop's own listings match (by id, or by exact title from the fresh
    listing cache). Uploading a report again for a period already uploaded
    replaces those listings' rows for it instead of counting them twice.
    """
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")
    try:
        chosen = json.loads(mapping)
        if not isinstance(chosen, dict):
            raise ValueError
        chosen = {k: (str(v) if v else None) for k, v in chosen.items() if k in ads_csv.FIELDS}
    except ValueError:
        raise HTTPException(status_code=422, detail="the column mapping could not be read") from None
    period = None
    if period_start or period_end:
        if not (period_start and period_end) or period_end < period_start:
            raise HTTPException(status_code=422, detail="enter the period's first and last day")
        if (period_end - period_start).days > SalesDaily.RETENTION_DAYS:
            raise HTTPException(status_code=422, detail="the period is longer than 13 months")
        period = (period_start, period_end)

    data = await _load(session, tenant, connection, enqueuer)
    # This shop's own listings only: its sales, its cache, its profiles and drafts.
    own_ids = set(data.sales) | set(data.ads) | set(data.info)
    titles = {lid: i.title for lid, i in data.info.items() if i.title}
    table = await _read_upload(file)
    try:
        parsed = ads_csv.parse_rows(table, chosen, own_ids, ads_csv.own_title_index(titles), period)
    except ads_csv.CsvError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    oldest = _today() - timedelta(days=SalesDaily.RETENTION_DAYS)
    rows = [r for r in parsed.rows if r.period_end >= oldest]
    replaced = 0
    upload_id = None
    if rows:
        upload_id = uuid.uuid4()
        # The same listing and period uploaded again replaces the earlier figure.
        for key in {(r.listing_id, r.period_start, r.period_end) for r in rows}:
            result = await session.execute(
                delete(AdSpend).where(
                    AdSpend.connection_id == connection.id,
                    AdSpend.listing_id == key[0],
                    AdSpend.period_start == key[1],
                    AdSpend.period_end == key[2],
                )
            )
            replaced += result.rowcount or 0
        merged: dict[tuple[int, date, date], ads_csv.ParsedRow] = {}
        for r in rows:  # a listing listed twice in one report is summed
            key = (r.listing_id, r.period_start, r.period_end)
            if key in merged:
                m = merged[key]
                m.spend_minor += r.spend_minor
                m.ad_orders += r.ad_orders
                m.ad_revenue_minor += r.ad_revenue_minor
            else:
                merged[key] = ads_csv.ParsedRow(**r.__dict__)
        for r in merged.values():
            session.add(
                AdSpend(
                    tenant_id=tenant.id, connection_id=connection.id, upload_id=upload_id,
                    listing_id=r.listing_id, period_start=r.period_start, period_end=r.period_end,
                    spend_minor=r.spend_minor, ad_orders=r.ad_orders, ad_revenue_minor=r.ad_revenue_minor,
                )
            )
        await session.commit()
    return AdsImportOut(
        upload_id=upload_id,
        matched=len(rows),
        replaced=replaced,
        skipped=parsed.skipped + (len(parsed.rows) - len(rows)),
        unmatched=[UnmatchedOut(line=u.line, label=u.label, why=u.why) for u in parsed.unmatched[:50]],
        unmatched_total=len(parsed.unmatched),
        spend=sum(r.spend_minor for r in rows),
        titles_refreshing=not data.cache_fresh,
    )


class AdsUploadOut(BaseModel):
    upload_id: uuid.UUID
    created_at: datetime
    period_start: date
    period_end: date
    listings: int
    spend: int


@router.get("/ads/uploads", response_model=list[AdsUploadOut])
async def ads_uploads(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[AdsUploadOut]:
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        return []
    rows = await session.execute(
        select(
            AdSpend.upload_id,
            func.min(AdSpend.created_at),
            func.min(AdSpend.period_start),
            func.max(AdSpend.period_end),
            func.count(func.distinct(AdSpend.listing_id)),
            func.sum(AdSpend.spend_minor),
        )
        .where(AdSpend.connection_id == connection.id)
        .group_by(AdSpend.upload_id)
        .order_by(func.min(AdSpend.created_at).desc())
    )
    return [
        AdsUploadOut(upload_id=u, created_at=c, period_start=s, period_end=e, listings=n, spend=int(sp or 0))
        for u, c, s, e, n, sp in rows.all()
    ]


@router.delete("/ads/uploads/{upload_id}", status_code=204)
async def delete_ads_upload(
    upload_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> None:
    result = await session.execute(
        delete(AdSpend).where(AdSpend.tenant_id == tenant.id, AdSpend.upload_id == upload_id)
    )
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="not found")
    await session.commit()

