"""A listing's video: where it is kept, which listing it belongs to, and which one a draft gets
(feature/listing-video; the rules: pipeline/video_rules.py).

One video for a listing group of a batch (it goes to every shop), and optionally one for that group in one
shop, which that shop's draft gets instead. The files sit under the batch's own folder, so they go with the
batch, and with the group's photos when upload retention removes those. A video is never an image: it has no
row among the batch's photos, no rank, and can never be the cover.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ListingVideo
from app.pipeline import grouping
from app.pipeline.sku import SkuParser
from app.pipeline.storage import Storage

#: "BR100-video.mp4" belongs with BR100's photos: the word is not part of a SKU.
_VIDEO_WORD = re.compile(r"[-_ ]+(video|clip|movie|reel)$", re.IGNORECASE)
POSTER_WIDTH = 960
POSTER_SECONDS = 30
#: The poster's frame: a second in (a first frame is often black), or the middle of a shorter video.
POSTER_AT = 1.0
NO_LONGER_KEPT = "This video's file is no longer kept (a listing's files are kept for a limited time). Add it again."


def clean_name(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).name[:255]


def extension(filename: str) -> str:
    return PurePosixPath(filename).suffix.lower()


def file_key(tenant_id: uuid.UUID, batch_id: uuid.UUID, video_id: uuid.UUID, filename: str) -> str:
    return f"{tenant_id}/{batch_id}/video/{video_id}{extension(filename)}"


def poster_key(tenant_id: uuid.UUID, batch_id: uuid.UUID, video_id: uuid.UUID) -> str:
    return f"{tenant_id}/{batch_id}/video/{video_id}.poster.jpg"


def group_for(folder: str | None, filename: str, mode: str | None) -> str:
    """The listing group a video uploaded with the batch's photos belongs to: placed by the same rule as a
    photo of that name in that folder (pipeline/grouping.py), "-video" at its end aside."""
    path = PurePosixPath(clean_name(filename))
    stem = _VIDEO_WORD.sub("", path.stem) or path.stem
    key, _ = grouping.place(folder or None, f"{stem}.jpg", SkuParser(), mode)
    return key or ""


def path_of(storage: Storage, key: str) -> Path:
    path = storage._path(key)  # noqa: SLF001 - a plain local file, appended piece by piece and sent as a stream
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


async def of_batch(session: AsyncSession, batch_id: uuid.UUID) -> list[ListingVideo]:
    return list((await session.execute(
        select(ListingVideo).where(ListingVideo.batch_id == batch_id).order_by(ListingVideo.created_at, ListingVideo.id)
    )).scalars())


async def for_draft(session: AsyncSession, batch_id: uuid.UUID, group_key: str | None,
                    connection_id: uuid.UUID) -> ListingVideo | None:
    """The video a draft of this group in this shop gets: the shop's own, else the listing's; only one that is
    ready and still kept. None: the draft is made exactly as one without a video."""
    rows = (await session.execute(
        select(ListingVideo).where(
            ListingVideo.batch_id == batch_id, ListingVideo.group_key == (group_key or ""),
            ListingVideo.status == "ready", ListingVideo.files_removed_at.is_(None),
            ListingVideo.connection_id.is_(None) | (ListingVideo.connection_id == connection_id))
    )).scalars().all()
    own = [v for v in rows if v.connection_id == connection_id]
    return (own or [v for v in rows if v.connection_id is None] or [None])[0]


def delete_files(storage: Storage, video: ListingVideo) -> int:
    freed = 0
    for key in (video.file_key, video.poster_key):
        if not key:
            continue
        try:
            path = storage._path(key)  # noqa: SLF001
            freed += path.stat().st_size
            path.unlink()
        except (FileNotFoundError, OSError):
            pass
    return freed


async def drop(session: AsyncSession, storage: Storage, video: ListingVideo) -> None:
    """The row and its files (only ours: nothing on Etsy changes)."""
    await asyncio.to_thread(delete_files, storage, video)
    await session.delete(video)


async def replace_older(session: AsyncSession, storage: Storage, video: ListingVideo) -> int:
    """A video that is ready takes the place of the one its listing (or its listing in that shop) had: one video
    a listing. Until then the earlier one stays, so a new file Etsy would not take loses the seller nothing."""
    older = (await session.execute(select(ListingVideo).where(
        ListingVideo.batch_id == video.batch_id, ListingVideo.group_key == video.group_key,
        ListingVideo.id != video.id,
        ListingVideo.connection_id.is_(None) if video.connection_id is None else ListingVideo.connection_id == video.connection_id,
    ))).scalars().all()
    for row in older:
        await drop(session, storage, row)
    return len(older)


async def remove_files(session: AsyncSession, storage: Storage, batch_id: uuid.UUID, group_key: str,
                       now: datetime, *, apply: bool) -> tuple[int, int]:
    """Upload retention: a group's video goes with its photos. Returns (files, bytes); the rows stay and say
    the file is no longer kept."""
    rows = (await session.execute(select(ListingVideo).where(
        ListingVideo.batch_id == batch_id, ListingVideo.group_key == (group_key or ""),
        ListingVideo.files_removed_at.is_(None)))).scalars().all()
    files = size = 0
    for video in rows:
        for key in (video.file_key, video.poster_key):
            if not key:
                continue
            try:
                size += storage._path(key).stat().st_size  # noqa: SLF001
                files += 1
            except (FileNotFoundError, OSError):
                pass
        if apply:
            await asyncio.to_thread(delete_files, storage, video)
            video.files_removed_at, video.file_key, video.poster_key = now, None, None
    return files, size


async def place_again(session: AsyncSession, batch_id: uuid.UUID, mode: str | None) -> None:
    """The seller grouped the batch's photos another way: a video that came with them follows the same rule."""
    for video in await of_batch(session, batch_id):
        if video.placed == "upload":
            video.group_key = group_for(video.upload_folder, video.filename, mode)


def ffmpeg() -> str | None:
    """The frame reader, when the image has one (a poster is a nicety: a video without one is as good on Etsy)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - not installed, or no binary for this machine
        return None


def _quiet() -> None:
    try:
        os.nice(10)
    except OSError:
        pass


def poster(path: Path, seconds: float) -> bytes | None:
    """One frame of the video as a JPEG, at most 960 px wide: made by a separate program at low priority, with
    a time limit. The seller's file is an input to it, never run. None when it cannot be made."""
    exe = ffmpeg()
    if exe is None:
        return None
    at = min(POSTER_AT, max(0.0, seconds / 2))
    with tempfile.TemporaryDirectory() as folder:
        out = Path(folder) / "poster.jpg"
        command = [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "1", "-ss", f"{at:.2f}", "-i", str(path),
                   "-frames:v", "1", "-vf", f"scale='min({POSTER_WIDTH},iw)':-2", "-q:v", "4", "-f", "image2", str(out)]
        try:
            subprocess.run(command, check=True, timeout=POSTER_SECONDS, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, preexec_fn=_quiet if os.name == "posix" else None)  # noqa: PLW1509
            data = out.read_bytes()
        except (subprocess.SubprocessError, OSError):
            return None
    return data if data[:2] == b"\xff\xd8" else None


def summary(video: ListingVideo) -> dict[str, Any]:
    """What a log line or an audit row says of a video: sizes and numbers, never its name."""
    return {"size": video.size, "duration_ms": video.duration_ms, "width": video.width, "height": video.height}
