"""Generated-content review: read, inline edit, and approve."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import active_tenant, get_session
from app.db.models import (
    Asset,
    ComplianceFinding,
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
from app.etsy.manual_fields import manual_fields_for
from app.etsy.publisher import link_for
from app.compliance.scanner import rescan
from app.compliance.trademarks import tenant_blocklist
from app.etsy.scheduling import cancel_for_content, state_of
from app.pipeline import versions
from app.pipeline.content import GeneratedListing, bounds_for, policy_for, validate_listing
from app.pipeline.personalization import DEFAULT_QUESTION
from app.pipeline.personalization import for_listing as personalization_for_listing
from app.pipeline.personalization import validate as validate_personalization

router = APIRouter(prefix="/api", tags=["content"])


async def publications(
    session: AsyncSession, content_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[schemas.PublicationOut]]:
    """Each content's drafts, one per shop (v5 §E), with the shop's name and link."""
    from app.api.shops import shop_label

    found: dict[uuid.UUID, list[schemas.PublicationOut]] = {i: [] for i in content_ids}
    if not content_ids:
        return found
    rows = await session.execute(
        select(ListingPublication, EtsyConnection, ListingProfile.content_template, Job)
        .join(EtsyConnection, EtsyConnection.id == ListingPublication.connection_id)
        .outerjoin(ListingProfile, ListingProfile.id == ListingPublication.profile_id)
        .outerjoin(Job, Job.id == ListingPublication.schedule_job_id)
        .where(ListingPublication.content_id.in_(content_ids))
        .order_by(EtsyConnection.position, EtsyConnection.connected_at)
    )
    for publication, connection, template, job in rows.all():
        found[publication.content_id].append(publication_out(publication, connection, template, job))
    return found


def publication_out(
    publication: ListingPublication,
    connection: EtsyConnection,
    template: str | None,
    job: Job | None = None,
) -> schemas.PublicationOut:
    from app.api.shops import shop_label

    schedule = state_of(publication, job)
    return schemas.PublicationOut(
        scheduled_for=publication.scheduled_for,
        schedule_status=schedule.status if schedule else None,
        schedule_note=schedule.note if schedule else None,
        connection_id=connection.id,
        shop_name=shop_label(connection),
        etsy_listing_id=publication.etsy_listing_id,
        state=publication.state,
        listing_link=link_for(publication.etsy_listing_id, publication.state),
        manual_steps=manual_steps(publication.state, template, publication.manual_done_keys()),
    )


def manual_steps(
    state: str, template: str | None, done: set[str] | frozenset[str] = frozenset()
) -> list[schemas.ManualStepOut]:
    """What a draft needs in Shop Manager, each with the seller's tick; nothing once live."""
    if state == "active":
        return []
    return [
        schemas.ManualStepOut(key=f.key, label=f.label, detail=f.detail, done=f.key in done)
        for f in manual_fields_for(template)
    ]


async def _template_of(session: AsyncSession, publication: ListingPublication) -> str | None:
    if publication.profile_id is None:
        return None
    profile = await session.get(ListingProfile, publication.profile_id)
    return profile.content_template if profile is not None else None


