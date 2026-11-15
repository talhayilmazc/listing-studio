"""``python -m app.cli listing-health-report <e-mail>``: Listing health's buckets for a real account. READ ONLY.

Per connected shop: whether its sales have been read (said plainly when they never were: then Listing health cannot
tell which listings sell), how many listings fall in each bucket, how many listings carry a brand name the seller
kept ("Keep it, I know the risk"), and how often each problem fires on listings that sell and on those that do not
(docs/listing-health.md). Counts only: no title, tag or buyer data is printed. Reads our own copy of the shop and its
sales; no Etsy call, nothing written.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import ConnectionStatus, EtsyConnection, SalesSync, Tenant

NEVER_STARTED = "never started: Listing health cannot tell which listings sell"


def sales_said(sync: SalesSync | None, known: bool) -> str:
    """Where the shop's sales read stands, in the report's words."""
    if known:
        return "yes"
    if sync is None or (sync.started_at is None and sync.state in ("queued", "estimating", "estimated")):
        queued = " (queued from Listing health)" if sync is not None and sync.queued_at is not None else ""
        return NEVER_STARTED + queued
    if sync.state in ("reading", "waiting"):
        total = sync.window_count if sync.window_count is not None else "?"
        return f"{sync.state}, {sync.read_count} of {total} sales so far: Listing health cannot tell which listings sell yet"
    return f"no ({sync.state}): Listing health cannot tell which listings sell"


async def run(sm: async_sessionmaker, email: str) -> str:
    from app.api import listing_health as api
    from app.api.shop import _shop_rows
    from app.pipeline import listing_health as H

    async with sm() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.email == email.strip().lower()))).scalar_one_or_none()
        if tenant is None:
            return f"no account with the address {email}"
        shops = list((await session.execute(select(EtsyConnection).where(
            EtsyConnection.tenant_id == tenant.id, EtsyConnection.status == ConnectionStatus.active,
        ).order_by(EtsyConnection.position))).scalars())
        w = api.windows()
        lines = [f"listing-health-report {tenant.email} (READ ONLY; selling = a sale in {w.selling_days} days, too new = under {w.new_days} days)"]
        for shop in shops:
            rows = api._fresh(await _shop_rows(session, tenant, shop))
            profiles = await api._Profiles(session, tenant, shop.id).load()
            sales = await api._sales(session, tenant, shop, w)
            kept = await api._kept(session, tenant, shop.id)
            judged = api._judge_rows(rows, profiles, sales, w, None, kept)
            buckets = Counter(v.bucket for _, v, _, _ in judged)
            selling = Counter(p.id for _, v, _, _ in judged if v.recent_units for p in v.problems)
            rest = Counter(p.id for _, v, _, _ in judged if not v.recent_units for p in v.problems)
            n_selling = sum(1 for _, v, _, _ in judged if v.recent_units)
            sync = await session.get(SalesSync, shop.id)
            lines.append(f"\n{shop.shop_name or shop.id}: {len(rows)} listings in the six-hour copy; sales read: {sales_said(sync, sales.known)}")
            lines.append("  " + ", ".join(f"{b} {buckets[b]}" for b in H.BUCKETS))
            lines.append(f"  kept by you (brand name): {sum(1 for _, v, _, _ in judged if v.kept_brands)} listings")
            for pid in sorted(set(selling) | set(rest)):
                lines.append(f"  {pid:20} selling {selling[pid]} of {n_selling}; not selling {rest[pid]} of {len(judged) - n_selling}")
        if not shops:
            lines.append("no connected shop")
    return "\n".join(lines)
