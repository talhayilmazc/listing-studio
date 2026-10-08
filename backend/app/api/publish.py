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

import contextvars
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
from app.core import limits, request_cost
from app.db.models import (
    Asset,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingGroupSetting,
    ListingProfile,
    ListingPublication,
    Tenant,
    UploadBatch,
)
from app.etsy.publisher import link_for, publication_for
from app.etsy.rate_limiter import DailyQuota
from app.etsy.shops import active_shops, owned_shop
from app.compliance.check import blocking_finding, listing_problem
from app.compliance.scanner import rescan
from app.pipeline.targets import is_fresh, resolve_target, shop_profiles

from app.pipeline.batch_names import names_by_id

#: Why a listing cannot get another draft once upload retention took its files.
FILES_REMOVED = (
    "its image files have been removed (kept for a limited time after publishing); "
    "upload the design again to create another draft"
)

router = APIRouter(prefix="/api", tags=["publish"])


async def _content_problem(session: AsyncSession, content: GeneratedContent) -> str | None:
    """Why this listing cannot go to any shop (its own text), or None."""
    return await listing_problem(session, content)


async def _files_removed(session: AsyncSession, content: GeneratedContent) -> bool:
    """Upload retention deleted this listing's images: no new draft can be made
    from it. A draft that already exists is unaffected and can still go live."""
    asset = await session.get(Asset, content.asset_id)
    return asset is not None and asset.files_removed_at is not None


async def _own_shop(session: AsyncSession, content: GeneratedContent) -> EtsyConnection | None:
    """The shop the listing was written for: its group's shop, else its batch's,
    else its profile's main shop (a profile is the account's since v8 §C)."""
    asset = await session.get(Asset, content.asset_id)
    shop_id = None
    if asset is not None:
        setting = (await session.execute(select(ListingGroupSetting).where(
            ListingGroupSetting.batch_id == content.batch_id,
            ListingGroupSetting.group_key == (asset.group_key or ""),
        ))).scalar_one_or_none()
        shop_id = setting.connection_id if setting is not None else None
    if shop_id is None:
        batch = await session.get(UploadBatch, content.batch_id)
        shop_id = batch.connection_id if batch is not None else None
    if shop_id is None and content.listing_profile_id is not None:
        profile = await session.get(ListingProfile, content.listing_profile_id)
        shop_id = profile.connection_id if profile is not None else None
    if shop_id is None:
        return None
    connection = await session.get(EtsyConnection, shop_id)
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
    *,
    report_unapproved: bool = False,
) -> _Plan:
    plan = _Plan()
    # The matrix's ticked cells: exactly those combinations, nothing else.
    pairs: dict[uuid.UUID, list[tuple[EtsyConnection, uuid.UUID | None]]] | None = None
    if request.pairs is not None:
        overrides = {t.connection_id: t.profile_id for t in request.targets or []}
        pairs = {}
        for pair in request.pairs:
            connection = await owned_shop(session, tenant.id, pair.connection_id)
            if connection is None:
                raise HTTPException(status_code=404, detail="shop not found")
            plan.shops[connection.id] = connection
            chosen = pairs.setdefault(pair.content_id, [])
            if all(c.id != connection.id for c, _ in chosen):
                chosen.append((connection, pair.profile_id or overrides.get(connection.id)))
        targets = None
    else:
        targets = await _targets(session, tenant, request)
    for connection, _ in targets or []:
        plan.shops[connection.id] = connection
    for content in contents:
        if pairs is not None and content.id not in pairs:
            continue  # not asked for
        if not content.approved:
            # Only what the seller approved (CLAUDE.md rule 3). Within one batch
            # the rest is passed over; across batches it is listed with why.
            if report_unapproved:
                plan.skip(content, "not approved")
            continue
        problem = await _content_problem(session, content)
        if problem:
            plan.skip(content, problem)
            continue
        if pairs is not None:
            shops = pairs[content.id]
        elif targets is not None:
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
            if await _files_removed(session, content):
                plan.skip(content, FILES_REMOVED, connection)
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
    #: The account's ceiling, as every other screen shows it (core/limits.py).
    ceiling: limits.EtsyCeiling | None = None
    #: "account" or "app": which of the two is the smaller right now.
    limited_by: str = "account"
    #: Requests one draft is planned at: the measured average + margin (core/request_cost.py).
    per_draft: int = request_cost.DEFAULT_PER_DRAFT


