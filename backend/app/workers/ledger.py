"""Reading the shop's payment account ledger into daily totals per type (v7 §C).

Upkeep jobs, like the sales read beside them (workers/sales.py), and built the
same way: the cost is shown first (one request returns how many entries the
window holds), the first read runs in the background, 100 entries a request,
each page's totals and position committed together so it resumes exactly, at
most :attr:`LedgerSync.DAILY_REQUESTS` a day; after that only entries created
since the last read. The window is fixed when the read starts, so paging by
offset is stable while it runs.

The first read covers :attr:`LedgerSync.FIRST_DAYS` days, which is what the
7/30/90-day figures need; each later read adds the new days, up to the 13
months kept. A shop's ledger has several entries per order (the payment, its
fees, a listing renewal), so reading it costs more than reading its sales.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConnectionStatus, EtsyConnection, LedgerDaily, LedgerSync
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
        start = end - LedgerSync.FIRST_DAYS * 86400
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


def begin(sync: LedgerSync) -> None:
    """The seller started the read (with the sales read): from the estimated window."""
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
    day = _now().date().isoformat()
    for cid in due:
        await _enqueue_job(ctx, "sync_ledger", str(cid), _job_id=f"ledger:{cid}:{day}")
    return len(due)
