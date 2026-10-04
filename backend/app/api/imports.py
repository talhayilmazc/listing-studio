"""Import from Etsy: the monthly statement and the Etsy Ads report (Analytics).

Two files the seller downloads from their own Shop Manager and uploads here.
Both are read in memory and **never kept**: what is stored is a total per
category, the per-order and per-listing amounts attribution needs, and the Ads
report's daily totals for the shop. Neither file holds buyer data and none is
stored.

After an upload the answer shows what was read (rows, dates, a total per
category) beside the app's own calculation for the same days, and every
difference with its reason (``pipeline/statement_compare.py``). The same report
can be read again per month, and ``/status`` lists every month's state.

Ad spend is shop level: the Ads report has no listing column, so nothing here
ties it to a listing. The old import that matched report rows to listings by
title is gone (migration 0043 deleted its rows).
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import active_tenant, get_session
from app.api.shops import selected_shop, shop_label
from app.db.models import (
    AdCharge,
    AdsDaily,
    EtsyConnection,
    LedgerDaily,
    LedgerSync,
    SaleLine,
    SalesSync,
    StatementImport,
    StatementListingFee,
    StatementOrder,
    Tenant,
)
from app.pipeline import ads_report, attribution, statement, statement_compare
from app.workers import ledger as ledger_worker

router = APIRouter(prefix="/api/analytics/import", tags=["analytics-import"])

MAX_BYTES = 8 * 1024 * 1024
#: Orders shown one by one when the statement and the sales read disagree on them.
SHOWN_DIFFERENCES = 20


# --- what the screens receive --------------------------------------------------------------------
class CategoryOut(BaseModel):
    key: str
    label: str
    group: str
    minor: int
    rows: int


class StatementOut(BaseModel):
    imported_at: datetime
    rows: int
    first_day: date
    last_day: date
    currency: str | None
    net_minor: int  # the categories add up to exactly this
    categories: list[CategoryOut]
    # Sales less what was collected for others (sales tax, buyer-paid state fees). Never the raw sales figure.
    revenue_minor: int
    # Refunds less the sales tax returned with them, and the two parts.
    refunds_net_minor: int
    refunded_minor: int
    tax_returned_minor: int
    etsy_fees_minor: int
    ads_minor: int
    shipping_minor: int
    pass_through_minor: int
    # Transfers to the bank: neither income nor cost.
    deposits: list[dict[str, Any]]
    deposits_minor: int
    unrecognised: list[dict[str, Any]]
    notes: list[str]


class AdsDayOut(BaseModel):
    day: date
    reported_minor: int | None
    charged_minor: int | None
    reason: str


class AdsOut(BaseModel):
    """The two honest figures for a month's ads, and how they square."""

    # "Ad spend for clicks this month": the Ads report, day by day.
    report_days: int
    month_days: int
    reported_minor: int | None
    report_revenue_minor: int | None
    report_orders: int | None
    clicks: int | None
    views: int | None
    # "Charged by Etsy this month": the statement.
    charged_minor: int | None
    charge_days: int
    # Per click day, when both are imported.
    matched_days: int | None = None
    billed_later: list[AdsDayOut] = []
    billed_from_before: list[AdsDayOut] = []
    billed_differently: list[AdsDayOut] = []
    exact: bool | None = None  # the differences explain the gap to the cent
    note: str


class OrderDifferenceOut(BaseModel):
    receipt_id: int
    statement_minor: int
    items_minor: int
    shipping_minor: int
    difference_minor: int


class OrdersOut(BaseModel):
    orders: int
    matched: int
    unmatched: int
    exact: int
    statement_minor: int
    items_minor: int
    shipping_minor: int
    difference_minor: int
    unmatched_minor: int
    differing: int
    largest: list[OrderDifferenceOut]
    note: str


class ComparisonOut(BaseModel):
    key: str
    label: str
    statement_minor: int | None
    ours_minor: int | None
    ours_source: str | None
    difference_minor: int | None
    status: str  # match | explained | unexplained | statement_only
    reason: str


class MonthOut(BaseModel):
    month: date
    shop_name: str | None
    statement: StatementOut | None
    ads: AdsOut
    orders: OrdersOut | None
    listing_fees: dict[str, int] | None
    comparison: list[ComparisonOut]


class MonthStatusOut(BaseModel):
    month: date
    statement_imported_at: datetime | None
    statement_rows: int | None
    statement_net_minor: int | None
    statement_first_day: date | None
    statement_last_day: date | None
    ads_days: int
    month_days: int
    # "complete" | "statement only" | "ads only" | "ads partial" | "nothing"
    state: str


