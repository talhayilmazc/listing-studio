"""Finish in Etsy: the listings whose How it's made the seller still has to tick in Etsy.

Etsy's API has no field for "How does your shop produce this item?" or "What tools are used to
make this item?" (pipeline/how_made.py), so every listing Listyro created, draft or active, is
listed here until the seller marks it done, grouped by the answers to tick (its profile's), then
by shop. Each row links to the listing in Etsy's editor (the same Shop Manager edit link the app
uses for drafts; it edits an active listing too).

``GET  /api/finish-in-etsy``         the groups, their shops and rows, and how many.
``POST /api/finish-in-etsy/done``    "Done" / "Mark done": the two ticks, for those listings.
``POST /api/finish-in-etsy/opened``  the seller opened one in Etsy from here: from now on the app
                                     reads whether the draft went live (workers/how_made.py) and
                                     the row asks "Mark done?". It is never marked done without
                                     the seller's click: the app cannot read the answers.

Done is per listing (``listing_publication.manual_done``, the same ticks a draft's Shop Manager
steps keep), so ticks made on the review card count here too. The caller's own listings only
(404). No Etsy call here; opening a draft queues the read of its state, at most once a minute
per shop.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_redis, get_session
from app.api.shops import shop_label
from app.core import audit
from app.db.models import ConnectionStatus, EtsyConnection, GeneratedContent, ListingProfile, ListingPublication, Tenant
from app.etsy.publisher import listing_edit_url
from app.pipeline import how_made

router = APIRouter(prefix="/api/finish-in-etsy", tags=["finish-in-etsy"])

#: A draft opened from here is watched this long for going live.
WATCH_FOR = timedelta(days=3)
#: At most one read of a shop's opened drafts per this many seconds, however often the page asks.
WATCH_EVERY_SECONDS = 60


class Row(BaseModel):
    key: uuid.UUID  # the listing's publication
    content_id: uuid.UUID | None
    asset_id: uuid.UUID | None
    title: str
    connection_id: uuid.UUID
    shop_name: str
    etsy_listing_id: int
    state: str  # draft | active
    editor_link: str
    opened_at: datetime | None
    #: Opened from here as a draft, and live on Etsy now: "Mark done?".
    went_live: bool


class ShopRows(BaseModel):
    connection_id: uuid.UUID
    shop_name: str
    rows: list[Row]


class Group(BaseModel):
    key: str
    #: "Shop alters it + computerized tools: 24 listings"
    label: str
    #: Etsy's wording to tick, or None when the profile has no answers yet.
    production: str | None
    tools: str | None
    set: bool
    count: int
    #: Profiles without answers in this group (to set them first).
    profiles: list[dict[str, str]] = Field(default_factory=list)
    shops: list[ShopRows]


class FinishOut(BaseModel):
    total: int
    drafts: int
    active: int
    groups: list[Group]


class Keys(BaseModel):
    keys: list[uuid.UUID] = Field(min_length=1, max_length=5000)


class DoneOut(BaseModel):
    done: int
    total: int


async def rows(session: AsyncSession, tenant_id: uuid.UUID) -> list[tuple[ListingPublication, EtsyConnection, ListingProfile | None, Any]]:
    """Every listing Listyro created, draft or active, in a connected shop, not marked done.
    Of the listing's text only its title and cover are read (not its tags, description and
    attributes: quality pass 2026-10, this page and the sidebar badge were the slowest)."""
    found = (await session.execute(
        select(ListingPublication, EtsyConnection, ListingProfile, GeneratedContent.title, GeneratedContent.asset_id)
        .join(EtsyConnection, EtsyConnection.id == ListingPublication.connection_id)
        .outerjoin(ListingProfile, ListingProfile.id == ListingPublication.profile_id)
        .outerjoin(GeneratedContent, GeneratedContent.id == ListingPublication.content_id)
        .where(ListingPublication.tenant_id == tenant_id, ListingPublication.state.in_(("draft", "active")),
               EtsyConnection.status == ConnectionStatus.active)
        .order_by(EtsyConnection.position, ListingPublication.created_at)
    )).all()
    return [(p, c, prof, SimpleNamespace(title=title, asset_id=asset_id) if p.content_id else None)
            for p, c, prof, title, asset_id in found if not how_made.is_done(p)]


async def count(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """How many: the sidebar badge, on every page. Two columns per listing, nothing else."""
    found = (await session.execute(
        select(ListingPublication.manual_done, ListingPublication.etsy_listing_id)
        .join(EtsyConnection, EtsyConnection.id == ListingPublication.connection_id)
        .where(ListingPublication.tenant_id == tenant_id, ListingPublication.state.in_(("draft", "active")),
               EtsyConnection.status == ConnectionStatus.active)
    )).all()
    return sum(1 for ticks, listing_id in found
               if not all((ticks or {}).get(k) == listing_id for k in how_made.DONE_KEYS))


def _group_key(profile: ListingProfile | None) -> str:
    if not how_made.is_set(profile):
        return "unset"
    return f"{profile.how_made_production}:{','.join(profile.how_made_tools)}"


def _group_label(profile: ListingProfile | None, n: int) -> str:
    listings = f"{n} listing{'s' if n != 1 else ''}"
    if not how_made.is_set(profile):
        return f"How it's made not set on the profile: {listings}"
    production = how_made.PRODUCTION[profile.how_made_production][1]
    tools = ", ".join(how_made.TOOLS[t][1] for t in profile.how_made_tools)
    return f"{production[:1].upper()}{production[1:]} + {tools}: {listings}"


async def finish_out(session: AsyncSession, tenant: Tenant) -> FinishOut:
    now = datetime.now(timezone.utc)
    groups: dict[str, dict] = {}
    for pub, shop, profile, content in await rows(session, tenant.id):
        key = _group_key(profile)
        g = groups.setdefault(key, {"profile": profile, "shops": {}, "count": 0, "unset": {}})
        g["count"] += 1
        if not how_made.is_set(profile) and profile is not None:
            g["unset"][str(profile.id)] = profile.name
        opened = pub.how_made_opened_at
        if opened is not None and opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        s = g["shops"].setdefault(shop.id, ShopRows(connection_id=shop.id, shop_name=shop_label(shop), rows=[]))
        s.rows.append(Row(
            key=pub.id, content_id=pub.content_id, asset_id=content.asset_id if content is not None else None,
            title=(content.title if content is not None else None) or pub.title or f"Listing {pub.etsy_listing_id}",
            connection_id=shop.id, shop_name=shop_label(shop), etsy_listing_id=pub.etsy_listing_id, state=pub.state,
            editor_link=listing_edit_url(pub.etsy_listing_id), opened_at=opened,
            went_live=pub.state == "active" and opened is not None and now - opened <= WATCH_FOR * 2,
        ))
    out = []
    for key, g in sorted(groups.items(), key=lambda kv: (kv[0] == "unset", -kv[1]["count"])):
        profile = g["profile"]
        out.append(Group(
            key=key, label=_group_label(profile, g["count"]), set=key != "unset",
            production=how_made.tick(profile, "creativity_production"), tools=how_made.tick(profile, "creativity_tools"),
            count=g["count"], profiles=[{"id": k, "name": v} for k, v in g["unset"].items()], shops=list(g["shops"].values()),
        ))
    every = [r for g in out for s in g.shops for r in s.rows]
    return FinishOut(total=len(every), drafts=sum(1 for r in every if r.state == "draft"),
                     active=sum(1 for r in every if r.state == "active"), groups=out)


async def _watch(session: AsyncSession, redis: Redis, enqueuer: Enqueuer, tenant: Tenant) -> None:
    """Queue the read of each shop's opened drafts, at most once a minute per shop."""
    since = datetime.now(timezone.utc) - WATCH_FOR
    shops = set((await session.execute(select(ListingPublication.connection_id).where(
        ListingPublication.tenant_id == tenant.id, ListingPublication.state == "draft",
        ListingPublication.how_made_opened_at.is_not(None), ListingPublication.how_made_opened_at >= since))).scalars())
    for shop in shops:
        if await redis.set(f"howmade-watch:{shop}", "1", nx=True, ex=WATCH_EVERY_SECONDS):
            await enqueuer.enqueue("watch_opened_drafts", str(shop), _job_id=f"howmade-watch:{shop}")


