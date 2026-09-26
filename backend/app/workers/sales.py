"""Reading the seller's own sales into daily totals (v7 §C1).

Upkeep jobs (Etsy's app-wide budget, not the seller's own limit). Etsy's
transactions endpoint takes only ``limit`` (at most 100) and ``offset``: no
date filter, no sort. So a read costs one request per 100 sales, whatever the
number of listings, and everything here is built around that:

* **Estimate first** (``estimate_sales``). One request gives the shop's total
  sale count and Etsy's order; a binary search over offsets (one request per
  step, ~log2 of the count) finds how many sales fall in the 13 months kept.
  The seller sees the cost before starting.
* **First read in the background** (``sync_sales`` while ``reading``). Chunks
  of :data:`CHUNK_PAGES` pages; each page's totals are added to ``sales_daily``
  and the position saved in the same transaction, so partial data is useful at
  once and an interrupted read resumes where it stopped. A sale already
  counted is skipped by its (created time, transaction id), which also covers
  new sales shifting the pages during a long read. At
  :attr:`SalesSync.DAILY_REQUESTS` a day it stops and carries on after the
  reset, so a big shop doesn't crowd out everyone else's work.
* **Then only what's new** (``sync_sales`` once ``complete``): sales newer than
  the newest one counted; usually a single request.

The raw pages exist only in memory inside the job: only the fields
pipeline/sales.py reads become totals, and nothing about buyers is kept.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import ConnectionStatus, EtsyConnection, SalesDaily, SalesSync, Tenant
from app.pipeline.sales import aggregate, sale_day, sale_key
from app.workers import gate
from app.workers.profiles import (
    _active_shop,
    _build_client,
    _connection_service,
    _enqueue_job,
    _resolve_shop_id,
    _run_gated,
    _token,
)

logger = logging.getLogger(__name__)

SCOPE = "transactions_r"
PAGE_SIZE = 100
#: Pages one job run reads before handing on to the next run.
CHUNK_PAGES = 20
#: Pages one catch-up read takes at most (a shop not read for a long time).
UPDATE_PAGES = 20
#: A resumed read steps back this far, in case cancelled sales moved the pages up.
RESUME_OVERLAP = 10
LOCK_SECONDS = 15 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def window_start(today: date) -> date:
    return today - timedelta(days=SalesDaily.RETENTION_DAYS)


def days_needed(pages: int, used_today: int = 0) -> int:
    """UTC days a read of ``pages`` requests takes at the daily pace."""
    if pages <= 0:
        return 0
    first = max(0, SalesSync.DAILY_REQUESTS - used_today)
    if pages <= first:
        return 1
    return 1 + math.ceil((pages - first) / SalesSync.DAILY_REQUESTS)


# --- the row, its lock and its daily pace ---------------------------------------------


async def _claim(session: AsyncSession, connection_id: uuid.UUID) -> bool:
    """Take the shop's read for this run; False if another run holds it."""
    now = _now()
    result = await session.execute(
        update(SalesSync)
        .where(
            SalesSync.connection_id == connection_id,
            (SalesSync.lock_until.is_(None)) | (SalesSync.lock_until < now),
        )
        .values(lock_until=now + timedelta(seconds=LOCK_SECONDS))
    )
    await session.commit()
    return bool(result.rowcount)


async def _release(session: AsyncSession, connection_id: uuid.UUID) -> None:
    await session.execute(update(SalesSync).where(SalesSync.connection_id == connection_id).values(lock_until=None))
    await session.commit()


def _roll_day(sync: SalesSync, today: date) -> None:
    if sync.requests_day != today:
        sync.requests_day = today
        sync.requests_today = 0


def _left_today(sync: SalesSync) -> int:
    return max(0, SalesSync.DAILY_REQUESTS - sync.requests_today)


class _Reader:
    """This run's requests, each counted against the read and today's pace."""

    def __init__(self, client: Any, shop_id: int, kw: dict[str, Any], sync: SalesSync) -> None:
        self.client, self.shop_id, self.kw, self.sync = client, shop_id, kw, sync
        self.requests = 0
        self.count: int | None = None

    async def page(self, offset: int, limit: int = PAGE_SIZE) -> list[dict[str, Any]]:
        resp = await self.client.get_shop_transactions(self.shop_id, limit=limit, offset=offset, **self.kw)
        self.requests += 1
        self.sync.requests_used += 1
        self.sync.requests_today += 1
        if resp.get("count") is not None:
            self.count = int(resp["count"])
        return list(resp.get("results") or [])


