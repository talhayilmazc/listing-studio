"""Upload retention: image files are deleted once their listing no longer needs them.

The production disk cannot grow and uploads are what fills it. A listing's
images are needed to create its draft on Etsy; once the listing is published
they are not needed again, so they are deleted a set number of days later.

What is deleted, per listing group: every image's original upload, its
processed copy and the previews cached beside them.

What is kept:
  * every row: the batch, the group's images (names, order), the listing text
    and its publication record, so history, counts and Analytics read as before
  * one small JPEG of the group's cover, so the app still shows which design
    the listing was
  * everything on Etsy: nothing there is touched

When, per group:
  * published through the app: ``published_days`` after its latest publication
  * nothing published from it: ``unpublished_days`` after it was last worked on
    (uploaded, written, or a draft created)

A group with work queued or running is left for the next run. Files are deleted
before the rows are marked, so a run that is cut off is simply finished by the
next one: nothing is ever marked removed while its files stay on disk.

Afterwards a group cannot be drafted again, regenerated, recropped or
reordered: the endpoints say so, and the seller uploads the design again if
they need to.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import AppSetting, Asset, GeneratedContent, Job, JobStatus, ListingPublication, UploadBatch
from app.pipeline.images import ImageProcessingError, resize_preview
from app.pipeline.storage import Storage

logger = logging.getLogger(__name__)

#: app_setting: the days an admin set, {"published_days": n, "unpublished_days": n}.
KEY = "upload_retention"
#: app_setting: what the last run that deleted anything (or tried to) did.
LAST_KEY = "upload_retention_last"
#: The kept cover is this wide: enough for every list in the app, ~40 KB.
THUMBNAIL_WIDTH = 448
MAX_DAYS = 3650

#: What a seller is told when they ask for something the files were needed for.
REMOVED = (
    "This listing's image files have been removed (they are kept for a limited time after "
    "publishing). Upload the design again to do this."
)


@dataclass(frozen=True)
class Policy:
    published_days: int
    unpublished_days: int


def defaults() -> Policy:
    settings = get_settings()
    return Policy(settings.upload_retention_days, settings.unpublished_retention_days)


def validate(value: dict[str, Any]) -> dict[str, int]:
    """The two day counts as whole numbers from 1 to ten years, or ValueError."""
    out = {}
    for name in ("published_days", "unpublished_days"):
        days = value.get(name)
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
            raise ValueError(f"{name} must be a whole number of days from 1 to {MAX_DAYS}")
        out[name] = days
    return out


async def policy(session: AsyncSession) -> Policy:
    """The days in force: the admin's, or the configured defaults."""
    row = await session.get(AppSetting, KEY)
    if row is not None:
        try:
            return Policy(**validate(row.value or {}))
        except ValueError:
            logger.error("app_setting %s is not usable; using the defaults", KEY)
    return defaults()


@dataclass
class _Image:
    id: uuid.UUID
    keys: list[str]  # the original and the processed copy
    name: str
    rank: int | None
    usable: bool  # processed: can be the cover


@dataclass
class Due:
    """One listing group whose files are past their time."""

    tenant_id: uuid.UUID
    batch_id: uuid.UUID
    reason: str  # "published" | "unpublished"
    since: datetime
    images: list[_Image] = field(default_factory=list)
    cover: _Image | None = None


