"""Analytics, the rebuilt screens: one month of a shop's money (pipeline/pnl.py).

``GET /api/analytics/month``: the month's receipt (with last month and the same
month last year beside it), what needs attention, and every listing's row with
its class and the reason. ``GET/PUT /api/analytics/product-costs``: what each
profile costs to make and ship, the only source of product cost.

Month totals: imported statement > Etsy's ledger > nothing. Listings: statement
order rows joined to the sales read by order number. Every figure carries how
it was arrived at (exact / calculated / estimated); one with nothing behind it
is null with the reason. The caller's own shop only.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import analytics as analytics_api
from app.api import imports as imports_api
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session
from app.api.shops import selected_shop, shop_label
from app.db.models import (
    AdsDaily,
    EtsyConnection,
    ListingProfile,
    SaleLine,
    SalesSync,
    StatementImport,
    StatementListingFee,
    StatementOrder,
    Tenant,
)
from app.pipeline import finance, listing_traffic, pnl, profile_shops, profit

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

#: Months offered: the 13 kept.
MONTHS = 13
#: Per-size product costs need the size that was sold, which the sales read does
#: not keep (it reads no variations). Off until that is decided.
SIZES_SUPPORTED = False


def _first(day: date) -> date:
    return day.replace(day=1)


def _shift(month: date, by: int) -> date:
    index = month.year * 12 + (month.month - 1) + by
    return date(index // 12, index % 12 + 1, 1)


def _last(month: date) -> date:
    return _shift(month, 1) - timedelta(days=1)


async def _lines(session: AsyncSession, connection: EtsyConnection, *, receipts: list[int] | None, month: date) -> list[pnl.Line]:
    rows: list[SaleLine] = []
    if receipts is None:
        rows = list((await session.execute(
            select(SaleLine).where(SaleLine.connection_id == connection.id, SaleLine.day >= month, SaleLine.day <= _last(month))
        )).scalars())
    else:
        for i in range(0, len(receipts), 400):
            rows += list((await session.execute(
                select(SaleLine).where(SaleLine.connection_id == connection.id, SaleLine.receipt_id.in_(receipts[i:i + 400]))
            )).scalars())
    return [pnl.Line(r.receipt_id, r.listing_id, r.day, r.quantity, r.price_minor, r.shipping_minor) for r in rows]


def _sales_note(sync: SalesSync | None) -> str | None:
    if sync is None or sync.state in ("none", "estimating", "estimated"):
        return "The shop's sales have not been read yet (start it above)."
    if sync.state in ("reading", "waiting"):
        return "The shop's sales are still being read."
    if not sync.has_lines:
        return "The shop's sales were read before order lines were kept; they are being read once more."
    return None


async def load_month(session: AsyncSession, tenant: Tenant, connection: EtsyConnection, month: date,
                     profile_of: dict[int, str]) -> pnl.MonthInput:
    data = pnl.MonthInput(month=month, costs=pnl.ProductCosts.from_stored(tenant.cost_settings), profile_of=profile_of)
    statement = await session.get(StatementImport, (connection.id, month))
    sync = await session.get(SalesSync, connection.id)
    data.sales_note = _sales_note(sync)
    if statement is not None:
        data.statement = {k: int(v or 0) for k, v in (statement.totals or {}).items()}
        data.deposits_minor = sum(int(d.get("minor") or 0) for d in statement.deposits or [])
        for row in (await session.execute(select(StatementOrder).where(
                StatementOrder.connection_id == connection.id, StatementOrder.month == month))).scalars():
            data.orders[row.receipt_id] = {k: int(v or 0) for k, v in (row.amounts or {}).items()}
        for row in (await session.execute(select(StatementListingFee).where(
                StatementListingFee.connection_id == connection.id, StatementListingFee.month == month))).scalars():
            data.listing_fees[row.listing_id] = int(row.amount_minor or 0) + int(row.credits_minor or 0)
        data.lines = await _lines(session, connection, receipts=list(data.orders), month=month)
    else:
        data.ledger, data.ledger_note = await imports_api._ledger(session, connection, month, _last(month))
        if data.ledger is None:
            data.ledger_note = None
        data.lines = await _lines(session, connection, receipts=None, month=month)
    ads = (await session.execute(
        select(func.count(), func.sum(AdsDaily.spend_minor), func.sum(AdsDaily.revenue_minor))
        .where(AdsDaily.connection_id == connection.id, AdsDaily.day >= month, AdsDaily.day <= _last(month))
    )).one()
    if ads[0]:
        data.ads_days, data.ads_spend, data.ads_revenue = int(ads[0]), int(ads[1] or 0), int(ads[2] or 0)
    return data


def _monthly_units(shop: finance.Shop, months: list[date]) -> dict[int, list[int]]:
    """Units sold per listing for each of ``months`` (oldest first), from the daily totals."""
    index = {m: i for i, m in enumerate(months)}
    out: dict[int, list[int]] = defaultdict(lambda: [0] * len(months))
    for listing_id, days in shop.sales.items():
        for d in days:
            i = index.get(_first(d.day))
            if i is not None:
                out[listing_id][i] += d.units
    return out


def _row_out(row: pnl.ListingRow, shop: finance.Shop, cls: tuple[str, str], trend: list[int], statement: bool) -> dict[str, Any]:
    best = row.best
    return {
        **analytics_api._ref(shop, row.listing_id),
        "class": cls[0],
        "reason": cls[1],
        "units": row.units,
        "orders": row.orders,
        "items_minor": row.items,
        "shipping_paid_minor": row.shipping_paid,
        "revenue_minor": row.revenue,
        "refunds_minor": row.refunds if statement else None,
        "fees_minor": row.fees if statement else None,
        "offsite_ads_minor": row.offsite_ads if statement else None,
        "other_minor": (row.labels + row.other) if statement else None,
        "product_cost_minor": -(row.product_cost + row.provider_shipping) if row.costed and row.units else None,
        "uncosted_units": row.uncosted_units,
        "before_cost_minor": row.before_cost,
        "result_minor": row.result,
        #: Per item sold, as far as the figures go: profit before ads, else before product cost.
        "per_unit_minor": round(best / row.units) if best is not None and row.units else None,
        "costed": row.costed,
        "basis": pnl.CALCULATED,
        "units_before": trend[-2] if len(trend) > 1 else 0,
        "trend": trend,
    }


@router.get("/month")
async def month_view(
    shop: uuid.UUID | None = None,
    month: str | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> dict[str, Any]:
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        return {"connected": False}
    loaded = await analytics_api._load(session, tenant, connection, enqueuer)
    today = datetime.now(timezone.utc).date()
    imported = set((await session.execute(
        select(StatementImport.month).where(StatementImport.connection_id == connection.id))).scalars())
    newest = _first(today)
    choices = [_shift(newest, -i) for i in range(MONTHS)]
    if month is not None:
        chosen = imports_api._parse_month(month)
        if chosen not in choices:
            raise HTTPException(status_code=422, detail="that month is outside the 13 months kept")
    else:
        # The newest month there is a statement for; else the month before this one
        # (a month in progress has no statement and only part of its sales).
        chosen = max(imported & set(choices), default=_shift(newest, -1))

    profile_of = {lid: i.profile_id for lid, i in loaded.shop.info.items() if i.profile_id}
    money = profit.MoneyFormat(loaded.shop.currency)

    data = await load_month(session, tenant, connection, chosen, profile_of)
    rows, unattributed = pnl.listing_rows(data)
    sheet = pnl.receipt(data, rows)
    compare: dict[str, Any] = {}
    for key, other in (("last_month", _shift(chosen, -1)), ("last_year", _shift(chosen, -12))):
        if other not in choices and key == "last_month":
            compare[key] = None
            continue
        compare[key] = pnl.receipt(await load_month(session, tenant, connection, other, profile_of))

    trend_months = [_shift(chosen, -i) for i in range(5, -1, -1)]
    units = _monthly_units(loaded.shop, trend_months)
    before = {lid: series[-2] for lid, series in units.items()}
    # A listing that sold last month and nothing this month belongs in the table too.
    for lid, sold in before.items():
        if sold >= pnl.FADING_MIN_BEFORE and lid not in rows:
            rows[lid] = pnl.ListingRow(lid, fees_known=data.statement is not None)
    # Views and favourites of the listings the app published (Part D); one that had
    # views this month and no sale is in the table too, so its conversion shows.
    traffic = await listing_traffic.month_traffic(session, connection.id, chosen, _last(chosen))
    for lid, t in traffic.items():
        if t.views and lid not in rows:
            rows[lid] = pnl.ListingRow(lid, fees_known=data.statement is not None)
    top = pnl.winners(rows)
    classes = {
        lid: pnl.classify(r, before.get(lid, 0), (loaded.shop.info.get(lid) or finance.ListingInfo()).launched, _last(chosen), top, money)
        for lid, r in rows.items()
    }
    listing_rows = [
        {**_row_out(r, loaded.shop, classes[lid], units.get(lid, [0] * 6), data.statement is not None),
         **(traffic[lid].out(r.orders) if lid in traffic else {
             "views": None, "favorites": None, "conversion": None, "views_days": 0,
             "traffic_note": listing_traffic.NOT_APP})}
        for lid, r in rows.items()
    ]
    listing_rows.sort(key=lambda r: -(r["result_minor"] if r["result_minor"] is not None else r["before_cost_minor"] or r["revenue_minor"]))
    counts: dict[str, int] = defaultdict(int)
    for cls, _ in classes.values():
        counts[cls] += 1

    return {
        "connected": True,
        "shop_name": shop_label(connection),
        "currency": loaded.shop.currency,
        "month": chosen,
        "months": [{"month": m, "statement": m in imported, "in_progress": m == newest} for m in choices],
        "sheet": sheet,
        **compare,
        "attention": pnl.attention(data, sheet, rows, classes, before, unattributed, money),
        "unattributed": {
            **unattributed,
            #: Etsy Ads: a cost of the whole shop, never given to a listing.
            "etsy_ads_minor": next((line["minor"] for s in sheet["sections"] for line in s["lines"] if line["key"] == "etsy_ads"), None),
        },
        "listings": listing_rows,
        "classes": dict(counts),
        "trend_months": trend_months,
        "titles_refreshing": not loaded.shop.cache_fresh,
        "sizes_supported": SIZES_SUPPORTED,
        "traffic_label": listing_traffic.LABEL,
    }


#: The title-style comparison looks back this far by default (days).
STYLE_PERIOD_DAYS = 90


@router.get("/title-styles")
async def title_styles(
    shop: uuid.UUID | None = None,
    days: int = STYLE_PERIOD_DAYS,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> dict[str, Any]:
    """Listings published with the app in the last ``days``, by title style: views per
    listing per day, favourites per view, orders per view, with sample sizes and 95%
    intervals; "not enough data yet" below the thresholds. Nothing is rewritten."""
    if days not in (30, 60, 90, 180, 365):
        raise HTTPException(status_code=422, detail="days must be 30, 60, 90, 180 or 365")
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        return {"connected": False}
    loaded = await analytics_api._load(session, tenant, connection, enqueuer)
    today = datetime.now(timezone.utc).date()
    out = await listing_traffic.compare_styles(
        session, connection.id, today - timedelta(days=days - 1), today, today,
        orders_known=loaded.shop.sales_from is not None,
    )
    return {"connected": True, "shop_name": shop_label(connection), **out}


# --- product costs --------------------------------------------------------------------------------------


class SizeCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    production: str | int | float | None = None
    shipping: str | int | float | None = None


class ProfileCost(SizeCost):
    sizes: dict[str, SizeCost] = {}


class ProductCostsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: dict[uuid.UUID, ProfileCost]


async def _costs_out(session: AsyncSession, tenant: Tenant, connection: EtsyConnection) -> dict[str, Any]:
    stored = (tenant.cost_settings or {}).get("profile_costs") or {}
    legacy = (tenant.cost_settings or {}).get("product_cost_by_profile") or {}
    # The profiles used in this shop: its own and those linked to it (v8 §C). A
    # profile's cost is the account's, the same in every shop it is used in.
    profiles = [p for p in await profile_shops.shop_profiles(session, connection.id, confirmed=False)
                if p.tenant_id == tenant.id]
    out = []
    for p in profiles:
        entry = stored.get(str(p.id)) or {}
        out.append({
            "profile_id": p.id,
            "name": p.name,
            "production": entry.get("production") if entry else legacy.get(str(p.id)),
            "shipping": entry.get("shipping"),
            "sizes": entry.get("sizes") or {},
        })
    return {"shop_name": shop_label(connection), "profiles": out, "sizes_supported": SIZES_SUPPORTED}


@router.get("/product-costs")
async def get_product_costs(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> dict[str, Any]:
    connection = await imports_api._shop(session, tenant, shop)
    return await _costs_out(session, tenant, connection)


@router.put("/product-costs")
async def put_product_costs(
    body: ProductCostsIn,
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> dict[str, Any]:
    """What each profile costs to make and what the provider charges to ship it.
    An emptied field means "not set": a cost is never assumed."""
    connection = await imports_api._shop(session, tenant, shop)
    own = {p.id for p in await profile_shops.shop_profiles(session, connection.id, confirmed=False)
           if p.tenant_id == tenant.id}
    if not set(body.profiles) <= own:
        raise HTTPException(status_code=404, detail="profile not found")
    try:
        clean = pnl.validate_profile_costs({str(k): v.model_dump() for k, v in body.profiles.items()})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    settings = dict(tenant.cost_settings or {})
    kept = {k: v for k, v in (settings.get("profile_costs") or {}).items() if k not in {str(i) for i in body.profiles}}
    settings["profile_costs"] = {**kept, **clean}
    # What was entered per profile before is replaced by what is saved here.
    legacy = dict(settings.get("product_cost_by_profile") or {})
    for pid in body.profiles:
        legacy.pop(str(pid), None)
    settings["product_cost_by_profile"] = legacy
    tenant.cost_settings = settings
    await session.commit()
    return await _costs_out(session, tenant, connection)
