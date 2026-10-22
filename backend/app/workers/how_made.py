"""Watching the drafts a seller opened from "Finish in Etsy" (pipeline/how_made.py).

Ticking How it's made on a draft in Etsy's editor ends with Publish, so a draft opened from
"Finish in Etsy" that is now live on Etsy was most likely finished there. This job reads the
state of a shop's opened drafts (getListingsByListingIds: up to 100 listings in one request,
counted in the app's budget as upkeep, never the seller's ceiling) and records the ones that
went live (``listing_publication.state`` "active"); the page then asks "Mark done?". It never
marks anything done: the app cannot read the answers. A listing Etsy no longer has is left to
the existing deleted-on-Etsy handling.

``watch_opened_drafts(shop)`` is queued by the page (at most once a minute per shop) and by the
``watch_all_opened_drafts`` cron every ten minutes, for drafts opened in the last three days.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.api.finish_in_etsy import WATCH_FOR
from app.core.config import get_settings
from app.db.models import ConnectionStatus, EtsyConnection, ListingPublication
from app.workers.profiles import _active_shop, _build_client, _connection_service, _run_gated, _shop_owner

logger = logging.getLogger(__name__)


async def _opened(session, connection_id: uuid.UUID) -> list[ListingPublication]:  # noqa: ANN001
    since = datetime.now(timezone.utc) - WATCH_FOR
    return list((await session.execute(select(ListingPublication).where(
        ListingPublication.connection_id == connection_id, ListingPublication.state == "draft",
        ListingPublication.how_made_opened_at.is_not(None), ListingPublication.how_made_opened_at >= since,
    ))).scalars())


async def watch_opened_drafts(ctx: dict[str, Any], connection_id: str) -> str:
    tenant_id = await _shop_owner(ctx, connection_id)
    if tenant_id is None:
        return "no-connection"
    return await _run_gated(ctx, "watch_opened_drafts", connection_id, tenant_id, lambda: _watch(ctx, connection_id))


async def _watch(ctx: dict[str, Any], connection_id: str) -> str:
    settings = get_settings()
    async with ctx["sessionmaker"]() as session:
        connection = await _active_shop(session, uuid.UUID(connection_id))
        if connection is None:
            return "no-connection"
        drafts = await _opened(session, connection.id)
        if not drafts:
            return "nothing-opened"
        token = await _connection_service(settings).get_valid_access_token(session, connection)
        kw = {"access_token": token, "tenant_id": connection.tenant_id}
        live: set[int] = set()
        async with httpx.AsyncClient(timeout=30.0) as http:
            client = _build_client(ctx, http, settings, shop=connection.id)
            ids = [d.etsy_listing_id for d in drafts]
            for start in range(0, len(ids), 100):
                resp = await client.get_listings_by_listing_ids(ids[start:start + 100], **kw)
                live |= {int(r["listing_id"]) for r in resp.get("results") or []
                         if isinstance(r, dict) and r.get("state") == "active" and r.get("listing_id") is not None}
        now = datetime.now(timezone.utc)
        for draft in drafts:
            if draft.etsy_listing_id in live:
                # Published in Etsy's editor after being opened from "Finish in Etsy": live now.
                draft.state = "active"
                draft.published_at = draft.published_at or now
        await session.commit()
        return f"live:{len(live)} of {len(drafts)}"


async def watch_all_opened_drafts(ctx: dict[str, Any]) -> int:
    """arq cron: each shop with drafts opened from "Finish in Etsy" in the last three days."""
    since = datetime.now(timezone.utc) - WATCH_FOR
    async with ctx["sessionmaker"]() as session:
        shops = set((await session.execute(
            select(ListingPublication.connection_id)
            .join(EtsyConnection, EtsyConnection.id == ListingPublication.connection_id)
            .where(ListingPublication.state == "draft", ListingPublication.how_made_opened_at.is_not(None),
                   ListingPublication.how_made_opened_at >= since, EtsyConnection.status == ConnectionStatus.active)
        )).scalars())
    for shop in shops:
        enqueue = ctx.get("enqueue")
        if enqueue is not None:
            await enqueue("watch_opened_drafts", str(shop))
        else:
            await ctx["redis"].enqueue_job("watch_opened_drafts", str(shop), _job_id=f"howmade-watch:{shop}")
    return len(shops)