@router.put(
    "/content/{content_id}/publications/{connection_id}/manual-steps/{key}",
    response_model=schemas.PublicationOut,
)
async def tick_manual_step(
    content_id: uuid.UUID,
    connection_id: uuid.UUID,
    key: str,
    body: schemas.ManualStepTick,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.PublicationOut:
    """The seller confirms (or un-confirms) making one setting on one draft by hand."""
    content = await _get(session, tenant, content_id)
    rows = await session.execute(
        select(ListingPublication, EtsyConnection)
        .join(EtsyConnection, EtsyConnection.id == ListingPublication.connection_id)
        .where(
            ListingPublication.content_id == content.id,
            ListingPublication.connection_id == connection_id,
            ListingPublication.tenant_id == tenant.id,
        )
    )
    found = rows.first()
    if found is None:
        raise HTTPException(status_code=404, detail="draft not found")
    publication, connection = found
    template = await _template_of(session, publication)
    if key not in {f.key for f in manual_fields_for(template)}:
        raise HTTPException(status_code=404, detail="no such setting for this draft")
    ticks = dict(publication.manual_done or {})  # a new dict, so the change is saved
    if body.done:
        ticks[key] = publication.etsy_listing_id
    else:
        ticks.pop(key, None)
    publication.manual_done = ticks
    await session.commit()
    job = await session.get(Job, publication.schedule_job_id) if publication.schedule_job_id else None
    return publication_out(publication, connection, template, job)


@router.post("/batches/{batch_id}/manual-steps/done", response_model=schemas.ManualStepsDone)
async def mark_manual_steps_done(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ManualStepsDone:
    """'Mark all as done': every setting on every approved draft of this batch.

    For a seller who set them in Shop Manager for the whole batch at once, rather
    than ticking each draft. Live listings and unapproved content are left alone.
    """
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    rows = await session.execute(
        select(ListingPublication)
        .join(GeneratedContent, GeneratedContent.id == ListingPublication.content_id)
        .where(
            GeneratedContent.batch_id == batch_id,
            GeneratedContent.tenant_id == tenant.id,
            GeneratedContent.approved.is_(True),
            ListingPublication.state != "active",
        )
    )
    updated = 0
    for publication in rows.scalars():
        keys = {f.key for f in manual_fields_for(await _template_of(session, publication))}
        missing = keys - publication.manual_done_keys()
        if not missing:
            continue
        ticks = dict(publication.manual_done or {})
        ticks.update({k: publication.etsy_listing_id for k in keys})
        publication.manual_done = ticks
        updated += 1
    await session.commit()
    return schemas.ManualStepsDone(updated_drafts=updated)


async def _profile_shops(
    session: AsyncSession, contents: list[GeneratedContent]
) -> dict[uuid.UUID, uuid.UUID]:
    ids = {c.listing_profile_id for c in contents if c.listing_profile_id}
    if not ids:
        return {}
    rows = await session.execute(
        select(ListingProfile.id, ListingProfile.connection_id).where(ListingProfile.id.in_(ids))
    )
    return {pid: cid for pid, cid in rows.all()}


async def unfinished_work(
    session: AsyncSession, contents: list[GeneratedContent]
) -> dict[uuid.UUID, list[schemas.WorkOut]]:
    """Each listing's latest draft and go-live job per shop, when it did not succeed.

    A failure stays on the card until it is no longer true: a draft that failed
    is not mentioned once the draft exists, nor a go-live once the listing is live.
    """
    from app.api.pauses import pause_out
    from app.api.shops import shop_label

    found: dict[uuid.UUID, list[schemas.WorkOut]] = {c.id: [] for c in contents}
    if not contents:
        return found
    tenant = await session.get(Tenant, contents[0].tenant_id)
    wanted = {str(c.id): c.id for c in contents}
    rows = await session.execute(
        select(Job, EtsyConnection)
        .join(EtsyConnection, EtsyConnection.id == Job.connection_id)
        .where(
            Job.tenant_id == contents[0].tenant_id,
            Job.batch_id.in_({c.batch_id for c in contents}),
            Job.type.in_((JobType.create_draft, JobType.publish_live)),
        )
        .order_by(Job.created_at)
    )
    latest: dict[tuple[uuid.UUID, uuid.UUID, JobType], tuple[Job, EtsyConnection]] = {}
    for job, connection in rows.all():
        content_id = wanted.get((job.payload or {}).get("content_id", ""))
        if content_id is not None:
            latest[(content_id, connection.id, job.type)] = (job, connection)  # the newest wins
    states = {
        (content_id, connection_id): state
        for content_id, connection_id, state in (
            await session.execute(
                select(ListingPublication.content_id, ListingPublication.connection_id, ListingPublication.state)
                .where(ListingPublication.content_id.in_(list(found)))
            )
        ).all()
    }
    for (content_id, connection_id, kind), (job, connection) in latest.items():
        if job.status in (JobStatus.succeeded, JobStatus.cancelled):
            continue
        state = states.get((content_id, connection_id))
        if kind is JobType.create_draft and state is not None:
            continue  # the draft exists now
        if kind is JobType.publish_live and state != "draft":
            continue  # live already, or the draft is gone
        found[content_id].append(
            schemas.WorkOut(
                kind="draft" if kind is JobType.create_draft else "publish",
                connection_id=connection.id,
                shop_name=shop_label(connection),
                job_id=job.id,
                status=job.status.value,
                error=job.last_error if job.status is JobStatus.failed else None,
                pause=pause_out(
                    job.paused_reason if job.status is JobStatus.queued else None,
                    tenant=tenant,
                    resumes_at=job.scheduled_at,
                ),
            )
        )
    return found


async def contents_out(
    session: AsyncSession, pairs: list[tuple[GeneratedContent, Asset]]
) -> list[schemas.ContentOut]:
    pubs = await publications(session, [c.id for c, _ in pairs])
    work = await unfinished_work(session, [c for c, _ in pairs])
    shops = await _profile_shops(session, [c for c, _ in pairs])
    findings: dict[uuid.UUID, list[schemas.FindingOut]] = {c.id: [] for c, _ in pairs}
    if pairs:
        rows = await session.execute(
            select(ComplianceFinding).where(
                ComplianceFinding.generated_content_id.in_([c.id for c, _ in pairs])
            )
        )
        for f in rows.scalars():
            findings[f.generated_content_id].append(
                schemas.FindingOut(rule=f.rule, severity=f.severity.value, detail=f.detail)
            )
    outs = [_to_out(c, a, pubs[c.id], shops.get(c.listing_profile_id)) for c, a in pairs]
    profile_ids = {c.listing_profile_id for c, _ in pairs if c.listing_profile_id}
    profiles = (
        {p.id: p for p in (await session.execute(select(ListingProfile).where(ListingProfile.id.in_(profile_ids)))).scalars()}
        if profile_ids
        else {}
    )
    for out, (c, _) in zip(outs, pairs):
        profile = profiles.get(c.listing_profile_id)
        setting, source = personalization_for_listing(
            c.personalization, profile.personalization if profile else None, profile.cached_payload if profile else None
        )
        out.personalization = _personalization_out(setting)
        out.personalization_source = source
        bounds = bounds_for(profile)
        out.title_min_length, out.title_max_length = bounds.min_length, bounds.max_length
        out.listing_style = "search" if (c.attributes or {}).get("search") else "classic"
        out.attributes = dict((c.attributes or {}).get("listing") or {})
    for out in outs:
        out.findings = findings.get(out.id, [])
        out.work = work.get(out.id, [])
    return outs


def _to_out(
    content: GeneratedContent,
    asset: Asset,
    pubs: list[schemas.PublicationOut] | None = None,
    connection_id: uuid.UUID | None = None,
) -> schemas.ContentOut:
    return schemas.ContentOut(
        id=content.id,
        asset_id=content.asset_id,
        written_from_asset_id=content.written_from_asset_id,
        title=content.title,
        tags=list(content.tags or []),
        description=content.description,
        approved=content.approved,
        connection_id=connection_id,
        publications=pubs or [],
        original_filename=asset.original_filename,
        parsed_sku=asset.parsed_sku,
        rank=asset.rank,
    )


def _personalization_out(setting: dict | None) -> schemas.PersonalizationOut | None:
    if setting is None:
        return None
    return schemas.PersonalizationOut(
        enabled=bool(setting.get("enabled")),
        question_text=setting.get("question_text"),
        instructions=setting.get("instructions") or None,
        required=bool(setting.get("required")),
        max_allowed_characters=setting.get("max_allowed_characters"),
    )


def _clean_personalization(value: dict | None) -> dict | None:
    """The seller's setting, checked against Etsy's limits (422 with the reason)."""
    if value is None:
        return None
    if value.get("enabled") and not str(value.get("question_text") or "").strip():
        value = {**value, "question_text": DEFAULT_QUESTION}
    try:
        return validate_personalization(value)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/batches/{batch_id}/personalization", response_model=list[schemas.ContentOut])
async def set_personalization_for_all(
    batch_id: uuid.UUID,
    body: schemas.BulkPersonalization,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[schemas.ContentOut]:
    """"Set for all" on the review page: one personalization for every listing of
    the batch (or the ones named). Drafts made from now on get it, in every shop."""
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    setting = _clean_personalization(body.personalization)
    query = select(GeneratedContent).where(GeneratedContent.batch_id == batch_id, GeneratedContent.tenant_id == tenant.id)
    if body.content_ids:
        query = query.where(GeneratedContent.id.in_(body.content_ids))
    for content in (await session.execute(query)).scalars():
        content.personalization = setting
    await session.commit()
    return await list_batch_content(batch_id, session, tenant)


async def _validation(
    session: AsyncSession, content: GeneratedContent
) -> schemas.ValidationInfo:
    # Apply the product-type policy from the content's profile (apparel bans
    # "digital download" etc.; digital-products does not).
    policy = None
    profile = None
    if content.listing_profile_id is not None:
        profile = await session.get(ListingProfile, content.listing_profile_id)
        if profile is not None:
            policy = policy_for(profile.content_template)
    listing = GeneratedListing(
        title=content.title or "",
        tags=list(content.tags or []),
        description=content.description or "",
    )
    errors = validate_listing(
        listing,
        policy,
        trademarks=await tenant_blocklist(session, content.tenant_id),
        title_rules=bounds_for(profile),
        for_seller=True,
    )
    return schemas.ValidationInfo(valid=not errors, errors=errors)


async def _get(session: AsyncSession, tenant: Tenant, content_id: uuid.UUID) -> GeneratedContent:
    content = await session.get(GeneratedContent, content_id)
    if content is None or content.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="content not found")
    return content


@router.get("/batches/{batch_id}/content", response_model=list[schemas.ContentOut])
async def list_batch_content(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[schemas.ContentOut]:
    # A batch the caller does not own must look exactly like one that does not
    # exist (B3). The query below is tenant-filtered too, so nothing leaks either
    # way, but an empty 200 would still confirm the id is well-formed and absent.
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")

    rows = await session.execute(
        select(GeneratedContent, Asset)
        .join(Asset, Asset.id == GeneratedContent.asset_id)
        .where(GeneratedContent.batch_id == batch_id, GeneratedContent.tenant_id == tenant.id)
        .order_by(Asset.rank)
    )
    return await contents_out(session, [(content, asset) for content, asset in rows.all()])


@router.patch("/content/{content_id}", response_model=schemas.ContentUpdateResult)
async def update_content(
    content_id: uuid.UUID,
    body: schemas.ContentUpdate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ContentUpdateResult:
    content = await _get(session, tenant, content_id)
    before = (content.title, list(content.tags or []), content.description)
    if body.title is not None:
        content.title = body.title
    if body.tags is not None:
        content.tags = body.tags
    if body.description is not None:
        content.description = body.description
    if (content.title, list(content.tags or []), content.description) != before:
        versions.mark_edited(content)  # its published versions say "edited" (Part D)
    if "personalization" in body.model_fields_set:
        content.personalization = _clean_personalization(body.personalization)
    await rescan(session, content)
    await session.commit()
    await session.refresh(content)
    asset = await session.get(Asset, content.asset_id)
    return schemas.ContentUpdateResult(
        content=(await contents_out(session, [(content, asset)]))[0], validation=await _validation(session, content)
    )


@router.post("/content/{content_id}/approve", response_model=schemas.ContentUpdateResult)
async def approve_content(
    content_id: uuid.UUID,
    body: schemas.ApproveUpdate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ContentUpdateResult:
    content = await _get(session, tenant, content_id)
    await rescan(session, content)
    validation = await _validation(session, content)
    if body.approved and not validation.valid:
        raise HTTPException(
            status_code=422,
            detail={"message": "cannot approve invalid content", "errors": validation.errors},
        )
    content.approved = body.approved
    if not body.approved:
        # A schedule is the seller's confirmation of an approved listing (v6 §G):
        # withdrawing the approval withdraws its schedules.
        await cancel_for_content(session, content.id, actor=tenant)
    await session.commit()
    await session.refresh(content)
    asset = await session.get(Asset, content.asset_id)
    return schemas.ContentUpdateResult(
        content=(await contents_out(session, [(content, asset)]))[0], validation=validation
    )


@router.post("/batches/{batch_id}/approve-all", response_model=schemas.ApproveAllResult)
async def approve_all(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ApproveAllResult:
    """'Approve all' (docs/duzeltmeler-v6.md §D): every listing in the batch that
    passes validation, exactly as approving it one by one would. The ones that do
    not pass are left unapproved and reported with the reason."""
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    rows = await session.execute(
        select(GeneratedContent, Asset)
        .join(Asset, Asset.id == GeneratedContent.asset_id)
        .where(GeneratedContent.batch_id == batch_id, GeneratedContent.tenant_id == tenant.id)
        .order_by(Asset.rank, Asset.original_filename)
    )
    result = schemas.ApproveAllResult()
    for content, asset in rows.all():
        if content.approved:
            result.already_approved += 1
            continue
        await rescan(session, content)
        validation = await _validation(session, content)
        if not validation.valid:
            result.skipped.append(
                schemas.ApproveSkipped(
                    content_id=content.id,
                    original_filename=asset.original_filename,
                    reason="; ".join(validation.errors),
                )
            )
            continue
        content.approved = True
        result.approved += 1
    await session.commit()
    return result
