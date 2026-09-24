"""Publish approved content to Etsy as draft listings, in one or more shops (via the queue).

Each (listing, shop) pair becomes its own job and its own draft, built from that
shop's own profile (docs/duzeltmeler-v5.md §E). One shop failing does not stop
the others, and every pair that cannot go is reported with its reason.

Before anything is queued, the Etsy requests it will take are estimated and
compared with what may still be spent today. A publish that would not fit is
refused, with how many listings would (quota protection, v5 §E). The 90% pause
still applies to each job as it starts (workers/gate.py).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_quota, get_session
from app.api.pauses import pause_out
from app.api.content import manual_steps
from app.api.shops import shop_label
from app.db.models import (
    ComplianceFinding,
    ComplianceSeverity,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingPublication,
    Tenant,
    UploadBatch,
)
from app.etsy.publisher import link_for, publication_for
from app.etsy.rate_limiter import DailyQuota
from app.etsy.shops import owned_shop
from app.pipeline.content import GeneratedListing, policy_for, validate_listing
from app.pipeline.targets import ESTIMATED_CALLS_PER_DRAFT, is_fresh, resolve_target, shop_profiles

router = APIRouter(prefix="/api", tags=["publish"])


async def _blocking(session: AsyncSession, content_id: uuid.UUID) -> bool:
    rows = await session.execute(
        select(ComplianceFinding.id).where(
            ComplianceFinding.generated_content_id == content_id,
            ComplianceFinding.severity == ComplianceSeverity.blocking,
        )
    )
    return rows.first() is not None


async def _content_problem(session: AsyncSession, content: GeneratedContent) -> str | None:
    """Why this listing cannot go to any shop (its own text), or None."""
    policy = None
    if content.listing_profile_id is not None:
        profile = await session.get(ListingProfile, content.listing_profile_id)
        if profile is not None:
            policy = policy_for(profile.content_template)
    errors = validate_listing(
        GeneratedListing(
            title=content.title or "",
            tags=list(content.tags or []),
            description=content.description or "",
        ),
        policy,
    )
    if errors:
        return "; ".join(errors)
    if await _blocking(session, content.id):
        return "blocking compliance finding"
    return None


async def _own_shop(session: AsyncSession, content: GeneratedContent) -> EtsyConnection | None:
    """The shop the listing was written for: its profile's shop."""
    if content.listing_profile_id is None:
        return None
    profile = await session.get(ListingProfile, content.listing_profile_id)
    if profile is None:
        return None
    connection = await session.get(EtsyConnection, profile.connection_id)
    if connection is None or connection.tenant_id != content.tenant_id:
        return None
    return connection if connection.status.value == "active" else None


@dataclass
class _Plan:
    jobs: list[tuple[GeneratedContent, EtsyConnection, uuid.UUID]] = field(default_factory=list)
    skipped: list[schemas.PublishSkipped] = field(default_factory=list)
    shops: dict[uuid.UUID, EtsyConnection] = field(default_factory=dict)
    ready: dict[uuid.UUID, int] = field(default_factory=dict)
    blocked: dict[uuid.UUID, list[schemas.PublishSkipped]] = field(default_factory=dict)

    def skip(self, content: GeneratedContent, reason: str, shop: EtsyConnection | None = None) -> None:
        row = schemas.PublishSkipped(
            content_id=content.id,
            reason=reason,
            connection_id=shop.id if shop else None,
            shop_name=shop_label(shop) if shop else None,
        )
        self.skipped.append(row)
        if shop is not None:
            self.blocked.setdefault(shop.id, []).append(row)


async def _targets(
    session: AsyncSession, tenant: Tenant, request: schemas.PublishRequest
) -> list[tuple[EtsyConnection, uuid.UUID | None]] | None:
    """The chosen shops (each must be one of the caller's own), or None = each listing's own."""
    if request.targets is None:
        return None
    chosen: list[tuple[EtsyConnection, uuid.UUID | None]] = []
    for target in request.targets:
        connection = await owned_shop(session, tenant.id, target.connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="shop not found")
        if all(c.id != connection.id for c, _ in chosen):
            chosen.append((connection, target.profile_id))
    if not chosen:
        raise HTTPException(status_code=422, detail="choose at least one shop")
    return chosen