async def _mine(session: AsyncSession, tenant: Tenant, keys: list[uuid.UUID]) -> list[ListingPublication]:
    found = list((await session.execute(select(ListingPublication).where(
        ListingPublication.id.in_(keys), ListingPublication.tenant_id == tenant.id))).scalars())
    if len(found) != len(set(keys)):
        raise HTTPException(status_code=404, detail="listing not found")
    return found


@router.get("", response_model=FinishOut)
async def finish_in_etsy(session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant),
                         redis: Redis = Depends(get_redis), enqueuer: Enqueuer = Depends(get_enqueuer)) -> FinishOut:
    await _watch(session, redis, enqueuer, tenant)
    return await finish_out(session, tenant)


@router.post("/done", response_model=DoneOut)
async def mark_done(body: Keys, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant)) -> DoneOut:
    """The seller ticked How it's made in Etsy for these listings."""
    for pub in await _mine(session, tenant, body.keys):
        how_made.mark_done(pub)
    audit.record(session, "listing.how_made_done", actor=tenant, target_tenant_id=tenant.id, count=len(body.keys))
    await session.commit()
    return DoneOut(done=len(body.keys), total=await count(session, tenant.id))


@router.post("/opened", response_model=DoneOut)
async def opened(body: Keys, session: AsyncSession = Depends(get_session), tenant: Tenant = Depends(active_tenant),
                 redis: Redis = Depends(get_redis), enqueuer: Enqueuer = Depends(get_enqueuer)) -> DoneOut:
    """Opened in Etsy from here ("Open in Etsy", "Open next"): a draft is watched from now on."""
    now = datetime.now(timezone.utc)
    for pub in await _mine(session, tenant, body.keys):
        if pub.state == "draft":  # only a draft can go live afterwards ("Mark done?")
            pub.how_made_opened_at = now
    await session.commit()
    await _watch(session, redis, enqueuer, tenant)
    return DoneOut(done=0, total=await count(session, tenant.id))
