"""Our records follow what the seller did on Etsy (production report, 2026-10-26).

A seller published 25 of the app's drafts by hand in Shop Manager and deleted 3 more; the
app still showed them as drafts with a pending go-live. Each record is now checked against
the listing's real state on Etsy, read only:

* before every go-live (``publisher.publish_live``: one getListing, through the rate limiter);
* by upkeep, for the app's drafts and scheduled listings, at most once every
  :data:`CHECK_EVERY` per listing (``workers/reconcile.py``);
* by the operator (``python -m app.cli reconcile-etsy <email> [--apply]``).

What a state means for our record (:func:`decide`):

* **active** while we hold a draft: the seller published it on Etsy. The record says live,
  "published in Etsy by the seller" (``published_by``), its schedule and open group-schedule
  slot close, and nothing is sent.
* **gone** (404/410): the seller deleted it. The record is marked "deleted on Etsy" (never
  deleted; ``publisher.mark_deleted_on_etsy``), its schedule and open slot close, and the card
  and the Scheduled page say so.
* **inactive, expired, sold out** while we hold a draft: it went live on Etsy and then changed
  there. Nothing is published over the seller's choice: the schedule is withdrawn, saying why.
* **draft**: as recorded; nothing changes.

Nothing here ever writes to Etsy.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.db.models import Job, JobStatus, ListingPublication, PlannedSlot, Tenant
from app.etsy.errors import EtsyClientError, EtsyServerError
from app.pipeline import versions

logger = logging.getLogger(__name__)

#: How often upkeep reads one listing's state at most.
CHECK_EVERY = timedelta(hours=6)
#: ``listing_publication.published_by`` for a draft the seller published in Shop Manager.
SELLER_ON_ETSY = "seller_on_etsy"
#: What Etsy's listing state is called when it has no such listing any more.
GONE = "gone"
#: Etsy states that mean the listing went live and then changed on Etsy.
CHANGED = frozenset({"inactive", "expired", "sold_out"})

PUBLISHED = "published in Etsy by the seller"
DELETED = "deleted on Etsy"
WITHDRAWN = "changed on Etsy"

PUBLISHED_NOTE = "Published in Etsy by the seller (in Shop Manager); nothing was sent."
DELETED_NOTE = "Deleted on Etsy (in Shop Manager); nothing was published."
#: The publication's schedule note when its draft was deleted on Etsy (the Scheduled page reads it).
DELETED_MARK = "the draft was deleted on Etsy"


def changed_note(state: str) -> str:
    word = state.replace("_", " ")
    return (f"Not published: it is {word} on Etsy (changed in Shop Manager), so nothing was published over it. "
            "Publish it in Shop Manager if you want it live.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def state_of(listing: dict[str, Any] | None) -> str:
    """Etsy's word for a listing ("draft", "active", ...), or :data:`GONE`."""
    if listing is None:
        return GONE
    return str(listing.get("state") or "unknown")


def decide(publication: ListingPublication, etsy_state: str) -> str | None:
    """What reconciling would change for this record, or None (as recorded, or unknown)."""
    if publication.state == "deleted_on_etsy":
        return None
    if etsy_state == GONE:
        return DELETED
    if publication.state == "active":
        return None
    if etsy_state == "active":
        return PUBLISHED
    if etsy_state in CHANGED and (publication.scheduled_for is not None and publication.schedule_note is None):
        return WITHDRAWN
    return None


def _went_live_at(listing: dict[str, Any] | None, now: datetime) -> datetime:
    """When Etsy says the state last changed (``state_timestamp``), else now."""
    stamp = (listing or {}).get("state_timestamp")
    try:
        at = datetime.fromtimestamp(int(stamp), tz=timezone.utc) if stamp else None
    except (TypeError, ValueError, OverflowError, OSError):
        at = None
    return at if at is not None and at <= now else now


async def open_slots(session: AsyncSession, content_id: uuid.UUID | None, connection_id: uuid.UUID) -> list[PlannedSlot]:
    """The listing's group-schedule slots in this shop that would still make a draft."""
    if content_id is None:
        return []
    return list((await session.execute(select(PlannedSlot).where(
        PlannedSlot.content_id == content_id, PlannedSlot.connection_id == connection_id,
        PlannedSlot.state.in_(("waiting", "drafting")),
    ))).scalars())


async def _cancel_schedule_job(session: AsyncSession, publication: ListingPublication, *, job_id: uuid.UUID | None,
                               why: str) -> None:
    job = await session.get(Job, publication.schedule_job_id) if publication.schedule_job_id else None
    if job is not None and job.status is JobStatus.queued and job.id != job_id:
        job.status, job.finished_at, job.last_error, job.paused_reason = JobStatus.cancelled, _now(), why, None


