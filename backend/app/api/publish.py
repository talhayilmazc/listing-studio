"""Publish approved content to Etsy as draft listings (via the queue)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.pauses import pause_out
from app.api.deps import Enqueuer, active_tenant, get_connection_service, get_enqueuer, get_session
from app.db.models import (
    ComplianceFinding,
    ComplianceSeverity,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    Tenant,
    UploadBatch,
)
from app.etsy.connection import ConnectionService
from app.etsy.publisher import link_for
from app.pipeline.content import GeneratedListing, policy_for, validate_listing

router = APIRouter(prefix="/api", tags=["publish"])


async def _blocking(session: AsyncSession, content_id: uuid.UUID) -> bool:
    rows = await session.execute(
        select(ComplianceFinding.id).where(
            ComplianceFinding.generated_content_id == content_id,
            ComplianceFinding.severity == ComplianceSeverity.blocking,
        )
    )
    return rows.first() is not None


def _reference_is_fresh(profile: ListingProfile) -> bool:
    if not profile.cached_payload or profile.updated_at is None:
        return False
    updated = profile.updated_at
    if updated.tzinfo is None:  # SQLite hands back naive datetimes
        updated = updated.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - updated).total_seconds()
    return age < ListingProfile.CACHE_MAX_AGE_SECONDS


async def _validation_reason(
    session: AsyncSession, content: GeneratedContent
) -> str | None:
    if not content.approved:
        return "not approved"
    if content.etsy_listing_id is not None:
        return "already published"
    policy = None
    if content.listing_profile_id is not None:
        profile = await session.get(ListingProfile, content.listing_profile_id)
        if profile is not None:
            policy = policy_for(profile.content_template)
            # A draft copies category, price and variations from the reference
            # listing, so that data must be within its 24-hour limit. Past it,
            # the retention job clears it; either way, refresh first.
            if not _reference_is_fresh(profile):
                return (
                    f"the Etsy data for profile \"{profile.name}\" is more than a day old; "
                    "refresh the profile, then try again"
                )
    errors = validate_listing(
        GeneratedListing(
            title=content.title or "",
            tags=list(content.tags or []),
            description=content.description or "",
        ),
        policy,
    )
    return "; ".join(errors) if errors else None


async def _enqueue_publish(
    session: AsyncSession,
    enqueuer: Enqueuer,
    *,
    tenant: Tenant,
    connection_id: uuid.UUID,
    content: GeneratedContent,
    job_type: JobType = JobType.create_draft,
    function: str = "run_publish_job",
) -> Job:
    # One unfinished job per content and action. A job paused until the daily
    # reset can wait for hours; asking again must not queue a second one (which
    # would create a second draft when both run).
    unfinished = await session.execute(
        select(Job).where(
            Job.tenant_id == tenant.id,
            Job.type == job_type,
            Job.status.in_((JobStatus.queued, JobStatus.running)),
        )
    )
    for existing in unfinished.scalars():
        if (existing.payload or {}).get("content_id") == str(content.id):
            return existing

    job = Job(
        tenant_id=tenant.id,
        connection_id=connection_id,
        type=job_type,
        payload={"content_id": str(content.id)},
        batch_id=content.batch_id,
        status=JobStatus.queued,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    await enqueuer.enqueue(function, str(job.id))
    return job


def _publish_live_reason(content: GeneratedContent) -> str | None:
    """Why this content can't be made active yet (E), or None if it can."""
    if not content.approved:
        return "not approved"
    if content.etsy_listing_id is None:
        return "no draft yet — create the draft first"
    if content.etsy_listing_state == "active":
        return "already published"
    return None


@router.post("/content/{content_id}/publish", response_model=schemas.PublishJobOut)
async def publish_one(
    content_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    service: ConnectionService = Depends(get_connection_service),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.PublishJobOut:
    content = await session.get(GeneratedContent, content_id)
    if content is None or content.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="content not found")

    connection = await service.get_active(session, tenant.id)
    if connection is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")

    reason = await _validation_reason(session, content)
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    if await _blocking(session, content.id):
        raise HTTPException(status_code=409, detail="content has a blocking compliance finding")

    job = await _enqueue_publish(
        session, enqueuer, tenant=tenant, connection_id=connection.id, content=content
    )
    return schemas.PublishJobOut(content_id=content.id, job_id=job.id)


