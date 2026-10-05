"""Where the one-time sales re-read stands, shop by shop (workers/sales.py).

One calculation for the seller's own shops (Analytics) and for the admin's view
of all of them: status, what has been read, what it has cost and, from what
every shop in the queue still needs, the day each is expected to finish. Counts
and dates only; nothing from the sales themselves.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConnectionStatus, EtsyConnection, SalesDaily, SalesSync, Tenant
from app.workers import gate
from app.workers import sales as worker


class RereadShop(BaseModel):
    connection_id: uuid.UUID
    shop_name: str | None = None
    #: "queued": not begun; its figures and nightly updates carry on meanwhile.
    #: "reading" | "waiting" (for the next night or for its turn) | "done" | "failed".
    status: str
    read_count: int = 0
    #: Sales in the 13 months, from the shop's last estimate (so "about").
    window_count: int | None = None
    requests_used: int = 0
    requests_left: int | None = None
    #: The UTC day it is expected to finish: an estimate, from the whole queue.
    finishes_on: date | None = None
    finished_at: datetime | None = None
    note: str | None = None


class RereadReport(BaseModel):
    #: Any shop here still to finish.
    active: bool
    #: The read covers sales from this day on (13 months).
    reads_back_to: date
    per_shop_daily: int = SalesSync.DAILY_REQUESTS
    #: When the next night's share opens (00:00 UTC).
    resets_at: datetime
    requests_left: int = 0
    finishes_on: date | None = None
    shops: list[RereadShop] = []


class RereadAdminShop(RereadShop):
    email: str | None = None


class RereadAdminReport(RereadReport):
    """The same, for every account, with the nightly share. Admin only."""

    nightly_cap: int
    budget_percent: int = worker.REREAD_BUDGET_PERCENT
    used_tonight: int
    queued: int = 0
    reading: int = 0
    done: int = 0
    failed: int = 0
    shops: list[RereadAdminShop] = []  # type: ignore[assignment]


def _status(sync: SalesSync) -> str:
    if sync.reread == "done":
        return "done"
    if sync.reread is None:
        return "queued"
    if sync.state == "failed":
        return "failed"
    return "reading" if sync.state == "reading" else "waiting"


async def _rows(session: AsyncSession, tenant_id: uuid.UUID | None) -> tuple[list[tuple[SalesSync, EtsyConnection]], dict[uuid.UUID, int]]:
    today = datetime.now(timezone.utc).date()
    # The whole queue places each shop in it; only the caller's own shops are returned.
    queue = await worker.reread_queue(session)
    plan = worker.reread_plan(
        [(s.connection_id, worker.reread_remaining(s), worker.used_today(s, today)) for s in queue],
        cap=worker.reread_cap(),
        used_tonight=await worker.reread_used(session, today),
        per_shop=SalesSync.DAILY_REQUESTS,
    )
    query = (
        select(SalesSync, EtsyConnection)
        .join(EtsyConnection, EtsyConnection.id == SalesSync.connection_id)
        .where(EtsyConnection.status == ConnectionStatus.active)
        .order_by(EtsyConnection.position, EtsyConnection.connected_at)
    )
    if tenant_id is not None:
        query = query.where(SalesSync.tenant_id == tenant_id)
    rows = [
        (sync, connection)
        for sync, connection in (await session.execute(query)).all()
        if worker.SCOPE in (connection.scopes or [])
        and (sync.reread is not None or (sync.state == "complete" and not sync.has_lines))
    ]
    return rows, plan


def _shop(sync: SalesSync, connection: EtsyConnection, plan: dict[uuid.UUID, int], today: date) -> dict[str, object]:
    from app.api.shops import shop_label

    status = _status(sync)
    nights = plan.get(sync.connection_id)
    return {
        "connection_id": connection.id,
        "shop_name": shop_label(connection),
        "status": status,
        # Before its turn the counts are the earlier read's: not this read's progress.
        "read_count": sync.read_count if status != "queued" else 0,
        "window_count": sync.window_count,
        "requests_used": sync.requests_used if status != "queued" else 0,
        "requests_left": None if status in ("done", "failed") else worker.reread_remaining(sync),
        "finishes_on": today + timedelta(days=nights) if nights is not None else None,
        "finished_at": sync.finished_at if status == "done" else None,
        "note": sync.note if status in ("waiting", "failed") else None,
    }


def _totals(shops: list[dict[str, object]]) -> dict[str, object]:
    open_ = [s for s in shops if s["status"] in ("queued", "reading", "waiting")]
    days = [s["finishes_on"] for s in open_ if s["finishes_on"] is not None]
    return {
        "active": bool(open_),
        "requests_left": sum(int(s["requests_left"] or 0) for s in open_),
        "finishes_on": max(days) if days and len(days) == len(open_) else None,  # type: ignore[type-var]
    }


def _frame(today: date) -> dict[str, object]:
    return {
        "reads_back_to": today - timedelta(days=SalesDaily.RETENTION_DAYS),
        "resets_at": gate.next_reset(datetime.now(timezone.utc)),
    }


async def for_account(session: AsyncSession, tenant: Tenant) -> RereadReport:
    today = datetime.now(timezone.utc).date()
    rows, plan = await _rows(session, tenant.id)
    shops = [_shop(sync, connection, plan, today) for sync, connection in rows]
    return RereadReport(**_frame(today), **_totals(shops), shops=[RereadShop(**s) for s in shops])


async def for_admin(session: AsyncSession) -> RereadAdminReport:
    today = datetime.now(timezone.utc).date()
    rows, plan = await _rows(session, None)
    emails = dict((await session.execute(select(Tenant.id, Tenant.email))).all()) if rows else {}
    shops = [{**_shop(sync, connection, plan, today), "email": emails.get(sync.tenant_id)} for sync, connection in rows]
    count = {k: sum(1 for s in shops if s["status"] == k) for k in ("queued", "reading", "waiting", "done", "failed")}
    return RereadAdminReport(
        **_frame(today),
        **_totals(shops),
        nightly_cap=worker.reread_cap(),
        used_tonight=await worker.reread_used(session, today),
        queued=count["queued"],
        reading=count["reading"] + count["waiting"],
        done=count["done"],
        failed=count["failed"],
        shops=[RereadAdminShop(**s) for s in shops],
    )