class StatusOut(BaseModel):
    shop_name: str | None
    currency: str | None
    months: list[MonthStatusOut]


# --- helpers -------------------------------------------------------------------------------------
def _minor(amount: Decimal) -> int:
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise statement.StatementError(f"an amount has more than two decimals: {amount}")
    return int(cents)


def _month(day: date) -> date:
    return day.replace(day=1)


def _month_days(month: date) -> int:
    return calendar.monthrange(month.year, month.month)[1]


async def _shop(session: AsyncSession, tenant: Tenant, shop: uuid.UUID | None) -> EtsyConnection:
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")
    return connection


async def _read(file: UploadFile) -> bytes:
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="the file is larger than 8 MB; a month's statement is far smaller")
    return data


def _parse_month(text: str) -> date:
    try:
        return datetime.strptime(text, "%Y-%m").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="the month is written YYYY-MM") from None


# --- storing ---------------------------------------------------------------------------------------
async def store_statement(session: AsyncSession, connection: EtsyConnection, parsed: statement.Statement) -> date:
    """Replace the month's stored statement with this one. The caller commits."""
    assert parsed.first_day is not None and parsed.last_day is not None
    month = _month(parsed.first_day)
    if _month(parsed.last_day) != month:
        raise statement.StatementError(
            f"the file covers {parsed.first_day:%B %Y} to {parsed.last_day:%B %Y}; a monthly statement covers one month"
        )
    cid, tid = connection.id, connection.tenant_id
    for table in (StatementImport, StatementOrder, StatementListingFee):
        await session.execute(delete(table).where(table.connection_id == cid, table.month == month))
    await session.execute(delete(AdCharge).where(AdCharge.connection_id == cid, AdCharge.month == month))
    session.add(StatementImport(
        connection_id=cid, month=month, tenant_id=tid, currency=parsed.currency, rows=parsed.rows,
        first_day=parsed.first_day, last_day=parsed.last_day, net_minor=_minor(parsed.net_total),
        totals={k: _minor(v) for k, v in parsed.totals.items()},
        counts=dict(parsed.counts),
        credits={k: _minor(v) for k, v in parsed.credits.items()},
        deposits=[{"day": day.isoformat(), "minor": _minor(amount)} for day, amount in parsed.deposits],
        unrecognised=[{"type": u.type, "title": u.title, "category": u.category, "minor": _minor(u.net)} for u in parsed.unrecognised],
        notes=list(parsed.notes),
        imported_at=datetime.now(timezone.utc),
    ))
    for order in parsed.orders.values():
        session.add(StatementOrder(
            connection_id=cid, month=month, receipt_id=order.receipt_id, tenant_id=tid, day=order.day,
            amounts={k: _minor(v) for k, v in order.amounts.items()},
        ))
    for listing_id, (fees, amount, credits) in parsed.listing_fees.items():
        session.add(StatementListingFee(
            connection_id=cid, month=month, listing_id=listing_id, tenant_id=tid, fees=fees,
            amount_minor=_minor(amount), credits_minor=_minor(credits),
        ))
    # One charge per click day; a click day billed on two statements keeps the later one.
    by_day: dict[date, tuple[date, Decimal]] = {}
    for charge in parsed.ad_charges:
        posted, amount = by_day.get(charge.click_day, (charge.posted, Decimal("0")))
        by_day[charge.click_day] = (max(posted, charge.posted), amount + charge.amount)
    for click_day, (posted, amount) in by_day.items():
        await session.execute(delete(AdCharge).where(AdCharge.connection_id == cid, AdCharge.click_day == click_day))
        session.add(AdCharge(connection_id=cid, click_day=click_day, tenant_id=tid, posted=posted, month=month, amount_minor=_minor(amount)))
    return month


async def store_ads(session: AsyncSession, connection: EtsyConnection, report: ads_report.AdsReport) -> list[date]:
    """Replace the report's days. Returns the months it touches. The caller commits."""
    first, last = report.first_day, report.last_day
    assert first is not None and last is not None
    await session.execute(
        delete(AdsDaily).where(AdsDaily.connection_id == connection.id, AdsDaily.day >= first, AdsDaily.day <= last)
    )
    now = datetime.now(timezone.utc)
    for d in report.days:
        session.add(AdsDaily(
            connection_id=connection.id, day=d.day, tenant_id=connection.tenant_id, views=d.views, clicks=d.clicks,
            orders=d.orders, revenue_minor=_minor(d.revenue), spend_minor=_minor(d.spend), currency=report.currency,
            imported_at=now,
        ))
    return sorted({_month(d.day) for d in report.days})