async def _budget(session: AsyncSession, quota: DailyQuota, tenant: Tenant, plan: _Plan) -> _Budget:
    """Will these drafts fit in what may still be spent today? (v5 §E) Each draft at
    the measured average plus a margin, not its worst case (core/request_cost.py)."""
    room = await limits.spendable(quota, tenant)
    remaining = room.amount
    per_draft = (await request_cost.draft_estimate(session)).per_draft
    drafts = len(plan.jobs)
    estimated = drafts * per_draft
    shops = max(1, len({c.id for _, c, _ in plan.jobs}))
    per_listing = shops * per_draft
    listings = len({content.id for content, _, _ in plan.jobs})
    fits = estimated <= remaining
    fit = min(listings, remaining // per_listing)
    message = None
    if not fits:
        reset = f"{room.ceiling.resets_label} (00:00 UTC)"
        # Say which number it is: the seller's own, or the app's shared budget.
        why = (
            f"the app's shared Etsy budget has room for only {remaining:,} more today (this is not your own "
            f"limit: you have {room.ceiling.remaining:,} of {room.ceiling.limit:,} left)"
            if room.limited_by == "app"
            else f"you have {remaining:,} of your {room.ceiling.limit:,} Etsy requests left today"
        )
        message = (
            f"{shops} shop{'s' if shops != 1 else ''} × {listings} listing"
            f"{'s' if listings != 1 else ''} ≈ {estimated:,} Etsy requests, but {why}. "
            + (
                f"{fit} listing{'s' if fit != 1 else ''} would fit; send fewer, or wait for the reset at {reset}."
                if fit
                else f"None would fit; wait for the reset at {reset}."
            )
        )
    return _Budget(estimated, remaining, fits, fit, message, ceiling=room.ceiling, limited_by=room.limited_by, per_draft=per_draft)


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
    # Drafts are not part of the listing allowance: only Etsy requests limit them.
    budget = await _budget(session, quota, tenant, plan)
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


async def _matrix(
    session: AsyncSession,
    tenant: Tenant,
    contents: list[GeneratedContent],
    request: schemas.PublishRequest,
    plan: _Plan,
) -> tuple[list[schemas.MatrixColumnOut], list[schemas.MatrixRowOut]]:
    """Every listing of the batch against every connected shop."""
    per_draft = (await request_cost.draft_estimate(session)).per_draft
    shops = await active_shops(session, tenant.id)
    overrides = {t.connection_id: t.profile_id for t in request.targets or []}
    planned = {(content.id, connection.id) for content, connection, _ in plan.jobs}
    assets = {
        a.id: a
        for a in (
            await session.execute(select(Asset).where(Asset.id.in_([c.asset_id for c in contents])))
        ).scalars()
    } if contents else {}
    states = {
        (content_id, connection_id): state
        for content_id, connection_id, state in (
            await session.execute(
                select(ListingPublication.content_id, ListingPublication.connection_id, ListingPublication.state)
                .where(ListingPublication.content_id.in_([c.id for c in contents]))
            )
        ).all()
    } if contents else {}
    profiles = {shop.id: await shop_profiles(session, shop.id) for shop in shops}

    rows: list[schemas.MatrixRowOut] = []
    for content in contents:
        own = await _own_shop(session, content)
        problem = "not approved yet" if not content.approved else await _content_problem(session, content)
        if not problem and await _files_removed(session, content):
            problem = FILES_REMOVED
        cells = []
        for shop in shops:
            state = states.get((content.id, shop.id))
            if state is not None:
                cells.append(schemas.MatrixCellOut(connection_id=shop.id, state="live" if state == "active" else "draft"))
                continue
            if problem:
                cells.append(schemas.MatrixCellOut(connection_id=shop.id, state="unavailable", reason=problem))
                continue
            target = await resolve_target(session, content, shop, profile_id=overrides.get(shop.id))
            if not target.ok or target.profile is None:
                cells.append(schemas.MatrixCellOut(
                    connection_id=shop.id, state="unavailable", reason=target.reason or "no profile for this shop",
                    profile_id=target.profile.id if target.profile else None,
                    profile_name=target.profile.name if target.profile else None,
                    setup=target.setup,
                ))
                continue
            cells.append(schemas.MatrixCellOut(
                connection_id=shop.id, state="available", profile_id=target.profile.id,
                profile_name=target.profile.name, chosen=(content.id, shop.id) in planned,
            ))
        asset = assets.get(content.asset_id)
        rows.append(schemas.MatrixRowOut(
            content_id=content.id, title=content.title, approved=content.approved,
            original_filename=asset.original_filename if asset else "",
            group_key=asset.group_key if asset else None,
            own_connection_id=own.id if own else None, cells=cells,
        ))
    rows.sort(key=lambda r: (r.group_key or "", r.original_filename))
    columns = [
        schemas.MatrixColumnOut(
            connection_id=shop.id,
            shop_name=shop_label(shop),
            profiles=[
                schemas.ProfileChoiceOut(id=p.id, name=p.name, content_template=p.content_template, is_fresh=is_fresh(p))
                for p in profiles[shop.id]
            ],
            drafts=plan.ready.get(shop.id, 0),
            estimated_calls=plan.ready.get(shop.id, 0) * per_draft,
        )
        for shop in shops
    ]
    return columns, rows


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
    budget = await _budget(session, quota, tenant, plan)
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
    columns, rows = await _matrix(session, tenant, contents, request, plan)
    return schemas.PublishPreviewOut(
        shops=shops,
        columns=columns,
        rows=rows,
        drafts=len(plan.jobs),
        estimated_calls=budget.estimated,
        calls_per_draft=budget.per_draft,
        budget_remaining=budget.remaining,
        limited_by=budget.limited_by,
        ceiling=schemas.EtsyCeilingOut(**budget.ceiling.out()) if budget.ceiling else None,
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
    *,
    report_unapproved: bool = False,
    planned: list[tuple[GeneratedContent, EtsyConnection]] | None = None,
) -> schemas.BatchPublishResult:
    """Make approved drafts active: the explicit "Publish now", per shop.

    With ``planned`` it is a dry run: what would go live is added to that list
    and nothing is queued.
    """
    only = set(request.connection_ids) if request.connection_ids else None
    result = schemas.BatchPublishResult()
    for content in contents:
        if not content.approved:
            if report_unapproved:
                result.skipped.append(schemas.PublishSkipped(content_id=content.id, reason="not approved"))
            continue  # only what the seller approved
        await rescan(session, content)
        blocked = await blocking_finding(session, content)
        if blocked:
            result.skipped.append(
                schemas.PublishSkipped(content_id=content.id, reason=f"compliance: {blocked}")
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
            if planned is not None:
                planned.append((content, connection))
                continue
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


# --- Several batches at once, from the Batches page --------------------------------
async def _action_contents(
    session: AsyncSession, tenant: Tenant, batch_ids: list[uuid.UUID]
) -> tuple[list[GeneratedContent], dict[uuid.UUID, str]]:
    """Every listing of these batches (each must be the caller's), and file names."""
    contents: list[GeneratedContent] = []
    for batch_id in dict.fromkeys(batch_ids):
        contents.extend(await _batch_contents(session, tenant, batch_id, None))
    names: dict[uuid.UUID, str] = {}
    if contents:
        rows = await session.execute(
            select(Asset.id, Asset.original_filename).where(
                Asset.id.in_([c.asset_id for c in contents])
            )
        )
        names = {asset_id: name for asset_id, name in rows.all()}
    return contents, names


# The names of the batches a bulk action spans, for its summary (set per request).
_batch_names: contextvars.ContextVar[dict[uuid.UUID, str]] = contextvars.ContextVar("batch_names", default={})


def _item(
    content: GeneratedContent,
    names: dict[uuid.UUID, str],
    *,
    shop_name: str | None = None,
    reason: str | None = None,
) -> schemas.BatchActionItem:
    return schemas.BatchActionItem(
        batch_id=content.batch_id,
        batch_name=_batch_names.get().get(content.batch_id),
        content_id=content.id,
        original_filename=names.get(content.asset_id, ""),
        title=content.title,
        shop_name=shop_name,
        reason=reason,
    )


@router.post("/batch-actions/preview", response_model=schemas.BatchActionPreview)
async def batch_action_preview(
    body: schemas.BatchActionRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    quota: DailyQuota = Depends(get_quota),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.BatchActionPreview:
    """What "create drafts" or "publish" would do across these batches, before
    anything is queued: each listing that would be acted on, and each that would
    be skipped with the reason. Only approved listings are ever acted on, and
    publishing only makes existing drafts live (CLAUDE.md rule 3)."""
    contents, names = await _action_contents(session, tenant, body.batch_ids)
    _batch_names.set(await names_by_id(session, body.batch_ids))
    by_id = {c.id: c for c in contents}
    out = schemas.BatchActionPreview(action=body.action)
    if body.action == "drafts":
        plan = await _plan_drafts(session, tenant, contents, schemas.PublishRequest(), report_unapproved=True)
        for content, connection, _ in plan.jobs:
            out.act.append(_item(content, names, shop_name=shop_label(connection)))
        for s in plan.skipped:
            out.skipped.append(_item(by_id[s.content_id], names, shop_name=s.shop_name, reason=s.reason))
        budget = await _budget(session, quota, tenant, plan)
        out.estimated_calls, out.fits, out.message = budget.estimated, budget.fits, budget.message
    else:
        planned: list[tuple[GeneratedContent, EtsyConnection]] = []
        result = await _publish_live(
            session, tenant, enqueuer, contents, schemas.LiveRequest(), report_unapproved=True, planned=planned
        )
        for content, connection in planned:
            out.act.append(_item(content, names, shop_name=shop_label(connection)))
        for s in result.skipped:
            out.skipped.append(_item(by_id[s.content_id], names, shop_name=s.shop_name, reason=s.reason))
    # Nothing was sent to Etsy or queued; drop what planning touched (rescans).
    await session.rollback()
    return out


@router.post("/batch-actions/run", response_model=schemas.BatchPublishResult)
async def batch_action_run(
    body: schemas.BatchActionRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    quota: DailyQuota = Depends(get_quota),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.BatchPublishResult:
    """Do it: the same checks as the preview, then queue the work. Drafts that
    would not fit in today's budget are refused as a whole, as on the review page."""
    contents, _ = await _action_contents(session, tenant, body.batch_ids)
    if body.action == "drafts":
        return await _publish(session, tenant, quota, enqueuer, contents, schemas.PublishRequest())
    return await _publish_live(session, tenant, enqueuer, contents, schemas.LiveRequest())


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
            tenant=tenant,
            resumes_at=job.scheduled_at,
        ),
    )