async def _open(ctx: dict[str, Any], session: AsyncSession, connection: EtsyConnection, http: httpx.AsyncClient):  # noqa: ANN202
    settings = get_settings()
    tenant = await session.get(Tenant, connection.tenant_id)
    token = await _token(_connection_service(settings), session, connection)
    client = _build_client(ctx, http, settings, shop=connection.id)
    kw = {"access_token": token, "tenant_id": connection.tenant_id,
          "tenant_limit": tenant.daily_quota if tenant else None}
    shop_id = await _resolve_shop_id(session, client, connection, kw)
    return client, shop_id, kw


async def _add_totals(session: AsyncSession, connection: EtsyConnection, sales: list[dict[str, Any]], since: date) -> int:
    """Add these sales to the daily totals (additive: pages of one day may come apart)."""
    totals = aggregate(sales, since)
    for (listing_id, day), t in totals.items():
        row = await session.get(SalesDaily, (connection.id, listing_id, day))
        if row is None:
            session.add(SalesDaily(
                connection_id=connection.id, listing_id=listing_id, day=day, tenant_id=connection.tenant_id,
                units=t.units, orders=t.orders, revenue_minor=t.revenue_minor, currency=t.currency,
            ))
        else:
            row.units += t.units
            row.orders += t.orders
            row.revenue_minor += t.revenue_minor
            row.currency = row.currency or t.currency
    return sum(t.orders for t in totals.values())


def _direction(rows: list[dict[str, Any]]) -> str:
    if len(rows) >= 2 and sale_key(rows[0]) < sale_key(rows[-1]):
        return "asc"
    return "desc"


def _before(a: tuple[int, int], b: tuple[int | None, int | None]) -> bool:
    return b[0] is None or a < (b[0], b[1] or 0)


def _after(a: tuple[int, int], b: tuple[int | None, int | None]) -> bool:
    return b[0] is None or a > (b[0], b[1] or 0)


def _keep_cursors(sync: SalesSync, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    keys = [sale_key(r) for r in rows]
    lo, hi = min(keys), max(keys)
    if _before(lo, (sync.oldest_ts, sync.oldest_id)):
        sync.oldest_ts, sync.oldest_id = lo
    if _after(hi, (sync.newest_ts, sync.newest_id)):
        sync.newest_ts, sync.newest_id = hi


async def _defer(ctx: dict[str, Any], sync: SalesSync, connection_id: str, why: str) -> None:
    """Stop for today; carry on after the reset."""
    resumes = gate.next_reset(_now())
    sync.state = "waiting" if sync.state in ("reading", "waiting") else sync.state
    sync.resumes_at = resumes
    sync.note = why
    delay = max(0.0, (resumes - _now()).total_seconds()) + gate.RESUME_SLACK_SECONDS
    await _enqueue_job(ctx, "sync_sales", connection_id, _defer_by=delay,
                       _job_id=f"sales-resume:{connection_id}:{resumes.date().isoformat()}")


# --- estimate ------------------------------------------------------------------------------


async def estimate_sales(ctx: dict[str, Any], connection_id: str) -> str:
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, uuid.UUID(connection_id))
        if connection is None:
            return "no-connection"
        if SCOPE not in (connection.scopes or []):
            return "no-permission"
        tenant_id = connection.tenant_id
    return await _run_gated(ctx, "estimate_sales", connection_id, tenant_id, lambda: _estimate(ctx, connection_id))


