"""Group scheduling (v8 §B): when each listing goes live in each shop of its group.

Pure: the seller's settings, the listings in order, each group's shops, and the
budget there is per UTC day in, the slots and the budget per day out. Nothing is
queued here (api/distribution.py confirms; workers/plans.py releases).

* Every shop of a group gets the same listings, in the same order.
* Each shop takes up to ``per_shop_per_day`` listings a day, inside the daily
  window (the seller's wall clock), ``spacing`` minutes apart.
* Shops of one group are staggered by ``stagger`` minutes each, so the same
  design never goes live in two of them in the same minute.
* A listing costs ~``DRAFT_REQUESTS`` Etsy requests when its draft is made
  (``DRAFT_LEAD`` before it goes live) and ``PUBLISH_REQUESTS`` when it goes
  live. Requests are counted on the UTC day they are made (Etsy's and our daily
  counters reset at 00:00 UTC). When a day has no room left, the shop's next
  listing moves to its first slot of the next day: drafts and go-lives both
  spill, the ceiling is never planned past.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.pipeline.targets import ESTIMATED_CALLS_PER_DRAFT

DRAFT_REQUESTS = ESTIMATED_CALLS_PER_DRAFT
PUBLISH_REQUESTS = 3
#: A draft is made this long before it goes live (room for a retry if Etsy blips).
DRAFT_LEAD = timedelta(hours=2)
#: No plan runs further than this.
MAX_DAYS = 366


class PlanRefused(ValueError):
    """The settings cannot make a schedule (message is safe to show)."""


@dataclass(frozen=True)
class Settings:
    start: date
    per_shop_per_day: int
    window_start: time
    window_end: time
    spacing_minutes: int
    stagger_minutes: int
    zone: str

    def check(self, largest_group: int) -> None:
        if self.per_shop_per_day < 1:
            raise PlanRefused("at least one listing per shop per day")
        if self.per_shop_per_day > 50:
            raise PlanRefused("at most 50 listings per shop per day")
        if self.window_end <= self.window_start:
            raise PlanRefused("the daily window must end after it starts")
        if self.spacing_minutes < 1:
            raise PlanRefused("leave at least a minute between listings in a shop")
        if largest_group > 1 and self.stagger_minutes < 1:
            raise PlanRefused("stagger the shops of a group by at least a minute, so a design never goes live in two at once")
        if self.stagger_minutes < 0:
            raise PlanRefused("the stagger cannot be negative")


@dataclass(frozen=True)
class Shop:
    id: uuid.UUID
    name: str
    group_id: uuid.UUID


@dataclass(frozen=True)
class Listing:
    content_id: uuid.UUID
    title: str
    group_id: uuid.UUID
    #: Shops of its group it cannot go to (no profile set up, a draft there...), with why.
    skip: dict[uuid.UUID, str] = field(default_factory=dict)


@dataclass
class Slot:
    content_id: uuid.UUID
    title: str
    shop_id: uuid.UUID
    shop_name: str
    group_id: uuid.UUID
    draft_at: datetime
    publish_at: datetime
    local_day: date
    local_time: str


@dataclass
class Plan:
    slots: list[Slot]
    #: UTC day -> Etsy requests this plan makes that day.
    requests: dict[date, int]
    #: UTC day -> what the account could spend that day for this plan.
    capacity: dict[date, int]
    finishes_on: date | None
    skipped: list[tuple[uuid.UUID, uuid.UUID, str]]
    #: Listings a day actually fits per shop when the window is too short for the setting.
    fits_per_day: int
    notes: list[str] = field(default_factory=list)


def _instant(day: date, at: time, zone: str) -> datetime | None:
    """The UTC instant of a wall-clock time; None for a time the clocks skip."""
    tz = ZoneInfo(zone)
    naive = datetime.combine(day, at)
    instant = naive.replace(tzinfo=tz).astimezone(timezone.utc)
    if instant.astimezone(tz).replace(tzinfo=None) != naive:
        return None
    return instant


def _times(settings: Settings, index: int) -> list[time]:
    """The wall-clock times of one shop's slots in a day (its stagger applied)."""
    start = datetime.combine(date(2000, 1, 3), settings.window_start) + timedelta(minutes=index * settings.stagger_minutes)
    end = datetime.combine(date(2000, 1, 3), settings.window_end)
    out = []
    for i in range(settings.per_shop_per_day):
        at = start + timedelta(minutes=i * settings.spacing_minutes)
        if at >= end or at.date() != start.date():
            break
        out.append(at.time())
    return out