# --- the month's report: what was read beside our own calculation ----------------------------------
def _statement_out(row: StatementImport) -> StatementOut:
    totals = {k: int(v) for k, v in (row.totals or {}).items()}
    counts = row.counts or {}
    group = lambda name: sum(v for k, v in totals.items() if statement.CATEGORIES.get(k, ("", "other"))[1] == name)  # noqa: E731
    t = lambda *names: sum(totals.get(n, 0) for n in names)  # noqa: E731
    deposits = list(row.deposits or [])
    return StatementOut(
        imported_at=row.imported_at, rows=row.rows, first_day=row.first_day, last_day=row.last_day, currency=row.currency,
        net_minor=row.net_minor,
        categories=[
            CategoryOut(key=key, label=label, group=g, minor=totals[key], rows=int(counts.get(key, 0)))
            for key, (label, g) in statement.CATEGORIES.items() if key in totals
        ],
        revenue_minor=t("sales", "sales_tax", "buyer_fees"),
        refunds_net_minor=t("refunds", "sales_tax_refund"),
        refunded_minor=t("refunds"),
        tax_returned_minor=t("sales_tax_refund"),
        etsy_fees_minor=group("etsy_fees"),
        ads_minor=group("ads"),
        shipping_minor=group("shipping"),
        pass_through_minor=group("pass_through"),
        deposits=deposits,
        deposits_minor=sum(int(d.get("minor") or 0) for d in deposits),
        unrecognised=list(row.unrecognised or []),
        notes=[str(n) for n in (row.notes or [])],
    )


def _day_out(d: ads_report.DayDifference) -> AdsDayOut:
    return AdsDayOut(
        day=d.day,
        reported_minor=None if d.reported is None else _minor(d.reported),
        charged_minor=None if d.charged is None else _minor(d.charged),
        reason=d.reason,
    )


async def _ads_out(session: AsyncSession, connection: EtsyConnection, month: date, has_statement: bool) -> AdsOut:
    last = month.replace(day=_month_days(month))
    days = list((await session.execute(
        select(AdsDaily).where(AdsDaily.connection_id == connection.id, AdsDaily.day >= month, AdsDaily.day <= last).order_by(AdsDaily.day)
    )).scalars())
    charges = list((await session.execute(
        select(AdCharge).where(AdCharge.connection_id == connection.id, AdCharge.month == month)
    )).scalars())
    out = AdsOut(
        report_days=len(days), month_days=_month_days(month),
        reported_minor=sum(d.spend_minor for d in days) if days else None,
        report_revenue_minor=sum(d.revenue_minor for d in days) if days else None,
        report_orders=sum(d.orders for d in days) if days else None,
        clicks=sum(d.clicks for d in days) if days else None,
        views=sum(d.views for d in days) if days else None,
        charged_minor=sum(c.amount_minor for c in charges) if has_statement else None,
        charge_days=len(charges),
        note="",
    )
    if not days and not has_statement:
        out.note = "Neither the statement nor the Ads report is imported for this month."
    elif not days:
        out.note = "The Ads report for this month is not imported: only what Etsy charged is known."
    elif not has_statement:
        out.note = "The statement for this month is not imported: only the spend for the month's clicks is known."
    else:
        report = ads_report.AdsReport(
            days=[ads_report.AdsDay(d.day, d.views, d.clicks, d.orders, Decimal(d.revenue_minor) / 100, Decimal(d.spend_minor) / 100) for d in days]
        )
        rec = ads_report.reconcile(
            [statement.AdCharge(click_day=c.click_day, posted=c.posted, amount=Decimal(c.amount_minor) / 100) for c in charges], report
        )
        out.matched_days = rec.matched_days
        out.billed_later = [_day_out(d) for d in rec.billed_later]
        out.billed_from_before = [_day_out(d) for d in rec.billed_from_before]
        out.billed_differently = [_day_out(d) for d in rec.billed_differently]
        out.exact = rec.exact
        partial = "" if len(days) == out.month_days else f" The Ads report covers {len(days)} of the month's {out.month_days} days."
        out.note = (
            "Each day's clicks are billed the next day, so the statement holds the last day of the month before and "
            "not its own last day." + partial
        )
    return out


