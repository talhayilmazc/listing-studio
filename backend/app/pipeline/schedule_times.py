"""When a scheduled listing's draft is made and when it goes live, said the same way everywhere
(fix/onboarding-followups, Part J).

The Schedule page (``api/scheduled.py``) and the send summary (``api/send.py``, "Show each listing")
showed one time: when it goes live. A seller could not see that the draft is made two hours before,
nor that "Now as drafts" never publishes anything. Every scheduled listing now carries one line:

* its draft exists: "Draft created at 1:41 PM · goes live at 3:41 PM CST" (both in the seller's time
  zone, the account's; the date too when the two fall on different days);
* its draft is still to be made: "Draft is created 2 hours before it goes live" (``group_plan.DRAFT_LEAD``);
  once that time has come: "Draft is being created · goes live at 3:41 PM CST";
* sent as a draft with no go-live time: "Stays a draft in your shop. Nothing will publish it."

Pure: no database, no Etsy. The clock is the app's (12-hour, with the zone's abbreviation, as every
other time the seller reads).
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.core.timezones import zone_abbreviation
from app.pipeline.group_plan import DRAFT_LEAD

STAYS_A_DRAFT = "Stays a draft in your shop. Nothing will publish it."
_LEAD_HOURS = int(DRAFT_LEAD.total_seconds() // 3600)
BEFORE_DRAFT = f"Draft is created {_LEAD_HOURS} hour{'s' if _LEAD_HOURS != 1 else ''} before it goes live"
BEING_DRAFTED = "Draft is being created"
PUBLISH_NOW = "Draft is created now and goes live right after"


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _clock(at: datetime, zone: str, *, day: bool) -> str:
    local = _aware(at).astimezone(ZoneInfo(zone))
    text = f"{local:%I:%M %p}".lstrip("0")
    return f"{local:%b} {local.day}, {text}" if day else text


def both(draft_at: datetime, live_at: datetime, zone: str, *, verb: str = "goes live") -> str:
    """ "Draft created at 1:41 PM · goes live at 3:41 PM CST"; with the dates when the days differ."""
    zone_d, zone_l = (_aware(t).astimezone(ZoneInfo(zone)) for t in (draft_at, live_at))
    day = zone_d.date() != zone_l.date()
    abbr_d, abbr_l = zone_abbreviation(_aware(draft_at), zone), zone_abbreviation(_aware(live_at), zone)
    first = _clock(draft_at, zone, day=day) + ("" if abbr_d == abbr_l else f" {abbr_d}")
    return f"Draft created at {first} · {verb} at {_clock(live_at, zone, day=day)} {abbr_l}"


def to_be_drafted(live_at: datetime, zone: str, now: datetime | None = None) -> str:
    """A go-live whose draft does not exist yet."""
    now = now or datetime.now(timezone.utc)
    if _aware(live_at) - DRAFT_LEAD > now:
        return BEFORE_DRAFT
    return f"{BEING_DRAFTED} · goes live at {_clock(live_at, zone, day=False)} {zone_abbreviation(_aware(live_at), zone)}"


#: The go-live verb for a scheduled draft by its Schedule-page status.
VERBS = {"published": "went live", "failed": "was to go live", "not_published": "was to go live"}
