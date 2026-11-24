"""A listing's video in the worker (feature/listing-video).

``process_listing_video``: a video that has arrived is read (duration, frame size, sound track: its own boxes,
pipeline/video_probe.py), checked against Etsy's rules (pipeline/video_rules.py) and given a poster frame,
inside the worker's memory budget with a small lease of its own. Nothing is transcoded.

``after_draft``: called by the draft job once the draft and its images are made. The listing's video, when it
has one, is uploaded then. It never fails the draft: a video Etsy refuses is recorded on the draft's record
with Etsy's reason, for the seller's "Try again". A listing without a video makes no request and no change.

``attach_listing_video``: the seller's "Try again" (only the video is sent again, after looking on the draft
for it) and "Replace video" (the video on the listing is taken off first: the seller's click, never ours).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core import limits
from app.core.config import get_settings
from app.db.models import ConnectionStatus, EtsyConnection, ListingPublication, ListingVideo, Tenant
from app.etsy.api import EtsyApiClient, RateLimitExceeded
from app.etsy.connection import ConnectionService
from app.etsy.errors import EtsyError, explain
from app.pipeline import listing_videos, video_probe, video_rules
from app.pipeline.storage import LocalStorage

logger = logging.getLogger(__name__)

#: The lease a video's reading takes of the worker's memory budget: the frame reader's own peak for one
#: 1080p frame, with room (the file itself is never loaded).
VIDEO_MB = 150
MIME = {".mp4": "video/mp4", ".mov": "video/quicktime"}
NO_ANSWER = ("Etsy did not answer in time. Press Try again: the app looks on the draft first, so the video is "
             "never sent twice.")
BUDGET = "Today's Etsy requests are used up. Try again after the reset."
OUR_SIDE = "Something went wrong on our side while sending the video. Try again in a minute."


class _Gone(Exception):
    pass


def _storage(ctx: dict[str, Any]) -> Any:
    return ctx.get("storage") or LocalStorage(get_settings().storage_dir)


def _read(path: Any) -> video_probe.Probe:
    with open(path, "rb") as fh:
        return video_probe.read(fh, path.stat().st_size)


async def process_listing_video(ctx: dict[str, Any], video_id: str) -> str:
    from app.workers import memory_budget
    from app.workers.fair import _enqueue_of

    storage = _storage(ctx)
    async with memory_budget.reserve(ctx.get("redis"), "process_listing_video", VIDEO_MB) as fits:
        if not fits:
            await memory_budget.wait_turn(_enqueue_of(ctx), "process_listing_video", video_id)
            return "waiting"
        async with ctx["sessionmaker"]() as session:
            video = await session.get(ListingVideo, uuid.UUID(video_id))
            if video is None or video.status != "processing" or not video.file_key:
                return "nothing"
            path = listing_videos.path_of(storage, video.file_key)
            problem: str | None = None
            probe = None
            try:
                probe = await asyncio.to_thread(_read, path)
            except (video_probe.NotAVideo, OSError):
                problem = video_rules.UNREADABLE
            if probe is not None:
                video.duration_ms = int(round(probe.seconds * 1000))
                video.width, video.height, video.has_audio = probe.width, probe.height, probe.has_audio
                problem = video_rules.check(filename=video.filename, size=video.size, seconds=probe.seconds).problem
            video.updated_at = datetime.now(timezone.utc)
            if problem:
                # The seller is told what is wrong; a file Etsy would not take is not kept.
                await asyncio.to_thread(listing_videos.delete_files, storage, video)
                video.status, video.error, video.file_key = "failed", problem, None
                await session.commit()
                return "refused"
            data = await asyncio.to_thread(listing_videos.poster, path, probe.seconds) if probe else None
            if data:
                key = listing_videos.poster_key(video.tenant_id, video.batch_id, video.id)
                await asyncio.to_thread(storage.put, key, data, "image/jpeg")
                video.poster_key = key
            video.status, video.error = "ready", None
            await listing_videos.replace_older(session, storage, video)
            await session.commit()
            return "ready"


def _client(ctx: dict[str, Any], http: httpx.AsyncClient, connection_id: uuid.UUID) -> EtsyApiClient:
    from app.workers.publish import _renewer

    settings = get_settings()
    return EtsyApiClient(
        client_id=settings.etsy_client_id, shared_secret=settings.etsy_client_secret, http_client=http,
        bucket=ctx["bucket"], quota=ctx["quota"], usage=ctx.get("usage"), cache=ctx.get("redis"),
        shop=connection_id, renew=_renewer(ctx, connection_id),
    )


def _active(answer: dict[str, Any]) -> list[dict[str, Any]]:
    return [v for v in (answer or {}).get("results") or [] if (v.get("video_state") or "active") == "active"]


async def send(ctx: dict[str, Any], session: Any, publication: ListingPublication, video: ListingVideo,
               connection: EtsyConnection, access_token: str, tenant: Tenant, *, mode: str) -> str:
    """Put ``video`` on the draft. ``mode``: "fresh" (the draft was just made: one request), "retry" (look on
    the listing first; a video that is there is the one that was sent) or "replace" (the seller's click: what
    is on the listing is taken off, then this one is sent). Never raises: the outcome is on the draft's record."""
    from app.workers import publish as publish_worker

    storage = _storage(ctx)
    publication.video_state, publication.video_error, publication.video_sent_id = "sending", None, video.id
    await session.commit()
    shop_id, listing_id, shop_name = connection.shop_id, publication.etsy_listing_id, connection.shop_name
    error: str | None = None
    etsy_id: int | None = None
    try:
        if not video.file_key or video.files_removed_at is not None:
            raise _Gone
        path = storage._path(video.file_key)  # noqa: SLF001
        if not path.exists():
            raise _Gone
        kw = {"access_token": access_token, "tenant_id": tenant.id, "tenant_limit": limits.ceiling_limit(tenant)}
        async with publish_worker.httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=180.0)) as http:
            client = _client(ctx, http, connection.id)
            there = _active(await client.get_listing_videos(listing_id, **kw)) if mode != "fresh" else []
            if mode == "retry" and there:
                etsy_id = there[0].get("video_id")  # it arrived the first time: nothing is sent again
            else:
                for old in there:  # "replace" only
                    await client.delete_listing_video(shop_id, listing_id, int(old["video_id"]), **kw)
                with open(path, "rb") as fh:
                    answer = await client.upload_listing_video(
                        shop_id, listing_id, video=fh, filename=video.filename,
                        mime_type=MIME.get(listing_videos.extension(video.filename), "video/mp4"), **kw)
                etsy_id = (answer or {}).get("video_id")
    except _Gone:
        error = listing_videos.NO_LONGER_KEPT
    except RateLimitExceeded:
        error = BUDGET
    except EtsyError as exc:
        error = explain(exc, shop_name)
    except (httpx.TransportError, OSError):
        error = NO_ANSWER
    except Exception:  # noqa: BLE001 - a video never fails the draft it belongs to
        logger.exception("listing video: sending failed for publication %s", publication.id)
        error = OUR_SIDE
    publication = await session.get(ListingPublication, publication.id)
    if publication is None:
        return "gone"
    if error is None:
        publication.video_state, publication.video_error, publication.etsy_video_id = "attached", None, etsy_id
    else:
        publication.video_state, publication.video_error = "failed", error
    await session.commit()
    return publication.video_state


