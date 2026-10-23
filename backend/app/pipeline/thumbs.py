"""Smaller copies of stored images for the screens (page weight, 2026-10-23).

Everything a seller looks at leaves the production server through a 14 Mbps uplink,
so a screen is only ever sent what it can show:

* ``small`` (240 px WebP): tiles of up to about 120 CSS px (lists, the grouping board,
  mockup results), sharp on a 2x screen;
* ``thumb`` (480 px WebP): grids and cards;
* ``medium`` (1200 px WebP): viewers and editors.

The full stored file is only for download, a ZIP and the Etsy upload. Each copy is made
once and stored beside its source as ``<source key>.d<VERSION>.<size>[-<ratio>].webp``,
so it is deleted with it (``Storage.delete_with_derivatives``) and counted with the
account's other files. ``python -m app.cli thumbnails`` makes the missing ones for files
stored before this. Presentation only: nothing here touches what is uploaded to Etsy.
"""

from __future__ import annotations


from app.pipeline.images import PREVIEW_ASPECTS, resize_preview
from app.pipeline.storage import DERIVATIVE, LEGACY, Storage

__all__ = ["DERIVATIVE", "LEGACY", "SIZES", "VERSION", "ensure", "key_for", "make", "size_for"]

#: Width in pixels of each size a screen may ask for.
SIZES: dict[str, int] = {"small": 240, "thumb": 480, "medium": 1200}
QUALITY: dict[str, int] = {"small": 74, "thumb": 76, "medium": 80}
ASPECTS = PREVIEW_ASPECTS

#: Bump when the crop or encoding changes: it is in every stored copy's key and in the
#: image URLs (``frontend/lib/api.ts`` ``IMAGE_VERSION``), so a browser that kept an
#: earlier copy for a year asks for the new one instead.
VERSION = 1

#: Widths from before (``?w=112`` … ``896``), for a page still open from before an update.
_LEGACY_WIDTHS = {112: "small", 224: "small", 448: "thumb", 896: "medium"}


class UnknownSize(ValueError):
    pass


def size_for(size: str | None, legacy_width: int | None = None) -> str | None:
    """The size a request asks for (``None``: the full file). Unknown values are refused,
    so no request can make the server resize to an arbitrary width."""
    if size is not None:
        if size not in SIZES:
            raise UnknownSize(f"size must be one of {sorted(SIZES)}")
        return size
    if legacy_width is not None:
        if legacy_width not in _LEGACY_WIDTHS:
            raise UnknownSize(f"size must be one of {sorted(SIZES)}")
        return _LEGACY_WIDTHS[legacy_width]
    return None


def key_for(source: str, size: str, aspect: str | None = None) -> str:
    ratio = f"-{aspect.replace(':', 'x')}" if aspect else ""
    return f"{source}.d{VERSION}.{size}{ratio}.webp"


def make(data: bytes, size: str, aspect: str | None = None) -> bytes:
    """A WebP copy ``SIZES[size]`` wide (never wider than the source), cropped to
    ``aspect`` around the artwork if one is given."""
    return resize_preview(data, SIZES[size], aspect, fmt="WEBP", quality=QUALITY[size])


def ensure(storage: Storage, source: str, size: str, aspect: str | None = None) -> bytes:
    """The stored copy, made and stored first if it is not there yet. Raises
    ``FileNotFoundError`` when the source itself is gone."""
    key = key_for(source, size, aspect)
    try:
        return storage.get(key)
    except FileNotFoundError:
        pass
    data = make(storage.get(source), size, aspect)
    storage.put(key, data, "image/webp")
    return data
