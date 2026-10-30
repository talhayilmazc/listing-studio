"""Where each listing group stands, for deciding when its image files can go.

A listing group (a batch's images sharing a ``group_key``) is:

  * ``drafted``: it has a draft (or a live listing) in **every target shop** and
    nothing is pending for it;
  * ``pending``: something will still read its files: a planned draft or a
    scheduled go-live (group schedules, distribution), or a queued or running
    job on its batch (a draft being made, images being replaced, writing);
  * ``in_review``: neither: not written yet, written but not drafted, or drafted
    in only some of its target shops;
  * ``removed``: upload retention already deleted its files.

Its **target shops** are every shop it is meant for: the shops the seller ticked
for it on the review page (pipeline/shop_targets.py), or, while they never
changed that, the shop it was written for (the group's own choice, else its
batch's); the shops of its planned drafts, the shops of the shop group it was
distributed to, and every shop it already has a draft in. A draft deleted on
Etsy does not count as one.

Pure reads; used by upload retention (pipeline/upload_retention.py) and the
storage report (pipeline/storage_report.py).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import NamedTuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Asset,
    ConnectionStatus,
    DesignDistribution,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    ListingGroupSetting,
    ListingProfile,
    ListingPublication,
    PlannedSlot,
    ShopGroupMember,
    UploadBatch,
)

class _Listing(NamedTuple):
    """What group_states reads of a listing (and destination_shops: send_to, target_shops)."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    batch_id: uuid.UUID
    asset_id: uuid.UUID | None
    created_at: datetime
    listing_profile_id: uuid.UUID | None
    send_to: dict | None
    target_shops: list | None


DRAFTED, PENDING, IN_REVIEW, REMOVED = "drafted", "pending", "in_review", "removed"
GroupKey = tuple[uuid.UUID, str]


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass
class GroupState:
    batch_id: uuid.UUID
    group_key: str
    tenant_id: uuid.UUID
    state: str = IN_REVIEW
    assets: list[Asset] = field(default_factory=list)
    content_ids: list[tuple[uuid.UUID, uuid.UUID, datetime]] = field(default_factory=list)  # (content, asset, written)
    targets: set[uuid.UUID] = field(default_factory=set)
    drafted: set[uuid.UUID] = field(default_factory=set)
    draft_times: dict[uuid.UUID, datetime] = field(default_factory=dict)
    #: Drafts exist in every target shop (pending or not).
    complete: bool = False
    #: The latest draft creation in a target shop (when ``complete``).
    drafted_at: datetime | None = None
    #: The last work on it: uploaded, written, or a draft created.
    worked_at: datetime | None = None
    why_pending: str | None = None


