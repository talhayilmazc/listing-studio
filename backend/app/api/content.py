"""Generated-content review: read, inline edit, and approve."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session
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
    ShopListingCache,
    Tenant,
    UploadBatch,
)
from app.etsy.manual_fields import manual_fields_for
from app.etsy.publisher import link_for
from app.compliance.scanner import rescan
from app.compliance.trademarks import tenant_blocklist
from app.etsy.scheduling import cancel_for_content, state_of
from app.pipeline import approval, creativity, manual, versions
from app.pipeline.content import GeneratedListing, bounds_for, check_listing, policy_for
from app.pipeline.generation import TITLE_WARNINGS
from app.pipeline import how_made
from app.pipeline.personalization import DEFAULT_QUESTION
from app.pipeline.reference import decode_etsy_text
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
        select(ListingPublication, EtsyConnection, ListingProfile, Job)
        .join(EtsyConnection, EtsyConnection.id == ListingPublication.connection_id)
        .outerjoin(ListingProfile, ListingProfile.id == ListingPublication.profile_id)
        .outerjoin(Job, Job.id == ListingPublication.schedule_job_id)
        .where(ListingPublication.content_id.in_(content_ids))
        .order_by(EtsyConnection.position, EtsyConnection.connected_at)
    )
    for publication, connection, profile, job in rows.all():
        found[publication.content_id].append(publication_out(publication, connection, profile, job))
    return found


def publication_out(
    publication: ListingPublication,
    connection: EtsyConnection,
    profile: ListingProfile | None,
    job: Job | None = None,
) -> schemas.PublicationOut:
    from app.api.shops import shop_label

    template = profile.content_template if profile is not None else None

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
        manual_steps=manual_steps(publication.state, template, publication.manual_done_keys(), profile),
    )


def manual_steps(
    state: str, template: str | None, done: set[str] | frozenset[str] = frozenset(), profile: ListingProfile | None = None,
) -> list[schemas.ManualStepOut]:
    """What a draft needs in Shop Manager, each with the seller's tick; nothing once live. The two
    How it's made questions say the profile's exact answers to tick (pipeline/how_made.py)."""
    if state == "active":
        return []
    out = []
    for f in manual_fields_for(template):
        detail = f.detail
        if f.key in how_made.DONE_KEYS:
            answer = how_made.tick(profile, f.key)
            detail = (f"Tick in Etsy: {answer}." if answer else
                      "Not set on the profile yet: set How it's made on the profile, then tick the same in Etsy.")
        out.append(schemas.ManualStepOut(key=f.key, label=f.label, detail=detail, done=f.key in done))
    return out


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
    profile = await session.get(ListingProfile, publication.profile_id) if publication.profile_id else None
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
    return publication_out(publication, connection, profile, job)


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


async def _own_shops(
    session: AsyncSession, contents: list[GeneratedContent]
) -> dict[uuid.UUID, uuid.UUID]:
    """The shop each listing was written for, by listing: its group's shop, else
    its batch's, else its profile's main shop (pipeline/shop_targets.py). The
    same shop the review page's matrix calls its own."""
    from app.pipeline import shop_targets

    out: dict[uuid.UUID, uuid.UUID] = {}
    for content in contents:
        shop = await shop_targets.own_shop(session, content)
        if shop is not None:
            out[content.id] = shop.id
    return out


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
    shops = await _own_shops(session, [c for c, _ in pairs])
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
    outs = [_to_out(c, a, pubs[c.id], shops.get(c.id)) for c, a in pairs]
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
        # The listing's profile: chosen at upload or on its group, changed there.
        out.profile_id, out.profile_name = (profile.id, profile.name) if profile else (None, None)
        # Etsy's Creativity Standards: a print-on-demand profile with no production partner.
        out.production_note = creativity.production_note(profile) if profile is not None else None
        out.how_made_line = how_made.after_publishing(profile) if profile is not None else None
        out.how_made_set = how_made.is_set(profile)
        out.attributes = dict((c.attributes or {}).get("listing") or {})
    for out in outs:
        out.findings = findings.get(out.id, [])
        out.work = work.get(out.id, [])
    # A listing the seller writes: Etsy's own rules block, the app's are notes on it.
    hand_written = [c for c, _ in pairs if manual.is_manual(c)]
    if hand_written:
        marks = await tenant_blocklist(session, hand_written[0].tenant_id)
        by_id = {out.id: out for out in outs}
        for c in hand_written:
            profile = profiles.get(c.listing_profile_id)
            check = check_listing(
                GeneratedListing(title=c.title or "", tags=list(c.tags or []), description=c.description or ""),
                policy_for(profile.content_template) if profile is not None else None,
                hand_written=True,
                trademarks=marks,
                title_rules=bounds_for(profile),
            )
            by_id[c.id].validation = schemas.ValidationInfo(valid=check.valid, errors=check.errors, warnings=check.warnings)
    # The search-readiness checklist: from what is stored here; nothing read from Etsy.
    from app.api.checklist import checklists_for

    if pairs:
        lists = await checklists_for(session, pairs[0][0].tenant_id, [c for c, _ in pairs], profiles)
        for out in outs:
            out.checklist = lists.get(out.id)
    # More images than Etsy keeps: the same notice as on the group's card.
    from app.api.batches import _batch_groups  # late: batches imports this module's helpers

    limits: dict[tuple[uuid.UUID, str], schemas.ImageLimitOut] = {}
    for batch_id in {c.batch_id for c, _ in pairs}:
        for group in await _batch_groups(session, batch_id):
            if group.image_limit is not None:
                limits[(batch_id, group.group_key)] = group.image_limit
    for out, (c, a) in zip(outs, pairs):
        out.image_limit = limits.get((c.batch_id, a.group_key or ""))
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
        group_key=asset.group_key or "",
        source=manual.MANUAL if manual.is_manual(content) else manual.AI,
        title_warnings=[str(w) for w in (content.attributes or {}).get(TITLE_WARNINGS) or []],
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
    # A listing the seller writes is stopped only by what Etsy refuses (and the
    # trademark filter); the app's quality rules are warnings on it.
    check = check_listing(
        listing,
        policy,
        hand_written=manual.is_manual(content),
        trademarks=await tenant_blocklist(session, content.tenant_id),
        title_rules=bounds_for(profile),
        for_seller=True,
    )
    return schemas.ValidationInfo(valid=check.valid, errors=check.errors, warnings=check.warnings)


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
    )
    # One fixed order: by group, as the batch page and the matrix list them. Every
    # cover has rank 1, so ordering by rank alone left the database to choose, and
    # the cards changed places after each change to a listing ("Set for all",
    # "Approve all", an edit).
    pairs = sorted(
        ((content, asset) for content, asset in rows.all()),
        key=lambda pair: ((pair[1].group_key or "").lower(), pair[1].original_filename.lower(), str(pair[0].id)),
    )
    return await contents_out(session, pairs)