@router.post("/batches/{batch_id}/publish", response_model=schemas.BatchPublishResult)
async def publish_batch(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    service: ConnectionService = Depends(get_connection_service),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.BatchPublishResult:
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")

    connection = await service.get_active(session, tenant.id)
    if connection is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")

    rows = await session.execute(
        select(GeneratedContent).where(GeneratedContent.batch_id == batch_id)
    )
    result = schemas.BatchPublishResult()
    for content in rows.scalars():
        reason = await _validation_reason(session, content)
        if reason == "not approved":
            continue  # only publish approved content; silently skip the rest
        if reason:
            result.skipped.append(schemas.PublishSkipped(content_id=content.id, reason=reason))
            continue
        if await _blocking(session, content.id):
            result.skipped.append(
                schemas.PublishSkipped(content_id=content.id, reason="blocking compliance finding")
            )
            continue
        job = await _enqueue_publish(
            session, enqueuer, tenant=tenant, connection_id=connection.id, content=content
        )
        result.jobs.append(schemas.PublishJobOut(content_id=content.id, job_id=job.id))
    return result


# --- Publish now (draft -> active) ------------------------------------------
@router.post("/content/{content_id}/publish-live", response_model=schemas.PublishJobOut)
async def publish_one_live(
    content_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    service: ConnectionService = Depends(get_connection_service),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.PublishJobOut:
    """Make an already-created, approved draft ACTIVE — the explicit "Publish now"."""
    content = await session.get(GeneratedContent, content_id)
    if content is None or content.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="content not found")

    connection = await service.get_active(session, tenant.id)
    if connection is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")

    reason = _publish_live_reason(content)
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    if await _blocking(session, content.id):
        raise HTTPException(status_code=409, detail="content has a blocking compliance finding")

    job = await _enqueue_publish(
        session,
        enqueuer,
        tenant=tenant,
        connection_id=connection.id,
        content=content,
        job_type=JobType.publish_live,
        function="run_publish_live_job",
    )
    return schemas.PublishJobOut(content_id=content.id, job_id=job.id)


@router.post("/batches/{batch_id}/publish-live", response_model=schemas.BatchPublishResult)
async def publish_batch_live(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    service: ConnectionService = Depends(get_connection_service),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.BatchPublishResult:
    """"Publish all": make every approved, already-drafted listing active.

    Only the seller's individually-approved drafts are published; anything not
    approved is silently skipped, and anything without a draft or with a blocking
    finding is reported as skipped.
    """
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")

    connection = await service.get_active(session, tenant.id)
    if connection is None:
        raise HTTPException(status_code=409, detail="connect your Etsy shop first")

    rows = await session.execute(
        select(GeneratedContent).where(GeneratedContent.batch_id == batch_id)
    )
    result = schemas.BatchPublishResult()
    for content in rows.scalars():
        reason = _publish_live_reason(content)
        if reason == "not approved":
            continue  # only publish what the seller approved; skip the rest silently
        if reason:
            result.skipped.append(schemas.PublishSkipped(content_id=content.id, reason=reason))
            continue
        if await _blocking(session, content.id):
            result.skipped.append(
                schemas.PublishSkipped(content_id=content.id, reason="blocking compliance finding")
            )
            continue
        job = await _enqueue_publish(
            session,
            enqueuer,
            tenant=tenant,
            connection_id=connection.id,
            content=content,
            job_type=JobType.publish_live,
            function="run_publish_live_job",
        )
        result.jobs.append(schemas.PublishJobOut(content_id=content.id, job_id=job.id))
    return result


@router.get("/jobs/{job_id}", response_model=schemas.JobStatusOut)
async def job_status(
    job_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.JobStatusOut:
    job = await session.get(Job, job_id)
    if job is None or job.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="job not found")

    listing_id = None
    url = None
    is_draft = True
    content_id = (job.payload or {}).get("content_id")
    if content_id:
        content = await session.get(GeneratedContent, uuid.UUID(content_id))
        if content and content.etsy_listing_id:
            listing_id = content.etsy_listing_id
            is_draft = content.etsy_listing_state != "active"
            # A draft links to Shop Manager (editable); active links to the public URL.
            url = link_for(listing_id, content.etsy_listing_state)

    return schemas.JobStatusOut(
        id=job.id,
        type=job.type.value,
        status=job.status.value,
        error=job.last_error,
        listing_id=listing_id,
        listing_url=url,
        is_draft=is_draft,
        pause=pause_out(
            job.paused_reason if job.status is JobStatus.queued else None,
            tenant_limit=tenant.daily_quota,
            resumes_at=job.scheduled_at,
        ),
    )
