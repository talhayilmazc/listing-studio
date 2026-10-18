"""Every status a batch is in, for the Batches page's filter (seller feedback D1; frontend
lib/batchStatus.ts has the same keys and labels).

A batch can be in several at once (some listings drafted, one published, one half-made):

* ``uploading``    its files are still being uploaded or processed (a mockup batch with its designs in is not);
* ``not_written``  its photos are in, no listing is written yet;
* ``in_review``    a written listing has no draft on Etsy yet;
* ``drafted``      a listing has a draft on Etsy (not scheduled);
* ``scheduled``    a draft is scheduled to go live, or a group plan will draft and schedule one;
* ``published``    a listing is live on Etsy;
* ``half_made``    a draft was created on Etsy but not finished (pipeline/unfinished.py, the
                   seller's list: finish, fix, or delete on Etsy);
* ``failed``       the batch failed, a draft failed before anything was created, or going live failed;
* ``deleted``      a draft was deleted on Etsy (seen when the shop was read).

Read from our own database only; no Etsy call.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    MockupDesign,
    PlannedSlot,
    UploadBatch,
    UploadBatchStatus,
)

STATUSES: tuple[str, ...] = ("uploading", "not_written", "in_review", "drafted", "scheduled", "published",
                             "half_made", "failed", "deleted")


async def batch_statuses(session: AsyncSession, tenant_id: uuid.UUID, batches: list[UploadBatch]) -> dict[uuid.UUID, list[str]]:
    """Each batch's statuses (in ``STATUSES`` order), from a handful of queries for all of them."""
    from app.pipeline import unfinished

    ids = [b.id for b in batches]
    found: dict[uuid.UUID, set[str]] = {b.id: set() for b in batches}
    if not ids:
        return {}
    contents: dict[uuid.UUID, uuid.UUID] = dict((await session.execute(
        select(GeneratedContent.id, GeneratedContent.batch_id).where(GeneratedContent.tenant_id == tenant_id,
                                                                     GeneratedContent.batch_id.in_(ids))
    )).all())
    with_content: dict[uuid.UUID, int] = defaultdict(int)
    for batch_id in contents.values():
        with_content[batch_id] += 1
    drafted_contents: set[uuid.UUID] = set()
    live_contents: set[str] = set()
    for pub in (await session.execute(select(ListingPublication).where(
            ListingPublication.tenant_id == tenant_id, ListingPublication.content_id.in_(list(contents) or [uuid.uuid4()])))).scalars():
        batch_id = contents.get(pub.content_id)
        if batch_id is None:
            continue
        if pub.state == "deleted_on_etsy":
            found[batch_id].add("deleted")
            continue
        drafted_contents.add(pub.content_id)
        if pub.state == "active":
            found[batch_id].add("published")
            live_contents.add(str(pub.content_id))
        elif pub.scheduled_for is not None:
            found[batch_id].add("scheduled")
        else:
            found[batch_id].add("drafted")
    for content_id in (await session.execute(select(PlannedSlot.content_id).where(
            PlannedSlot.tenant_id == tenant_id, PlannedSlot.state.in_(("waiting", "drafting")),
            PlannedSlot.content_id.in_(list(contents) or [uuid.uuid4()])))).scalars():
        if content_id in contents:
            found[contents[content_id]].add("scheduled")
    undrafted: dict[uuid.UUID, int] = defaultdict(int)
    for content_id, batch_id in contents.items():
        if content_id not in drafted_contents:
            undrafted[batch_id] += 1
    for record in await unfinished.records(session, tenant_id, with_blocks=False):
        if record.batch_id not in found or record.dismissed:
            continue
        if record.etsy_listing_id is not None and record.category in unfinished.SELLER:
            found[record.batch_id].add("half_made")
        elif record.etsy_listing_id is None and record.category in (unfinished.NOT_ON_ETSY, unfinished.UNCHECKED) \
                and record.content_id is not None and record.content_id not in drafted_contents:
            found[record.batch_id].add("failed")
    # Going live failed (the draft is still a draft).
    for job in (await session.execute(select(Job).where(
            Job.tenant_id == tenant_id, Job.type == JobType.publish_live, Job.status == JobStatus.failed,
            Job.batch_id.in_(ids)))).scalars():
        if str((job.payload or {}).get("content_id")) not in live_contents:  # a later try made it live
            found[job.batch_id].add("failed")
    # A batch made on the mockup designs page keeps the "uploading" status it was created with
    # (each design is its own request; nothing marks the batch finished): once it has designs,
    # it is not "uploading" but waiting to be written.
    with_designs = set((await session.execute(select(MockupDesign.batch_id).where(
        MockupDesign.tenant_id == tenant_id, MockupDesign.batch_id.in_(ids)))).scalars())
    for b in batches:
        mine = found[b.id]
        uploading = b.status in (UploadBatchStatus.uploading, UploadBatchStatus.processing) and b.id not in with_designs
        if uploading:
            mine.add("uploading")
        if b.status is UploadBatchStatus.failed:
            mine.add("failed")
        if not with_content[b.id] and not uploading and b.status is not UploadBatchStatus.failed:
            mine.add("not_written")
        if undrafted[b.id]:
            mine.add("in_review")
    return {b: [s for s in STATUSES if s in found[b]] for b in found}
