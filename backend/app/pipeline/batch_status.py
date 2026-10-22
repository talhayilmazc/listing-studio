"""Every status a batch is in: the one definition the Batches filter, its counts, every batch
badge (Batches, the batch page header, Overview) and the batch summary use (frontend
lib/batchStatus.ts has the same keys, labels and order; a test keeps them together).

Each listing is looked at in every shop it is meant for (its ticked shops, else the shop it
was written for; and any shop it already has a draft, a go-live or a planned slot in):

* ``uploading``         its files are still being uploaded or processed (a mockup batch with its
                        designs in is not);
* ``not_written``       a group of its photos has no listing written yet;
* ``in_review``         a written listing is not approved yet (and has nothing on Etsy);
* ``approved``          an approved listing has nothing on Etsy yet ("Approved, not drafted");
* ``partly_drafted``    a listing has drafts in some of its shops, and a shop with nothing at all
                        (not half-made, failed or scheduled there);
* ``drafted``           a listing has a draft in every one of its shops and is not live in all of
                        them (drafts not yet scheduled);
* ``scheduled``         a draft is scheduled to go live, or a group plan will draft and schedule one;
* ``partly_published``  something of the batch is live, but not every listing in every shop;
* ``published``         **every** listing of the batch is live in **every** shop it was meant for
                        (and every group of photos is written);
* ``half_made``         a listing's draft was created on Etsy and not finished, in a shop where it
                        has no finished draft (finish it, or fix then finish). An earlier draft of a
                        listing that later finished elsewhere ("Replaced: delete on Etsy") is not
                        the batch's listing: it is in the unfinished-drafts list, not here;
* ``failed``            the batch failed; a draft failed before anything was created, or going live
                        failed, in a shop where the listing has no draft or live listing since;
* ``deleted``           a draft was deleted on Etsy and the listing has no draft there now.

Read from our own database only; no Etsy call.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Asset,
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingGroupSetting,
    ListingProfile,
    ListingPublication,
    MockupDesign,
    PlannedSlot,
    UploadBatch,
    UploadBatchStatus,
)

STATUSES: tuple[str, ...] = ("uploading", "not_written", "in_review", "approved", "partly_drafted", "drafted",
                             "scheduled", "partly_published", "published", "half_made", "failed", "deleted")

#: The one status a badge shows for a batch: what needs the seller first, then how far it got.
HEADLINE: tuple[str, ...] = ("failed", "half_made", "uploading", "not_written", "in_review", "approved",
                             "partly_drafted", "drafted", "scheduled", "partly_published", "published", "deleted")

#: The unfinished-draft kinds that are the batch's own listing left half-made in a shop.
HALF_MADE_KINDS = ("finish", "fix_personalization", "fix_shop")
UNSORTED = "~unsorted"


def headline(statuses: list[str]) -> str | None:
    return next((s for s in HEADLINE if s in statuses), None)


#: How far one listing got, furthest first: each listing is in exactly one stage.
STAGES: tuple[str, ...] = ("published", "partly_published", "scheduled", "drafted", "partly_drafted",
                           "approved", "in_review")
#: What can go wrong with one listing: said beside the badge, never the badge itself,
#: unless nothing in the batch can go further.
FLAGS: tuple[str, ...] = ("failed", "half_made", "deleted")


@dataclass
class Overview:
    """A batch's badge and what is said beside it (A4): the stage most of its listings are in
    ("In review"), with a note per thing gone wrong ("1 draft failed"). "Failed" is the badge
    only when the batch itself failed or nothing in it can go further."""

    statuses: list[str]
    headline: str | None
    notes: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


_NOTE = {
    "failed": ("{n} draft failed", "{n} drafts failed"),
    "half_made": ("{n} draft half-made on Etsy", "{n} drafts half-made on Etsy"),
    "deleted": ("{n} draft deleted on Etsy", "{n} drafts deleted on Etsy"),
}


def _overview(statuses: list[str], stages: dict[str, int], flags: dict[str, int], *, uploading: bool,
              batch_failed: bool, unwritten: int) -> Overview:
    counts = {**{s: n for s, n in stages.items() if n}, **({"not_written": unwritten} if unwritten else {})}
    notes = [(_NOTE[f][0] if n == 1 else _NOTE[f][1]).format(n=n) for f in FLAGS if (n := flags.get(f, 0))]
    if uploading:
        badge = "uploading"
    elif batch_failed:
        badge = "failed"
    elif not counts:
        # Nothing written and nothing moving: what went wrong is all there is to say.
        badge = next((f for f in FLAGS if flags.get(f)), None) or headline(statuses)
        notes = [] if badge == "failed" else notes
    else:
        order = ["not_written", *reversed(STAGES)]  # on a tie, the one that needs the seller first
        badge = max(counts, key=lambda s: (counts[s], -order.index(s)))
    return Overview(statuses=statuses, headline=badge, notes=notes, counts=counts)


async def batch_overviews(session: AsyncSession, tenant_id: uuid.UUID, batches: list[UploadBatch]) -> dict[uuid.UUID, Overview]:
    """Every batch's statuses, badge and notes (the Batches list, the batch page, Overview)."""
    details: dict[uuid.UUID, dict] = {}
    statuses = await batch_statuses(session, tenant_id, batches, details=details)
    return {
        b.id: _overview(statuses.get(b.id, []), **details.get(b.id, {"stages": {}, "flags": {}, "uploading": False,
                                                                        "batch_failed": False, "unwritten": 0}))
        for b in batches
    }


