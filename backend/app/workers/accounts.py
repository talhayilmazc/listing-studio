"""Deleting an account the seller asked to delete (Settings → Delete account).

Two steps, so that what matters most happens at once and the rest cannot be
forgotten:

1. **At once, in the request** (:func:`request_deletion`): every shop is
   disconnected exactly as "Disconnect" does (its tokens are deleted, everything
   read from Etsy for it is deleted, its queued and scheduled work is
   cancelled), every other queued job of the account is cancelled, every session
   ends, and the account can no longer sign in.
2. **By a job** (:func:`finish_deletion`; queued by the request and swept daily
   and at worker start, so a job lost with Redis is still done): the account's
   uploaded image files are deleted, then the account row, which takes with it
   every row that belongs to the account (batches, images' records, listings,
   profiles, publication records, analytics, usage records, settings).

What is kept, and why (docs/etsy-compliance.md):

* ``audit_log`` rows about the account: the action, when, and counts. They
  never held titles, images, e-mail addresses or Etsy content, and they name the
  account only by an id that no longer leads anywhere (the link is cleared).
* ``ai_call`` rows: our own cost records (model, tokens, cost). Numbers only;
  their link to the account is cleared.
* The invite code that was redeemed: its hash and dates, unlinked.
* Database backups: 14 days, then gone (deploy/backup.sh).

Nothing on Etsy is touched: the seller's drafts and listings are theirs and
stay in their shop.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.config import get_settings
from app.db.models import (
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    Tenant,
    TenantStatus,
    UploadBatch,
)
from app.etsy.connection import ConnectionService
from app.etsy.shops import active_shops
from app.pipeline.storage import LocalStorage, Storage

logger = logging.getLogger(__name__)

#: The longest the second step may take, as the Terms and the Privacy Policy promise.
DEADLINE_DAYS = 30
#: Why its queued work did not run.
CANCELLED_MESSAGE = "cancelled: the account was deleted"
#: What signing in says while the account is being deleted.
BEING_DELETED = "this account is being deleted"


async def request_deletion(
    session: AsyncSession, tenant: Tenant, service: ConnectionService, *, now: datetime | None = None
) -> dict[str, int]:
    """Step 1. Commits. Returns counts of what was disconnected and cancelled."""
    now = now or datetime.now(timezone.utc)
    shops = await active_shops(session, tenant.id)
    summary = {"shops": len(shops), "queued_jobs": 0, "scheduled_publishes": 0, "planned_drafts": 0}
    for shop in shops:
        gone = await service.disconnect(session, shop, actor=tenant)
        for key in ("queued_jobs", "scheduled_publishes", "planned_drafts"):
            summary[key] += gone.get(key, 0)
    # Work that belongs to no shop any more (writing, replacing): cancelled too.
    cancelled = await session.execute(
        update(Job)
        .where(Job.tenant_id == tenant.id, Job.status == JobStatus.queued)
        .values(status=JobStatus.cancelled, last_error=CANCELLED_MESSAGE, paused_reason=None, finished_at=now)
        .execution_options(synchronize_session=False)
    )
    summary["queued_jobs"] += cancelled.rowcount or 0
    tenant.deletion_requested_at = now
    tenant.status = TenantStatus.suspended  # no sign-in, no request, no job from here on
    audit.record(session, "account.deletion_requested", actor=tenant, target_tenant_id=tenant.id, by="seller", **summary)
    await session.commit()
    return summary


async def finish_deletion(session: AsyncSession, storage: Storage, tenant_id: uuid.UUID) -> bool:
    """Step 2 for one account. Commits. False when it has to wait (a job of the
    account is still running) or there is nothing to do."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or tenant.deletion_requested_at is None:
        return False
    running = await session.scalar(
        select(func.count()).select_from(Job).where(Job.tenant_id == tenant_id, Job.status == JobStatus.running)
    )
    if running:
        return False  # it finishes its step first; the next sweep deletes the account
    counts = {
        "batches": int(await session.scalar(select(func.count()).select_from(UploadBatch).where(UploadBatch.tenant_id == tenant_id)) or 0),
        "listings": int(await session.scalar(select(func.count()).select_from(GeneratedContent).where(GeneratedContent.tenant_id == tenant_id)) or 0),
        "shops": int(await session.scalar(select(func.count()).select_from(EtsyConnection).where(EtsyConnection.tenant_id == tenant_id)) or 0),
    }
    # Files first, then the rows: a run cut short is finished by the next one, and
    # no row is gone while its files remain.
    storage.delete_prefix(str(tenant_id))
    requested = tenant.deletion_requested_at
    # Counts and ids only: the row says an account was deleted, not whose.
    audit.record(session, "account.deleted", actor=None, by="seller", account=str(tenant_id),
                 requested_at=requested.isoformat() if requested else None, **counts)
    await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
    await session.commit()
    logger.info("account deleted: %s", counts)
    return True


async def _forget_counters(redis: Any, tenant_id: uuid.UUID) -> None:
    """The account's counters in Redis (its Etsy requests today and the like)."""
    if redis is None:
        return
    try:
        keys = [key async for key in redis.scan_iter(f"*{tenant_id}*")]
        if keys:
            await redis.delete(*keys)
    except Exception:  # noqa: BLE001 - they expire by themselves within two days
        logger.warning("account deletion: counters left to expire")


async def delete_account(ctx: dict[str, Any], tenant_id: str) -> str:
    """arq job: step 2 for one account."""
    storage = ctx.get("storage") or LocalStorage(get_settings().storage_dir)
    async with ctx["sessionmaker"]() as session:
        done = await finish_deletion(session, storage, uuid.UUID(tenant_id))
    if done:
        await _forget_counters(ctx.get("redis"), uuid.UUID(tenant_id))
    return "deleted" if done else "waiting"


async def finish_account_deletions(ctx: dict[str, Any]) -> int:
    """arq cron: every account whose deletion was asked for and is not finished."""
    storage = ctx.get("storage") or LocalStorage(get_settings().storage_dir)
    done = 0
    async with ctx["sessionmaker"]() as session:
        waiting = list((await session.execute(
            select(Tenant.id).where(Tenant.deletion_requested_at.is_not(None))
        )).scalars())
    for tenant_id in waiting:
        async with ctx["sessionmaker"]() as session:
            if await finish_deletion(session, storage, tenant_id):
                done += 1
                await _forget_counters(ctx.get("redis"), tenant_id)
    return done
