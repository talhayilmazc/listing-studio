"""Reading the shop's payment account ledger into daily totals per type (v7 §C).

Upkeep jobs, like the sales read beside them (workers/sales.py), and built the
same way: the cost is shown first (one request returns how many entries the
window holds), the first read runs in the background, 100 entries a request,
each page's totals and position committed together so it resumes exactly, at
most :attr:`LedgerSync.DAILY_REQUESTS` a day; after that only entries created
since the last read. The window is fixed when the read starts, so paging by
offset is stable while it runs.

The first read covers :attr:`LedgerSync.FIRST_DAYS` days, which is what the
7/30/90-day figures need; each later read adds the new days. A shop's ledger
has several entries per order (the payment, its fees, a listing renewal), so
reading it costs more than reading its sales.

The rest of the 13 months kept is filled in afterwards by :func:`backfill_ledger`,
so last year's season has real fees to compare with: backwards from the first
read's start, one :attr:`LedgerSync.SLICE_DAYS` slice at a time. It is the
lowest-priority Etsy work in the app: it starts only once the shop's sales and
its first ledger read are done, shares the shop's daily ledger cap (leaving
:attr:`LedgerSync.BACKFILL_RESERVE` for the nightly update), stands aside for
the day once the app has used :data:`BACKFILL_GLOBAL_PERCENT` of Etsy's daily
budget, and takes as many days as that needs. Until a period is reached its
fees stay estimates.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import ConnectionStatus, EtsyConnection, LedgerDaily, LedgerSync, SalesDaily, SalesSync
from app.etsy.errors import EtsyClientError, EtsyServerError
from app.pipeline.ledger import aggregate
from app.workers import gate
from app.workers.profiles import ShopAccessLost, _active_shop, _enqueue_job, _run_gated
from app.workers.sales import SCOPE, _open

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
CHUNK_PAGES = 20
UPDATE_PAGES = 20
LOCK_SECONDS = 15 * 60
#: The backfill waits for tomorrow once the app has used this much of Etsy's daily budget.
BACKFILL_GLOBAL_PERCENT = 50
#: The backfill starts a little after whatever queued it, so that work's lock is free.
BACKFILL_DELAY = 120
NIGHTLY_BACKFILL_DELAY = 20 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _midnight(stamp: int) -> int:
    """The UTC midnight at or before an epoch second."""
    return stamp - stamp % 86400


def pages_for(count: int) -> int:
    return max(1, math.ceil(count / PAGE_SIZE))


async def _claim(session: AsyncSession, connection_id: uuid.UUID) -> bool:
    now = _now()
    result = await session.execute(
        update(LedgerSync)
        .where(
            LedgerSync.connection_id == connection_id,
            (LedgerSync.lock_until.is_(None)) | (LedgerSync.lock_until < now),
        )
        .values(lock_until=now + timedelta(seconds=LOCK_SECONDS))
    )
    await session.commit()
    return bool(result.rowcount)


async def _release(session: AsyncSession, connection_id: uuid.UUID) -> None:
    await session.execute(update(LedgerSync).where(LedgerSync.connection_id == connection_id).values(lock_until=None))
    await session.commit()


def _roll_day(sync: LedgerSync) -> None:
    today = _now().date()
    if sync.requests_day != today:
        sync.requests_day = today
        sync.requests_today = 0


def _left_today(sync: LedgerSync) -> int:
    return max(0, LedgerSync.DAILY_REQUESTS - sync.requests_today)


class _Reader:
    def __init__(self, client: Any, shop_id: int, kw: dict[str, Any], sync: LedgerSync) -> None:
        self.client, self.shop_id, self.kw, self.sync = client, shop_id, kw, sync
        self.requests = 0
        self.count: int | None = None

    async def page(self, start: int, end: int, offset: int, limit: int = PAGE_SIZE) -> list[dict[str, Any]]:
        resp = await self.client.get_ledger_entries(
            self.shop_id, min_created=start, max_created=end, limit=limit, offset=offset, **self.kw
        )
        self.requests += 1
        self.sync.requests_used += 1
        self.sync.requests_today += 1
        if resp.get("count") is not None:
            self.count = int(resp["count"])
        return list(resp.get("results") or [])


async def _add(session: AsyncSession, connection: EtsyConnection, entries: list[dict[str, Any]]) -> None:
    for (day, kind), t in aggregate(entries).items():
        row = await session.get(LedgerDaily, (connection.id, day, kind))
        if row is None:
            session.add(LedgerDaily(
                connection_id=connection.id, day=day, ledger_type=kind, tenant_id=connection.tenant_id,
                amount_minor=t.amount_minor, entries=t.entries, currency=t.currency,
            ))
        else:
            row.amount_minor += t.amount_minor
            row.entries += t.entries
            row.currency = row.currency or t.currency


async def _gated(ctx: dict[str, Any], function: str, connection_id: str, body) -> str:  # noqa: ANN001
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, uuid.UUID(connection_id))
        if connection is None:
            return "no-connection"
        if SCOPE not in (connection.scopes or []):
            return "no-permission"
        tenant_id = connection.tenant_id
    return await _run_gated(ctx, function, connection_id, tenant_id, body)


# --- estimate ------------------------------------------------------------------------------


async def estimate_ledger(ctx: dict[str, Any], connection_id: str) -> str:
    return await _gated(ctx, "estimate_ledger", connection_id, lambda: _estimate(ctx, connection_id))


async def _estimate(ctx: dict[str, Any], connection_id: str) -> str:
    cid = uuid.UUID(connection_id)
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, cid)
        sync = await session.get(LedgerSync, cid)
        if connection is None or sync is None:
            return "no-connection"
        _roll_day(sync)
        end = int(_now().timestamp())
        start = _midnight(end - LedgerSync.FIRST_DAYS * 86400)  # whole days only
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                client, shop_id, kw = await _open(ctx, session, connection, http)
                reader = _Reader(client, shop_id, kw, sync)
                first = await reader.page(start, end, 0, limit=1)
                count = reader.count if reader.count is not None else len(first)
        except (ShopAccessLost, EtsyClientError) as exc:
            await _stopped(session, cid, _why(exc))
            return "failed"
        sync.window_start, sync.window_end, sync.total_count = start, end, count
        sync.state = "estimated"
        sync.note = None
        sync.updated_at = _now()
        await session.commit()
    return f"estimate:{count}"


# --- reading -------------------------------------------------------------------------------


async def sync_ledger(ctx: dict[str, Any], connection_id: str) -> str:
    async with ctx["sessionmaker"]() as session:
        sync = await session.get(LedgerSync, uuid.UUID(connection_id))
        if sync is None or sync.state not in ("reading", "waiting", "complete"):
            return "not-started"
    return await _gated(ctx, "sync_ledger", connection_id, lambda: _sync(ctx, connection_id))


async def _sync(ctx: dict[str, Any], connection_id: str) -> str:
    cid = uuid.UUID(connection_id)
    async with ctx["sessionmaker"]() as session:
        if not await _claim(session, cid):
            return "busy"
        try:
            connection = await _active_shop(session, cid)
            sync = await session.get(LedgerSync, cid)
            if connection is None or sync is None:
                return "no-connection"
            _roll_day(sync)
            if _left_today(sync) <= 0:
                await _defer(ctx, sync, connection_id)
                await session.commit()
                return "paced"
            try:
                async with httpx.AsyncClient(timeout=30.0) as http:
                    client, shop_id, kw = await _open(ctx, session, connection, http)
                    reader = _Reader(client, shop_id, kw, sync)
                    if sync.state == "complete":
                        return await _update(ctx, session, connection, sync, reader)
                    return await _first_read(ctx, session, connection, sync, reader)
            except (ShopAccessLost, EtsyClientError) as exc:
                await _stopped(session, cid, _why(exc))
                return "failed"
            except (EtsyServerError, httpx.HTTPError):
                await _stopped(session, cid, "Etsy didn't answer while reading your fees; it tries again within the hour.", keep=True)
                raise
        finally:
            await _release(session, cid)


async def _first_read(
    ctx: dict[str, Any], session: AsyncSession, connection: EtsyConnection, sync: LedgerSync, reader: _Reader
) -> str:
    assert sync.window_start is not None and sync.window_end is not None
    sync.state, sync.resumes_at, sync.note = "reading", None, None
    for _ in range(min(CHUNK_PAGES, _left_today(sync))):
        rows = await reader.page(sync.window_start, sync.window_end, sync.next_offset)
        await _add(session, connection, rows)
        sync.read_count += len(rows)
        sync.next_offset += len(rows)
        done = len(rows) < PAGE_SIZE
        del rows
        sync.updated_at = _now()
        await session.commit()  # the page's totals and the position, together
        if done:
            sync.state = "complete"
            sync.synced_until = sync.window_end
            sync.finished_at = _now()
            await session.commit()
            logger.info("ledger read complete: shop=%s entries=%d requests=%d", connection.id, sync.read_count, sync.requests_used)
            await _enqueue_job(ctx, "backfill_ledger", str(connection.id), _defer_by=BACKFILL_DELAY,
                               _job_id=f"ledger-backfill:{connection.id}:start")
            return f"complete:{sync.read_count}"
    if _left_today(sync) > 0:
        await _enqueue_job(ctx, "sync_ledger", str(connection.id), _job_id=f"ledger-read:{connection.id}:{sync.next_offset}")
    else:
        await _defer(ctx, sync, str(connection.id))
    await session.commit()
    return f"reading:{sync.read_count}"


async def _update(
    ctx: dict[str, Any], session: AsyncSession, connection: EtsyConnection, sync: LedgerSync, reader: _Reader
) -> str:
    """Entries created since the last read, in a window fixed when the update
    starts. The window's end and the position in it are kept with each page, so
    an update cut short (or longer than one run) carries on instead of
    counting its first pages twice."""
    start = (sync.synced_until or int(_now().timestamp())) + 1
    if sync.update_end is None:
        sync.update_end, sync.next_offset = int(_now().timestamp()), 0
    end = sync.update_end
    if end <= start:
        sync.update_end = None
        await session.commit()
        return "updated:0"
    added = 0
    finished = False
    for _ in range(min(UPDATE_PAGES, _left_today(sync))):
        rows = await reader.page(start, end, sync.next_offset)
        await _add(session, connection, rows)
        added += len(rows)
        sync.next_offset += len(rows)
        finished = len(rows) < PAGE_SIZE
        del rows
        sync.updated_at = _now()
        await session.commit()  # the page's totals and the position, together
        if finished:
            break
    if finished:
        sync.synced_until, sync.update_end, sync.next_offset = end, None, 0
    elif _left_today(sync) > 0:
        await _enqueue_job(ctx, "sync_ledger", str(connection.id), _job_id=f"ledger-update:{connection.id}:{end}:{sync.next_offset}")
    # else: tomorrow's nightly run carries on from the kept position.
    sync.updated_at = _now()
    await session.commit()
    return f"updated:{added}"


def _why(exc: Exception) -> str:
    if isinstance(exc, ShopAccessLost):
        return "The shop's sign-in couldn't be renewed. Reconnect it in Shops, then try again."
    status = getattr(exc, "status_code", "")
    return (
        f"Etsy refused to show this shop's payment ledger (it answered {status}). "
        "Reconnect the shop in Shops and try again; if it happens again, contact support."
    )


async def _stopped(session: AsyncSession, connection_id: uuid.UUID, why: str, *, keep: bool = False) -> None:
    await session.rollback()
    sync = await session.get(LedgerSync, connection_id)
    if sync is None:
        return
    if not keep:
        sync.state = "failed"
    sync.note = why
    sync.updated_at = _now()
    await session.commit()


async def _defer(ctx: dict[str, Any], sync: LedgerSync, connection_id: str) -> None:
    resumes = gate.next_reset(_now())
    if sync.state in ("reading", "waiting"):
        sync.state = "waiting"
    sync.resumes_at = resumes
    sync.note = "today's share of the budget for reading fees is used"
    delay = max(0.0, (resumes - _now()).total_seconds()) + gate.RESUME_SLACK_SECONDS
    await _enqueue_job(ctx, "sync_ledger", connection_id, _defer_by=delay,
                       _job_id=f"ledger-resume:{connection_id}:{resumes.date().isoformat()}")


async def begin(session: AsyncSession, sync: LedgerSync) -> None:
    """The seller started the read (with the sales read): from the estimated
    window, and from nothing. Totals from an earlier, unfinished read are
    removed first, or its pages would be counted twice; the history starts over
    with it."""
    await session.execute(delete(LedgerDaily).where(LedgerDaily.connection_id == sync.connection_id))
    sync.backfill_state, sync.backfill_target, sync.covered_from = "none", None, None
    sync.slice_start, sync.slice_offset, sync.backfill_requests, sync.backfill_note = None, 0, 0, None
    sync.state = "reading"
    sync.next_offset = 0
    sync.update_end = None
    sync.read_count = 0
    sync.synced_until = None
    sync.started_at = _now()
    sync.finished_at = None
    sync.resumes_at = None
    sync.note = None


def resume(sync: LedgerSync) -> None:
    if sync.synced_until is not None:
        sync.state = "complete"
    elif sync.started_at is not None:
        sync.state = "reading"
    else:
        sync.state = "estimated"
    sync.note = None


# --- the 13-month history, backwards ----------------------------------------------------------


def covered_from(sync: LedgerSync) -> int | None:
    """Everything from this epoch second on is in the daily totals."""
    if sync.synced_until is None or sync.window_start is None:
        return None
    return sync.covered_from if sync.covered_from is not None else sync.window_start


def backfill_target(now: datetime) -> int:
    """The 13-month edge: the midnight the sales read also reaches back to."""
    edge = now.date() - timedelta(days=SalesDaily.RETENTION_DAYS - 1)
    return int(datetime(edge.year, edge.month, edge.day, tzinfo=timezone.utc).timestamp())


def resume_backfill(sync: LedgerSync) -> None:
    """After a refusal (e.g. once the shop is reconnected): from the kept slice and offset."""
    sync.backfill_state = "reading"
    sync.backfill_note = None


async def backfill_ledger(ctx: dict[str, Any], connection_id: str) -> str:
    async with ctx["sessionmaker"]() as session:
        sync = await session.get(LedgerSync, uuid.UUID(connection_id))
        if sync is None or sync.state != "complete" or sync.backfill_state in ("complete", "failed"):
            return "not-due"
        sales = await session.get(SalesSync, sync.connection_id)
        if sales is None or sales.state != "complete":
            return "sales-first"  # the sales read finishing queues this again
    return await _gated(ctx, "backfill_ledger", connection_id, lambda: _backfill(ctx, connection_id))


async def _busy_app(ctx: dict[str, Any], tenant_id: uuid.UUID) -> bool:
    quota = ctx.get("quota")
    if quota is None:
        return False
    _, used = await quota.usage(tenant_id)
    return used >= get_settings().global_daily_limit * BACKFILL_GLOBAL_PERCENT // 100


async def _backfill(ctx: dict[str, Any], connection_id: str) -> str:
    cid = uuid.UUID(connection_id)
    async with ctx["sessionmaker"]() as session:
        if not await _claim(session, cid):
            return "busy"  # an update is running; the nightly run queues this again
        try:
            connection = await _active_shop(session, cid)
            sync = await session.get(LedgerSync, cid)
            if connection is None or sync is None or sync.state != "complete" or sync.update_end is not None:
                return "not-due"
            if sync.backfill_target is None:
                sync.backfill_target = backfill_target(_now())
                sync.covered_from = sync.window_start
                sync.slice_start, sync.slice_offset = None, 0
            sync.backfill_state, sync.backfill_note = "reading", None
            _roll_day(sync)
            budget = min(CHUNK_PAGES, _left_today(sync) - LedgerSync.BACKFILL_RESERVE)
            if budget <= 0:
                return await _backfill_waits(session, sync, "today's share of the budget for reading fees is used")
            if await _busy_app(ctx, connection.tenant_id):
                return await _backfill_waits(session, sync, "the app's shared Etsy budget is busy today; history waits for a quieter day")
            read = 0
            try:
                async with httpx.AsyncClient(timeout=30.0) as http:
                    client, shop_id, kw = await _open(ctx, session, connection, http)
                    reader = _Reader(client, shop_id, kw, sync)
                    for _ in range(budget):
                        assert sync.covered_from is not None and sync.backfill_target is not None
                        if sync.covered_from <= sync.backfill_target:
                            break
                        if sync.slice_start is None:  # the next slice back, on whole days
                            sync.slice_start = max(sync.backfill_target,
                                                   _midnight(sync.covered_from - 1) - (LedgerSync.SLICE_DAYS - 1) * 86400)
                            sync.slice_offset = 0
                        rows = await reader.page(sync.slice_start, sync.covered_from - 1, sync.slice_offset)
                        await _add(session, connection, rows)
                        read += len(rows)
                        sync.slice_offset += len(rows)
                        sync.backfill_requests += 1
                        if len(rows) < PAGE_SIZE:  # the slice is done: the totals now reach back to its start
                            sync.covered_from, sync.slice_start, sync.slice_offset = sync.slice_start, None, 0
                        del rows
                        sync.updated_at = _now()
                        await session.commit()  # the page's totals and the position, together
            except (ShopAccessLost, EtsyClientError) as exc:
                await session.rollback()
                sync = await session.get(LedgerSync, cid)
                sync.backfill_state, sync.backfill_note = "failed", _why(exc)
                await session.commit()
                return "failed"
            except (EtsyServerError, httpx.HTTPError):
                await session.rollback()
                sync = await session.get(LedgerSync, cid)
                return await _backfill_waits(session, sync, "Etsy didn't answer; the history carries on tomorrow")
            if sync.covered_from <= sync.backfill_target:
                sync.backfill_state, sync.backfill_note = "complete", None
                await session.commit()
                logger.info("ledger history complete: shop=%s requests=%d", connection.id, sync.backfill_requests)
                return f"complete:{read}"
            await session.commit()
            await _enqueue_job(ctx, "backfill_ledger", connection_id,
                               _job_id=f"ledger-backfill:{connection_id}:{sync.covered_from}:{sync.slice_offset}")
            return f"reading:{read}"
        finally:
            await _release(session, cid)


async def _backfill_waits(session: AsyncSession, sync: LedgerSync, why: str) -> str:
    """Nothing more today; the nightly run queues it again."""
    sync.backfill_state, sync.backfill_note = "waiting", why
    sync.updated_at = _now()
    await session.commit()
    return "paced"


async def due_updates(session: AsyncSession) -> list[uuid.UUID]:
    """Shops whose ledger read is done (for the nightly update) or stalled."""
    now = _now()
    rows = await session.execute(
        select(LedgerSync.connection_id, LedgerSync.state, LedgerSync.updated_at, EtsyConnection.scopes)
        .join(EtsyConnection, EtsyConnection.id == LedgerSync.connection_id)
        .where(EtsyConnection.status == ConnectionStatus.active)
    )
    due = []
    for cid, state, updated, scopes in rows.all():
        if SCOPE not in (scopes or []):
            continue
        updated = updated if updated.tzinfo else updated.replace(tzinfo=timezone.utc)
        if state == "complete" or (state in ("reading", "waiting") and now - updated > timedelta(hours=1)):
            due.append(cid)
    return due


async def sync_all_ledgers(ctx: dict[str, Any]) -> int:
    """Cron: bring every read ledger up to date, and carry on reads that stalled."""
    async with ctx["sessionmaker"]() as session:
        due = await due_updates(session)
        unfinished = (await session.execute(
            select(LedgerSync.connection_id).where(
                LedgerSync.state == "complete", LedgerSync.backfill_state.in_(("none", "reading", "waiting")))
        )).scalars().all()
    day = _now().date().isoformat()
    for cid in due:
        await _enqueue_job(ctx, "sync_ledger", str(cid), _job_id=f"ledger:{cid}:{day}")
    # History last: after every shop's update has had its turn.
    for cid in unfinished:
        if cid in due:
            await _enqueue_job(ctx, "backfill_ledger", str(cid), _defer_by=NIGHTLY_BACKFILL_DELAY,
                               _job_id=f"ledger-backfill:{cid}:{day}")
    return len(due)