async def group_states(
    session: AsyncSession, *, batch_ids: set[uuid.UUID] | None = None, with_removed: bool = False,
    targets_only: bool = False,
) -> dict[GroupKey, GroupState]:
    """Every group's state (of ``batch_ids``, else all), keyed by (batch, group_key).

    ``targets_only``: only ``targets`` is worked out, and without the shops drafted in, which the
    caller reads itself (the batch list, every page view: it read each listing's photos and
    publications three times over for the names of a few shops; the load test)."""
    columns = (Asset.id, Asset.batch_id, Asset.tenant_id, Asset.group_key) if targets_only else (Asset,)
    query = select(*columns, UploadBatch.created_at, UploadBatch.connection_id).join(UploadBatch, UploadBatch.id == Asset.batch_id)
    if batch_ids is not None:
        query = query.where(Asset.batch_id.in_(batch_ids))
    if not with_removed:
        query = query.where(Asset.files_removed_at.is_(None))
    rows = (await session.execute(query)).all()
    if not rows:
        return {}
    groups: dict[GroupKey, GroupState] = {}
    group_of: dict[uuid.UUID, GroupKey] = {}
    batch_shop: dict[uuid.UUID, uuid.UUID | None] = {}
    for row in rows:
        asset, created, shop = (row if targets_only else row[0]), row[-2], row[-1]
        key = (asset.batch_id, asset.group_key or "")
        g = groups.setdefault(key, GroupState(asset.batch_id, asset.group_key or "", asset.tenant_id, worked_at=_utc(created)))
        if not targets_only:
            g.assets.append(asset)
        group_of[asset.id] = key
        batch_shop[asset.batch_id] = shop
    batches = set(batch_shop)

    # The shop a group goes to unless the seller chose others: its profile's main shop (B3:
    # no batch or group shop decides it any more); a group with no profile keeps the shop
    # stored before (its own, else its batch's).
    settings = list((await session.execute(
        select(ListingGroupSetting).where(ListingGroupSetting.batch_id.in_(batches))
    )).scalars())
    chosen = {(s.batch_id, s.group_key or ""): s.connection_id for s in settings}
    group_profile = {(s.batch_id, s.group_key or ""): s.profile_id for s in settings}
    from app.pipeline.shop_targets import destination_shops

    # Only the columns read here (and by destination_shops): a listing's text, tags and JSON are
    # not needed to know where it goes (the load test, scripts/load_test.py).
    contents = [_Listing(*row) for row in (await session.execute(
        select(*(getattr(GeneratedContent, c) for c in _Listing._fields)).where(GeneratedContent.batch_id.in_(batches))
    )).all()]
    tenant_ids = {c.tenant_id for c in contents}
    chosen_shops: dict[uuid.UUID, list[uuid.UUID] | None] = {}
    for tid in tenant_ids:
        chosen_shops.update(await destination_shops(session, tid, [c for c in contents if c.tenant_id == tid]))
    content_rows = [(c.id, c.asset_id, c.created_at, chosen_shops.get(c.id), c.listing_profile_id) for c in contents]
    profile_ids = {p for p in group_profile.values() if p} | {row[4] for row in content_rows if row[4]}
    main_shop = dict((await session.execute(
        select(ListingProfile.id, ListingProfile.connection_id).where(ListingProfile.id.in_(profile_ids))
    )).all()) if profile_ids else {}
    own: dict[GroupKey, uuid.UUID] = {}
    for key, g in groups.items():
        shop = main_shop.get(group_profile.get(key)) or chosen.get(key) or batch_shop.get(g.batch_id)
        if shop is not None:
            g.targets.add(shop)
            own[key] = shop

    content_group: dict[uuid.UUID, GroupKey] = {}
    for cid, asset_id, written, ticked, listing_profile in content_rows:
        key = group_of.get(asset_id)
        if key is None:
            continue
        g = groups[key]
        main = main_shop.get(listing_profile)
        if main is not None and own.get(key) != main:
            # Written with a profile: its main shop is the default.
            g.targets.discard(own.get(key))  # type: ignore[arg-type]
            g.targets.add(main)
            own[key] = main
        if ticked is not None:
            # The seller chose where it goes ("Where and when"): those shops, not the default.
            g.targets.discard(own.get(key))  # type: ignore[arg-type]
            g.targets.update(ticked)
        g.content_ids.append((cid, asset_id, _utc(written)))
        g.worked_at = max(g.worked_at, _utc(written))
        content_group[cid] = key
    cids = list(content_group)

    if cids:
        for pub in () if targets_only else (await session.execute(
            select(ListingPublication.content_id, ListingPublication.state, ListingPublication.created_at,
                   ListingPublication.connection_id, ListingPublication.scheduled_for, ListingPublication.schedule_note)
            .join(GeneratedContent, GeneratedContent.id == ListingPublication.content_id)
            .where(GeneratedContent.batch_id.in_(batches))  # (a join, not 1,000s of ids to bind)
        )).all():
            if pub.content_id not in content_group:
                continue
            g = groups[content_group[pub.content_id]]
            if pub.state == "deleted_on_etsy":
                continue
            made = _utc(pub.created_at)
            g.drafted.add(pub.connection_id)
            g.targets.add(pub.connection_id)
            if made is not None:
                g.worked_at = max(g.worked_at, made)
                g.draft_times[pub.connection_id] = max(g.draft_times.get(pub.connection_id, made), made)
            if pub.scheduled_for is not None and pub.state != "active" and pub.schedule_note is None:
                g.why_pending = g.why_pending or "a go-live is scheduled"
        for slot in (await session.execute(
            select(PlannedSlot.content_id, PlannedSlot.state, PlannedSlot.connection_id)
            .join(GeneratedContent, GeneratedContent.id == PlannedSlot.content_id)
            .where(GeneratedContent.batch_id.in_(batches))
        )).all():
            if slot.content_id not in content_group:
                continue
            g = groups[content_group[slot.content_id]]
            if slot.state != "cancelled":  # a failed one still names a shop it was meant for
                g.targets.add(slot.connection_id)
            if slot.state in ("waiting", "drafting"):
                g.why_pending = g.why_pending or "a group schedule has a draft or go-live to come"
        # Distributed to a shop group: meant for every connected shop in it.
        sent = (await session.execute(
            select(DesignDistribution.content_id, DesignDistribution.group_id)
            .join(GeneratedContent, GeneratedContent.id == DesignDistribution.content_id)
            .where(GeneratedContent.batch_id.in_(batches), DesignDistribution.group_id.is_not(None))
        )).all()
        sent = [(cid, gid) for cid, gid in sent if cid in content_group]
        if sent:
            members: dict[uuid.UUID, set[uuid.UUID]] = {}
            for shop_id, gid in (await session.execute(
                select(EtsyConnection.id, ShopGroupMember.group_id)
                .join(ShopGroupMember, ShopGroupMember.connection_id == EtsyConnection.id)
                .where(
                    ShopGroupMember.group_id.in_({gid for _, gid in sent}),
                    EtsyConnection.status == ConnectionStatus.active,
                )
            )).all():
                members.setdefault(gid, set()).add(shop_id)
            for cid, gid in sent:
                groups[content_group[cid]].targets |= members.get(gid, set())

    if targets_only:
        return groups

    # Work queued or running on the batch may still read the files.
    # (Of these batches only: every queued job of every account was read here, so one seller's
    # 3,000 queued drafts slowed every other seller's batch list.)
    busy: set[uuid.UUID] = set()
    queued = select(Job.batch_id, Job.payload["batch_id"].as_string()).where(
        Job.status.in_([JobStatus.queued, JobStatus.running]))
    if batch_ids is not None:
        keys = [str(b) for b in batches]
        queued = queued.where(or_(Job.batch_id.in_(batches), Job.payload["batch_id"].as_string().in_(keys)))
    for job_batch, payload_batch in (await session.execute(queued)).all():
        for value in (job_batch, payload_batch):
            try:
                busy.add(uuid.UUID(str(value)))
            except (TypeError, ValueError):
                pass

    for g in groups.values():
        if all(a.files_removed_at is not None for a in g.assets):
            g.state = REMOVED
        elif g.batch_id in busy:
            g.state, g.why_pending = PENDING, g.why_pending or "work is queued or running"
        elif g.why_pending:
            g.state = PENDING
        elif g.targets and g.targets <= g.drafted:
            g.state = DRAFTED
        else:
            g.state = IN_REVIEW
    for g in groups.values():
        g.complete = bool(g.targets) and g.targets <= g.drafted
        if g.complete:  # the drafted clock starts at the last target shop's draft
            g.drafted_at = max((g.draft_times[s] for s in g.targets if s in g.draft_times), default=None)
    return groups
