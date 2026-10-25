"""One-click fix for personalization text Etsy would refuse (2026-10-25).

A personalization question with instructions over 120 characters, or more than one word in
capitals, is never sent: the listing's draft waits, held (workers/publish.py), and the card
says why. This module is the fix:

* what is wrong, per field, with the caps already fixed (``hold_out``; on every card and profile);
* a shorter version by the content model, beside the original (``POST /api/personalization/shorten``):
  nothing changes until the seller presses "Use this";
* "Use this" on a profile (``POST /api/profiles/{id}/personalization/fix``) or a listing that has
  its own setting (``POST /api/content/{id}/personalization/fix``): the text is saved, checked
  against Etsy's rules again, and every held listing it reaches is queued again
  (``requeue_held``). Saving a valid personalization the ordinary way does the same.

Re-queueing carries on only what the seller had already asked for, for approved listings: a
held draft goes ahead; a group schedule whose draft was held drafts and goes live at the next
time the shop's rules allow. Nothing that was not approved is drafted or published.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_redis, get_session
from app.core import audit
from app.db.models import (
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingPublication,
    PlannedSlot,
    Tenant,
)
from app.pipeline import personalization as P
from app.pipeline import personalization_fix as F
from app.workers.recovery import WAIT_PERSONALIZATION

router = APIRouter(prefix="/api", tags=["personalization"])


def hold_out(setting: dict[str, Any] | None, source: str, profile: ListingProfile | None) -> schemas.PersonalizationHoldOut | None:
    """What a card or profile shows when this setting would be refused by Etsy; None when it can go."""
    reason = P.hold_line(setting)
    if reason is None:
        return None
    return schemas.PersonalizationHoldOut(
        reason=reason,
        source="listing" if source == "listing" else "profile",
        profile_id=profile.id if profile is not None and source != "listing" else None,
        profile_name=profile.name if profile is not None and source != "listing" else None,
        fields=[schemas.PersonalizationFieldOut(
            field=f.field, text=f.text, length=len(f.text), limit=f.limit, extra_caps=f.extra_caps,
            caps_fix=f.caps_fix, needs_shortening=f.needs_shortening,
        ) for f in P.field_problems(setting)],
    )


class ShortenIn(BaseModel):
    field: str = Field(default="instructions", pattern="^(instructions|question)$")
    text: str = Field(min_length=1, max_length=2000)


class ShortenOut(BaseModel):
    #: The offer (within Etsy's limit, capitals fixed), or None with ``message``.
    text: str | None = None
    message: str | None = None


@router.post("/personalization/shorten", response_model=ShortenOut)
async def shorten(
    body: ShortenIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    redis: Redis = Depends(get_redis),
) -> ShortenOut:
    """A version of the text within Etsy's limit, with the same meaning, by the content model.
    Only an offer: nothing is saved. Kept a week by the text's hash (one call per text)."""
    from app.core import ai_meter, llm_status
    from app.core.config import get_settings
    from app.core.llm_status import LLMUnavailable
    from app.pipeline.llm import client_for

    text = " ".join(body.text.split())
    fits = F.acceptable(text, body.field)
    if fits is not None:
        return ShortenOut(text=fits)  # within the limit already once the capitals are fixed: no model call
    key = F.cache_key(body.field, text)
    try:
        cached = await redis.get(key)
    except Exception:  # noqa: BLE001 - the cache is a saving, never a requirement
        cached = None
    if cached:
        return ShortenOut(text=cached.decode() if isinstance(cached, bytes) else str(cached))
    if await llm_status.current(redis):
        return ShortenOut(message=llm_status.SELLER_MESSAGE)
    try:
        async with ai_meter.scope(tenant.id, session):
            offer = await F.shorten(client_for(get_settings(), "content"), text, body.field)
    except LLMUnavailable as exc:
        await llm_status.report(redis, exc)
        return ShortenOut(message=llm_status.SELLER_MESSAGE)
    except Exception:  # noqa: BLE001 - an offer that could not be made is said, never a crash
        offer = None
    if offer is None:
        return ShortenOut(message=f"A shorter version could not be written. Edit it to {F.limit_for(body.field)} "
                                  "characters or fewer, and keep one word at most in capitals.")
    try:
        await redis.set(key, offer, ex=F.CACHE_SECONDS)
    except Exception:  # noqa: BLE001
        pass
    return ShortenOut(text=offer)


class FixIn(BaseModel):
    #: The new text for each field it changes (the rest of the setting stays as it is).
    question_text: str | None = Field(default=None, max_length=200)
    instructions: str | None = Field(default=None, max_length=2000)


