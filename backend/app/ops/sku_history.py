"""``python -m app.cli sku-history <email> --since <time>`` and
``python -m app.cli sku-restore <email> --since <time> [--apply]``.

sku-history (READ ONLY: one read-only transaction, SELECTs only, no Etsy call): every SKU
written to Etsy for the account since the time, from the audit log (``listing.sku_changed``
with ``on_etsy``, written by workers/skus.py after Etsy kept the SKU; and
``listing.sku_etsy_requested``, written by api/skus.py when the seller pressed "Update it")
and the content versions the write started (reason ``sku_edited``). The SKU job has no job
row (workers/profiles.py::_run_gated), so these are its records. Per write: time, shop,
Etsy listing id, old → new, and the trigger. A request with no write after it (failed, or
still queued) is listed too, and the SKU changes made only in Listyro (never sent) are
counted with their batches.

Triggers: ``seller_click`` ("Update it" in the confirmation); ``restore`` (this CLI);
writes recorded before 2026-10-29 carry none. They are shown as clicks, inferred, because
the server then queued a SKU job only for ``confirm: true``, which only the "Update it"
button sent (components/SkuEditor.tsx); the page's checks sent ``confirm: false``, which
queued nothing.

sku-restore: a dry run by default, listing what it would restore. Only writes NOT triggered
by a click are restored (never a click, never a restore, never an inferred click), and only
when no later write changed that draft again; ``--apply`` queues the normal SKU job
(workers/skus.py, trigger ``restore``) to write the old SKU back, through the queue and the
rate limiter like every Etsy call. Nothing else changes on Etsy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AuditLog, ContentVersion, EtsyConnection, ListingPublication, Tenant

CLICK = "seller_click"
RESTORE = "restore"
INFERRED = "seller_click (inferred: recorded before triggers were; only the Update it button could send it)"


def parse_since(raw: str) -> datetime:
    """``2026-10-28`` or ``2026-10-28T09:30`` (UTC unless an offset is given, ``+03:00``)."""
    at = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


@dataclass
class Write:
    at: datetime
    shop_id: str | None
    publication_id: str | None
    etsy_listing_id: Any
    sku_from: str | None
    sku_to: str | None
    trigger: str | None  # None: recorded before triggers were

    @property
    def is_click(self) -> bool:
        return self.trigger in (None, CLICK, "unrecorded")

    @property
    def shown_trigger(self) -> str:
        return INFERRED if self.trigger in (None, "unrecorded") else self.trigger


async def _tenant(session: AsyncSession, email: str) -> Tenant | None:
    return (await session.execute(select(Tenant).where(func.lower(Tenant.email) == email.strip().lower()))).scalar_one_or_none()


async def _rows(session: AsyncSession, tenant: Tenant, action: str, since: datetime) -> list[AuditLog]:
    return list((await session.execute(select(AuditLog).where(
        AuditLog.action == action, AuditLog.target_tenant_id == tenant.id, AuditLog.created_at >= since,
    ).order_by(AuditLog.created_at))).scalars())


async def writes(session: AsyncSession, tenant: Tenant, since: datetime) -> list[Write]:
    """Every SKU Etsy kept for the account since ``since``, oldest first."""
    return [
        Write(at=r.created_at, shop_id=r.details.get("shop_id"), publication_id=r.details.get("publication_id"),
              etsy_listing_id=r.details.get("etsy_listing_id"), sku_from=r.details.get("sku_from"),
              sku_to=r.details.get("sku_to"), trigger=r.details.get("trigger"))
        for r in await _rows(session, tenant, "listing.sku_changed", since) if r.details.get("on_etsy")
    ]


def _when(at: datetime, zone: ZoneInfo | None) -> str:
    utc = at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"{utc} ({at.astimezone(zone).strftime('%H:%M %Z')})" if zone else utc


async def _shops(session: AsyncSession, tenant: Tenant) -> dict[str, str]:
    rows = (await session.execute(select(EtsyConnection).where(EtsyConnection.tenant_id == tenant.id))).scalars()
    return {str(s.id): s.display_name or s.shop_name or str(s.id)[:8] for s in rows}


def _zone(tenant: Tenant) -> ZoneInfo | None:
    try:
        return ZoneInfo(tenant.time_zone) if tenant.time_zone else None
    except Exception:  # noqa: BLE001 - an unknown zone only loses the local time
        return None


async def history(sm: async_sessionmaker, email: str, since: datetime) -> str:
    async with sm() as session:
        if session.bind.dialect.name == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            tenant = await _tenant(session, email)
            if tenant is None:
                return f"no account {email!r}\n"
            zone, shops = _zone(tenant), await _shops(session, tenant)
            done = await writes(session, tenant, since)
            asked = await _rows(session, tenant, "listing.sku_etsy_requested", since)
            local = [r for r in await _rows(session, tenant, "listing.sku_changed", since) if not r.details.get("on_etsy")]
            versions = (await session.execute(select(func.count()).select_from(ContentVersion).join(
                ListingPublication, ListingPublication.id == ContentVersion.publication_id).where(
                ListingPublication.tenant_id == tenant.id, ContentVersion.reason == "sku_edited",
                ContentVersion.created_at >= since))).scalar_one()
            lines = [f"sku-history {tenant.email} since {_when(since, zone)} (read only: no Etsy call, nothing written)", ""]
            by_trigger: dict[str, int] = {}
            for w in done:
                by_trigger[w.shown_trigger] = by_trigger.get(w.shown_trigger, 0) + 1
            lines.append(f"SKUs written to Etsy: {len(done)}" + (" · " + ", ".join(f"{k}: {v}" for k, v in by_trigger.items()) if done else ""))
            lines.append(f"  content versions started by those writes (job record): {versions}")
            for w in done:
                lines.append(f"  {_when(w.at, zone)} · {shops.get(w.shop_id or '', '?')} · Etsy listing {w.etsy_listing_id} · "
                             f"{w.sku_from or 'no SKU'} → {w.sku_to or 'no SKU'} · trigger: {w.shown_trigger}")
            written = {(w.publication_id, w.sku_to) for w in done}
            open_asks = [r for r in asked if (r.details.get("publication_id"), r.details.get("sku_to")) not in written]
            lines += ["", f"\"Update it\" pressed, nothing written after it (failed or still queued): {len(open_asks)}"]
            for r in open_asks:
                d = r.details
                lines.append(f"  {_when(r.created_at, zone)} · {shops.get(d.get('shop_id') or '', '?')} · Etsy listing "
                             f"{d.get('etsy_listing_id')} · {d.get('sku_from') or 'no SKU'} → {d.get('sku_to') or 'no SKU'}")
            per_batch: dict[str, int] = {}
            for r in local:
                key = (r.details.get("batch_id") or "?")[:8]
                per_batch[key] = per_batch.get(key, 0) + 1
            lines += ["", f"SKU changes in Listyro only (never sent to Etsy): {len(local)}"
                      + (" · by batch: " + ", ".join(f"{k} {v}" for k, v in per_batch.items()) if local else "")]
            return "\n".join(lines) + "\n"
        finally:
            await session.rollback()


async def restore(sm: async_sessionmaker, email: str, since: datetime, *, apply: bool = False, enqueue: Any = None) -> str:
    async with sm() as session:
        tenant = await _tenant(session, email)
        if tenant is None:
            return f"no account {email!r}\n"
        zone, shops = _zone(tenant), await _shops(session, tenant)
        done = await writes(session, tenant, since)
        latest: dict[str | None, Write] = {}
        for w in done:
            latest[w.publication_id] = w
        lines = [f"sku-restore {tenant.email} since {_when(since, zone)} ({'APPLY' if apply else 'dry run: nothing queued'})", ""]
        plan: list[tuple[Write, ListingPublication]] = []
        skipped: list[str] = []
        for w in done:
            where = f"{shops.get(w.shop_id or '', '?')} · Etsy listing {w.etsy_listing_id} · {w.sku_from or 'no SKU'} → {w.sku_to or 'no SKU'}"
            if w.is_click or w.trigger == RESTORE:
                skipped.append(f"  kept ({w.shown_trigger}): {where}")
                continue
            if latest.get(w.publication_id) is not w:
                skipped.append(f"  kept (changed again later on Etsy): {where}")
                continue
            pub = await session.get(ListingPublication, uuid.UUID(w.publication_id)) if w.publication_id else None
            if pub is None or pub.tenant_id != tenant.id or pub.state == "deleted_on_etsy" or not w.sku_from:
                skipped.append(f"  cannot restore ({'no old SKU' if pub is not None and not w.sku_from else 'draft gone'}): {where}")
                continue
            plan.append((w, pub))
        lines.append(f"to restore (not triggered by a click): {len(plan)}")
        for w, pub in plan:
            lines.append(f"  {_when(w.at, zone)} · {shops.get(w.shop_id or '', '?')} · Etsy listing {w.etsy_listing_id} · "
                         f"now {w.sku_to or 'no SKU'} → back to {w.sku_from} · trigger was {w.shown_trigger}")
        lines += [f"left alone: {len(skipped)}", *skipped]
        if apply and plan:
            from app.core import audit
            from app.workers.skus import update_spec

            if enqueue is None:
                from app.api.deps import get_enqueuer

                enqueue = get_enqueuer().enqueue
            for w, pub in plan:
                audit.record(session, "listing.sku_etsy_requested", actor=None, target_tenant_id=tenant.id, trigger=RESTORE,
                             publication_id=str(pub.id), shop_id=str(pub.connection_id),
                             etsy_listing_id=int(pub.etsy_listing_id), sku_from=w.sku_to, sku_to=w.sku_from)
                await enqueue("update_sku_on_etsy", update_spec(pub.id, w.sku_from, RESTORE),
                              _job_id=f"sku:{pub.id}:{w.sku_from}:restore")
            await session.commit()
            lines.append(f"queued {len(plan)} SKU job(s); each writes the old SKU through the queue and the rate limiter")
        return "\n".join(lines) + "\n"
