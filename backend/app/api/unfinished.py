"""Half-made drafts and "Finish these drafts" (pipeline/unfinished.py).

``GET  /api/drafts/unfinished[?batch_id=]``  the account's listings created on Etsy whose
                                             draft did not finish: shop, Etsy listing id,
                                             the step that failed and why, and whether it
                                             can be finished now (the shop's setup).
``POST /api/drafts/unfinished/finish``       finish them: the same draft job runs again and
                                             carries on with the listing Etsy already has
                                             (etsy/publisher.py); no second listing is made
                                             and nothing is deleted on Etsy. One that still
                                             lacks a setting of its shop is not started and
                                             says what to set up where.

Only the caller's own drafts (another account's are never listed or started).
Nothing here calls Etsy: the job does, through the queue, the gate and the bucket.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session
from app.db.models import EtsyConnection, GeneratedContent, JobType, Tenant
from app.etsy.publisher import listing_edit_url
from app.pipeline import unfinished

router = APIRouter(prefix="/api/drafts", tags=["drafts"])


class UnfinishedOut(BaseModel):
    attempt_id: uuid.UUID
    content_id: uuid.UUID
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


class FinishRequest(BaseModel):
    #: The drafts to finish; null = every one of the account's (or of ``batch_id``) that can be.
    attempt_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)
    batch_id: uuid.UUID | None = None


class FinishSkipped(BaseModel):
    attempt_id: uuid.UUID
    reason: str


class FinishOut(BaseModel):
    started: int
    skipped: list[FinishSkipped] = Field(default_factory=list)


def _out(item: unfinished.HalfMade) -> UnfinishedOut:
    return UnfinishedOut(
        attempt_id=item.attempt_id, content_id=item.content_id, batch_id=item.batch_id,
        connection_id=item.connection_id, shop_name=item.shop_name, etsy_listing_id=item.etsy_listing_id,
        edit_url=listing_edit_url(item.etsy_listing_id), title=item.title, step=item.step, error=item.error,
        finishing=item.finishing, blocked=item.blocked, profile_id=item.profile_id, profile_name=item.profile_name,
        setup=item.setup,
    )


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

    wanted = set(body.attempt_ids) if body.attempt_ids is not None else None
    out = FinishOut(started=0)
    for item in await unfinished.half_made(session, tenant.id, batch_id=body.batch_id):
        if wanted is not None and item.attempt_id not in wanted:
            continue
        if item.finishing:
            continue  # already on its way
        if item.blocked:
            out.skipped.append(FinishSkipped(attempt_id=item.attempt_id, reason=item.blocked))
            continue
        content = await session.get(GeneratedContent, item.content_id)
        shop = await session.get(EtsyConnection, item.connection_id)
        if content is None or shop is None or content.tenant_id != tenant.id or shop.tenant_id != tenant.id:
            continue
        await _enqueue(session, enqueuer, tenant=tenant, connection=shop, content=content,
                       profile_id=uuid.UUID(item.job_profile_id) if item.job_profile_id else None,
                       job_type=JobType.create_draft, function="run_publish_job")
        out.started += 1
    return out
