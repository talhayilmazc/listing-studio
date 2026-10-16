"""Half-made drafts: listings created on Etsy whose draft did not finish.

A draft is some fifteen Etsy requests. ``createDraftListing`` comes first, and its
listing id is kept on the :class:`DraftAttempt` from the moment Etsy returns it
(etsy/publisher.py). When a later step is refused (the inventory, a property, an
image), the listing stays on Etsy as a draft with only part of its settings, and the
attempt stays too. Finishing it runs the same draft job again: it reads that listing
back and carries on with it (the settings again, the missing images), so no second
listing is ever created, and nothing is ever deleted on Etsy.

Everything here is read from our own database (no Etsy call): the seller's
"Finish these drafts" list (api/unfinished.py) and the operator's read-only
``python -m app.cli profile-shop-check`` both use it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ConnectionStatus,
    DraftAttempt,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
)
from app.pipeline import links as L
from app.pipeline import profile_shops

#: The step a job's error names ("... while setting sizes, prices and SKU ...", etsy/errors.py).
_STEP = re.compile(r"\bwhile ([A-Za-z ,'-]+?)(?:[:;()]|$)")
#: What explain() said about a refused inventory update before it named the cause:
#: Etsy answers 404 when the inventory carries another shop's processing profile.
_OLD_INVENTORY_404 = "could not find the listing while setting sizes, prices and SKU"


@dataclass
class HalfMade:
    attempt_id: uuid.UUID
    content_id: uuid.UUID
    batch_id: uuid.UUID | None
    connection_id: uuid.UUID
    shop_name: str
    etsy_listing_id: int
    title: str
    #: The step that failed and what the seller is told about it.
    step: str | None = None
    error: str | None = None
    failed_at: datetime | None = None
    #: A job is already finishing it.
    finishing: bool = False
    #: Why it cannot be finished yet (a setting this shop lacks), and whether one click sets it up.
    blocked: str | None = None
    profile_id: uuid.UUID | None = None
    profile_name: str | None = None
    setup: bool = False
    #: The profile the last try used (kept for the next one).
    job_profile_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def step_of(error: str | None) -> str | None:
    match = _STEP.search(error or "")
    return match.group(1).strip() if match else None


def plain_error(error: str | None, shop: str) -> str | None:
    """The stored error, in words the seller can act on. Errors stored before the
    cause was named said "Etsy could not find the listing" for a refused inventory:
    the listing is there, the processing profile was not."""
    if error and _OLD_INVENTORY_404 in error.lower():
        return (f"Etsy refused this draft's sizes, prices and SKU: they carried another shop's processing "
                f"profile, which {shop} does not have. Finishing sets them with {shop}'s own.")
    return error


async def half_made(
    session: AsyncSession, tenant_id: uuid.UUID, *, batch_id: uuid.UUID | None = None, with_blocks: bool = True,
) -> list[HalfMade]:
    """The account's drafts that exist on Etsy but did not finish, oldest first.
    ``with_blocks``: also say whether each can be finished now (the shop's setup)."""
    from app.pipeline.targets import resolve_target  # late: targets imports a lot

    rows = (await session.execute(
        select(DraftAttempt, EtsyConnection, GeneratedContent)
        .join(EtsyConnection, EtsyConnection.id == DraftAttempt.connection_id)
        .join(GeneratedContent, GeneratedContent.id == DraftAttempt.content_id)
        .where(DraftAttempt.tenant_id == tenant_id, DraftAttempt.etsy_listing_id.is_not(None),
               EtsyConnection.status == ConnectionStatus.active)
        .order_by(DraftAttempt.created_at)
    )).all()
    if batch_id is not None:
        rows = [r for r in rows if r[2].batch_id == batch_id]
    if not rows:
        return []
    done = set((await session.execute(
        select(ListingPublication.content_id, ListingPublication.connection_id).where(ListingPublication.tenant_id == tenant_id)
    )).all())
    failed: dict[tuple[uuid.UUID, str], Job] = {}  # the latest failure says what went wrong
    active: dict[tuple[uuid.UUID, str], Job] = {}  # a job finishing it now
    for job in (await session.execute(
        select(Job).where(Job.tenant_id == tenant_id, Job.type == JobType.create_draft).order_by(Job.created_at)
    )).scalars():
        key = (job.connection_id, str((job.payload or {}).get("content_id")))
        if job.status is JobStatus.failed:
            failed[key] = job
        elif job.status in (JobStatus.queued, JobStatus.running):
            active[key] = job
    out: list[HalfMade] = []
    for attempt, shop, content in rows:
        if (content.id, shop.id) in done:
            continue  # finished since (the attempt goes with it; a leftover is not unfinished)
        name = shop.shop_name or f"shop {shop.shop_id}"
        key = (shop.id, str(content.id))
        item = HalfMade(
            attempt_id=attempt.id, content_id=content.id, batch_id=content.batch_id, connection_id=shop.id,
            shop_name=name, etsy_listing_id=int(attempt.etsy_listing_id), title=attempt.title or content.title or "",
            finishing=key in active,
        )
        job = failed.get(key)
        if job is not None:
            item.job_profile_id = (job.payload or {}).get("profile_id")
            item.error = plain_error(job.last_error, name)
            item.step = step_of(job.last_error)
            item.failed_at = job.finished_at
        if with_blocks:
            target = await resolve_target(session, content, shop)
            if not target.ok and not target.stale:  # a stale profile is refreshed by the job itself
                state = profile_shops.state_of(target.profile, target.link) if target.profile and target.link else None
                if state is not None and state.missing:
                    item.blocked = L.setup_message(next(iter(state.missing)), name)
                else:
                    item.blocked = target.reason or "no profile for this shop"
                item.setup = bool(target.setup or (state is not None and state.missing))
                if target.profile is not None:
                    item.profile_id, item.profile_name = target.profile.id, target.profile.name
        out.append(item)
    return out
