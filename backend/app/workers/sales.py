"""Reading the seller's own sales into daily totals (v7 §C1).

Upkeep job (app-wide budget, not the seller's limit), daily per shop that
granted ``transactions_r``, and on demand from the Analytics page. The first
run reads back 13 months; later runs re-read the last few days (late
payments and refunds settle there) and replace those days' totals. The raw
transaction pages exist only in memory inside this job: only the four fields
pipeline/sales.py reads become totals, and nothing about buyers is kept.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.db.models import ConnectionStatus, EtsyConnection, SalesDaily, Tenant
from app.pipeline.sales import aggregate, sale_day
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
#: Pages read at most in one run (first run: 13 months of a busy shop).
MAX_PAGES = 60
#: Days re-read on later runs.
RECENT_DAYS = 3


async def sync_sales(ctx: dict[str, Any], connection_id: str) -> str:
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, uuid.UUID(connection_id))
        if connection is None:
            return "no-connection"
        if SCOPE not in (connection.scopes or []):
            return "no-permission"  # connected before transactions_r: reconnect first
        tenant_id = connection.tenant_id
    return await _run_gated(ctx, "sync_sales", connection_id, tenant_id, lambda: _sync(ctx, connection_id))


async def _sync(ctx: dict[str, Any], connection_id: str) -> str:
    settings = get_settings()
    service = _connection_service(settings)
    today = datetime.now(timezone.utc).date()
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, uuid.UUID(connection_id))
        if connection is None:
            return "no-connection"
        has_history = await session.scalar(
            select(func.count()).select_from(SalesDaily).where(SalesDaily.connection_id == connection.id)
        )
        since = today - timedelta(days=RECENT_DAYS if has_history else SalesDaily.RETENTION_DAYS)
        tenant = await session.get(Tenant, connection.tenant_id)
        token = await _token(service, session, connection)
        transactions: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=30.0) as http:
            client = _build_client(ctx, http, settings, shop=connection.id)
            kw = {"access_token": token, "tenant_id": connection.tenant_id,
                  "tenant_limit": tenant.daily_quota if tenant else None}
            shop_id = await _resolve_shop_id(session, client, connection, kw)
            for page in range(MAX_PAGES):
                resp = await client.get_shop_transactions(shop_id, limit=PAGE_SIZE, offset=page * PAGE_SIZE, **kw)
                rows = resp.get("results") or []
                transactions.extend(rows)
                days = [d for d in (sale_day(r) for r in rows) if d is not None]
                # Newest first: once a whole page is older than the window, stop.
                if len(rows) < PAGE_SIZE or (days and max(days) < since):
                    break
            else:
                logger.warning("sales: shop %s has more than %d pages; totals start later", connection.id, MAX_PAGES)

        totals = aggregate(transactions, since)
        del transactions  # nothing raw outlives the aggregation
        await session.execute(
            delete(SalesDaily).where(SalesDaily.connection_id == connection.id, SalesDaily.day >= since)
        )
        for (listing_id, day), t in totals.items():
            session.add(
                SalesDaily(
                    connection_id=connection.id, listing_id=listing_id, day=day, tenant_id=connection.tenant_id,
                    units=t.units, orders=t.orders, revenue_minor=t.revenue_minor, currency=t.currency,
                )
            )
        await session.commit()
    return f"sales:{len(totals)}"


async def sync_all_sales(ctx: dict[str, Any]) -> int:
    """Cron: a sales read for every connected shop that granted the permission."""
    async with ctx["sessionmaker"]() as session:
        rows = await session.execute(
            select(EtsyConnection.id, EtsyConnection.scopes).where(EtsyConnection.status == ConnectionStatus.active)
        )
        shops = [cid for cid, scopes in rows.all() if SCOPE in (scopes or [])]
    day = datetime.now(timezone.utc).date().isoformat()
    for cid in shops:
        await _enqueue_job(ctx, "sync_sales", str(cid), _job_id=f"sales:{cid}:{day}")
    return len(shops)

