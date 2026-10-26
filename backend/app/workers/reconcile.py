"""Upkeep: the app's drafts checked against Etsy, at most once every 6 hours each (2026-10-26).

A seller can publish or delete the app's drafts in Shop Manager. Every hour this looks at
each connected shop's drafts (scheduled ones first) not read in the last
``reconcile.CHECK_EVERY``, reads their state on Etsy and makes our records follow
(``etsy/reconcile.py``): live means "published in Etsy by the seller", gone means "deleted on
Etsy"; their pending go-live and group-schedule slots close. Nothing is written to Etsy.

Cost: getListingsByListingIds, one request per 100 drafts; a draft it does not return is
read alone before it counts as deleted (at most :data:`SINGLES` per run, the rest next hour).
Upkeep (``gate.UPKEEP``): never counts against the seller's own ceiling.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import or_, select

from app.core import limits
from app.core.config import get_settings
from app.db.models import ConnectionStatus, EtsyConnection, ListingPublication, Tenant
from app.etsy import reconcile
from app.workers.profiles import _active_shop, _build_client, _connection_service, _enqueue_job, _run_gated, _shop_owner

logger = logging.getLogger(__name__)

#: Drafts one shop's run reads at most (getListingsByListingIds: one request per 100).
MAX_LISTINGS = 300
#: Drafts a run reads one by one at most (those the batch read did not return).
SINGLES = 50


def due_query(now: datetime, connection_id: uuid.UUID | None = None):  # noqa: ANN201
    """The app's drafts due a look: never read, or not in the last 6 hours."""
    query = select(ListingPublication).where(
        ListingPublication.state == "draft",
        or_(ListingPublication.etsy_checked_at.is_(None),
            ListingPublication.etsy_checked_at < now - reconcile.CHECK_EVERY),
    )
    if connection_id is not None:
        query = query.where(ListingPublication.connection_id == connection_id)
    return query


async def reconcile_listings(ctx: dict[str, Any], connection_id: str) -> str:
    """Upkeep: one shop's drafts, read on Etsy and followed in our records."""
    tenant_id = await _shop_owner(ctx, connection_id)
    if tenant_id is None:
        return "no-connection"
    return await _run_gated(ctx, "reconcile_listings", connection_id, tenant_id, lambda: _run(ctx, connection_id))


async def _run(ctx: dict[str, Any], connection_id: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, uuid.UUID(connection_id))
        if connection is None:
            return "no-connection"
        due = list((await session.execute(
            due_query(now, connection.id)
            .order_by(ListingPublication.scheduled_for.is_(None), ListingPublication.etsy_checked_at.is_not(None),
                      ListingPublication.etsy_checked_at, ListingPublication.created_at)
            .limit(MAX_LISTINGS)
        )).scalars())
        if not due:
            return "reconcile:0"
        tenant = await session.get(Tenant, connection.tenant_id)
        token = await _connection_service(settings).get_valid_access_token(session, connection)
        kw = {"access_token": token, "tenant_id": connection.tenant_id,
              "tenant_limit": limits.ceiling_limit(tenant) if tenant else None}
        async with httpx.AsyncClient(timeout=30.0) as http:
            client = _build_client(ctx, http, settings, shop=connection.id)
            reading = await reconcile.read_states(client, [p.etsy_listing_id for p in due], kw, singles=SINGLES)
        changes: dict[str, int] = {}
        for publication in due:
            state = reading.states.get(publication.etsy_listing_id)
            if state is None:
                continue  # not read this time: the next run tries again
            change = await reconcile.apply(session, publication, state, reading.listings.get(publication.etsy_listing_id),
                                           now=now)
            if change:
                changes[change] = changes.get(change, 0) + 1
        await session.commit()
    if changes:
        logger.info("reconcile: shop %s, %s, %d request(s)", connection_id, changes, reading.requests)
    return f"reconcile:{len(due)}:{reading.requests}:" + ",".join(f"{k}={v}" for k, v in sorted(changes.items()))


async def reconcile_all_shops(ctx: dict[str, Any]) -> int:
    """Cron (hourly): queue a check for every connected shop with a draft due one."""
    now = datetime.now(timezone.utc)
    async with ctx["sessionmaker"]() as session:
        shops = list((await session.execute(
            select(EtsyConnection.id)
            .where(EtsyConnection.status == ConnectionStatus.active)
            .where(EtsyConnection.id.in_(due_query(now).with_only_columns(ListingPublication.connection_id)))
        )).scalars())
    hour = now.strftime("%Y-%m-%dT%H")
    for cid in shops:
        await _enqueue_job(ctx, "reconcile_listings", str(cid), _job_id=f"reconcile:{cid}:{hour}")
    return len(shops)
