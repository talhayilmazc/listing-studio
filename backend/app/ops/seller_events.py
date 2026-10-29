"""``python -m app.cli seller-events <email> --since <time>``: what a seller met on a page
(``seller_event``, api/events.py). READ ONLY: one read-only transaction, SELECTs only.

First the totals: send summaries (how many listings were chosen, how many could go, and per
reason how many could not), frontend errors and failed actions grouped by message. Then each
event in time order: time (UTC and the account's zone), kind, page, action, message, counts.
No personal data is stored to show: paths, actions, short messages with e-mail addresses
removed, counts.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import SellerEvent, Tenant
from app.ops.sku_history import _when, _zone


async def run(sm: async_sessionmaker, email: str, since: datetime) -> str:
    async with sm() as session:
        if session.bind.dialect.name == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            tenant = (await session.execute(select(Tenant).where(
                func.lower(Tenant.email) == email.strip().lower()))).scalar_one_or_none()
            if tenant is None:
                return f"no account {email!r}\n"
            zone = _zone(tenant)
            events = list((await session.execute(select(SellerEvent).where(
                SellerEvent.tenant_id == tenant.id, SellerEvent.at >= since).order_by(SellerEvent.at))).scalars())
            lines = [f"seller-events {tenant.email} since {_when(since, zone)} (read only)", ""]
            summaries = [e for e in events if e.kind == "send_summary"]
            reasons: Counter[str] = Counter()
            chosen = going = 0
            for e in summaries:
                counts = dict(e.counts or {})
                chosen += int(counts.pop("chosen", 0))
                going += int(counts.pop("going", 0))
                reasons.update({k: int(v) for k, v in counts.items()})
            lines.append(f"send summaries: {len(summaries)} · listings chosen {chosen} · could go {going}")
            for reason, n in reasons.most_common():
                lines.append(f"  could not go, {reason.replace('_', ' ')}: {n}")
            for kind, title in (("frontend_error", "frontend errors"), ("action_failed", "failed actions")):
                rows = [e for e in events if e.kind == kind]
                lines.append(f"{title}: {len(rows)}")
                for (action, message), n in Counter((e.action or "", e.message or "") for e in rows).most_common(15):
                    lines.append(f"  {n} × {action or '(no action)'}: {message or '(no message)'}")
            lines += ["", "events:"]
            for e in events:
                counts = ", ".join(f"{k} {v}" for k, v in (e.counts or {}).items())
                lines.append(f"  {_when(e.at, zone)} · {e.kind} · {e.page or '-'} · {e.action or '-'}"
                             + (f" · {e.message}" if e.message else "") + (f" · {counts}" if counts else ""))
            if not events:
                lines.append("  none")
            return "\n".join(lines) + "\n"
        finally:
            await session.rollback()
