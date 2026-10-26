"""Turning personalization on for listings already on Etsy (2026-10-26).

A seller's 92 scheduled listings had personalization off (from their profile) while their
titles said "Custom Name …" and "Personalized …". Their drafts were already on Etsy, so
changing the setting in the app reached only drafts made later. These endpoints turn it on
and, for every draft or live listing the listing already has on Etsy, queue one job that
writes it there and reads it back (``workers/personalization_etsy.py``):

* ``POST /api/content/{id}/personalization/etsy``: one listing (the review card, the
  "Turn on personalization" of the mismatch warning, the Scheduled page);
* ``POST /api/batches/{id}/personalization/etsy``: "Turn on personalization for these N
  listings" on the review page;
* ``POST /api/profiles/{id}/personalization/etsy``: "Also apply to N existing drafts of this
  profile", offered after personalization is turned on in the profile.

Which question (``use``): "profile" is the profile's own setting when it is on, else its
reference listing's question (copied to the listing; the profile is not changed); "listing"
is a question written for the listing. Etsy's rules are checked before anything is saved or
sent (a question Etsy would refuse: 422, and the card shows the one-click fix). Nothing is
applied that the seller did not press; nothing is published.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import schemas
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session
from app.core import audit
from app.db.models import (
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    ListingProfile,
    ListingPublication,
    Tenant,
    UploadBatch,
)
from app.pipeline import personalization as P
from app.workers import personalization_etsy as W

router = APIRouter(prefix="/api", tags=["personalization"])


class TurnOnIn(BaseModel):
    #: "profile": the profile's question (or its reference listing's); "listing": ``personalization``.
    use: Literal["profile", "listing"] = "profile"
    personalization: dict[str, Any] | None = None


class BulkTurnOnIn(TurnOnIn):
    content_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class Skipped(BaseModel):
    content_id: uuid.UUID
    title: str
    reason: str


class TurnOnOut(BaseModel):
    content: schemas.ContentOut
    #: Listings already on Etsy (drafts and live ones) it is being written to.
    on_etsy: int = 0
    message: str


class BulkTurnOnOut(BaseModel):
    contents: list[schemas.ContentOut]
    turned_on: int = 0
    on_etsy: int = 0
    skipped: list[Skipped] = Field(default_factory=list)
    message: str


class ProfileApplyOut(BaseModel):
    profile: schemas.ProfileOut
    on_etsy: int = 0
    message: str


class Refused(Exception):
    """Why this listing's personalization cannot be turned on as asked (the seller's words)."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _listing_setting(value: dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        raise Refused("Write the question buyers answer first.")
    value = {**value, "enabled": True}
    if not str(value.get("question_text") or "").strip():
        value["question_text"] = P.DEFAULT_QUESTION
    problems = P.etsy_problems(value)
    if problems:
        raise Refused(f"Etsy would refuse it: {'; '.join(problems)}.")
    return P.validate(value)


async def on_etsy(session: AsyncSession, content_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[ListingPublication]]:
    """Each listing's drafts and live listings on Etsy, in shops still connected."""
    found: dict[uuid.UUID, list[ListingPublication]] = {i: [] for i in content_ids}
    if not content_ids:
        return found
    rows = await session.execute(
        select(ListingPublication)
        .join(EtsyConnection, EtsyConnection.id == ListingPublication.connection_id)
        .where(ListingPublication.content_id.in_(content_ids),
               ListingPublication.state.in_(("draft", "active")),
               EtsyConnection.status == ConnectionStatus.active)
    )
    for publication in rows.scalars():
        found[publication.content_id].append(publication)
    return found


async def turn_on(
    session: AsyncSession, tenant: Tenant, content: GeneratedContent, use: str, value: dict[str, Any] | None,
) -> dict[str, Any]:
    """Turn the listing's personalization on as asked; the setting it now has. Raises Refused.
    Does not commit."""
    profile = await session.get(ListingProfile, content.listing_profile_id) if content.listing_profile_id else None
    if use == "listing":
        content.personalization = _listing_setting(value)
    else:
        question, where = P.profile_question(profile.personalization if profile else None,
                                             profile.cached_payload if profile else None)
        if question is None:
            raise Refused("Its profile has no personalization question to use; write one for this listing.", 409)
        problems = P.etsy_problems(question)
        if problems:
            raise Refused(f"Its profile's question breaks Etsy's rules ({'; '.join(problems)}). "
                          "Use the suggested text on the profile first.")
        # The profile's own setting: the listing follows it. Its reference's question (the
        # profile has it off): copied to this listing only; the profile is not changed.
        content.personalization = None if where == "profile" else P.validate(question)
    setting, _ = P.for_listing(content.personalization, profile.personalization if profile else None,
                               profile.cached_payload if profile else None)
    assert setting is not None and setting.get("enabled")
    return setting


async def queue_writes(publications: list[ListingPublication], enqueuer: Enqueuer) -> int:
    """One write job per listing on Etsy; each marked queued. The caller commits first."""
    for publication in publications:
        await enqueuer.enqueue("apply_personalization_on_etsy", W.spec_for(publication.id))
    return len(publications)


def _line(on: int, publications: list[ListingPublication]) -> str:
    live = sum(1 for p in publications if p.state == "active")
    drafts = len(publications) - live
    if not publications:
        head = "Personalization is on." if on == 1 else f"Personalization is on for {on} listings."
        return head + " Drafts made from now on get it."
    parts = [f"{drafts} draft{'' if drafts == 1 else 's'}"] if drafts else []
    if live:
        parts.append(f"{live} live listing{'' if live == 1 else 's'}")
    head = "Personalization is on." if on == 1 else f"Personalization is on for {on} listings."
    return (f"{head} Writing it to {' and '.join(parts)} already on Etsy; each is read back and checked, "
            "and the card says when it is done.")


