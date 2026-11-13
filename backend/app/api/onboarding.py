"""The first-run checklist, the example designs and the admin's funnel (pipeline/onboarding.py,
pipeline/examples.py, pipeline/funnel.py).

* ``GET /api/onboarding``: the account's four steps, ticked from what it has done.
* ``POST /api/onboarding/dismiss``: close the "You're set up" note (only once every step is done).
* ``POST /api/examples``: the account's example batch (made once; no model call, nothing counted).
* ``GET /api/admin/funnel?days=7|30``: sign-up to first send, for accounts that signed up in the
  window, with the conversion between steps and the median time to a first approval. Admin only
  (404 for everyone else); counts and times only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import require_admin
from app.api.deps import active_tenant, get_ingestor, get_session
from app.db.models import Tenant
from app.pipeline import examples, funnel, onboarding
from app.pipeline.ingest import BatchIngestor

router = APIRouter(prefix="/api", tags=["onboarding"])


class StepOut(BaseModel):
    key: str
    title: str
    body: str
    href: str
    action: str
    done: bool
    done_at: datetime | None
    confirm_profile_id: uuid.UUID | None = None
    second_href: str | None = None
    second_action: str | None = None


class OnboardingOut(BaseModel):
    steps: list[StepOut]
    next: str | None
    all_done: bool
    dismissed: bool
    example_batch_id: uuid.UUID | None


class ExampleOut(BaseModel):
    batch_id: uuid.UUID


class FunnelStepOut(BaseModel):
    name: str
    label: str
    count: int
    from_previous: float | None


class FunnelOut(BaseModel):
    days: int
    cohort: int
    steps: list[FunnelStepOut]
    median_seconds_to_first_approval: float | None
    approved_with_time: int


@router.get("/onboarding", response_model=OnboardingOut)
async def get_onboarding(
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
) -> OnboardingOut:
    found = await onboarding.checklist(session, tenant)
    return OnboardingOut(
        steps=[StepOut(**s.__dict__) for s in found.steps], next=found.next, all_done=found.all_done,
        dismissed=found.dismissed, example_batch_id=found.example_batch_id,
    )


@router.post("/onboarding/dismiss", status_code=204)
async def dismiss(
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
) -> Response:
    found = await onboarding.checklist(session, tenant)
    if not found.all_done:
        raise HTTPException(status_code=409, detail="Finish the steps first; the note closes once they are done.")
    if tenant.setup_note_dismissed_at is None:
        tenant.setup_note_dismissed_at = datetime.now(timezone.utc)
        await session.commit()
    return Response(status_code=204)


@router.post("/examples", response_model=ExampleOut)
async def try_examples(
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    ingestor: BatchIngestor = Depends(get_ingestor),
) -> ExampleOut:
    batch = await examples.create(session, tenant, ingestor)
    return ExampleOut(batch_id=batch.id)


@router.get("/admin/funnel", response_model=FunnelOut)
async def admin_funnel(
    days: int = Query(7),
    session: AsyncSession = Depends(get_session, scope="function"),
    _admin: Tenant = Depends(require_admin),
) -> FunnelOut:
    if days not in (7, 30):
        raise HTTPException(status_code=422, detail="days is 7 or 30")
    found = await funnel.report(session, days)
    median = found.median_to_first_approval
    return FunnelOut(
        days=found.days, cohort=found.cohort,
        steps=[FunnelStepOut(**s.__dict__) for s in found.steps],
        median_seconds_to_first_approval=median.total_seconds() if median is not None else None,
        approved_with_time=found.approved_with_time,
    )