@dataclass
class Result:
    applied: bool
    published_days: int
    unpublished_days: int
    published_groups: int = 0
    unpublished_groups: int = 0
    images: int = 0
    files: int = 0
    freed_bytes: int = 0
    thumbnails: int = 0
    thumbnail_bytes: int = 0
    #: Groups past their time whose batch has work queued or running: next run.
    waiting: int = 0
    at: str = ""

    @property
    def groups(self) -> int:
        return self.published_groups + self.unpublished_groups

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def due_groups(session: AsyncSession, now: datetime, days: Policy) -> tuple[list[Due], int]:
    """Groups whose files should go now, and how many more are only waiting for
    running work to finish."""
    horizon = now - timedelta(days=min(days.published_days, days.unpublished_days))
    rows = (
        await session.execute(
            select(Asset, UploadBatch.created_at)
            .join(UploadBatch, UploadBatch.id == Asset.batch_id)
            .where(Asset.files_removed_at.is_(None), UploadBatch.created_at < horizon)
            .order_by(Asset.batch_id)
        )
    ).all()
    if not rows:
        return [], 0
    batch_ids = {asset.batch_id for asset, _ in rows}

    contents = (
        await session.execute(
            select(GeneratedContent.id, GeneratedContent.asset_id, GeneratedContent.created_at).where(
                GeneratedContent.batch_id.in_(batch_ids)
            )
        )
    ).all()
    publications = (
        await session.execute(
            select(ListingPublication.content_id, ListingPublication.published_at, ListingPublication.created_at).where(
                ListingPublication.content_id.in_([c.id for c in contents])
            )
        )
    ).all() if contents else []
    # Work that has not finished may still read the files (a draft being
    # created, images being replaced): its batch waits for the next run.
    busy: set[uuid.UUID] = set()
    for job in (
        await session.execute(select(Job).where(Job.status.in_([JobStatus.queued, JobStatus.running])))
    ).scalars():
        for value in (job.batch_id, (job.payload or {}).get("batch_id")):
            try:
                busy.add(uuid.UUID(str(value)))
            except (TypeError, ValueError):
                pass

    group_of: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
    created: dict[uuid.UUID, datetime] = {}
    members: dict[tuple[uuid.UUID, str], list[Asset]] = {}
    for asset, batch_created in rows:
        group = (asset.batch_id, asset.group_key or "")  # the root group, as everywhere else
        group_of[asset.id] = group
        created[asset.batch_id] = _utc(batch_created)
        members.setdefault(group, []).append(asset)

    written: dict[tuple[uuid.UUID, str], list[tuple[uuid.UUID, uuid.UUID, datetime]]] = {}
    for content_id, asset_id, at in contents:
        if asset_id in group_of:
            written.setdefault(group_of[asset_id], []).append((content_id, asset_id, _utc(at)))
    published: dict[uuid.UUID, list[datetime]] = {}
    drafted: dict[uuid.UUID, list[datetime]] = {}
    for content_id, published_at, drafted_at in publications:
        if published_at is not None:
            published.setdefault(content_id, []).append(_utc(published_at))
        if drafted_at is not None:
            drafted.setdefault(content_id, []).append(_utc(drafted_at))

    due: list[Due] = []
    waiting = 0
    for group, assets in members.items():
        batch_id = group[0]
        texts = written.get(group, [])
        went_live = [at for content_id, _, _ in texts for at in published.get(content_id, [])]
        if went_live:
            reason, since, keep = "published", max(went_live), days.published_days
        else:
            worked = [created[batch_id], *(at for _, _, at in texts)]
            worked += [at for content_id, _, _ in texts for at in drafted.get(content_id, [])]
            reason, since, keep = "unpublished", max(worked), days.unpublished_days
        if since > now - timedelta(days=keep):
            continue
        if batch_id in busy:
            waiting += 1
            continue
        images = [
            _Image(
                id=a.id, keys=[k for k in (a.storage_key, a.processed_key) if k], name=a.original_filename.lower(),
                rank=a.rank, usable=a.processed_key is not None,
            )
            for a in assets
        ]
        by_id = {i.id: i for i in images}
        # The cover is the image the listing text is attached to; without text,
        # the first image in the seller's order.
        newest = max(texts, key=lambda t: t[2], default=None)
        cover = by_id.get(newest[1]) if newest else None
        if cover is None:
            usable = [i for i in images if i.usable]
            cover = min(usable, key=lambda i: (i.rank if i.rank is not None else 1_000_000, i.name), default=None)
        due.append(Due(tenant_id=assets[0].tenant_id, batch_id=batch_id, reason=reason, since=since, images=images, cover=cover))
    return due, waiting


def _thumbnail(storage: Storage, group: Due) -> tuple[str, int] | None:
    """Write (or find) the group's kept cover. None when there is no usable cover."""
    cover = group.cover
    if cover is None or not cover.keys:
        return None
    key = f"{group.tenant_id}/{group.batch_id}/kept/{cover.id}.jpg"
    if storage.exists(key):  # an earlier run was cut off after writing it
        return key, len(storage.get(key))
    try:
        data = resize_preview(storage.get(cover.keys[-1]), THUMBNAIL_WIDTH)
    except (FileNotFoundError, ImageProcessingError):
        return None
    storage.put(key, data, "image/jpeg")
    return key, len(data)


def _measure(storage: Storage, group: Due) -> tuple[int, int]:
    files = size = 0
    for image in group.images:
        for key in image.keys:
            n, b = storage.measure_with_derivatives(key)
            files, size = files + n, size + b
    return files, size


def _delete(storage: Storage, group: Due) -> None:
    for image in group.images:
        for key in image.keys:
            storage.delete_with_derivatives(key)


async def run(
    session: AsyncSession, storage: Storage, *, now: datetime | None = None, apply: bool = True
) -> Result:
    """Delete the files of every group past its time; with ``apply=False`` only
    count what would go. Commits group by group."""
    now = now or datetime.now(timezone.utc)
    days = await policy(session)
    result = Result(applied=apply, published_days=days.published_days, unpublished_days=days.unpublished_days, at=now.isoformat())
    due, result.waiting = await due_groups(session, now, days)
    for group in due:
        files, size = await asyncio.to_thread(_measure, storage, group)
        if apply:
            kept = await asyncio.to_thread(_thumbnail, storage, group)
            await asyncio.to_thread(_delete, storage, group)
            await session.execute(
                update(Asset)
                .where(Asset.id.in_([i.id for i in group.images]))
                .values(files_removed_at=now, processed_key=None)
            )
            if kept is not None and group.cover is not None:
                await session.execute(update(Asset).where(Asset.id == group.cover.id).values(thumbnail_key=kept[0]))
                result.thumbnails += 1
                result.thumbnail_bytes += kept[1]
            await session.commit()
        if group.reason == "published":
            result.published_groups += 1
        else:
            result.unpublished_groups += 1
        result.images += len(group.images)
        result.files += files
        result.freed_bytes += size
    return result


async def remember(session: AsyncSession, result: Result) -> None:
    """Keep the last run's figures for Admin > Usage and the daily summary."""
    row = await session.get(AppSetting, LAST_KEY)
    if row is None:
        session.add(AppSetting(key=LAST_KEY, value=result.as_dict()))
    else:
        row.value = result.as_dict()
    await session.commit()


async def last(session: AsyncSession) -> dict[str, Any] | None:
    row = await session.get(AppSetting, LAST_KEY)
    return dict(row.value) if row is not None and row.value else None
