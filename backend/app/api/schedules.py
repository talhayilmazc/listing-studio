"""Scheduled publishing (docs/duzeltmeler-v6.md §G).

The seller picks when each approved listing's draft goes live, one listing or a
whole batch at once (the browser spreads a batch over days in the seller's own
time zone and sends each time in UTC). Only a draft of an approved listing can
be scheduled, a schedule can be moved or cancelled until it runs, and the
scheduled view lists every schedule with its shop, time and outcome.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import active_tenant, get_session
from app.compliance.check import listing_problem
from app.db.models import EtsyConnection, GeneratedContent, Job, ListingPublication, Tenant
from app.etsy.publisher import link_for
from app.etsy.scheduling import ScheduleBusy, ScheduleRefused, cancel, set_schedule, state_of
from app.etsy.shops import owned_shop

router = APIRouter(prefix="/api", tags=["schedules"])

#: How long a finished schedule stays in the scheduled view.
SHOWN_AFTER = timedelta(days=7)


def schedule_out(
    publication: ListingPublication,
    content: GeneratedContent,
    connection: EtsyConnection,
    job: Job | None,
) -> schemas.ScheduleOut:
    from app.api.shops import shop_label

    state = state_of(publication, job)
    assert state is not None and publication.scheduled_for is not None
    return schemas.ScheduleOut(
        content_id=content.id,
        connection_id=connection.id,
        shop_name=shop_label(connection),
        title=content.title,
        asset_id=content.asset_id,
        batch_id=content.batch_id,
        etsy_listing_id=publication.etsy_listing_id,
        # Back link to the listing on Etsy (CLAUDE.md): its draft, or the live page.
        listing_link=link_for(publication.etsy_listing_id, publication.state),
        scheduled_for=publication.scheduled_for,
        status=state.status,
        note=state.note,
    )


async def _draft(
    session: AsyncSession, tenant: Tenant, content_id: uuid.UUID, connection_id: uuid.UUID
) -> tuple[GeneratedContent, ListingPublication, EtsyConnection] | None:
    """This account's listing, its shop and its draft there; None if any is not theirs."""
    content = await session.get(GeneratedContent, content_id)
    if content is None or content.tenant_id != tenant.id:
        return None
    connection = await owned_shop(session, tenant.id, connection_id)
    if connection is None:
        return None
    rows = await session.execute(
        select(ListingPublication).where(
            ListingPublication.content_id == content.id,
            ListingPublication.connection_id == connection.id,
        )
    )
    publication = rows.scalars().first()
    if publication is None:
        return None
    return content, publication, connection


@router.get("/schedules", response_model=list[schemas.ScheduleOut])
async def list_schedules(
    shop: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> list[schemas.ScheduleOut]:
    """Every scheduled listing, soonest first: upcoming ones and the last week's."""
    if shop is not None and await owned_shop(session, tenant.id, shop) is None:
        raise HTTPException(status_code=404, detail="shop not found")
    since = datetime.now(timezone.utc) - SHOWN_AFTER
    query = (
        select(ListingPublication, GeneratedContent, EtsyConnection, Job)
        .join(GeneratedContent, GeneratedContent.id == ListingPublication.content_id)
        .join(EtsyConnection, EtsyConnection.id == ListingPublication.connection_id)
        .outerjoin(Job, Job.id == ListingPublication.schedule_job_id)
        .where(
            ListingPublication.tenant_id == tenant.id,
            ListingPublication.scheduled_for.is_not(None),
            ListingPublication.scheduled_for >= since,
        )
        .order_by(ListingPublication.scheduled_for)
    )
    if shop is not None:
        query = query.where(ListingPublication.connection_id == shop)
    rows = await session.execute(query)
    return [schedule_out(p, c, conn, job) for p, c, conn, job in rows.all()]


@router.post("/schedules", response_model=schemas.ScheduleResult)
async def schedule(
    body: schemas.ScheduleRequest,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> schemas.ScheduleResult:
    """Schedule (or move) drafts going live. Each must be the existing draft of a
    listing the seller approved; the others are listed with the reason."""
    result = schemas.ScheduleResult()
    for item in body.items:
        found = await _draft(session, tenant, item.content_id, item.connection_id)

        def skip(reason: str) -> None:
            result.skipped.append(
                schemas.ScheduleSkipped(
                    content_id=item.content_id, connection_id=item.connection_id, reason=reason
                )
            )

        if found is None:
            skip("no draft of this listing in that shop; create the draft first")
            continue
        content, publication, connection = found
        if not body.replace and publication.scheduled_for is not None:
            skip("already scheduled; change it on its own")
            continue
        problem = await listing_problem(session, content)
        if problem:
            skip(problem)
            continue
        try:
            await set_schedule(session, content, publication, item.run_at)
        except ScheduleRefused as exc:
            skip(str(exc))
            continue
        await session.flush()
        result.scheduled.append(schedule_out(publication, content, connection, None))
    await session.commit()
    return result


@router.delete("/schedules/{content_id}/{connection_id}", status_code=204)
async def cancel_schedule(
    content_id: uuid.UUID,
    connection_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
) -> None:
    found = await _draft(session, tenant, content_id, connection_id)
    if found is None or found[1].scheduled_for is None:
        raise HTTPException(status_code=404, detail="schedule not found")
    try:
        await cancel(session, found[1])
    except ScheduleBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