async def _content(session: AsyncSession, tenant: Tenant, content_id: uuid.UUID) -> GeneratedContent:
    content = await session.get(GeneratedContent, content_id)
    if content is None or content.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="content not found")
    return content


@router.post("/content/{content_id}/personalization/etsy", response_model=TurnOnOut)
async def turn_on_for_listing(
    content_id: uuid.UUID,
    body: TurnOnIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> TurnOnOut:
    """Turn this listing's personalization on, and write it to its drafts and live listings on Etsy."""
    from app.api.content import contents_out
    from app.api.personalization_fix import requeue_held
    from app.db.models import Asset

    content = await _content(session, tenant, content_id)
    try:
        await turn_on(session, tenant, content, body.use, body.personalization)
    except Refused as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    pubs = (await on_etsy(session, [content.id]))[content.id]
    for publication in pubs:
        W.mark(publication, W.QUEUED)
    audit.record(session, "listing.personalization_turned_on", actor=tenant, target_tenant_id=tenant.id,
                 content_id=str(content.id), use=body.use, on_etsy=len(pubs))
    await session.commit()
    await queue_writes(pubs, enqueuer)
    await requeue_held(session, tenant, [content], enqueuer)  # a held draft goes ahead with it
    await session.refresh(content)
    asset = await session.get(Asset, content.asset_id)
    return TurnOnOut(content=(await contents_out(session, [(content, asset)]))[0], on_etsy=len(pubs),
                     message=_line(1, pubs))


@router.post("/batches/{batch_id}/personalization/etsy", response_model=BulkTurnOnOut)
async def turn_on_for_listings(
    batch_id: uuid.UUID,
    body: BulkTurnOnIn,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> BulkTurnOnOut:
    """"Turn on personalization for these N listings": each one as asked, written to what it
    already has on Etsy; a listing that cannot be turned on is skipped with its reason."""
    from app.api.content import list_batch_content
    from app.api.personalization_fix import requeue_held

    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="batch not found")
    contents = list((await session.execute(select(GeneratedContent).where(
        GeneratedContent.batch_id == batch_id, GeneratedContent.tenant_id == tenant.id,
        GeneratedContent.id.in_(body.content_ids)))).scalars())
    if len(contents) != len(set(body.content_ids)):
        raise HTTPException(status_code=404, detail="content not found")
    done: list[GeneratedContent] = []
    skipped: list[Skipped] = []
    for content in contents:
        try:
            async with session.begin_nested():
                await turn_on(session, tenant, content, body.use, body.personalization)
            done.append(content)
        except Refused as exc:
            skipped.append(Skipped(content_id=content.id, title=content.title or "Untitled", reason=str(exc)))
    pubs = [p for ps in (await on_etsy(session, [c.id for c in done])).values() for p in ps]
    for publication in pubs:
        W.mark(publication, W.QUEUED)
    audit.record(session, "listing.personalization_turned_on", actor=tenant, target_tenant_id=tenant.id,
                 batch_id=str(batch_id), listings=len(done), use=body.use, on_etsy=len(pubs), skipped=len(skipped))
    await session.commit()
    await queue_writes(pubs, enqueuer)
    if done:
        await requeue_held(session, tenant, done, enqueuer)
    message = _line(len(done), pubs) if done else "Nothing was turned on."
    if skipped:
        message += f" {len(skipped)} skipped (the reason is beside each)."
    return BulkTurnOnOut(contents=await list_batch_content(batch_id, session, tenant), turned_on=len(done),
                         on_etsy=len(pubs), skipped=skipped, message=message)


async def profile_drafts(session: AsyncSession, profile: ListingProfile) -> list[ListingPublication]:
    """The drafts and live listings on Etsy of the listings that follow this profile."""
    from app.api.personalization_fix import following

    ids = [c.id for c in await following(session, profile)]
    return [p for ps in (await on_etsy(session, ids)).values() for p in ps]


@router.post("/profiles/{profile_id}/personalization/etsy", response_model=ProfileApplyOut)
async def apply_to_profile_drafts(
    profile_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> ProfileApplyOut:
    """"Also apply to N existing drafts of this profile": the profile's personalization written to
    the drafts and live listings already on Etsy of every listing that follows it."""
    from app.api.profiles import _out as profile_out

    profile = await session.get(ListingProfile, profile_id)
    if profile is None or profile.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="profile not found")
    setting = P.effective(profile.personalization, profile.cached_payload)
    if not setting or not setting.get("enabled"):
        raise HTTPException(status_code=409, detail="Personalization is off on this profile: turn it on first.")
    problems = P.etsy_problems(setting)
    if problems:
        raise HTTPException(status_code=422, detail=f"Etsy would refuse it: {'; '.join(problems)}. "
                            "Use the suggested text first.")
    pubs = await profile_drafts(session, profile)
    for publication in pubs:
        W.mark(publication, W.QUEUED)
    audit.record(session, "profile.personalization_applied_on_etsy", actor=tenant, target_tenant_id=tenant.id,
                 profile_id=str(profile.id), on_etsy=len(pubs))
    await session.commit()
    await queue_writes(pubs, enqueuer)
    if pubs:
        live = sum(1 for p in pubs if p.state == "active")
        message = (f"Writing it to {len(pubs)} listing{'' if len(pubs) == 1 else 's'} already on Etsy"
                   + (f" ({live} live)" if live else "") + "; each is read back and checked on its card.")
    else:
        message = "None of this profile's listings has a draft on Etsy yet."
    await session.refresh(profile)
    return ProfileApplyOut(profile=await profile_out(session, tenant, profile), on_etsy=len(pubs), message=message)