async def _plan_drafts(
    session: AsyncSession,
    tenant: Tenant,
    contents: list[GeneratedContent],
    request: schemas.PublishRequest,
) -> _Plan:
    plan = _Plan()
    targets = await _targets(session, tenant, request)
    for connection, _ in targets or []:
        plan.shops[connection.id] = connection
    for content in contents:
        if not content.approved:
            continue  # only what the seller approved; the rest is skipped silently
        problem = await _content_problem(session, content)
        if problem:
            plan.skip(content, problem)
            continue
        if targets is not None:
            shops = targets
        else:
            own = await _own_shop(session, content)
            if own is None:
                plan.skip(content, "its profile's shop is not connected; choose a shop")
                continue
            plan.shops[own.id] = own
            shops = [(own, None)]
        for connection, profile_id in shops:
            if await publication_for(session, content.id, connection.id) is not None:
                plan.skip(content, "already has a draft in this shop", connection)
                continue
            target = await resolve_target(session, content, connection, profile_id=profile_id)
            if not target.ok or target.profile is None:
                plan.skip(content, target.reason or "no profile for this shop", connection)
                continue
            plan.jobs.append((content, connection, target.profile.id))
            plan.ready[connection.id] = plan.ready.get(connection.id, 0) + 1
    return plan


@dataclass
class _Budget:
    estimated: int
    remaining: int
    fits: bool
    listings_that_fit: int
    message: str | None


