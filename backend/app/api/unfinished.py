"""Half-made drafts and "Finish draft" (pipeline/unfinished.py).

``GET  /api/drafts/unfinished[?batch_id=]``  the account's drafts that exist on Etsy and need
                                             something: one per design and shop, each with
                                             its category, shown as three lists: finish
                                             these, fix these (the personalization question,
                                             the shop's setup), delete these on Etsy (replaced
                                             by another draft of the same design, matched
                                             across batches too; or their batch was deleted,
                                             so the app has no text to finish them with).
``POST /api/drafts/unfinished/finish``       finish them (all, or ``record_ids``): the same
                                             draft job runs again with ``finish`` set and
                                             carries on with the listing Etsy already has. A
                                             draft whose listing id the app found only in a
                                             create snapshot is linked to that listing first.
                                             The job never makes a second listing of a design
                                             already in the shop (etsy/publisher.py
                                             ``_existing_for_finish``). Nothing is deleted on Etsy.
``POST /api/drafts/unfinished/{record_id}/dismiss``  a draft to delete on Etsy, taken off the
                                             list once the seller has deleted it there (only
                                             the app's own record changes; Etsy is never touched).

Only the caller's own drafts (another account's are never listed or started). Nothing
here calls Etsy: the job does, through the queue, the gate and the bucket.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session
from app.core import audit
from app.db.models import DraftAttempt, DraftDismissal, EtsyConnection, GeneratedContent, JobType, Tenant
from app.etsy.publisher import listing_edit_url
from app.pipeline import unfinished

router = APIRouter(prefix="/api/drafts", tags=["drafts"])

#: The seller's three lists.
SECTIONS = {
    unfinished.FINISH: "finish",
    unfinished.FIX_PERSONALIZATION: "fix",
    unfinished.FIX_SHOP: "fix",
    unfinished.REPLACED: "delete",
    unfinished.BATCH_DELETED: "delete",
}


class UnfinishedOut(BaseModel):
    #: How this draft is addressed (finish, dismiss).
    record_id: str
    attempt_id: uuid.UUID | None = None
    content_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None
    connection_id: uuid.UUID
    shop_name: str
    etsy_listing_id: int
    #: The draft in Shop Manager (a draft has no public page).
    edit_url: str
    title: str
    step: str | None = None
    error: str | None = None
    finishing: bool = False
    blocked: str | None = None
    profile_id: uuid.UUID | None = None
    profile_name: str | None = None
    setup: bool = False
    #: What to do: finish, fix_personalization, fix_shop, replaced, batch_deleted; and the list it is in.
    category: str
    category_label: str
    section: str
    #: Why Etsy would refuse its personalization question (fix it on the card, then finish).
    personalization_problems: list[str] = Field(default_factory=list)
    #: Another draft of the same design in this shop: its listing id, link and what matched.
    replaced_by: int | None = None
    replaced_edit_url: str | None = None
    replaced_key: str | None = None
    #: "Finish draft" can run now; "I deleted it on Etsy" takes it off the list.
    can_finish: bool = False
    can_dismiss: bool = False


class FinishRequest(BaseModel):
    #: The drafts to finish; null = every one of the account's (or of ``batch_id``) that can be.
    record_ids: list[str] | None = Field(default=None, max_length=500)
    #: The same by draft attempt (kept for older pages).
    attempt_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)
    batch_id: uuid.UUID | None = None


class FinishSkipped(BaseModel):
    record_id: str
    attempt_id: uuid.UUID | None = None
    reason: str


class FinishOut(BaseModel):
    started: int
    skipped: list[FinishSkipped] = Field(default_factory=list)


def _out(item: unfinished.DraftRecord) -> UnfinishedOut:
    assert item.etsy_listing_id is not None
    return UnfinishedOut(
        record_id=item.record_id, attempt_id=item.attempt_id, content_id=item.content_id, batch_id=item.batch_id,
        connection_id=item.connection_id, shop_name=item.shop_name, etsy_listing_id=item.etsy_listing_id,
        edit_url=listing_edit_url(item.etsy_listing_id), title=item.title, step=item.step, error=item.error,
        finishing=item.finishing, blocked=item.blocked, profile_id=item.profile_id, profile_name=item.profile_name,
        setup=item.setup, category=item.category, category_label=item.label, section=SECTIONS[item.category],
        personalization_problems=item.personalization_problems, replaced_by=item.replaced_by,
        replaced_edit_url=listing_edit_url(item.replaced_by) if item.replaced_by else None,
        replaced_key=item.replaced_key,
        can_finish=item.category == unfinished.FINISH and not item.finishing,
        can_dismiss=item.category in unfinished.DISMISSABLE,
    )


def _why_not(item: unfinished.DraftRecord) -> str | None:
    if item.category == unfinished.REPLACED:
        return (f"replaced by a newer draft of the same design (listing {item.replaced_by}, matched by "
                f"{item.replaced_key}); delete this one on Etsy if you no longer want it")
    if item.category == unfinished.BATCH_DELETED:
        return (f"its batch was deleted, so the app no longer has this listing's text; finish listing "
                f"{item.etsy_listing_id} in Shop Manager, or delete it there")
    if item.category == unfinished.FIX_PERSONALIZATION:
        return ("Etsy would refuse its personalization question: " + "; ".join(item.personalization_problems)
                + ". Change it on the listing's card (or the profile), then finish it")
    if item.category == unfinished.FIX_SHOP:
        return item.blocked or "its shop is not set up for this listing's profile yet"
    if item.category != unfinished.FINISH:
        return item.label
    return None


@router.get("/unfinished", response_model=list[UnfinishedOut])
async def list_unfinished(
    batch_id: uuid.UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[UnfinishedOut]:
    return [_out(i) for i in await unfinished.half_made(session, tenant.id, batch_id=batch_id)]


@router.post("/unfinished/finish", response_model=FinishOut)
async def finish_unfinished(
    body: FinishRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> FinishOut:
    from app.api.publish import _enqueue  # one unfinished job per listing and shop, as for any draft

    wanted: set[str] | None = None
    if body.record_ids is not None or body.attempt_ids is not None:
        wanted = set(body.record_ids or []) | {str(a) for a in body.attempt_ids or []}
    out = FinishOut(started=0)
    for item in await unfinished.half_made(session, tenant.id, batch_id=body.batch_id):
        if wanted is not None and item.record_id not in wanted and str(item.attempt_id) not in wanted:
            continue
        if item.finishing:
            continue  # already on its way
        reason = _why_not(item)
        if reason:
            out.skipped.append(FinishSkipped(record_id=item.record_id, attempt_id=item.attempt_id, reason=reason))
            continue
        content = await session.get(GeneratedContent, item.content_id) if item.content_id else None
        shop = await session.get(EtsyConnection, item.connection_id)
        if content is None or shop is None or content.tenant_id != tenant.id or shop.tenant_id != tenant.id:
            continue
        if item.attempt_id is None:
            # The listing id is known from the job's create snapshot (the attempt was lost):
            # link it, so the job carries on with that listing and never makes another.
            session.add(DraftAttempt(tenant_id=tenant.id, content_id=content.id, connection_id=shop.id,
                                     etsy_listing_id=item.etsy_listing_id, title=item.title or None))
            await session.flush()
        await _enqueue(session, enqueuer, tenant=tenant, connection=shop, content=content,
                       profile_id=uuid.UUID(item.job_profile_id) if item.job_profile_id else None,
                       job_type=JobType.create_draft, function="run_publish_job", extra={"finish": True})
        out.started += 1
    return out


@router.post("/unfinished/{record_id}/dismiss", status_code=204)
async def dismiss_replaced(
    record_id: str,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> None:
    """Take a draft to delete on Etsy off the list (the seller deleted it there, or will):
    one replaced by another draft of its design, or one whose batch was deleted. Only the
    app's own record changes (the listing id is remembered as dismissed; its draft attempt,
    if any, goes). Nothing is sent to Etsy."""
    item = next((i for i in await unfinished.records(session, tenant.id, with_blocks=False)
                 if (i.record_id == record_id or str(i.attempt_id) == record_id)
                 and i.etsy_listing_id is not None and not i.dismissed), None)
    if item is None:
        raise HTTPException(status_code=404, detail="draft not found")
    if item.category not in unfinished.DISMISSABLE:
        raise HTTPException(status_code=409, detail="Only a draft to delete on Etsy (replaced, or its batch deleted) "
                                                    "can be taken off this list.")
    audit.destructive(session, "draft_attempt.dismissed", actor=tenant, tenant_id=tenant.id,
                      shop_id=item.connection_id, object_id=str(item.attempt_id or item.record_id),
                      etsy_listing_id=item.etsy_listing_id, category=item.category)
    session.add(DraftDismissal(tenant_id=tenant.id, connection_id=item.connection_id, etsy_listing_id=item.etsy_listing_id))
    if item.attempt_id is not None:
        attempt = await session.get(DraftAttempt, item.attempt_id)
        if attempt is not None and attempt.tenant_id == tenant.id:
            await session.delete(attempt)
    await session.commit()
