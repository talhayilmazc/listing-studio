"""Release scheduled publishes when they are due (docs/duzeltmeler-v6.md §G).

Cron, every minute. Each due schedule becomes the same publish-live job that
"Publish now" queues, so the daily budget (a full budget defers it to 00:00 UTC,
with the reason shown), the compliance check and the go-live read-back all
apply. Before releasing, it checks again that the seller still approves the
listing, that its shop is connected and that its text passes; if not, nothing
is published and the reason is kept for the seller.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.compliance.check import listing_problem
from app.db.models import (
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    Tenant,
    TenantStatus,
)
from app.etsy.scheduling import NOT_APPROVED, new_publish_job

logger = logging.getLogger(__name__)


async def _unfinished_job(session, publication: ListingPublication) -> Job | None:  # noqa: ANN001
    """A publish-live job for this draft already queued (e.g. "Publish now")."""
    rows = await session.execute(
        select(Job).where(
            Job.tenant_id == publication.tenant_id,
            Job.connection_id == publication.connection_id,
            Job.type == JobType.publish_live,
            Job.status.in_((JobStatus.queued, JobStatus.running)),
        )
    )
    for job in rows.scalars():
        if (job.payload or {}).get("content_id") == str(publication.content_id):
            return job
    return None


async def release_scheduled_publishes(ctx: dict[str, Any]) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    released: list[str] = []
    held = 0
    async with ctx["sessionmaker"]() as session:
        rows = await session.execute(
            select(ListingPublication, GeneratedContent, EtsyConnection, Tenant)
            .join(GeneratedContent, GeneratedContent.id == ListingPublication.content_id)
            .join(EtsyConnection, EtsyConnection.id == ListingPublication.connection_id)
            .join(Tenant, Tenant.id == ListingPublication.tenant_id)
            .where(
                ListingPublication.scheduled_for.is_not(None),
                ListingPublication.scheduled_for <= now,
                ListingPublication.schedule_job_id.is_(None),
                ListingPublication.schedule_note.is_(None),
                ListingPublication.state != "active",
            )
            .order_by(ListingPublication.scheduled_for)
        )
        for publication, content, connection, tenant in rows.all():
            reason: str | None = None
            if tenant.status is TenantStatus.suspended:
                reason = "Not published: the account is suspended."
            elif connection.status is not ConnectionStatus.active:
                reason = "Not published: the shop is no longer connected."
            elif not content.approved:
                reason = NOT_APPROVED
            else:
                problem = await listing_problem(session, content)
                if problem:
                    reason = f"Not published: {problem}"
            if reason is not None:
                publication.schedule_note = reason
                held += 1
                continue
            job = await _unfinished_job(session, publication)
            if job is None:
                job = new_publish_job(content, publication)
                session.add(job)
                await session.flush()
                released.append(str(job.id))
            publication.schedule_job_id = job.id
        await session.commit()

    # Enqueued after the commit, so the worker finds the job row it is given.
    for job_id in released:
        await _enqueue(ctx, "run_publish_live_job", job_id)
    counts = {"released": len(released), "held": held}
    if released or held:
        logger.info("scheduled publishing: %s", counts)
    return counts


async def _enqueue(ctx: dict[str, Any], function: str, *args: Any) -> None:
    enqueue = ctx.get("enqueue")  # test hook
    if enqueue is not None:
        await enqueue(function, *args)
        return
    await ctx["redis"].enqueue_job(function, *args)