@dataclass
class _Listing:
    content: GeneratedContent
    shops: set[uuid.UUID] = field(default_factory=set)
    live: set[uuid.UUID] = field(default_factory=set)
    drafts: set[uuid.UUID] = field(default_factory=set)  # a draft there (not live)
    scheduled: set[uuid.UUID] = field(default_factory=set)
    deleted: set[uuid.UUID] = field(default_factory=set)
    half: set[uuid.UUID] = field(default_factory=set)
    failed: set[uuid.UUID] = field(default_factory=set)

    @property
    def on_etsy(self) -> set[uuid.UUID]:
        return self.live | self.drafts


async def batch_statuses(
    session: AsyncSession, tenant_id: uuid.UUID, batches: list[UploadBatch], *, details: dict | None = None,
) -> dict[uuid.UUID, list[str]]:
    """Each batch's statuses (in ``STATUSES`` order), from a handful of queries for all of them."""
    from app.pipeline import unfinished

    ids = [b.id for b in batches]
    if not ids:
        return {}
    found: dict[uuid.UUID, set[str]] = {b.id: set() for b in batches}
    by_batch = {b.id: b for b in batches}
    active = {c.id for c in (await session.execute(select(EtsyConnection).where(
        EtsyConnection.tenant_id == tenant_id, EtsyConnection.status == ConnectionStatus.active))).scalars()}

    contents = list((await session.execute(select(GeneratedContent).where(
        GeneratedContent.tenant_id == tenant_id, GeneratedContent.batch_id.in_(ids)))).scalars())
    listings = {c.id: _Listing(c) for c in contents}
    # The shop each listing was written for: its group's, else its batch's, else its profile's main shop.
    assets = dict((await session.execute(select(Asset.id, Asset.group_key).where(Asset.batch_id.in_(ids)))).all())
    group_shop = {(s.batch_id, s.group_key): s.connection_id for s in (await session.execute(
        select(ListingGroupSetting).where(ListingGroupSetting.batch_id.in_(ids)))).scalars()}
    profile_shop = dict((await session.execute(select(ListingProfile.id, ListingProfile.connection_id).where(
        ListingProfile.tenant_id == tenant_id))).all())
    from app.pipeline.shop_targets import destination_shops

    chosen = await destination_shops(session, tenant_id, contents)
    for item in listings.values():
        c = item.content
        ticked = set(chosen[c.id]) if chosen.get(c.id) is not None else None
        if ticked is None:
            own = group_shop.get((c.batch_id, assets.get(c.asset_id) or "")) or by_batch[c.batch_id].connection_id \
                or profile_shop.get(c.listing_profile_id)
            ticked = {own} if own else set()
        item.shops = {s for s in ticked if s in active}

    if listings:
        for pub in (await session.execute(select(ListingPublication).where(
                ListingPublication.tenant_id == tenant_id, ListingPublication.content_id.in_(list(listings))))).scalars():
            item = listings[pub.content_id]
            if pub.state == "deleted_on_etsy":
                item.deleted.add(pub.connection_id)
                continue
            item.shops.add(pub.connection_id)  # a listing is meant for every shop it was drafted in
            if pub.state == "active":
                item.live.add(pub.connection_id)
            else:
                item.drafts.add(pub.connection_id)
                if pub.scheduled_for is not None:
                    item.scheduled.add(pub.connection_id)
        for content_id, shop in (await session.execute(select(PlannedSlot.content_id, PlannedSlot.connection_id).where(
                PlannedSlot.tenant_id == tenant_id, PlannedSlot.state.in_(("waiting", "drafting")),
                PlannedSlot.content_id.in_(list(listings))))).all():
            item = listings[content_id]
            item.shops.add(shop)
            item.scheduled.add(shop)
        for record in await unfinished.records(session, tenant_id, with_blocks=False):
            item = listings.get(record.content_id) if record.content_id else None
            if item is None or record.dismissed or record.connection_id in item.on_etsy:
                continue
            if record.etsy_listing_id is not None and record.category in HALF_MADE_KINDS:
                item.half.add(record.connection_id)
            elif record.etsy_listing_id is None and record.category in (unfinished.NOT_ON_ETSY, unfinished.UNCHECKED):
                item.failed.add(record.connection_id)
        # Going live failed (the draft is still a draft there).
        for job in (await session.execute(select(Job).where(
                Job.tenant_id == tenant_id, Job.type == JobType.publish_live, Job.status == JobStatus.failed,
                Job.batch_id.in_(ids)))).scalars():
            try:
                item = listings.get(uuid.UUID(str((job.payload or {}).get("content_id"))))
            except (TypeError, ValueError):
                item = None
            if item is not None and job.connection_id not in item.live:
                item.failed.add(job.connection_id)

    per_batch: dict[uuid.UUID, list[_Listing]] = defaultdict(list)
    for item in listings.values():
        per_batch[item.content.batch_id].append(item)
    written_groups: dict[uuid.UUID, set[str]] = defaultdict(set)
    for item in listings.values():
        written_groups[item.content.batch_id].add(assets.get(item.content.asset_id) or "")
    groups: dict[uuid.UUID, set[str]] = defaultdict(set)
    for batch_id, key in (await session.execute(select(Asset.batch_id, Asset.group_key).where(
            Asset.batch_id.in_(ids), Asset.status.is_not(None)))).all():
        if (key or "") != UNSORTED:
            groups[batch_id].add(key or "")
    with_designs = set((await session.execute(select(MockupDesign.batch_id).where(
        MockupDesign.tenant_id == tenant_id, MockupDesign.batch_id.in_(ids)))).scalars())

    for b in batches:
        mine = found[b.id]
        items = per_batch.get(b.id, [])
        uploading = b.status in (UploadBatchStatus.uploading, UploadBatchStatus.processing) and b.id not in with_designs
        if uploading:
            mine.add("uploading")
        if b.status is UploadBatchStatus.failed:
            mine.add("failed")
        unwritten = groups[b.id] - written_groups[b.id] if items else groups[b.id]
        if (unwritten or not items) and not uploading and b.status is not UploadBatchStatus.failed:
            mine.add("not_written")
        all_live = bool(items)
        stages: dict[str, int] = defaultdict(int)
        flags: dict[str, int] = defaultdict(int)
        for item in items:
            missing = item.shops - item.on_etsy - item.half - item.failed - item.scheduled
            # This listing's one stage (how far it got) and what went wrong with it.
            if item.shops and item.shops <= item.live:
                stage = "published"
            elif item.live:
                stage = "partly_published"
            elif item.scheduled:
                stage = "scheduled"
            elif item.shops and item.shops <= item.on_etsy:
                stage = "drafted"
            elif item.on_etsy:
                stage = "partly_drafted"
            elif item.content.approved:
                stage = "approved"
            else:
                stage = "in_review"
            if item.failed or item.half or (item.deleted - item.on_etsy):
                # Gone wrong and gone no further: counted by what went wrong, not as a stage.
                if not item.on_etsy and not item.scheduled:
                    stage = None
                for flag, hit in (("failed", item.failed), ("half_made", item.half), ("deleted", item.deleted - item.on_etsy)):
                    if hit:
                        flags[flag] += 1
            if stage:
                stages[stage] += 1
            if not item.content.approved and not item.on_etsy and not item.scheduled:
                mine.add("in_review")
            elif item.content.approved and not item.on_etsy and (missing or not item.shops):
                mine.add("approved")  # nothing on Etsy yet (or no shop chosen yet)
            if item.on_etsy and missing:
                mine.add("partly_drafted")  # drafts in some shops; a shop with nothing at all
            if item.shops and item.shops <= item.on_etsy and item.drafts - item.scheduled:
                mine.add("drafted")
            if item.scheduled:
                mine.add("scheduled")
            if item.half:
                mine.add("half_made")
            if item.failed:
                mine.add("failed")
            if item.deleted - item.on_etsy:
                mine.add("deleted")
            if not item.shops or not item.shops <= item.live:
                all_live = False
        if all_live and not unwritten:
            mine.add("published")
        elif any(item.live for item in items):
            mine.add("partly_published")
        if details is not None:
            details[b.id] = {"stages": dict(stages), "flags": dict(flags), "uploading": uploading,
                             "batch_failed": b.status is UploadBatchStatus.failed, "unwritten": len(unwritten)}
    return {b: [s for s in STATUSES if s in found[b]] for b in found}
