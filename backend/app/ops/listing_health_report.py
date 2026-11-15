"""``python -m app.cli listing-health-report <e-mail>``: Listing health's buckets for a real account. READ ONLY.

Per connected shop: whether its sales have been read, how many listings fall in each bucket, and how often each
problem fires on listings that sell and on those that do not (docs/listing-health.md). Counts only: no title, tag or
buyer data is printed. Reads our own copy of the shop and its sales; no Etsy call, nothing written.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import ConnectionStatus, EtsyConnection, Tenant


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
            judged = api._judge_rows(rows, profiles, sales, w)
            buckets = Counter(v.bucket for _, v, _, _ in judged)
            selling = Counter(p.id for _, v, _, _ in judged if v.recent_units for p in v.problems)
            rest = Counter(p.id for _, v, _, _ in judged if not v.recent_units for p in v.problems)
            n_selling = sum(1 for _, v, _, _ in judged if v.recent_units)
            lines.append(f"\n{shop.shop_name or shop.id}: {len(rows)} listings in the six-hour copy; sales read: {'yes' if sales.known else 'no'}")
            lines.append("  " + ", ".join(f"{b} {buckets[b]}" for b in H.BUCKETS))
            for pid in sorted(set(selling) | set(rest)):
                lines.append(f"  {pid:20} selling {selling[pid]} of {n_selling}; not selling {rest[pid]} of {len(judged) - n_selling}")
        if not shops:
            lines.append("no connected shop")
    return "\n".join(lines)