class RequeuedOut(BaseModel):
    drafts: int = 0
    schedules: int = 0
    listings: list[uuid.UUID] = Field(default_factory=list)

    def line(self) -> str | None:
        if not self.listings:
            return None
        n = len(self.listings)
        return f"{n} held listing{'' if n == 1 else 's'} going ahead now."


class ProfileFixOut(BaseModel):
    profile: schemas.ProfileOut
    requeued: RequeuedOut
    message: str | None = None


class ContentFixOut(BaseModel):
    content: schemas.ContentOut
    requeued: RequeuedOut
    message: str | None = None


def _fixed(setting: dict[str, Any] | None, body: FixIn) -> dict[str, Any]:
    """The setting with the new text, checked against Etsy's rules (422 with why when not)."""
    base = dict(setting or {})
    if not base.get("enabled"):
        raise HTTPException(status_code=409, detail="Personalization is off here: there is nothing to fix.")
    if body.question_text is not None:
        base["question_text"] = body.question_text
    if body.instructions is not None:
        base["instructions"] = body.instructions
    try:
        return P.validate({**base, "enabled": True})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Etsy would still refuse it: {exc}.") from exc


async def _own_profile(session: AsyncSession, tenant: Tenant, profile_id: uuid.UUID) -> ListingProfile:
    profile = await session.get(ListingProfile, profile_id)
    if profile is None or profile.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="profile not found")
    return profile


async def following(session: AsyncSession, profile: ListingProfile) -> list[GeneratedContent]:
    """The listings whose drafts get this profile's personalization (no setting of their own)."""
    return list((await session.execute(select(GeneratedContent).where(
        GeneratedContent.tenant_id == profile.tenant_id,
        GeneratedContent.listing_profile_id == profile.id,
        GeneratedContent.personalization.is_(None),
    ))).scalars())