async def after_draft(ctx: dict[str, Any], session: Any, *, content: Any, connection: EtsyConnection, asset: Any,
                      access_token: str, tenant: Tenant) -> None:
    """The draft job's last step. No video for this listing: nothing is read from Etsy and nothing sent."""
    try:
        video = await listing_videos.for_draft(session, content.batch_id, asset.group_key, connection.id)
        if video is None:
            return
        from sqlalchemy import select

        publication = (await session.execute(select(ListingPublication).where(
            ListingPublication.content_id == content.id, ListingPublication.connection_id == connection.id))).scalars().first()
        if publication is None or publication.video_state == "attached":
            return
        # A draft this job only finished (it was made by an earlier try) may hold the video already: look first.
        mode = "retry" if publication.video_state is not None else "fresh"
        await send(ctx, session, publication, video, connection, access_token, tenant, mode=mode)
    except Exception:  # noqa: BLE001 - the draft is made whatever became of its video
        logger.exception("listing video: could not be added after the draft of content %s", getattr(content, "id", None))
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass


async def attach_listing_video(ctx: dict[str, Any], arg: str) -> str:
    """``arg``: "<publication id>|<video id>|<retry or replace>". The seller's own work (their click)."""
    from app.workers.profiles import _run_gated

    publication_id, video_id, mode = (arg.split("|") + ["retry"])[:3]
    async with ctx["sessionmaker"]() as session:
        publication = await session.get(ListingPublication, uuid.UUID(publication_id))
        if publication is None:
            return "gone"
        connection = await session.get(EtsyConnection, publication.connection_id)
        if connection is None:
            return "gone"
        tenant_id = connection.tenant_id
    return await _run_gated(ctx, "attach_listing_video", arg, tenant_id,
                            lambda: _attach(ctx, uuid.UUID(publication_id), uuid.UUID(video_id), mode))


async def _attach(ctx: dict[str, Any], publication_id: uuid.UUID, video_id: uuid.UUID, mode: str) -> str:
    from app.workers import publish as publish_worker

    settings = get_settings()
    # The draft worker's own way to the shop's key (one place for it).
    service = ConnectionService(publish_worker.get_cipher(), client_id=settings.etsy_client_id, token_url=settings.etsy_oauth_token_url)
    async with ctx["sessionmaker"]() as session:
        publication = await session.get(ListingPublication, publication_id)
        video = await session.get(ListingVideo, video_id)
        if publication is None or video is None:
            return "gone"
        connection = await session.get(EtsyConnection, publication.connection_id)
        tenant = await session.get(Tenant, video.tenant_id)
        if connection is None or tenant is None or connection.tenant_id != tenant.id or connection.status is not ConnectionStatus.active:
            publication.video_state, publication.video_error = "failed", "This shop is not connected; connect it again, then try again."
            await session.commit()
            return "failed"
        try:
            token = await service.get_valid_access_token(session, connection)
        except Exception:  # noqa: BLE001 - said to the seller, never a silent stop
            publication.video_state, publication.video_error = "failed", "Etsy did not accept this shop's sign-in; connect it again, then try again."
            await session.commit()
            return "failed"
        return await send(ctx, session, publication, video, connection, token, tenant,
                          mode="replace" if mode == "replace" else "retry")
