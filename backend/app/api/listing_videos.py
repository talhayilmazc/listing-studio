"""A listing's video (feature/listing-video; rules: pipeline/video_rules.py, worker: workers/listing_video.py).

The seller's own video file for a listing group of a batch, and optionally for that group in one shop. It
arrives in 8 MB pieces (resumable, as a Photoshop mockup does), the worker reads it and makes its poster, and
the draft job attaches it to the Etsy draft after the images. Nothing here calls Etsy: sending a video again
or replacing the one on a listing is queued, on the seller's own click and confirmation only.

A batch with no video answers an empty list: nothing else about it changes.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import images
from app.api.deps import Enqueuer, active_tenant, get_enqueuer, get_session, get_storage
from app.core import audit, storage_cap
from app.db.models import Asset, EtsyConnection, GeneratedContent, ListingPublication, ListingVideo, Tenant, UploadBatch
from app.etsy.shops import owned_shop
from app.pipeline import examples, listing_videos, video_rules
from app.pipeline.storage import Storage

router = APIRouter(prefix="/api", tags=["listing-videos"])

CHUNK_BYTES = 8 * 1024 * 1024
#: Kept free on the server's disk beyond the upload itself (the disk is 30 GB and shared).
DISK_RESERVE = 3 * 1024 ** 3
DISK_FULL = "The server is short of space right now. Please try again later."
CONFIRM = "Confirm that this video should be sent to the listing on Etsy."
#: A video still "processing" this long after it arrived is given to the worker again (its job was lost).
READ_AGAIN_AFTER_SECONDS = 120
NOT_READY = "This video is not ready to send yet."
BUSY = "This video is being sent to that listing right now."


class VideoIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    #: The listing group it is for ("" is the root group). With ``place``: ignored, the group comes from the
    #: file's folder and name by the batch's own grouping (a video uploaded with the batch's photos).
    group_key: str | None = Field(default=None, max_length=512)
    upload_folder: str | None = Field(default=None, max_length=512)
    place: bool = False
    #: Only this shop's drafts get it; None: every shop's.
    connection_id: uuid.UUID | None = None


class VideoOut(BaseModel):
    id: uuid.UUID
    group_key: str
    connection_id: uuid.UUID | None = None
    filename: str
    size: int
    received: int
    chunk_size: int = CHUNK_BYTES
    #: receiving · processing · ready · failed
    status: str
    #: What is wrong with it, in one plain sentence.
    error: str | None = None
    seconds: float | None = None
    #: "0:08"
    duration: str = ""
    width: int | None = None
    height: int | None = None
    poster_url: str | None = None
    #: False: its file went with the listing's photos (kept for a limited time).
    kept: bool = True


class DraftOut(BaseModel):
    """One draft or listing of this batch on Etsy, and what became of the video there."""
    content_id: uuid.UUID
    group_key: str
    connection_id: uuid.UUID
    shop_name: str | None = None
    #: None: no video was sent with it · sending · attached · failed
    video_state: str | None = None
    video_error: str | None = None
    video_id: uuid.UUID | None = None


class VideosOut(BaseModel):
    limits: dict[str, Any]
    videos: list[VideoOut]
    drafts: list[DraftOut]


class SendIn(BaseModel):
    content_id: uuid.UUID
    connection_id: uuid.UUID
    #: True: the video on the listing is taken off first ("Replace video"). False: "Try again" / "Add to this draft".
    replace: bool = False
    confirm: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _out(video: ListingVideo) -> VideoOut:
    seconds = video.duration_ms / 1000 if video.duration_ms else None
    kept = video.files_removed_at is None
    poster = None
    if video.poster_key and kept:
        poster = f"/api/listing-videos/{video.id}/poster?v={images.stamp(video.poster_key)}"
    return VideoOut(
        id=video.id, group_key=video.group_key, connection_id=video.connection_id, filename=video.filename, size=video.size,
        received=video.received, status=video.status, seconds=seconds, duration=video_rules.duration_label(seconds),
        error=video.error if kept else listing_videos.NO_LONGER_KEPT, width=video.width, height=video.height,
        poster_url=poster, kept=kept,
    )


async def _batch(session: AsyncSession, tenant: Tenant, batch_id: uuid.UUID) -> UploadBatch:
    batch = await session.get(UploadBatch, batch_id)
    if batch is None or batch.tenant_id != tenant.id or batch.deleted_at is not None:
        raise HTTPException(status_code=404, detail="batch not found")
    return batch


async def _own(session: AsyncSession, tenant: Tenant, video_id: uuid.UUID, *, lock: bool = False) -> ListingVideo:
    query = select(ListingVideo).where(ListingVideo.id == video_id, ListingVideo.tenant_id == tenant.id)
    video = (await session.execute(query.with_for_update() if lock else query)).scalars().first()
    if video is None:
        raise HTTPException(status_code=404, detail="video not found")
    await _batch(session, tenant, video.batch_id)
    return video


async def _drafts(session: AsyncSession, batch_id: uuid.UUID) -> list[DraftOut]:
    rows = (await session.execute(
        select(ListingPublication, Asset.group_key, EtsyConnection.shop_name, EtsyConnection.display_name)
        .join(GeneratedContent, GeneratedContent.id == ListingPublication.content_id)
        .join(Asset, Asset.id == GeneratedContent.asset_id)
        .join(EtsyConnection, EtsyConnection.id == ListingPublication.connection_id)
        .where(GeneratedContent.batch_id == batch_id, ListingPublication.state != "deleted_on_etsy")
        .order_by(ListingPublication.created_at)
    )).all()
    return [DraftOut(content_id=p.content_id, group_key=group or "", connection_id=p.connection_id,
                     shop_name=display or name, video_state=p.video_state, video_error=p.video_error,
                     video_id=p.video_sent_id) for p, group, name, display in rows]


@router.get("/batches/{batch_id}/videos", response_model=VideosOut)
async def batch_videos(
    batch_id: uuid.UUID,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> VideosOut:
    batch = await _batch(session, tenant, batch_id)
    videos = await listing_videos.of_batch(session, batch.id)
    now = _now()
    for video in videos:
        updated = video.updated_at if video.updated_at.tzinfo else video.updated_at.replace(tzinfo=timezone.utc)
        if video.status == "processing" and (now - updated).total_seconds() > READ_AGAIN_AFTER_SECONDS:
            # One job a video and two minutes at most (the job's id), so a page that asks often queues nothing more.
            await enqueuer.enqueue("process_listing_video", str(video.id),
                                   _job_id=f"listing-video:{video.id}:{int(now.timestamp()) // READ_AGAIN_AFTER_SECONDS}")
    return VideosOut(limits=video_rules.limits(), videos=[_out(v) for v in videos],
                     drafts=await _drafts(session, batch.id) if videos else [])


@router.post("/batches/{batch_id}/videos", response_model=VideoOut, status_code=201)
async def open_video(
    batch_id: uuid.UUID,
    body: VideoIn,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> VideoOut:
    """A new video for a listing; nothing is stored until its first piece arrives. A file Etsy would not take
    for its kind or size is refused here, before any of it is sent."""
    from app.api.mockups import _room

    batch = await _batch(session, tenant, batch_id)
    if batch.is_example:
        raise HTTPException(status_code=409, detail=examples.NOT_SENT)
    name = listing_videos.clean_name(body.filename)
    problem = video_rules.file_problem(name, body.size)
    if problem:
        raise HTTPException(status_code=413 if body.size > video_rules.MAX_BYTES else 422, detail=problem)
    shop = None
    if body.connection_id is not None:
        shop = await owned_shop(session, tenant.id, body.connection_id)
        if shop is None:
            raise HTTPException(status_code=404, detail="shop not found")
    group = listing_videos.group_for(body.upload_folder, name, batch.grouping_mode) if body.place else (body.group_key or "")
    await _room(session, storage, tenant, body.size)
    root = listing_videos.path_of(storage, f"{tenant.id}/{batch.id}/video/x").parent
    if shutil.disk_usage(root).free < body.size + DISK_RESERVE:
        raise HTTPException(status_code=507, detail=DISK_FULL)
    video = ListingVideo(
        id=uuid.uuid4(), tenant_id=tenant.id, batch_id=batch.id, group_key=group, connection_id=shop.id if shop else None,
        filename=name, upload_folder=(body.upload_folder or None) if body.place else None,
        placed="upload" if body.place else "slot", size=body.size, received=0, status="receiving",
    )
    video.file_key = listing_videos.file_key(tenant.id, batch.id, video.id, name)
    session.add(video)
    audit.record(session, "listing_video.started", actor=tenant, target_tenant_id=tenant.id, by="seller",
                 object_id=str(video.id), size=body.size)
    await session.commit()
    return _out(video)


def _append(part: Any, target: Any, skip: int) -> None:
    with open(part, "rb") as src, open(target, "ab") as dst:
        src.seek(skip)
        shutil.copyfileobj(src, dst, 1024 * 1024)


@router.put("/listing-videos/{video_id}/chunk", response_model=VideoOut)
async def put_chunk(
    video_id: uuid.UUID,
    request: Request,
    offset: int = Query(ge=0),
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> VideoOut:
    """One piece (8 MB at most). A piece sent again changes nothing; one that leaves a gap is refused and told
    where the file ends. The last piece hands the video to the worker."""
    video = await _own(session, tenant, video_id)
    if video.status != "receiving" or not video.file_key:
        return _out(video)  # a last piece sent twice: it is already with the worker
    key = video.file_key
    await session.commit()  # no connection is held while the piece arrives (core/db_release.py)
    target = listing_videos.path_of(storage, key)
    part = target.with_name(f"{video_id}.{offset}.{uuid.uuid4().hex[:8]}.part")
    written = 0
    done = False
    try:
        with open(part, "wb") as fh:
            async for piece in request.stream():
                written += len(piece)
                if written > CHUNK_BYTES:
                    raise HTTPException(status_code=413, detail=f"A piece is at most {CHUNK_BYTES // (1024 * 1024)} MB.")
                fh.write(piece)
        video = await _own(session, tenant, video_id, lock=True)
        if video.status != "receiving":
            return _out(video)
        if offset + written > video.size:
            raise HTTPException(status_code=422, detail="This piece goes past the end of the file.")
        if offset > video.received:
            raise HTTPException(status_code=409, detail={"message": "A piece is missing before this one.", "received": video.received})
        if offset + written > video.received:
            await asyncio.to_thread(_append, part, target, video.received - offset)
            video.received = offset + written
            video.updated_at = _now()
        if video.received == video.size:
            video.status, done = "processing", True
            storage_cap.added(tenant.id, video.size)
        await session.commit()
    finally:
        part.unlink(missing_ok=True)
    if done:
        await enqueuer.enqueue("process_listing_video", str(video.id), _job_id=f"listing-video:{video.id}")
    return _out(video)


@router.get("/listing-videos/{video_id}", response_model=VideoOut)
async def video_state(
    video_id: uuid.UUID, session: AsyncSession = Depends(get_session, scope="function"), tenant: Tenant = Depends(active_tenant)
) -> VideoOut:
    return _out(await _own(session, tenant, video_id))


@router.get("/listing-videos/{video_id}/poster")
async def poster(
    video_id: uuid.UUID,
    request: Request,
    v: str | None = None,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> Response:
    video = await _own(session, tenant, video_id)
    if not video.poster_key or video.files_removed_at is not None:
        raise HTTPException(status_code=404, detail="This file is no longer stored.")
    return await images.send_stored(request, storage, key=video.poster_key, url_version=images.stamp(video.poster_key),
                                    asked=v, size=None)


@router.delete("/listing-videos/{video_id}", status_code=204)
async def remove(
    video_id: uuid.UUID,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    storage: Storage = Depends(get_storage),
) -> None:
    """The video leaves the listing here: drafts made from now on get none. Nothing on Etsy changes."""
    video = await _own(session, tenant, video_id, lock=True)
    audit.destructive(session, "listing_video.deleted", actor=tenant, tenant_id=tenant.id, shop_id=video.connection_id,
                      object_id=video.id)
    await listing_videos.drop(session, storage, video)
    await session.commit()
    return None


@router.post("/listing-videos/{video_id}/send", response_model=DraftOut)
async def send_to_listing(
    video_id: uuid.UUID,
    body: SendIn,
    session: AsyncSession = Depends(get_session, scope="function"),
    tenant: Tenant = Depends(active_tenant),
    enqueuer: Enqueuer = Depends(get_enqueuer),
) -> DraftOut:
    """"Try again" / "Add to this draft" (only the video is sent, after looking on the listing for it) and
    "Replace video" (the one on the listing is taken off first). Only on the seller's click, confirmed."""
    if not body.confirm:
        raise HTTPException(status_code=422, detail=CONFIRM)
    video = await _own(session, tenant, video_id)
    if video.status != "ready" or video.files_removed_at is not None:
        raise HTTPException(status_code=409, detail=listing_videos.NO_LONGER_KEPT if video.files_removed_at else NOT_READY)
    shop = await owned_shop(session, tenant.id, body.connection_id)
    content = await session.get(GeneratedContent, body.content_id)
    if shop is None or content is None or content.tenant_id != tenant.id or content.batch_id != video.batch_id:
        raise HTTPException(status_code=404, detail="listing not found")
    if video.connection_id is not None and video.connection_id != shop.id:
        raise HTTPException(status_code=404, detail="listing not found")
    asset = await session.get(Asset, content.asset_id)
    if asset is None or (asset.group_key or "") != video.group_key:
        raise HTTPException(status_code=404, detail="listing not found")
    publication = (await session.execute(select(ListingPublication).where(
        ListingPublication.content_id == content.id, ListingPublication.connection_id == shop.id,
        ListingPublication.state != "deleted_on_etsy").with_for_update())).scalars().first()
    if publication is None:
        raise HTTPException(status_code=404, detail="listing not found")
    if publication.video_state == "sending":
        raise HTTPException(status_code=409, detail=BUSY)
    mode = "replace" if body.replace else "retry"
    publication.video_state, publication.video_error, publication.video_sent_id = "sending", None, video.id
    audit.record(session, "listing.video_sent", actor=tenant, target_tenant_id=tenant.id, by="seller",
                 object_id=str(publication.id), trigger="seller_click", mode=mode)
    await session.commit()
    await enqueuer.enqueue("attach_listing_video", f"{publication.id}|{video.id}|{mode}",
                           _job_id=f"listing-video-send:{publication.id}:{uuid.uuid4().hex[:8]}")
    return DraftOut(content_id=content.id, group_key=video.group_key, connection_id=shop.id,
                    shop_name=shop.display_name or shop.shop_name, video_state="sending", video_id=video.id)