@router.post("/profiles/{profile_id}/personalization/fix", response_model=ProfileFixOut)
async def fix_profile(
    profile_id: uuid.UUID,
    body: FixIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> ProfileFixOut:
    """"Use this" on the profile: its personalization gets the new text (the seller's own setting
    from now on), and every held listing that follows it goes ahead."""
    from app.api.profiles import _out as profile_out

    profile = await _own_profile(session, tenant, profile_id)
    setting = P.effective(profile.personalization, profile.cached_payload)
    profile.personalization = _fixed(setting, body)
    audit.record(session, "profile.personalization_fixed", actor=tenant, target_tenant_id=tenant.id,
                 profile_id=str(profile.id))
    await session.flush()
    requeued = await requeue_held(session, tenant, await following(session, profile), enqueuer)
    await session.refresh(profile)
    return ProfileFixOut(profile=await profile_out(session, tenant, profile), requeued=requeued, message=requeued.line())


@router.post("/content/{content_id}/personalization/fix", response_model=ContentFixOut)
async def fix_content(
    content_id: uuid.UUID,
    body: FixIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> ContentFixOut:
    """"Use this" on a listing card: a listing with its own setting gets the new text; one that
    follows its profile changes the profile (every listing that follows it goes ahead too)."""
    from app.api.content import contents_out
    from app.compliance.check import personalization_setting
    from app.db.models import Asset

    content = await session.get(GeneratedContent, content_id)
    if content is None or content.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="content not found")
    setting, source = await personalization_setting(session, content)
    if source == "listing":
        content.personalization = _fixed(setting, body)
        audit.record(session, "listing.personalization_fixed", actor=tenant, target_tenant_id=tenant.id,
                     content_id=str(content.id))
        await session.flush()
        requeued = await requeue_held(session, tenant, [content], enqueuer)
    else:
        profile = await _own_profile(session, tenant, content.listing_profile_id) if content.listing_profile_id else None
        if profile is None:
            raise HTTPException(status_code=409, detail="This listing has no profile to fix.")
        profile.personalization = _fixed(setting, body)
        audit.record(session, "profile.personalization_fixed", actor=tenant, target_tenant_id=tenant.id,
                     profile_id=str(profile.id))
        await session.flush()
        requeued = await requeue_held(session, tenant, await following(session, profile), enqueuer)
    await session.refresh(content)
    asset = await session.get(Asset, content.asset_id)
    return ContentFixOut(content=(await contents_out(session, [(content, asset)]))[0], requeued=requeued,
                         message=requeued.line())


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def requeue_held(
    session: AsyncSession, tenant: Tenant, contents: list[GeneratedContent], enqueuer: Enqueuer
) -> RequeuedOut:
    """Carry on what was held for these listings' personalization, now that it can be sent.
    Commits. Only approved listings; only what the seller had asked for:

    * a draft job held for it (queued, ``personalization_held``) runs again;
    * a draft that failed for it before the hold existed (the draft step's own refusal) is
      queued again, unless the listing has a draft in that shop or a newer draft job;
    * a group schedule that failed for that reason waits again and drafts at once; its go-live
      (and that of a held draft's schedule) moves to the next time the shop's rules allow.
    """
    from app.compliance.check import personalization_hold

    out = RequeuedOut()
    now = datetime.now(timezone.utc)
    ready = [c for c in contents if c.tenant_id == tenant.id and c.approved and not await personalization_hold(session, c)]
    if not ready:
        await session.commit()
        return out
    ids = {str(c.id): c for c in ready}
    to_run: list[uuid.UUID] = []
    jobs = list((await session.execute(select(Job).where(
        Job.tenant_id == tenant.id, Job.type == JobType.create_draft,
        Job.status.in_((JobStatus.queued, JobStatus.failed, JobStatus.running, JobStatus.succeeded)),
    ).order_by(Job.created_at))).scalars())
    newest: dict[tuple[str, Any], Job] = {}
    for job in jobs:
        cid = (job.payload or {}).get("content_id")
        if cid in ids:
            newest[(cid, job.connection_id)] = job
    drafts = {(str(c), s) for c, s in (await session.execute(select(
        ListingPublication.content_id, ListingPublication.connection_id).where(
        ListingPublication.content_id.in_([c.id for c in ready]),
        ListingPublication.state != "deleted_on_etsy"))).all()}
    touched: set[str] = set()
    held_job_ids: set[uuid.UUID] = set()
    for (cid, shop_id), job in newest.items():
        if (cid, shop_id) in drafts:
            continue
        if job.status is JobStatus.queued and job.paused_reason == WAIT_PERSONALIZATION:
            job.paused_reason, job.last_error, job.scheduled_at = None, None, now
            to_run.append(job.id)
            held_job_ids.add(job.id)
            touched.add(cid)
        elif job.status is JobStatus.failed and P.is_held(job.last_error) and not (job.payload or {}).get("planned_slot"):
            again = Job(tenant_id=tenant.id, connection_id=shop_id, type=JobType.create_draft, batch_id=job.batch_id,
                        payload={k: v for k, v in (job.payload or {}).items() if k not in ("etsy_error", "left_out")},
                        status=JobStatus.queued)
            session.add(again)
            await session.flush()
            to_run.append(again.id)
            touched.add(cid)
    out.drafts = len(to_run)
    # Group schedules: one failed for this reason waits again (drafted at once); one whose draft was held
    # keeps following its job. Either goes live at the next time its shop's rules allow.
    slots = list((await session.execute(select(PlannedSlot).where(
        PlannedSlot.tenant_id == tenant.id, PlannedSlot.content_id.in_([c.id for c in ready]),
        PlannedSlot.state.in_(("failed", "drafting"))))).scalars())
    moved: list[PlannedSlot] = []
    for slot in slots:
        if slot.state == "failed" and P.is_held(slot.note):
            slot.state, slot.note, slot.job_id, slot.draft_at = "waiting", None, None, now
            moved.append(slot)
        elif slot.state == "drafting" and slot.job_id in held_job_ids:
            moved.append(slot)
    for slot in moved:
        touched.add(str(slot.content_id))
    out.schedules = len(moved)
    await session.flush()
    await _next_allowed(session, tenant, moved, now)
    out.listings = [ids[c].id for c in ids if c in touched]
    await session.commit()
    for job_id in to_run:
        await enqueuer.enqueue("run_publish_job", str(job_id))
    return out


async def _next_allowed(session: AsyncSession, tenant: Tenant, slots: list[PlannedSlot], now: datetime) -> None:
    """Slots whose time passed (or is about to): the next time each shop's rules allow
    (spacing, the day's limit, the same design not in the same minute; pipeline/reschedule.py)."""
    from app.api import scheduled as S
    from app.pipeline import reschedule as R

    late = [s for s in slots if _aware(s.publish_at) < now + R.LEAD]
    if not late:
        return
    world = await S._world(session, tenant)
    items = S._items(world)
    keys = {i.key for i in items}
    desired = {f"slot:{s.id}": now for s in late if f"slot:{s.id}" in keys}
    by_key = {f"slot:{s.id}": s for s in late}
    placed = {c.key: c.new for c in R.settle(items, desired, world.rules, now)} if desired else {}
    for key, slot in by_key.items():
        at = placed.get(key) or (now + R.LEAD)
        slot.publish_at = at
        slot.draft_at = min(_aware(slot.draft_at), now)