async def _orders_out(session: AsyncSession, connection: EtsyConnection, month: date) -> tuple[OrdersOut | None, attribution.OrderJoin | None, str | None]:
    rows = list((await session.execute(
        select(StatementOrder).where(StatementOrder.connection_id == connection.id, StatementOrder.month == month)
    )).scalars())
    if not rows:
        return None, None, None
    receipts = [r.receipt_id for r in rows]
    lines: dict[int, list[SaleLine]] = {}
    for start in range(0, len(receipts), 500):
        for line in (await session.execute(
            select(SaleLine).where(SaleLine.connection_id == connection.id, SaleLine.receipt_id.in_(receipts[start:start + 500]))
        )).scalars():
            lines.setdefault(line.receipt_id, []).append(line)
    join = attribution.reconcile_orders({r.receipt_id: dict(r.amounts or {}) for r in rows}, lines)
    sync = await session.get(SalesSync, connection.id)
    if join.matched == join.orders:
        note = "Every order on the statement is in the sales read."
    elif sync is None:
        note = "Sales have not been read for this shop, so orders cannot be tied to listings yet. Start the sales read in Analytics."
    elif sync.state != "complete":
        note = "The sales read is still running; the orders it has not reached yet are not tied to listings."
    elif not sync.has_lines:
        note = "This shop's sales were read before order lines were kept. They are read once more by themselves (tonight), then orders are tied to listings."
    else:
        note = "Some orders on the statement are not in the sales read (it is brought up to date every night)."
    out = OrdersOut(
        orders=join.orders, matched=join.matched, unmatched=join.unmatched, exact=join.exact,
        statement_minor=join.statement_minor, items_minor=join.items_minor, shipping_minor=join.shipping_minor,
        difference_minor=join.difference_minor, unmatched_minor=join.unmatched_minor, differing=len(join.differing),
        largest=[
            OrderDifferenceOut(receipt_id=d.receipt_id, statement_minor=d.statement_minor, items_minor=d.items_minor,
                               shipping_minor=d.shipping_minor, difference_minor=d.difference_minor)
            for d in join.differing[:SHOWN_DIFFERENCES]
        ],
        note=note,
    )
    return out, (join if join.matched else None), (None if join.matched else note)


async def _ledger(session: AsyncSession, connection: EtsyConnection, first: date, last: date) -> tuple[dict[str, int] | None, str | None]:
    """The ledger's totals per type for these days, and what to say about its reach."""
    rows = (await session.execute(
        select(LedgerDaily.ledger_type, func.sum(LedgerDaily.amount_minor))
        .where(LedgerDaily.connection_id == connection.id, LedgerDaily.day >= first, LedgerDaily.day <= last)
        .group_by(LedgerDaily.ledger_type)
    )).all()
    sync = await session.get(LedgerSync, connection.id)
    reached = ledger_worker.covered_from(sync) if sync is not None else None
    if not rows:
        if sync is None or reached is None:
            return None, "The Etsy ledger has not been read for this shop yet (start it in Analytics), so there is nothing of ours to compare."
        return None, "The Etsy ledger read has no entries for these days."
    note = None
    start = datetime(first.year, first.month, first.day, tzinfo=timezone.utc).timestamp()
    if reached is not None and reached > start:
        since = datetime.fromtimestamp(reached, tz=timezone.utc).date()
        note = f"The ledger read reaches back only to {since:%b} {since.day}, {since.year}, so the days before it are missing from our figure."
    return {kind: int(amount or 0) for kind, amount in rows}, note


async def month_report(session: AsyncSession, connection: EtsyConnection, month: date) -> MonthOut:
    row = await session.get(StatementImport, (connection.id, month))
    ads = await _ads_out(session, connection, month, row is not None)
    if row is None:
        return MonthOut(month=month, shop_name=shop_label(connection), statement=None, ads=ads, orders=None, listing_fees=None, comparison=[])
    orders, join, sales_note = await _orders_out(session, connection, month)
    ledger, ledger_note = await _ledger(session, connection, row.first_day, row.last_day)
    fees = (await session.execute(
        select(func.count(), func.sum(StatementListingFee.fees), func.sum(StatementListingFee.amount_minor), func.sum(StatementListingFee.credits_minor))
        .where(StatementListingFee.connection_id == connection.id, StatementListingFee.month == month)
    )).one()
    lines = statement_compare.compare(
        {k: int(v) for k, v in (row.totals or {}).items()}, {k: int(v) for k, v in (row.credits or {}).items()},
        ledger=ledger, ledger_note=ledger_note, join=join, sales_note=sales_note,
    )
    return MonthOut(
        month=month, shop_name=shop_label(connection), statement=_statement_out(row), ads=ads, orders=orders,
        listing_fees={"listings": int(fees[0] or 0), "fees": int(fees[1] or 0), "minor": int(fees[2] or 0), "credits_minor": int(fees[3] or 0)},
        comparison=[
            ComparisonOut(key=line.key, label=line.label, statement_minor=line.statement_minor, ours_minor=line.ours_minor,
                          ours_source=line.ours_source, difference_minor=line.difference_minor, status=line.status, reason=line.reason)
            for line in lines
        ],
    )


