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

Its **target shops** are every shop it is meant for: the shop it was written for
(the group's own choice, else its batch's), the shops of its planned drafts, the
shops of the shop group it was distributed to, and every shop it already has a
draft in. A draft deleted on Etsy does not count as one.

Pure reads; used by upload retention (pipeline/upload_retention.py) and the
storage report (pipeline/storage_report.py).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
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
    ListingPublication,
    PlannedSlot,
    UploadBatch,
)

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
    #: The latest draft creation in a target shop (when ``drafted``).
    drafted_at: datetime | None = None
    #: The last work on it: uploaded, written, or a draft created.
    worked_at: datetime | None = None
    why_pending: str | None = None


async def group_states(
    session: AsyncSession, *, batch_ids: set[uuid.UUID] | None = None, with_removed: bool = False
) -> dict[GroupKey, GroupState]:
    """Every group's state (of ``batch_ids``, else all), keyed by (batch, group_key)."""
    query = select(Asset, UploadBatch.created_at, UploadBatch.connection_id).join(UploadBatch, UploadBatch.id == Asset.batch_id)
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
    for asset, created, shop in rows:
        key = (asset.batch_id, asset.group_key or "")
        g = groups.setdefault(key, GroupState(asset.batch_id, asset.group_key or "", asset.tenant_id, worked_at=_utc(created)))
        g.assets.append(asset)
        group_of[asset.id] = key
        batch_shop[asset.batch_id] = shop
    batches = set(batch_shop)

    # The shop each group was written for: its own choice, else its batch's.
    chosen = {
        (s.batch_id, s.group_key or ""): s.connection_id
        for s in (await session.execute(
            select(ListingGroupSetting).where(ListingGroupSetting.batch_id.in_(batches))
        )).scalars()
    }
    for key, g in groups.items():
        shop = chosen.get(key) or batch_shop.get(g.batch_id)
        if shop is not None:
            g.targets.add(shop)

    content_group: dict[uuid.UUID, GroupKey] = {}
    for cid, asset_id, written in (await session.execute(
        select(GeneratedContent.id, GeneratedContent.asset_id, GeneratedContent.created_at).where(
            GeneratedContent.batch_id.in_(batches)
        )
    )).all():
        key = group_of.get(asset_id)
        if key is None:
            continue
        g = groups[key]
        g.content_ids.append((cid, asset_id, _utc(written)))
        g.worked_at = max(g.worked_at, _utc(written))
        content_group[cid] = key
    cids = list(content_group)

    if cids:
        for pub in (await session.execute(
            select(ListingPublication).where(ListingPublication.content_id.in_(cids))
        )).scalars():
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
            select(PlannedSlot).where(PlannedSlot.content_id.in_(cids))
        )).scalars():
            g = groups[content_group[slot.content_id]]
            if slot.state != "cancelled":  # a failed one still names a shop it was meant for
                g.targets.add(slot.connection_id)
            if slot.state in ("waiting", "drafting"):
                g.why_pending = g.why_pending or "a group schedule has a draft or go-live to come"
        # Distributed to a shop group: meant for every connected shop in it.
        sent = (await session.execute(
            select(DesignDistribution.content_id, DesignDistribution.group_id).where(
                DesignDistribution.content_id.in_(cids), DesignDistribution.group_id.is_not(None)
            )
        )).all()
        if sent:
            members: dict[uuid.UUID, set[uuid.UUID]] = {}
            for shop_id, gid in (await session.execute(
                select(EtsyConnection.id, EtsyConnection.group_id).where(
                    EtsyConnection.group_id.in_({gid for _, gid in sent}),
                    EtsyConnection.status == ConnectionStatus.active,
                )
            )).all():
                members.setdefault(gid, set()).add(shop_id)
            for cid, gid in sent:
                groups[content_group[cid]].targets |= members.get(gid, set())

    # Work queued or running on the batch may still read the files.
    busy: set[uuid.UUID] = set()
    for job in (await session.execute(
        select(Job).where(Job.status.in_([JobStatus.queued, JobStatus.running]))
    )).scalars():
        for value in (job.batch_id, (job.payload or {}).get("batch_id")):
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
        if g.state == DRAFTED:  # the drafted clock starts at the last target shop's draft
            g.drafted_at = max((g.draft_times[s] for s in g.targets if s in g.draft_times), default=None)
    return groups
