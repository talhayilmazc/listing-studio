"""A listing's video: Etsy's rules, in one place (feature/listing-video).

Two sources, each limit marked with its own (``CONFIRMED``):

* **Etsy's developer pages** (read 24 November 2026). ``uploadListingVideo``:
  ``POST /v3/application/shops/{shop_id}/listings/{listing_id}/videos``, multipart, with ``video`` (the file) and
  ``name`` (its file name), or ``video_id`` of a video the shop already has; OAuth scope ``listings_w`` (which
  the app already asks for: no new scope). "It's important to consider there is a rate limiting to perform 10
  uploads per listing per day." A second upload does not replace the first; one more than Etsy keeps answers 409,
  and so does a video that is already on the listing. ``getListingVideos`` and ``deleteListingVideo`` (scope
  ``listings_w``).
* **Etsy's Help article "How to Add a Listing Video"** (help.etsy.com/hc/en-us/articles/360053206073), read by
  the owner and given to us on 24 November 2026 (no program reads Etsy's site): 3 to 15 seconds; up to 100 MB; at
  least 500 px (1080 px is ideal); an aspect ratio between 2:1 and 1:2; MP4, MOV, FLV, AVI, 3GP or MPEG; silent
  (Etsy removes the sound); up to 2 videos a listing.

Listyro sends one video a listing. "At least 500 px" is read as the frame's shorter side (a 320 x 568 video is
"320 px"); a frame size the file does not say is left to Etsy. Nothing is transcoded.

The browser's copy: frontend/lib/videoRules.ts (both read tests/fixtures/video_rule_cases.json).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OPERATION = "uploadListingVideo"
SCOPE = "listings_w"
HELP_ARTICLE = "https://help.etsy.com/hc/en-us/articles/360053206073"
HELP_READ = "2026-11-24"
#: Etsy's API reference: "10 uploads per listing per day".
UPLOADS_PER_LISTING_PER_DAY = 10
#: Etsy keeps up to 2 videos a listing (its Help article and its developer tutorial). Listyro attaches one.
ETSY_ACTIVE_VIDEOS = 2
VIDEOS_PER_LISTING = 1

MIN_SECONDS = 3.0
MAX_SECONDS = 15.0
#: A container's own duration is rarely a round number (a "15 second" clip reads 15.02): this much is let through.
SECONDS_TOLERANCE = 0.25
MAX_BYTES = 100 * 1024 * 1024
#: The frame's shorter side, at least (1080 is what Etsy calls ideal).
MIN_PIXELS = 500
IDEAL_PIXELS = 1080
#: Between 2:1 and 1:2: the longer side is at most twice the shorter.
MAX_ASPECT = 2.0
ASPECT_TOLERANCE = 0.01
EXTENSIONS = (".mp4", ".mov", ".flv", ".avi", ".3gp", ".mpeg", ".mpg")
#: The one line the slot shows beside a video (Etsy's Help: listing videos are silent).
SILENT = "Etsy shows listing videos without sound."

#: Where each limit comes from: "api" (Etsy's developer pages) or "help" (Etsy's Help article, read by the owner).
CONFIRMED = {
    "endpoint": "api", "scope": "api", "uploads_per_listing_per_day": "api", "active_videos": "help",
    "seconds": "help", "bytes": "help", "pixels": "help", "aspect": "help", "extensions": "help", "sound": "help",
}

NOT_A_VIDEO = "Choose a video file: MP4, MOV, FLV, AVI, 3GP or MPEG."
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


def frame_problem(width: int | None, height: int | None) -> str | None:
    """Too small, or too long and thin. A frame size the file does not say: nothing is said (Etsy decides)."""
    if not width or not height:
        return None
    small, large = min(width, height), max(width, height)
    if small < MIN_PIXELS:
        return f"This video is {small} px; Etsy needs at least {MIN_PIXELS} px."
    ratio = large / small
    if ratio > MAX_ASPECT + ASPECT_TOLERANCE:
        shape = f"{ratio:.1f}:1" if width > height else f"1:{ratio:.1f}"
        return f"This video is {shape}; Etsy takes shapes between 2:1 and 1:2."
    return None


@dataclass
class Checked:
    #: The first thing wrong, in one plain sentence; None: it can be sent.
    problem: str | None


def check(*, filename: str, size: int, seconds: float | None, width: int | None = None, height: int | None = None) -> Checked:
    """A video as the worker read it: every rule, the first one broken."""
    return Checked(file_problem(filename, size) or duration_problem(seconds) or frame_problem(width, height))


def limits() -> dict[str, Any]:
    """The limits as the page shows them (and checks before sending anything)."""
    return {"min_seconds": MIN_SECONDS, "max_seconds": MAX_SECONDS, "seconds_tolerance": SECONDS_TOLERANCE,
            "max_bytes": MAX_BYTES, "min_pixels": MIN_PIXELS, "max_aspect": MAX_ASPECT, "extensions": list(EXTENSIONS)}
