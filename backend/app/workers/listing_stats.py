"""Each app-published listing's views and favourites on Etsy, once a day (Part D).

Etsy's listing data carries each listing's **lifetime** ``views`` and
``num_favorers``. Once a day, for every listing the app published in a shop,
the totals are read and the day's increase is stored (``listing_stat_daily``).

Cost: a listing the shop sync already read today (``shop_listing_cache``,
fetched this UTC day) costs nothing; the rest are read with
getListingsByListingIds, **one request per 100 listings** per shop per day.
The job is upkeep (``gate.UPKEEP``): it never counts against the seller's own
ceiling. A day the job does not run is not lost: the next reading's increase
covers both days and is stored on the day it was read.

What this is not: listing page views on Etsy, not search impressions. Etsy's
API has no impressions, search terms or traffic sources (docs/analytics.md).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.core import limits
from app.core.config import get_settings
from app.db.models import (
    ConnectionStatus,
    EtsyConnection,
    ListingPublication,
    ListingStatDaily,
    ShopListingCache,
    Tenant,
)
from app.workers.profiles import (
    _active_shop,
    _build_client,
    _connection_service,
    _enqueue_job,
    _run_gated,
    _shop_owner,
)

logger = logging.getLogger(__name__)

#: Listings per getListingsByListingIds request (Etsy's maximum).
BATCH = 100
#: A listing first read within this long of going live: its lifetime total is
#: the increase since it went live, so it counts. Older ones start the next day.
FIRST_DAY_WINDOW = timedelta(hours=48)
#: The most listings one shop's daily read covers (JOB_COST = this / BATCH).
MAX_LISTINGS = 1000


def _aware(at: datetime | None) -> datetime | None:
    return at.replace(tzinfo=timezone.utc) if at is not None and at.tzinfo is None else at


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def increase(total: int | None, before: int | None, *, new: bool) -> int | None:
    """The day's increase: today's lifetime total less the last one; for a listing
    that went live within :data:`FIRST_DAY_WINDOW` and has no earlier total, the
    total itself; otherwise unknown (None)."""
    if total is None:
        return None
    if before is not None:
        return max(0, total - before)
    return total if new else None


async def published_listings(session: Any, connection_id: uuid.UUID) -> dict[int, datetime | None]:
    """The shop's listings the app published (live at some point), with when."""
    rows = await session.execute(
        select(ListingPublication.etsy_listing_id, ListingPublication.published_at).where(
            ListingPublication.connection_id == connection_id,
            ListingPublication.published_at.is_not(None),
            ListingPublication.state != "deleted_on_etsy",
        )
    )
    return {int(lid): _aware(at) for lid, at in rows.all()}


async def record(
    session: Any, connection: EtsyConnection, day: date, readings: dict[int, tuple[int | None, int | None]],
    published: dict[int, datetime | None], now: datetime,
) -> int:
    """Store one day's readings ``{listing: (views, favourites)}``; returns rows written."""
    written = 0
    for lid, (views, favs) in readings.items():
        if await session.get(ListingStatDaily, (connection.id, lid, day)) is not None:
            continue
        before = (await session.execute(
            select(ListingStatDaily)
            .where(ListingStatDaily.connection_id == connection.id, ListingStatDaily.listing_id == lid,
                   ListingStatDaily.day < day)
            .order_by(ListingStatDaily.day.desc())
            .limit(1)
        )).scalar_one_or_none()
        went_live = published.get(lid)
        new = before is None and went_live is not None and now - went_live <= FIRST_DAY_WINDOW
        session.add(ListingStatDaily(
            connection_id=connection.id, listing_id=lid, day=day, tenant_id=connection.tenant_id,
            views_total=views, favorites_total=favs,
            views=increase(views, before.views_total if before else None, new=new),
            favorites=increase(favs, before.favorites_total if before else None, new=new),
        ))
        written += 1
    return written


def _reading(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    return _int(payload.get("views")), _int(payload.get("num_favorers"))


async def read_listing_stats(ctx: dict[str, Any], connection_id: str) -> str:
    """Upkeep: today's views and favourites for one shop's app-published listings."""
    tenant_id = await _shop_owner(ctx, connection_id)
    if tenant_id is None:
        return "no-connection"
    return await _run_gated(
        ctx, "read_listing_stats", connection_id, tenant_id, lambda: _read(ctx, connection_id)
    )


async def _read(ctx: dict[str, Any], connection_id: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    day = now.date()
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, uuid.UUID(connection_id))
        if connection is None:
            return "no-connection"
        published = await published_listings(session, connection.id)
        done = set((await session.execute(
            select(ListingStatDaily.listing_id).where(
                ListingStatDaily.connection_id == connection.id, ListingStatDaily.day == day)
        )).scalars())
        wanted = sorted(set(published) - done)[:MAX_LISTINGS]
        if not wanted:
            return "stats:0"
        readings: dict[int, tuple[int | None, int | None]] = {}
        # Listings the shop sync already read today: no request.
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        for row in (await session.execute(
            select(ShopListingCache).where(
                ShopListingCache.connection_id == connection.id,
                ShopListingCache.listing_id.in_(wanted),
                ShopListingCache.fetched_at >= start,
            )
        )).scalars():
            if "views" in (row.payload or {}):
                readings[row.listing_id] = _reading(row.payload)
        rest = [lid for lid in wanted if lid not in readings]
        requests = 0
        if rest:
            tenant = await session.get(Tenant, connection.tenant_id)
            token = await _connection_service(settings).get_valid_access_token(session, connection)
            kw = {"access_token": token, "tenant_id": connection.tenant_id,
                  "tenant_limit": limits.ceiling_limit(tenant) if tenant else None}
            async with httpx.AsyncClient(timeout=30.0) as http:
                client = _build_client(ctx, http, settings, shop=connection.id)
                for i in range(0, len(rest), BATCH):
                    resp = await client.get_listings_by_listing_ids(rest[i : i + BATCH], **kw)
                    requests += 1
                    for item in resp.get("results") or []:
                        lid = _int(item.get("listing_id"))
                        if lid in published:
                            readings[lid] = _reading(item)
        written = await record(session, connection, day, readings, published, now)
        await session.commit()
    logger.info("listing stats: shop %s, %d listing(s), %d request(s)", connection_id, written, requests)
    return f"stats:{written}:{requests}"


async def read_all_listing_stats(ctx: dict[str, Any]) -> int:
    """Cron: queue today's read for every connected shop with an app-published listing."""
    async with ctx["sessionmaker"]() as session:
        shops = list((await session.execute(
            select(EtsyConnection.id)
            .where(EtsyConnection.status == ConnectionStatus.active)
            .where(EtsyConnection.id.in_(
                select(ListingPublication.connection_id).where(ListingPublication.published_at.is_not(None))
            ))
        )).scalars())
    day = datetime.now(timezone.utc).date().isoformat()
    for cid in shops:
        await _enqueue_job(ctx, "read_listing_stats", str(cid), _job_id=f"stats:{cid}:{day}")
    return len(shops)


def daily_requests(listings_per_shop: list[int]) -> int:
    """Requests a day for shops with these many app-published listings (at most)."""
    return sum(-(-min(n, MAX_LISTINGS) // BATCH) for n in listings_per_shop if n)
