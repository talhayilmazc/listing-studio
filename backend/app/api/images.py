"""How every image is sent to the screens (page weight, 2026-10-23).

A browser never downloads the same image twice:

* an image URL that carries the image's current version (``?v=``) is kept for a year
  (``private, max-age=31536000, immutable``): a changed image has a new version, so a new URL;
* any other image URL is checked every time (``private, no-cache``);
* every image has an ``ETag``, and ``If-None-Match`` with it is answered ``304`` with no body.

The caller has already checked that the image is the account's own: a 304 is only
ever an answer about one's own image, so an ETag tells another account nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable

from fastapi import HTTPException, Request, Response

from app.pipeline import thumbs
from app.pipeline.images import ImageProcessingError
from app.pipeline.storage import Storage

IMMUTABLE = "private, max-age=31536000, immutable"
REVALIDATE = "private, no-cache"


def stamp(*parts: object) -> str:
    """A short version of an image from what identifies its bytes (its storage key: a key
    is never written twice with different bytes)."""
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def size_of(size: str | None, w: int | None) -> str | None:
    """The size asked for (None: the full file); a refused value is a 400."""
    try:
        return thumbs.size_for(size, w)
    except thumbs.UnknownSize as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def etag(version: str, size: str | None = None, aspect: str | None = None) -> str:
    return f'"{version}.{size or "full"}{"-" + aspect.replace(":", "x") if aspect else ""}.d{thumbs.VERSION}"'


def _matches(request: Request, tag: str) -> bool:
    asked = request.headers.get("if-none-match")
    if not asked:
        return False
    return asked.strip() == "*" or tag in {t.strip().removeprefix("W/") for t in asked.split(",")}


async def send(
    request: Request,
    *,
    tag: str,
    versioned: bool,
    media_type: str,
    load: Callable[[], bytes],
) -> Response:
    """The image (``load``, run off the event loop) or a 304, with its cache headers."""
    headers = {"ETag": tag, "Cache-Control": IMMUTABLE if versioned else REVALIDATE}
    if _matches(request, tag):
        return Response(status_code=304, headers=headers)
    try:
        data = await asyncio.to_thread(load)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="image not found") from exc
    except ImageProcessingError as exc:
        raise HTTPException(status_code=422, detail="image cannot be resized") from exc
    return Response(content=data, media_type=media_type, headers=headers)


async def send_stored(
    request: Request,
    storage: Storage,
    *,
    key: str,
    url_version: str,
    asked: str | None,
    size: str | None,
    aspect: str | None = None,
    media_type: str = "image/jpeg",
) -> Response:
    """A stored image, or its copy of ``size`` (made once and stored: pipeline/thumbs.py).
    ``url_version`` is what ``?v=`` must be for the browser to keep it; the ETag follows
    the stored file itself (``key``)."""
    tag = etag(stamp(key), size, aspect)
    versioned = asked is not None and asked == url_version
    if size is None:
        return await send(request, tag=tag, versioned=versioned, media_type=media_type, load=lambda: storage.get(key))
    return await send(request, tag=tag, versioned=versioned, media_type="image/webp",
                      load=lambda: thumbs.ensure(storage, key, size, aspect))