@router.patch("/content/{content_id}", response_model=schemas.ContentUpdateResult)
async def update_content(
    content_id: uuid.UUID,
    body: schemas.ContentUpdate,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ContentUpdateResult:
    content = await _get(session, tenant, content_id)
    before = (content.title, list(content.tags or []), content.description)
    if body.base is not None:
        stale = _changed_elsewhere(content, body)
        if stale:
            raise HTTPException(
                status_code=409,
                detail=f"This listing's {' and '.join(stale)} changed in another tab or window. "
                "Reload this listing to see it, or keep what you typed here.",
            )
    if body.title is not None:
        if body.title != content.title and (content.attributes or {}).get(TITLE_WARNINGS):
            # The seller's own title: the warnings were about the app's.
            content.attributes = {k: v for k, v in content.attributes.items() if k != TITLE_WARNINGS}
        content.title = body.title
    if body.tags is not None:
        content.tags = body.tags
    if body.description is not None:
        content.description = body.description
    if manual.is_manual(content) and (content.title, content.description) != (before[0], before[2]):
        # A listing the seller writes: the description's title block follows the
        # title they type, for as long as they have not edited the description.
        profile = await session.get(ListingProfile, content.listing_profile_id) if content.listing_profile_id else None
        manual.follow_title(content, profile, before[0])
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


def _changed_elsewhere(content: GeneratedContent, body: schemas.ContentUpdate) -> list[str]:
    """The fields this save would overwrite although they changed since the card read
    them (another tab or window saved them). A field already holding what is sent is
    not a conflict."""
    assert body.base is not None
    stale: list[str] = []
    for name in ("title", "tags", "description"):
        new = getattr(body, name)
        if new is None or name not in body.base.model_fields_set:
            continue
        empty: object = [] if name == "tags" else ""
        stored = getattr(content, name) or empty
        base = getattr(body.base, name) or empty
        if name == "tags":
            stored, base, new = list(stored), list(base), list(new)  # type: ignore[arg-type]
        if stored != base and stored != new:
            stale.append(name)
    return stale


@router.post("/content/{content_id}/checklist", response_model=schemas.ChecklistOut)
async def preview_checklist(
    content_id: uuid.UUID,
    body: schemas.ChecklistPreview,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ChecklistOut:
    """The checklist for the text on the card as the seller edits it, before it is
    saved. Nothing is saved, nothing is read from Etsy and no model is called."""
    from app.api.checklist import checklists_for

    content = await _get(session, tenant, content_id)
    profile = await session.get(ListingProfile, content.listing_profile_id) if content.listing_profile_id else None
    profiles = {profile.id: profile} if profile is not None and profile.tenant_id == tenant.id else {}
    edits = body.model_dump(exclude_unset=True, exclude_none=True)
    lists = await checklists_for(session, tenant.id, [content], profiles, edits={content.id: edits})
    return lists[content.id]


@router.get("/content/{content_id}/reference-text", response_model=schemas.ReferenceText)
async def reference_text(
    content_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> schemas.ReferenceText:
    """"Start from the profile's reference title and tags": the seller's own
    reference listing, from their shop's listing cache while it is fresh (6 hours).
    Nothing is saved: the card fills its fields and the seller edits and saves."""
    content = await _get(session, tenant, content_id)
    profile = await session.get(ListingProfile, content.listing_profile_id) if content.listing_profile_id else None
    if profile is None or profile.tenant_id != tenant.id or profile.reference_listing_id is None:
        raise HTTPException(status_code=409, detail="This listing's profile has no reference listing to start from.")
    row = await session.get(ShopListingCache, (tenant.id, profile.reference_listing_id))
    fetched = None
    if row is not None:
        fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
    if row is None or (datetime.now(timezone.utc) - fetched).total_seconds() >= ShopListingCache.STALE_SECONDS:
        # Listing content is shown for six hours at most: read the shop again first.
        await enqueuer.enqueue("sync_shop_listings", str(profile.connection_id))
        raise HTTPException(
            status_code=409,
            detail="Your shop's listings are being read from Etsy again; try in a minute.",
        )
    payload = row.payload or {}
    return schemas.ReferenceText(
        title=decode_etsy_text(payload.get("title")) or "",
        tags=[str(t) for t in payload.get("tags") or []],
        listing_id=profile.reference_listing_id,
        profile_name=profile.name,
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
    refused: list[tuple[uuid.UUID, bool, list[str]]] = []
    noted: list[tuple[uuid.UUID, bool, list[str]]] = []
    for content, asset in rows.all():
        if content.approved:
            result.already_approved += 1
            continue
        await rescan(session, content)
        validation = await _validation(session, content)
        hand_written = manual.is_manual(content)
        if not validation.valid:
            result.skipped.append(
                schemas.ApproveSkipped(
                    content_id=content.id,
                    original_filename=asset.original_filename,
                    reason="; ".join(validation.errors),
                    reasons=list(dict.fromkeys(approval.short(e) for e in validation.errors)),
                )
            )
            refused.append((content.id, hand_written, validation.errors))
            continue
        if validation.warnings:
            noted.append((content.id, hand_written, validation.warnings))
        content.approved = True
        result.approved += 1
    await session.commit()
    # One line per reason with how many, not the same long list for every listing.
    groups = approval.group(refused)
    result.problems = [
        schemas.ApproveGroup(line=g.line(), count=len(g.content_ids), content_ids=g.content_ids) for g in groups
    ]
    result.summary = approval.summary(len(result.skipped), groups)
    result.notes = [
        schemas.ApproveGroup(line=g.line(), count=len(g.content_ids), content_ids=g.content_ids)
        for g in approval.group(noted)
    ]
    return result