async def _estimate(ctx: dict[str, Any], connection_id: str) -> str:
    today = _now().date()
    start = window_start(today)
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, uuid.UUID(connection_id))
        sync = await session.get(SalesSync, uuid.UUID(connection_id))
        if connection is None or sync is None:
            return "no-connection"
        _roll_day(sync, today)
        async with httpx.AsyncClient(timeout=30.0) as http:
            client, shop_id, kw = await _open(ctx, session, connection, http)
            reader = _Reader(client, shop_id, kw, sync)
            first = await reader.page(0)
            count = reader.count if reader.count is not None else len(first)
            direction = _direction(first)
            del first

            async def day_at(offset: int) -> date | None:
                rows = await reader.page(offset, limit=1)
                return sale_day(rows[0]) if rows else None

            # Binary search for the 13-month boundary: newest first, the first
            # offset older than it; oldest first, the first offset inside it.
            lo, hi = 0, count
            while lo < hi:
                mid = (lo + hi) // 2
                d = await day_at(mid)
                older = d is None or d < start
                if (direction == "desc") == older:
                    hi = mid
                else:
                    lo = mid + 1
        in_window = lo if direction == "desc" else count - lo
        sync.direction = direction
        sync.total_count = count
        sync.window_count = in_window
        sync.window_start = start
        sync.start_offset = 0 if direction == "desc" else lo
        # One request per 100 sales, and for newest-first one more to see the edge.
        pages = math.ceil(in_window / PAGE_SIZE)
        if direction == "desc" and in_window < count and in_window % PAGE_SIZE == 0:
            pages += 1
        sync.pages_estimate = max(1, pages)
        sync.state = "estimated"
        sync.note = None
        sync.updated_at = _now()
        await session.commit()
        logger.info("sales estimate: shop=%s sales=%d in_window=%d pages=%d probes=%d",
                    connection.id, count, in_window, sync.pages_estimate, reader.requests)
    return f"estimate:{in_window}"


# --- reading -------------------------------------------------------------------------------


async def sync_sales(ctx: dict[str, Any], connection_id: str) -> str:
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, uuid.UUID(connection_id))
        if connection is None:
            return "no-connection"
        if SCOPE not in (connection.scopes or []):
            return "no-permission"  # connected before transactions_r: reconnect first
        sync = await session.get(SalesSync, connection.id)
        if sync is None or sync.state not in ("reading", "waiting", "complete"):
            return "not-started"  # the first read starts when the seller has seen its cost
        tenant_id = connection.tenant_id
    return await _run_gated(ctx, "sync_sales", connection_id, tenant_id, lambda: _sync(ctx, connection_id))


async def _sync(ctx: dict[str, Any], connection_id: str) -> str:
    cid = uuid.UUID(connection_id)
    async with ctx["sessionmaker"]() as session:
        if not await _claim(session, cid):
            return "busy"
        try:
            connection = await _active_shop(session, cid)
            sync = await session.get(SalesSync, cid)
            if connection is None or sync is None:
                return "no-connection"
            _roll_day(sync, _now().date())
            if _left_today(sync) <= 0:
                await _defer(ctx, sync, connection_id, "today's share of the budget for reading sales is used")
                await session.commit()
                return "paced"
            async with httpx.AsyncClient(timeout=30.0) as http:
                client, shop_id, kw = await _open(ctx, session, connection, http)
                reader = _Reader(client, shop_id, kw, sync)
                if sync.state == "complete":
                    return await _update(ctx, session, connection, sync, reader)
                return await _first_read(ctx, session, connection, sync, reader)
        finally:
            await _release(session, cid)


async def _first_read(
    ctx: dict[str, Any], session: AsyncSession, connection: EtsyConnection, sync: SalesSync, reader: _Reader
) -> str:
    start = sync.window_start or window_start(_now().date())
    sync.state = "reading"
    sync.resumes_at = None
    sync.note = None
    if sync.oldest_ts is not None or sync.newest_ts is not None:
        # A resumed read: step back a little in case sales were cancelled meanwhile
        # (which moves the rest up); the cursors skip whatever was counted.
        sync.next_offset = max(sync.start_offset, sync.next_offset - RESUME_OVERLAP)
    pages = min(CHUNK_PAGES, _left_today(sync))
    for _ in range(pages):
        offset = sync.next_offset
        rows = await reader.page(offset)
        if sync.direction is None:
            sync.direction = _direction(rows)
        if sync.direction == "desc":
            fresh = [r for r in rows if _before(sale_key(r), (sync.oldest_ts, sync.oldest_id))]
        else:
            fresh = [r for r in rows if _after(sale_key(r), (sync.newest_ts, sync.newest_id))]
        inside = [r for r in fresh if (sale_day(r) or start) >= start]
        sync.read_count += await _add_totals(session, connection, inside, start)
        _keep_cursors(sync, fresh)
        sync.next_offset = offset + len(rows)
        past_window = sync.direction == "desc" and len(inside) < len(fresh)
        done = len(rows) < PAGE_SIZE or past_window
        del rows, fresh, inside
        sync.updated_at = _now()
        await session.commit()  # this page's totals and the position, together
        if done:
            sync.state = "complete"
            sync.finished_at = _now()
            # Sales made during a long read sit above where it began; the first
            # update picks them up (they are newer than the newest counted).
            connection.sales_synced_at = sync.finished_at
            await session.commit()
            logger.info("sales read complete: shop=%s sales=%d requests=%d",
                        connection.id, sync.read_count, sync.requests_used)
            return f"complete:{sync.read_count}"

    if _left_today(sync) > 0:
        await _enqueue_job(ctx, "sync_sales", str(connection.id), _job_id=f"sales-read:{connection.id}:{sync.next_offset}")
    else:
        await _defer(ctx, sync, str(connection.id), "today's share of the budget for reading sales is used")
    await session.commit()
    return f"reading:{sync.read_count}"


