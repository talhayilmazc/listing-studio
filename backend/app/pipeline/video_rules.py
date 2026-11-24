"""A listing's video: Etsy's rules, in one place (feature/listing-video).

What Etsy's own developer pages say (read 24 November 2026; nothing else was read, and Etsy's site was not):

* ``uploadListingVideo``: ``POST /v3/application/shops/{shop_id}/listings/{listing_id}/videos``, multipart, with
  ``video`` (the file) and ``name`` (its file name), or ``video_id`` of a video the shop already has. OAuth scope
  ``listings_w`` (which the app already asks for: no new scope). "It's important to consider there is a rate
  limiting to perform 10 uploads per listing per day." (API reference, operation uploadListingVideo.)
* "A listing can have up to 2 active videos." A second upload does not replace the first; a third answers 409
  ("Delete one with the deleteListingVideo endpoint, then upload the new video."), and so does a video that is
  already on the listing. (Developer tutorial "Listings", section on videos.) Listyro sends one video a listing.
* ``getListingVideos`` (``GET /v3/application/listings/{listing_id}/videos``) and ``deleteListingVideo``
  (``DELETE /v3/application/shops/{shop_id}/listings/{listing_id}/videos/{video_id}``, scope ``listings_w``).

What those pages do NOT say: the file formats, the largest file, how long a video may be, its resolution, and
whether its sound is kept. Those limits are below: 5-15 seconds and MP4 or MOV from the owner's brief, and 100 MB
as the largest file (what Etsy's seller Help is remembered to say; that page was not read: no program reads
Etsy's site). ``CONFIRMED`` says where each comes from; when a person has checked one against Etsy's own page,
only the constant and its entry change. Nothing here tells a seller that Etsy removes a video's sound, and no
resolution is asked for: no page we read says either.

The browser's copy: frontend/lib/videoRules.ts (both read tests/fixtures/video_rule_cases.json).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OPERATION = "uploadListingVideo"
SCOPE = "listings_w"
#: Etsy's API reference: "10 uploads per listing per day".
UPLOADS_PER_LISTING_PER_DAY = 10
#: Etsy's developer tutorial: "A listing can have up to 2 active videos." Listyro attaches one.
ETSY_ACTIVE_VIDEOS = 2
VIDEOS_PER_LISTING = 1

MIN_SECONDS = 5.0
MAX_SECONDS = 15.0
#: A container's own duration is rarely a round number (a "15 second" clip reads 15.02): this much is let through.
SECONDS_TOLERANCE = 0.25
MAX_BYTES = 100 * 1024 * 1024
EXTENSIONS = (".mp4", ".mov")

#: Where each limit comes from: "api" (Etsy's developer pages, read as above), "brief" (the owner's brief; not on
#: those pages), "unconfirmed" (remembered, to be checked by a person) or "not stated" (no limit is applied).
CONFIRMED = {
    "endpoint": "api", "scope": "api", "uploads_per_listing_per_day": "api", "active_videos": "api",
    "seconds": "brief", "bytes": "unconfirmed", "extensions": "brief", "resolution": "not stated", "sound": "not stated",
}

NOT_A_VIDEO = "Choose an MP4 or MOV video."
UNREADABLE = "This file could not be read as a video. Export it again as MP4 or MOV and add it again."


def seconds_text(seconds: float) -> str:
    """22 s, 4.6 s: whole when it is whole to a tenth."""
    rounded = round(seconds, 1)
    return f"{int(rounded)} s" if rounded == int(rounded) else f"{rounded} s"


def megabytes(size: int) -> str:
    mb = size / (1024 * 1024)
    return f"{mb:.0f} MB" if mb >= 10 else f"{mb:.1f} MB"


def duration_label(seconds: float | None) -> str:
    """0:08, as a player shows it."""
    if seconds is None:
        return ""
    whole = int(round(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def file_problem(filename: str, size: int) -> str | None:
    """What is wrong with a file before any of it is sent: its kind, then its size."""
    if not filename.lower().endswith(EXTENSIONS):
        return NOT_A_VIDEO
    if size > MAX_BYTES:
        return f"This video is {megabytes(size)}; Etsy takes videos up to {megabytes(MAX_BYTES)}."
    if size <= 0:
        return UNREADABLE
    return None


def duration_problem(seconds: float | None) -> str | None:
    if seconds is None or seconds <= 0:
        return UNREADABLE
    if seconds < MIN_SECONDS - SECONDS_TOLERANCE or seconds > MAX_SECONDS + SECONDS_TOLERANCE:
        return f"This video is {seconds_text(seconds)}; Etsy takes {int(MIN_SECONDS)}–{int(MAX_SECONDS)} s."
    return None


@dataclass
class Checked:
    #: The first thing wrong, in one plain sentence; None: it can be sent.
    problem: str | None


def check(*, filename: str, size: int, seconds: float | None) -> Checked:
    """A video as the worker read it: every rule, the first one broken."""
    return Checked(file_problem(filename, size) or duration_problem(seconds))


def limits() -> dict[str, Any]:
    """The limits as the page shows them (and checks before sending anything)."""
    return {"min_seconds": MIN_SECONDS, "max_seconds": MAX_SECONDS, "seconds_tolerance": SECONDS_TOLERANCE,
            "max_bytes": MAX_BYTES, "extensions": list(EXTENSIONS)}