async def _budget(quota: DailyQuota, tenant: Tenant, plan: _Plan) -> _Budget:
    """Will these drafts fit in what may still be spent today? (v5 §E)"""
    tenant_used, global_used = await quota.usage(tenant.id)
    remaining = max(0, min(tenant.daily_quota - tenant_used, quota.pause_at - global_used))
    drafts = len(plan.jobs)
    estimated = drafts * ESTIMATED_CALLS_PER_DRAFT
    shops = max(1, len({c.id for _, c, _ in plan.jobs}))
    per_listing = shops * ESTIMATED_CALLS_PER_DRAFT
    listings = len({content.id for content, _, _ in plan.jobs})
    fits = estimated <= remaining
    fit = min(listings, remaining // per_listing)
    message = None
    if not fits:
        message = (
            f"{shops} shop{'s' if shops != 1 else ''} × {listings} listing"
            f"{'s' if listings != 1 else ''} ≈ {estimated:,} Etsy requests, but only "
            f"{remaining:,} can be spent today. "
            + (
                f"{fit} listing{'s' if fit != 1 else ''} would fit; publish fewer, or wait "
                "for the reset at 00:00 UTC."
                if fit
                else "None would fit; wait for the reset at 00:00 UTC."
            )
        )
    return _Budget(estimated, remaining, fits, fit, message)


async def _enqueue(
    session: AsyncSession,
    enqueuer: Enqueuer,
    *,
    tenant: Tenant,
    connection: EtsyConnection,
    content: GeneratedContent,
    profile_id: uuid.UUID | None = None,
    job_type: JobType = JobType.create_draft,
    function: str = "run_publish_job",
) -> Job:
    # One unfinished job per content, shop and action. A job paused until the
    # daily reset can wait for hours; asking again must not queue a second one
    # (which would create a second draft when both run).
    unfinished = await session.execute(
        select(Job).where(
            Job.tenant_id == tenant.id,
            Job.connection_id == connection.id,
            Job.type == job_type,
            Job.status.in_((JobStatus.queued, JobStatus.running)),
        )
    )
    for existing in unfinished.scalars():
        if (existing.payload or {}).get("content_id") == str(content.id):
            return existing

    payload = {"content_id": str(content.id)}
    if profile_id is not None:
        payload["profile_id"] = str(profile_id)
    job = Job(
        tenant_id=tenant.id,
        connection_id=connection.id,
        type=job_type,
        payload=payload,
        batch_id=content.batch_id,
        status=JobStatus.queued,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    await enqueuer.enqueue(function, str(job.id))
    return job


def _job_out(content: GeneratedContent, connection: EtsyConnection, job: Job) -> schemas.PublishJobOut:
    return schemas.PublishJobOut(
        content_id=content.id,
        job_id=job.id,
        connection_id=connection.id,
        shop_name=shop_label(connection),
    )


async def _batch_contents(
    session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID, only: list[uuid.UUID] | None
) -> list[GeneratedContent]:
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    query = select(GeneratedContent).where(GeneratedContent.batch_id == batch_id)
    if only:
        query = query.where(GeneratedContent.id.in_(only))
    return list((await session.execute(query)).scalars())


async def _one_content(session: AsyncSession, tenant: Tenant, content_id: uuid.UUID) -> GeneratedContent:
    content = await session.get(GeneratedContent, content_id)
    if content is None or content.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="content not found")
    return content


async def _publish(
    session: AsyncSession,
    tenant: Tenant,
    quota: DailyQuota,
    enqueuer: Enqueuer,
    contents: list[GeneratedContent],
    request: schemas.PublishRequest,
    *,
    single: GeneratedContent | None = None,
) -> schemas.BatchPublishResult:
    plan = await _plan_drafts(session, tenant, contents, request)
    if single is not None and not single.approved:
        raise HTTPException(status_code=409, detail="not approved")
    if single is not None and not plan.jobs:
        # A single listing that can go nowhere: say why, as before.
        reasons = "; ".join(dict.fromkeys(s.reason for s in plan.skipped)) or "nothing to publish"
        raise HTTPException(status_code=409, detail=reasons)
    budget = await _budget(quota, tenant, plan)
    if not budget.fits:
        raise HTTPException(status_code=409, detail=budget.message)
    result = schemas.BatchPublishResult(skipped=plan.skipped)
    for content, connection, profile_id in plan.jobs:
        job = await _enqueue(
            session, enqueuer, tenant=tenant, connection=connection, content=content, profile_id=profile_id
        )
        result.jobs.append(_job_out(content, connection, job))
    return result


@router.post("/content/{content_id}/publish", response_model=schemas.BatchPublishResult)
async def publish_one(
    content_id: uuid.UUID,
    request: schemas.PublishRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    quota: DailyQuota = Depends(get_quota),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.BatchPublishResult:
    """Create this listing's draft in each chosen shop (default: its own shop)."""
    content = await _one_content(session, tenant, content_id)
    return await _publish(
        session, tenant, quota, enqueuer, [content], request or schemas.PublishRequest(), single=content
    )


@router.post("/batches/{batch_id}/publish", response_model=schemas.BatchPublishResult)
async def publish_batch(
    batch_id: uuid.UUID,
    request: schemas.PublishRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    quota: DailyQuota = Depends(get_quota),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.BatchPublishResult:
    request = request or schemas.PublishRequest()
    contents = await _batch_contents(session, tenant, batch_id, request.content_ids)
    return await _publish(session, tenant, quota, enqueuer, contents, request)


@router.post("/batches/{batch_id}/publish/preview", response_model=schemas.PublishPreviewOut)
async def publish_preview(
    batch_id: uuid.UUID,
    request: schemas.PublishRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    quota: DailyQuota = Depends(get_quota),
) -> schemas.PublishPreviewOut:
    """What publishing would do, before it is confirmed: per shop, and the budget."""
    request = request or schemas.PublishRequest()
    contents = await _batch_contents(session, tenant, batch_id, request.content_ids)
    plan = await _plan_drafts(session, tenant, contents, request)
    budget = await _budget(quota, tenant, plan)
    shops = []
    for connection in plan.shops.values():
        profiles = await shop_profiles(session, connection.id)
        shops.append(
            schemas.ShopTargetOut(
                connection_id=connection.id,
                shop_name=shop_label(connection),
                ready=plan.ready.get(connection.id, 0),
                blocked=plan.blocked.get(connection.id, []),
                profiles=[
                    schemas.ProfileChoiceOut(
                        id=p.id, name=p.name, content_template=p.content_template, is_fresh=is_fresh(p)
                    )
                    for p in profiles
                ],
            )
        )
    return schemas.PublishPreviewOut(
        shops=shops,
        drafts=len(plan.jobs),
        estimated_calls=budget.estimated,
        calls_per_draft=ESTIMATED_CALLS_PER_DRAFT,
        budget_remaining=budget.remaining,
        fits=budget.fits,
        listings_that_fit=budget.listings_that_fit,
        message=budget.message,
    )


# --- Publish now (draft -> active) ------------------------------------------
async def _publish_live(
    session: AsyncSession,
    tenant: Tenant,
    enqueuer: Enqueuer,
    contents: list[GeneratedContent],
    request: schemas.LiveRequest,
) -> schemas.BatchPublishResult:
    """Make approved drafts active: the explicit "Publish now", per shop."""
    only = set(request.connection_ids) if request.connection_ids else None
    result = schemas.BatchPublishResult()
    for content in contents:
        if not content.approved:
            continue  # only what the seller approved; skip the rest silently
        if await _blocking(session, content.id):
            result.skipped.append(
                schemas.PublishSkipped(content_id=content.id, reason="blocking compliance finding")
            )
            continue
        rows = await session.execute(
            select(ListingPublication, EtsyConnection)
            .join(EtsyConnection, EtsyConnection.id == ListingPublication.connection_id)
            .where(
                ListingPublication.content_id == content.id,
                ListingPublication.tenant_id == tenant.id,
            )
        )
        drafts = [
            (pub, conn)
            for pub, conn in rows.all()
            if conn.status.value == "active" and (only is None or conn.id in only)
        ]
        waiting = [(pub, conn) for pub, conn in drafts if pub.state != "active"]
        if not waiting:
            result.skipped.append(
                schemas.PublishSkipped(
                    content_id=content.id,
                    reason="already published" if drafts else "no draft yet — create the draft first",
                )
            )
            continue
        for _, connection in waiting:
            job = await _enqueue(
                session,
                enqueuer,
                tenant=tenant,
                connection=connection,
                content=content,
                job_type=JobType.publish_live,
                function="run_publish_live_job",
            )
            result.jobs.append(_job_out(content, connection, job))
    return result


@router.post("/content/{content_id}/publish-live", response_model=schemas.BatchPublishResult)
async def publish_one_live(
    content_id: uuid.UUID,
    request: schemas.LiveRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.BatchPublishResult:
    """Make this listing's approved drafts active, in each shop that has one."""
    content = await _one_content(session, tenant, content_id)
    if not content.approved:
        raise HTTPException(status_code=409, detail="not approved")
    if request is not None and request.connection_ids:
        for connection_id in request.connection_ids:
            if await owned_shop(session, tenant.id, connection_id) is None:
                raise HTTPException(status_code=404, detail="shop not found")
    result = await _publish_live(session, tenant, enqueuer, [content], request or schemas.LiveRequest())
    if not result.jobs:
        raise HTTPException(
            status_code=409, detail=result.skipped[0].reason if result.skipped else "nothing to publish"
        )
    return result


@router.post("/batches/{batch_id}/publish-live", response_model=schemas.BatchPublishResult)
async def publish_batch_live(
    batch_id: uuid.UUID,
    request: schemas.LiveRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.BatchPublishResult:
    """"Publish all": every approved, already-drafted listing, in each shop.

    Only the seller's individually-approved drafts are published; anything not
    approved is silently skipped, and anything without a draft or with a blocking
    finding is reported as skipped.
    """
    request = request or schemas.LiveRequest()
    if request.connection_ids:
        for connection_id in request.connection_ids:
            if await owned_shop(session, tenant.id, connection_id) is None:
                raise HTTPException(status_code=404, detail="shop not found")
    contents = await _batch_contents(session, tenant, batch_id, request.content_ids)
    return await _publish_live(session, tenant, enqueuer, contents, request)


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
    steps: list[schemas.ManualStepOut] = []
    content_id = (job.payload or {}).get("content_id")
    if content_id:
        publication = await publication_for(session, uuid.UUID(content_id), job.connection_id)
        if publication is not None:
            listing_id = publication.etsy_listing_id
            is_draft = publication.state != "active"
            # A draft links to Shop Manager (editable); active links to the public URL.
            url = link_for(listing_id, publication.state)
            profile = (
                await session.get(ListingProfile, publication.profile_id)
                if publication.profile_id
                else None
            )
            steps = manual_steps(
                publication.state,
                profile.content_template if profile else None,
                publication.manual_done_keys(),
            )
    connection = await session.get(EtsyConnection, job.connection_id)

    return schemas.JobStatusOut(
        id=job.id,
        type=job.type.value,
        status=job.status.value,
        error=job.last_error,
        listing_id=listing_id,
        listing_url=url,
        is_draft=is_draft,
        connection_id=job.connection_id,
        shop_name=shop_label(connection) if connection is not None else None,
        manual_steps=steps,
        pause=pause_out(
            job.paused_reason if job.status is JobStatus.queued else None,
            tenant_limit=tenant.daily_quota,
            resumes_at=job.scheduled_at,
        ),
    )