async def mark_published_by_seller(
    session: AsyncSession, publication: ListingPublication, listing: dict[str, Any] | None, *,
    job_id: uuid.UUID | None = None, now: datetime | None = None,
) -> None:
    """The seller published this draft on Etsy: the record says so; nothing is sent. Does not commit."""
    now = now or _now()
    await _cancel_schedule_job(session, publication, job_id=job_id,
                               why="cancelled: already published in Etsy by the seller")
    publication.state = "active"
    publication.published_at = _went_live_at(listing, now)
    publication.published_by = SELLER_ON_ETSY
    publication.schedule_job_id = None
    publication.schedule_note = None
    for slot in await open_slots(session, publication.content_id, publication.connection_id):
        slot.state, slot.note = "published_on_etsy", PUBLISHED_NOTE
    await versions.went_live(session, publication, publication.published_at)
    audit.record(session, "publication.published_on_etsy", actor=None, target_tenant_id=publication.tenant_id,
                 by="system", shop_id=str(publication.connection_id), object_id=str(publication.id),
                 etsy_listing_id=publication.etsy_listing_id)


async def withdraw_changed(session: AsyncSession, publication: ListingPublication, etsy_state: str, *,
                           job_id: uuid.UUID | None = None) -> None:
    """Live once and changed on Etsy since: its go-live is not released. Does not commit."""
    note = changed_note(etsy_state)
    await _cancel_schedule_job(session, publication, job_id=job_id, why="cancelled: " + note)
    publication.schedule_job_id = None
    publication.schedule_note = note
    for slot in await open_slots(session, publication.content_id, publication.connection_id):
        slot.state, slot.note = "cancelled", note


async def apply(
    session: AsyncSession,
    publication: ListingPublication,
    etsy_state: str,
    listing: dict[str, Any] | None = None,
    *,
    job_id: uuid.UUID | None = None,
    actor: Tenant | None = None,
    now: datetime | None = None,
) -> str | None:
    """Make our record follow Etsy's state; returns what changed (or None). Does not commit.
    Only our own rows change: nothing is written to Etsy."""
    from app.etsy.publisher import mark_deleted_on_etsy

    now = now or _now()
    if etsy_state != "unknown":
        publication.etsy_checked_at = now
    change = decide(publication, etsy_state)
    if change == PUBLISHED:
        await mark_published_by_seller(session, publication, listing, job_id=job_id, now=now)
    elif change == DELETED:
        await mark_deleted_on_etsy(session, publication, actor=actor, job_id=job_id)
    elif change == WITHDRAWN:
        await withdraw_changed(session, publication, etsy_state, job_id=job_id)
    return change


@dataclass
class Reading:
    """What Etsy said about the listings asked for, and what it cost."""

    states: dict[int, str]
    listings: dict[int, dict[str, Any]]
    requests: int = 0
    #: Listings that could not be read this time (no answer, Etsy's error): left as they are.
    unread: list[int] | None = None


async def read_states(
    client: Any, listing_ids: Iterable[int], kw: dict[str, Any], *, singles: int = 50,
) -> Reading:
    """Each listing's state on Etsy, read only. getListingsByListingIds first (100 per request);
    a listing it does not return is read alone (getListing) before it counts as gone, at most
    ``singles`` of them per call (the rest stay unread until the next one)."""
    wanted = list(dict.fromkeys(int(i) for i in listing_ids))
    out = Reading(states={}, listings={}, unread=[])
    for start in range(0, len(wanted), 100):
        chunk = wanted[start:start + 100]
        try:
            resp = await client.get_listings_by_listing_ids(chunk, **kw)
        except (httpx.TransportError, EtsyServerError, EtsyClientError):
            out.requests += 1
            continue  # each is read alone below
        out.requests += 1
        for row in resp.get("results") or []:
            try:
                lid = int(row.get("listing_id"))
            except (TypeError, ValueError):
                continue
            if lid in chunk:
                out.states[lid], out.listings[lid] = state_of(row), row
    for lid in wanted:
        if lid in out.states:
            continue
        if singles <= 0:
            out.unread.append(lid)
            continue
        singles -= 1
        out.requests += 1
        try:
            row = await client.get_listing(lid, **kw)
        except EtsyClientError as exc:
            if exc.status_code in (404, 410):
                out.states[lid] = GONE
            else:
                out.unread.append(lid)
            continue
        except (httpx.TransportError, EtsyServerError):
            out.unread.append(lid)
            continue
        out.states[lid], out.listings[lid] = state_of(row), row
    return out
