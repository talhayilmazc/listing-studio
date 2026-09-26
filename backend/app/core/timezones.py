"""Wall-clock times in the seller's own time zone (scheduled publishing).

Each account has an IANA time zone (``tenant.time_zone``, detected from the
browser on first sign-in, editable in Settings). A schedule is entered as a
wall-clock time in that zone and converted to a UTC instant exactly once, here,
when it is saved; the stored instant is what runs. The conversion uses the
zone's rules for that date, so a time chosen before a daylight-saving change
still goes out at the wall-clock time the seller picked.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones


class TimeRefused(ValueError):
    """The wall-clock time can't be used (message is safe to show)."""


@lru_cache(maxsize=1)
def _zones() -> frozenset[str]:
    return frozenset(available_timezones())


def valid_zone(name: str | None) -> bool:
    if not name or name not in _zones():
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def parse_wall_clock(value: str) -> datetime:
    """``YYYY-MM-DDTHH:MM`` (as a datetime-local input gives it) → naive datetime."""
    try:
        naive = datetime.fromisoformat(value)
    except ValueError:
        raise TimeRefused("choose a date and time") from None
    if naive.tzinfo is not None:
        raise TimeRefused("send the time without a zone; your account's time zone is used")
    return naive.replace(second=0, microsecond=0)


def to_utc(value: str, zone: str) -> datetime:
    """The UTC instant a wall-clock time in ``zone`` means.

    A time that doesn't exist on that day (clocks go forward past it) is
    refused; a time that happens twice (clocks go back) is the first of the two,
    and the zone abbreviation shown with it says which.
    """
    naive = parse_wall_clock(value)
    tz = ZoneInfo(zone)
    local = naive.replace(tzinfo=tz)  # fold=0: the first occurrence when ambiguous
    instant = local.astimezone(timezone.utc)
    # Round trip: a wall-clock time inside a spring-forward gap comes back different.
    if instant.astimezone(tz).replace(tzinfo=None) != naive:
        raise TimeRefused(
            f"{naive:%H:%M} doesn't exist on {naive:%b} {naive.day} in your time zone "
            "(the clocks go forward then); choose another time"
        )
    return instant


def zone_abbreviation(instant: datetime, zone: str) -> str:
    """"CDT", "EST", "GMT+3": the zone's name at that instant."""
    return instant.astimezone(ZoneInfo(zone)).tzname() or zone


def wall_clock(instant: datetime, zone: str) -> str:
    """The instant as ``YYYY-MM-DDTHH:MM`` in ``zone``."""
    return instant.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%dT%H:%M")

