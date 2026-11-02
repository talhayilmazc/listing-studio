"""The seasonal planner's spread: a window of days, the same rules as every other schedule. Pure.

``group_plan.plan`` fills the first days it can, which is right for "from Monday, ten a day". A season has
an end, so this spreads each shop's listings evenly across the window instead, on the same terms:

* a shop's daily limit, counted across every schedule (``Taken``), and the planner's own "a day";
* minutes between listings in a shop, against what is already scheduled there too;
* the same design never in two shops in the same minute (the shops are staggered, as in a group plan);
* the account's and the app's Etsy budget per UTC day (``capacity``, the one every plan uses).

Nothing is moved quietly and nothing is dropped quietly. A listing that lands on another day than its even
share is counted (``moved``); one that fits nowhere in the window is listed with why, and the seller sees
all of it before anything is saved. Slots are the ordinary ones: ``workers/plans.py`` releases them and going
live re-checks the approval (CLAUDE.md rule 3).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.pipeline import group_plan as G

#: A listing that does not fit: the shop's limit, or the budget.
NO_ROOM = "no_room"
NO_BUDGET = "no_budget"
MINUTES_IN_A_DAY = 24 * 60


@dataclass(frozen=True)
class Window:
    first: date
    last: date

    @property
    def days(self) -> list[date]:
        return [self.first + timedelta(days=n) for n in range((self.last - self.first).days + 1)]


@dataclass
class Taken:
    """What is already scheduled in a shop, across every plan: ``minutes[day]`` holds local minutes of the day."""

    minutes: dict[date, list[int]] = field(default_factory=dict)

    def on(self, day: date) -> int:
        return len(self.minutes.get(day, []))


@dataclass(frozen=True)
class DidNotFit:
    content_id: uuid.UUID
    title: str
    shop_id: uuid.UUID
    shop_name: str
    why: str


@dataclass
class ShopDays:
    shop_id: uuid.UUID
    name: str
    cap: int
    new: dict[date, int] = field(default_factory=dict)
    taken: dict[date, int] = field(default_factory=dict)
    moved: int = 0

    @property
    def total(self) -> int:
        return sum(self.new.values())


@dataclass
class SeasonPlan:
    slots: list[G.Slot]
    shops: list[ShopDays]
    did_not_fit: list[DidNotFit]
    requests: dict[date, int]
    capacity: dict[date, int]
    fits_per_day: int

    def fingerprint(self) -> str:
        """Changes when what would be saved changes: a preview and a save must describe the same plan."""
        lines = sorted(f"{s.content_id}|{s.shop_id}|{s.publish_at:%Y-%m-%dT%H:%M}" for s in self.slots)
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def even_day(index: int, count: int, days: int) -> int:
    """Which day the listing at ``index`` of ``count`` belongs on when they are shared across ``days``.
    The first day and, as nearly as it divides, the last both get one: ``count`` 3 over 7 is days 0, 3, 6."""
    if count <= 1 or days <= 1:
        return 0
    return round(index * (days - 1) / (count - 1)) if count <= days else index * days // count


def _clear(minute: int, taken: list[int], gap: int) -> bool:
    return all(abs(minute - other) >= gap for other in taken)


def spread(
    window: Window,
    settings: G.Settings,
    listings: list[G.Listing],
    shops: list[G.Shop],
    *,
    taken: dict[uuid.UUID, Taken],
    capacity: Callable[[date], int],
    now: datetime,
    draft_requests: int = G.DRAFT_REQUESTS,
) -> SeasonPlan:
    groups: dict[Any, list[G.Shop]] = {}
    for shop in shops:
        groups.setdefault(shop.group_id, []).append(shop)
    settings.check(max((len(v) for v in groups.values()), default=0))
    unique: dict[uuid.UUID, G.Shop] = {}
    for shop in shops:
        unique.setdefault(shop.id, shop)
    queues: dict[uuid.UUID, list[G.Listing]] = {s: [] for s in unique}
    for listing in listings:
        for shop in groups.get(listing.group_id, []):
            if shop.id not in listing.skip and listing not in queues[shop.id]:
                queues[shop.id].append(listing)

    index = G._stagger_index(list(unique), groups)
    days = window.days
    gap = settings.spacing_minutes
    spent: dict[date, int] = {}
    room: dict[date, int] = {}
    same_minute: set[tuple[uuid.UUID, int]] = set()
    rows: list[ShopDays] = []
    slots: list[G.Slot] = []
    missed: list[DidNotFit] = []
    # Every time the window holds, not only the first "a day" of them: some may be taken already.
    every_time = replace(settings, per_shop_per_day=MINUTES_IN_A_DAY)
    fits = min((min(settings.per_shop_per_day, len(G._times(every_time, index[s]))) for s in unique), default=settings.per_shop_per_day)

    def left(day: date) -> int:
        if day not in room:
            room[day] = max(0, int(capacity(day)))
        return room[day] - spent.get(day, 0)

    for shop in unique.values():
        have = taken.get(shop.id) or Taken()
        cap = min(settings.per_shop_per_day, shop.daily) if shop.daily else settings.per_shop_per_day
        row = ShopDays(shop.id, shop.name, cap, taken={d: have.on(d) for d in days if have.on(d)})
        rows.append(row)
        times = G._times(every_time, index[shop.id])
        queue = queues[shop.id]
        placed: dict[date, list[int]] = {d: list(have.minutes.get(d, [])) for d in days}
        out_of_budget = False

        def place(listing: G.Listing, day: date) -> bool:
            nonlocal out_of_budget
            limit = shop.daily if shop.daily else settings.per_shop_per_day
            mine = row.new.get(day, 0)
            if mine >= settings.per_shop_per_day or len(placed[day]) >= limit:
                return False
            for at in times:
                minute = at.hour * 60 + at.minute
                publish_at = G._instant(day, at, settings.zone)
                if publish_at is None or publish_at <= now + timedelta(minutes=5):
                    continue
                if not _clear(minute, placed[day], gap):
                    continue
                stamp = int(publish_at.timestamp()) // 60
                if (listing.content_id, stamp) in same_minute:
                    continue
                draft_at = max(now, publish_at - G.DRAFT_LEAD)
                need = {draft_at.date(): draft_requests}
                need[publish_at.date()] = need.get(publish_at.date(), 0) + G.PUBLISH_REQUESTS
                if any(left(k) < v for k, v in need.items()):
                    out_of_budget = True
                    return False
                for k, v in need.items():
                    spent[k] = spent.get(k, 0) + v
                same_minute.add((listing.content_id, stamp))
                placed[day].append(minute)
                row.new[day] = mine + 1
                slots.append(G.Slot(
                    content_id=listing.content_id, title=listing.title, shop_id=shop.id, shop_name=shop.name,
                    group_id=listing.group_id, draft_at=draft_at, publish_at=publish_at, local_day=day,
                    local_time=at.strftime("%H:%M"),
                ))
                return True
            return False

        for i, listing in enumerate(queue):
            ideal = even_day(i, len(queue), len(days))
            # Its own day, then the nearest day that has room, later ones first when it is a tie.
            order = sorted(range(len(days)), key=lambda d: (abs(d - ideal), d < ideal))
            for d in order:
                if place(listing, days[d]):
                    row.moved += d != ideal
                    break
            else:
                missed.append(DidNotFit(listing.content_id, listing.title, shop.id, shop.name, NO_BUDGET if out_of_budget else NO_ROOM))
    return SeasonPlan(
        slots=sorted(slots, key=lambda s: (s.publish_at, s.shop_name)), shops=rows, did_not_fit=missed,
        requests=dict(sorted(spent.items())), capacity={k: room[k] for k in sorted(spent)}, fits_per_day=fits,
    )


def local_minutes(rows: list[tuple[uuid.UUID, datetime]], zone: str) -> dict[uuid.UUID, Taken]:
    """What is scheduled already, as each shop's local days and minutes."""
    tz = ZoneInfo(zone)
    out: dict[uuid.UUID, Taken] = {}
    for shop_id, at in rows:
        local = at.astimezone(tz)
        out.setdefault(shop_id, Taken()).minutes.setdefault(local.date(), []).append(local.hour * 60 + local.minute)
    return out