# --- endpoints -------------------------------------------------------------------------------------
@router.post("/statement", response_model=MonthOut)
async def import_statement(
    file: UploadFile = File(...),
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> MonthOut:
    """Read a monthly statement CSV, keep its totals and per-order amounts, and
    answer with what was read beside the app's own calculation."""
    connection = await _shop(session, tenant, shop)
    data = await _read(file)
    try:
        parsed = statement.parse(data)
        month = await store_statement(session, connection, parsed)
    except statement.StatementError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="the file is not a text CSV; download the statement again as CSV") from None
    finally:
        del data  # the file itself is never kept
    await session.commit()
    return await month_report(session, connection, month)


@router.post("/ads", response_model=list[MonthOut])
async def import_ads(
    file: UploadFile = File(...),
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[MonthOut]:
    """Read an Etsy Ads report (daily totals for the shop), keep the days, and
    answer with each month it covers: the spend for its clicks beside what the
    statement charged."""
    connection = await _shop(session, tenant, shop)
    data = await _read(file)
    try:
        report = ads_report.parse(data)
        months = await store_ads(session, connection, report)
    except (ads_report.AdsReportError, statement.StatementError) as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="the file is not a text CSV; download the report again") from None
    finally:
        del data
    await session.commit()
    return [await month_report(session, connection, month) for month in months]


@router.get("/status", response_model=StatusOut)
async def import_status(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> StatusOut:
    """Every month's state for one shop: the last 13 months and anything imported."""
    connection = await selected_shop(session, tenant, shop)
    if connection is None:
        return StatusOut(shop_name=None, currency=None, months=[])
    statements = {r.month: r for r in (await session.execute(
        select(StatementImport).where(StatementImport.connection_id == connection.id)
    )).scalars()}
    ads_days: dict[date, int] = {}
    for (day,) in (await session.execute(select(AdsDaily.day).where(AdsDaily.connection_id == connection.id))).all():
        ads_days[_month(day)] = ads_days.get(_month(day), 0) + 1
    today = datetime.now(timezone.utc).date()
    months = set(statements) | set(ads_days)
    cursor = _month(today)
    for _ in range(13):
        months.add(cursor)
        cursor = _month(cursor - timedelta(days=1))
    out = []
    for month in sorted(months, reverse=True):
        row, days, total = statements.get(month), ads_days.get(month, 0), _month_days(month)
        if row is not None and days >= total:
            state = "complete"
        elif row is not None and days:
            state = "ads partial"
        elif row is not None:
            state = "statement only"
        elif days:
            state = "ads only"
        else:
            state = "nothing"
        out.append(MonthStatusOut(
            month=month, statement_imported_at=row.imported_at if row else None, statement_rows=row.rows if row else None,
            statement_net_minor=row.net_minor if row else None, statement_first_day=row.first_day if row else None,
            statement_last_day=row.last_day if row else None, ads_days=days, month_days=total, state=state,
        ))
    currency = next((r.currency for r in statements.values() if r.currency), None)
    return StatusOut(shop_name=shop_label(connection), currency=currency, months=out)


@router.get("/months/{month}", response_model=MonthOut)
async def import_month(
    month: str,
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> MonthOut:
    connection = await _shop(session, tenant, shop)
    return await month_report(session, connection, _parse_month(month))


@router.delete("/months/{month}", status_code=204)
async def delete_month(
    month: str,
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> None:
    """Remove what was imported for one month of one of the caller's shops."""
    connection = await _shop(session, tenant, shop)
    first = _parse_month(month)
    last = first.replace(day=_month_days(first))
    for table in (StatementImport, StatementOrder, StatementListingFee):
        await session.execute(delete(table).where(table.connection_id == connection.id, table.month == first))
    await session.execute(delete(AdCharge).where(AdCharge.connection_id == connection.id, AdCharge.month == first))
    await session.execute(delete(AdsDaily).where(AdsDaily.connection_id == connection.id, AdsDaily.day >= first, AdsDaily.day <= last))
    await session.commit()
