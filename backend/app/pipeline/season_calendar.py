"""The seasonal planner's calendar: the holidays and when we suggest going live for each.

Dated config: the dates come from each holiday's own rule, so the calendar does not run out, and the
windows below are **our advice**, last looked at on :data:`REVIEWED`. Etsy publishes no such windows and
we hold no figures saying these work: the page says so beside them (``ABOUT``). Change a window here and
nowhere else (``frontend/lib/seasonPlan.ts`` only shows what the server sends).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

#: When these windows were last looked at.
REVIEWED = date(2026, 11, 2)

ABOUT = (
    "Our advice, not Etsy's: go live early enough to be on the shelf before buyers start looking, and finish while "
    "there is still time to make and ship. Etsy publishes no launch dates and we hold no figures that say these work."
)


@dataclass(frozen=True)
class Holiday:
    key: str
    name: str
    #: The window opens this many days before the holiday, and closes this many before it.
    opens: int
    closes: int
    why: str


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + (nth - 1) * 7)


def easter(year: int) -> date:
    """Western Easter (the anonymous Gregorian computus, as Meeus gives it)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741  (the algorithm's own letter)
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


_DATES = {
    "thanksgiving": lambda y: _nth_weekday(y, 11, 3, 4),  # the fourth Thursday of November (US)
    "christmas": lambda y: date(y, 12, 25),
    "new_year": lambda y: date(y, 1, 1),
    "valentines": lambda y: date(y, 2, 14),
    "st_patricks": lambda y: date(y, 3, 17),
    "easter": easter,
    "mothers_day": lambda y: _nth_weekday(y, 5, 6, 2),  # the second Sunday of May (US)
    "fathers_day": lambda y: _nth_weekday(y, 6, 6, 3),  # the third Sunday of June (US)
    "july_4": lambda y: date(y, 7, 4),
    "back_to_school": lambda y: date(y, 8, 15),  # no one date: schools in the US start around mid-August
    "halloween": lambda y: date(y, 10, 31),
}

HOLIDAYS = [
    Holiday("thanksgiving", "Thanksgiving", 45, 14, "Shirts for the day and the family get-together; two weeks leaves time to ship."),
    Holiday("christmas", "Christmas", 75, 21, "Gift shopping starts early, and made-to-order items need time before the last shipping dates."),
    Holiday("new_year", "New Year", 45, 10, "Often bought alongside Christmas orders, so it opens early and closes late."),
    Holiday("valentines", "Valentine's Day", 60, 14, "Gifts, some of them personalized: leave time to make them."),
    Holiday("st_patricks", "St. Patrick's Day", 45, 14, "Worn on the day, so it has to arrive before it."),
    Holiday("easter", "Easter", 50, 14, "The date moves every year, so check this window against it."),
    Holiday("mothers_day", "Mother's Day", 50, 14, "Gifts, some of them personalized: leave time to make them."),
    Holiday("fathers_day", "Father's Day", 50, 14, "Gifts, some of them personalized: leave time to make them."),
    Holiday("july_4", "4th of July", 50, 14, "Worn on the day, so it has to arrive before it."),
    Holiday("back_to_school", "Back to school", 60, 7, "US schools start at different times in August, so this date is only a middle."),
    Holiday("halloween", "Halloween", 75, 14, "Costumes and shirts are planned well ahead of the day."),
]

BY_KEY = {h.key: h for h in HOLIDAYS}


@dataclass(frozen=True)
class Season:
    holiday: Holiday
    on: date
    #: The window as we suggest it, whatever today is.
    opens: date
    closes: date
    #: From today: when the window has started, its first day left. None when it has closed.
    first_left: date | None


def _next_date(key: str, today: date) -> date:
    rule = _DATES[key]
    on = rule(today.year)
    return on if on >= today else rule(today.year + 1)


def season_of(holiday: Holiday, today: date) -> Season:
    on = _next_date(holiday.key, today)
    opens, closes = on - timedelta(days=holiday.opens), on - timedelta(days=holiday.closes)
    return Season(holiday, on, opens, closes, None if closes < today else max(opens, today))


def calendar(today: date) -> list[Season]:
    """Every holiday from today forward, soonest first."""
    return sorted((season_of(h, today) for h in HOLIDAYS), key=lambda s: s.on)
