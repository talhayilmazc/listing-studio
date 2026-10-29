"""Deleting a batch: hidden at once with "Restore" for ``UploadBatch.RESTORE_DAYS`` (7) days,
then removed for good (migration 0095).

Only Listyro's records: nothing on Etsy changes, ever. Deleting (:func:`soft_delete`) hides the
batch from every page and endpoint (they answer 404 as for a batch that does not exist),
cancels its queued jobs, its scheduled go-lives and its group-schedule drafts not made yet
(there is nothing left to review them against), and keeps its uploads, listings and settings.
:func:`restore` brings the batch back as it was, except what was cancelled (said to the seller
before deleting). After the seven days :func:`purge_expired` removes it as deleting did
before: uploads and their files, listings and settings cascade; the record that drafts were
made and published (``listing_publication``) is detached and kept. Each step is audited.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.db.models import (
    Asset,
    GeneratedContent,
    Job,
    JobStatus,
    ListingPublication,
    PlannedSlot,
    Tenant,
    UploadBatch,
)
from app.etsy.scheduling import record_cancel
from app.pipeline.storage import Storage


def restore_until(batch: UploadBatch) -> datetime | None:
    if batch.deleted_at is None:
        return None
    at = batch.deleted_at if batch.deleted_at.tzinfo else batch.deleted_at.replace(tzinfo=timezone.utc)
    return at + timedelta(days=UploadBatch.RESTORE_DAYS)


async def soft_delete(session: AsyncSession, tenant: Tenant, batch: UploadBatch, result: Any) -> None:
    """Hide the batch ("Restore" for seven days) and cancel its queued work. ``result`` is a
    BatchDeleteResult; its counts say what was cancelled and what stays on Etsy."""
    content_ids = select(GeneratedContent.id).where(GeneratedContent.batch_id == batch.id)
    kept = list((await session.execute(select(ListingPublication).where(ListingPublication.content_id.in_(content_ids)))).scalars())
    schedules = 0
    for publication in kept:
        if publication.scheduled_for is not None and publication.state != "active":
            publication.scheduled_for, publication.schedule_job_id = None, None
            publication.schedule_note = "cancelled: its batch was deleted"
            record_cancel(session, publication, actor=tenant, reason="batch deleted")
            schedules += 1
    slots = 0
    for slot in (await session.execute(select(PlannedSlot).where(
        PlannedSlot.content_id.in_(content_ids), PlannedSlot.state == "waiting"
    ))).scalars():
        slot.state, slot.note = "cancelled", "its batch was deleted"
        slots += 1
    now = datetime.now(timezone.utc)
    queued = list((await session.execute(select(Job).where(
        Job.tenant_id == tenant.id, Job.batch_id == batch.id, Job.status == JobStatus.queued))).scalars())
    for job in queued:
        job.status, job.finished_at, job.last_error = JobStatus.cancelled, now, "cancelled: the batch was deleted"
    files = await session.scalar(select(func.count()).select_from(Asset).where(Asset.batch_id == batch.id))
    batch.deleted_at = now
    audit.destructive(session, "batch.deleted", actor=tenant, tenant_id=tenant.id, shop_id=batch.connection_id,
                      object_id=batch.id, batch_id=str(batch.id), files=int(files or 0), publications_kept=len(kept),
                      jobs_cancelled=len(queued), schedules_cancelled=schedules, slots_cancelled=slots,
                      restorable_until=restore_until(batch).isoformat())
    result.deleted += 1
    result.listings_left_on_etsy += len(kept)
    result.jobs_cancelled += len(queued)
    result.schedules_cancelled += schedules + slots
    result.restorable_until = restore_until(batch)


async def restore(session: AsyncSession, tenant: Tenant, batch: UploadBatch) -> bool:
    """Bring a deleted batch back while it can be (False: not deleted, or too late)."""
    until = restore_until(batch)
    if until is None or until <= datetime.now(timezone.utc):
        return False
    batch.deleted_at = None
    audit.record(session, "batch.restored", actor=tenant, target_tenant_id=tenant.id, batch_id=str(batch.id))
    return True


async def purge(session: AsyncSession, storage: Storage, batch: UploadBatch) -> int:
    """Remove a deleted batch for good: rows cascade, publications are detached (content_id ->
    NULL) and kept, its files are deleted. Returns the number of uploads removed."""
    files = int(await session.scalar(select(func.count()).select_from(Asset).where(Asset.batch_id == batch.id)) or 0)
    audit.destructive(session, "batch.purged", actor=None, tenant_id=batch.tenant_id, shop_id=batch.connection_id,
                      object_id=batch.id, batch_id=str(batch.id), files=files)
    tenant_id, batch_id = batch.tenant_id, batch.id
    await session.delete(batch)
    await session.flush()
    storage.delete_prefix(f"{tenant_id}/{batch_id}")
    return files


async def purge_expired(session: AsyncSession, storage: Storage, *, now: datetime | None = None) -> int:
    """Every batch deleted more than seven days ago, removed for good. Returns how many."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=UploadBatch.RESTORE_DAYS)
    due = list((await session.execute(select(UploadBatch).where(
        UploadBatch.deleted_at.is_not(None), UploadBatch.deleted_at <= cutoff))).scalars())
    for batch in due:
        await purge(session, storage, batch)
        await session.commit()
    return len(due)