def plan(
    settings: Settings,
    listings: list[Listing],
    shops: list[Shop],
    *,
    capacity: callable,  # type: ignore[valid-type]  # (UTC date) -> requests this plan may use that day
    now: datetime,
) -> Plan:
    groups: dict[uuid.UUID, list[Shop]] = {}
    for shop in shops:
        groups.setdefault(shop.group_id, []).append(shop)
    settings.check(max((len(v) for v in groups.values()), default=0))

    # Each shop's queue: its group's listings, in order, less the ones it cannot take.
    queues: dict[uuid.UUID, list[Listing]] = {s.id: [] for s in shops}
    skipped: list[tuple[uuid.UUID, uuid.UUID, str]] = []
    for listing in listings:
        for shop in groups.get(listing.group_id, []):
            if shop.id in listing.skip:
                skipped.append((listing.content_id, shop.id, listing.skip[shop.id]))
            else:
                queues[shop.id].append(listing)

    index = {s.id: [g.id for g in groups[s.group_id]].index(s.id) for s in shops}
    fits = min((len(_times(settings, index[s.id])) for s in shops), default=settings.per_shop_per_day)
    spent: dict[date, int] = {}
    room: dict[date, int] = {}

    def left(day: date) -> int:
        if day not in room:
            room[day] = max(0, int(capacity(day)))
        return room[day] - spent.get(day, 0)

    slots: list[Slot] = []
    day = settings.start
    for _ in range(MAX_DAYS):
        if not any(queues.values()):
            break
        for shop in shops:  # in group order: the stagger follows it
            queue = queues[shop.id]
            for at in _times(settings, index[shop.id]):
                if not queue:
                    break
                publish_at = _instant(day, at, settings.zone)
                if publish_at is None or publish_at <= now + timedelta(minutes=5):
                    continue  # a time the clocks skip, or already past
                draft_at = max(now, publish_at - DRAFT_LEAD)
                d_day, p_day = draft_at.date(), publish_at.date()
                need = {d_day: DRAFT_REQUESTS}
                need[p_day] = need.get(p_day, 0) + PUBLISH_REQUESTS
                if any(left(k) < v for k, v in need.items()):
                    break  # this shop's day is full: the rest of its queue spills to tomorrow
                for k, v in need.items():
                    spent[k] = spent.get(k, 0) + v
                listing = queue.pop(0)
                slots.append(Slot(
                    content_id=listing.content_id, title=listing.title, shop_id=shop.id, shop_name=shop.name,
                    group_id=shop.group_id, draft_at=draft_at, publish_at=publish_at, local_day=day,
                    local_time=at.strftime("%H:%M"),
                ))
        day += timedelta(days=1)
    if any(queues.values()):
        raise PlanRefused(
            f"it would take more than {MAX_DAYS} days with this budget and these settings; "
            "allow more listings a day or a longer window"
        )
    notes = []
    if fits < settings.per_shop_per_day:
        notes.append(
            f"The window fits {fits} listing{'s' if fits != 1 else ''} a day per shop with this spacing and stagger, "
            f"not {settings.per_shop_per_day}."
        )
    return Plan(
        slots=sorted(slots, key=lambda s: (s.publish_at, s.shop_name)),
        requests=dict(sorted(spent.items())),
        capacity={k: room[k] for k in sorted(spent)},
        finishes_on=max((s.local_day for s in slots), default=None),
        skipped=skipped,
        fits_per_day=fits,
        notes=notes,
    )


def split_evenly(content_ids: list[uuid.UUID], group_ids: list[uuid.UUID]) -> dict[uuid.UUID, uuid.UUID]:
    """Approved listings divided across the chosen groups, in order: the first
    ``n / groups`` to the first group, and so on (60 over 4 = 15 each; any
    remainder goes one each to the first groups)."""
    if not group_ids:
        raise PlanRefused("choose at least one group")
    base, extra = divmod(len(content_ids), len(group_ids))
    out: dict[uuid.UUID, uuid.UUID] = {}
    i = 0
    for n, group in enumerate(group_ids):
        take = base + (1 if n < extra else 0)
        for cid in content_ids[i:i + take]:
            out[cid] = group
        i += take
    return out