async def _update(
    ctx: dict[str, Any], session: AsyncSession, connection: EtsyConnection, sync: SalesSync, reader: _Reader
) -> str:
    """Only the sales newer than the newest one counted."""
    start = window_start(_now().date())
    added = 0
    if sync.direction == "asc":
        # Oldest first: new sales are at the end.
        offset = max(0, (sync.total_count or 0) - RESUME_OVERLAP)
    else:
        offset = 0
    finished = False
    for _ in range(min(UPDATE_PAGES, _left_today(sync))):
        rows = await reader.page(offset)
        new = [r for r in rows if _after(sale_key(r), (sync.newest_ts, sync.newest_id))]
        added += await _add_totals(session, connection, [r for r in new if (sale_day(r) or start) >= start], start)
        _keep_cursors(sync, new)
        offset += len(rows)
        if sync.direction == "asc":
            finished = len(rows) < PAGE_SIZE
        else:
            finished = len(new) < len(rows) or len(rows) < PAGE_SIZE
        del rows, new
        await session.commit()
        if finished:
            break
    if reader.count is not None:
        sync.total_count = reader.count
    sync.last_update_requests = reader.requests
    sync.updated_at = _now()
    if finished:
        connection.sales_synced_at = _now()
    else:
        # Far behind (not read for a long while): carry on in the next run.
        await _enqueue_job(ctx, "sync_sales", str(connection.id), _job_id=f"sales-update:{connection.id}:{offset}")
    await session.commit()
    return f"updated:{added}"


# --- starting, and the nightly round ----------------------------------------------------------


async def begin_first_read(session: AsyncSession, sync: SalesSync) -> None:
    """The seller saw the estimate and started: reset the read and the shop's totals."""
    await session.execute(delete(SalesDaily).where(SalesDaily.connection_id == sync.connection_id))
    sync.state = "reading"
    sync.next_offset = sync.start_offset
    sync.read_count = 0
    sync.oldest_ts = sync.oldest_id = sync.newest_ts = sync.newest_id = None
    sync.started_at = _now()
    sync.finished_at = None
    sync.resumes_at = None
    sync.note = None


async def sync_all_sales(ctx: dict[str, Any]) -> int:
    """Cron: bring every read shop up to date, and carry on reads that stalled."""
    now = _now()
    async with ctx["sessionmaker"]() as session:
        rows = await session.execute(
            select(SalesSync.connection_id, SalesSync.state, SalesSync.updated_at, EtsyConnection.scopes)
            .join(EtsyConnection, EtsyConnection.id == SalesSync.connection_id)
            .where(EtsyConnection.status == ConnectionStatus.active)
        )
        due = []
        for cid, state, updated, scopes in rows.all():
            if SCOPE not in (scopes or []):
                continue
            updated = updated if updated.tzinfo else updated.replace(tzinfo=timezone.utc)
            if state == "complete" or (state in ("reading", "waiting") and now - updated > timedelta(hours=1)):
                due.append(cid)
    day = now.date().isoformat()
    for cid in due:
        await _enqueue_job(ctx, "sync_sales", str(cid), _job_id=f"sales:{cid}:{day}")
    return len(due)
