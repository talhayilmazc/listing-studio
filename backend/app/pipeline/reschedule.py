"""Editing scheduled go-lives on the Scheduled page (B8). Pure: items and rules in, new times out.

An edit (one row's time, a drag to another day, or a bulk action on a selection) gives the
times the seller asked for; :func:`settle` then keeps the rules, moving a time later only
where it must, and says why in one line:

* **minutes between** listings in a shop (the shop's groups' setting; at least 1, so two never
  go live in a shop in the same minute);
* a shop's **daily limit**, counted across every schedule (``etsy_connection.daily_limit``,
  else the smallest of its groups' "a day"; none: no limit): a listing past it moves to the
  next day at the same wall-clock time;
* the same design never goes live in two shops in the same minute;
* nothing before now (plus a few minutes).

Wall-clock times are the seller's time zone (``Rules.zone``), converted once with that day's
rules (a time the clocks skip moves to the next hour). Bulk actions:

``set_time``   the first selected (in time order) at a time, each next ``minutes_between`` later;
``shift``      every selected by ``minutes`` (negative: earlier);
``spread``     evenly between two times, in time order;
``move_day``   to one day, each keeping its wall-clock time;
``times``      given times, one per key (a row's own picker, a drag to another day).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

#: Nothing is planned closer to now than this.
LEAD = timedelta(minutes=5)
#: How far a settle may push one time (a year of days at most).
MAX_STEPS = 5000


class EditRefused(ValueError):
    """The edit cannot be made (message is safe to show)."""


@dataclass(frozen=True)
class Item:
    key: str
    shop_id: uuid.UUID
    content_id: uuid.UUID | None
    at: datetime  # UTC
    #: Its time can still change (waiting, or a draft not going live yet).
    movable: bool = True
    shop_name: str = ""


@dataclass
class Rules:
    zone: str
    spacing: dict[uuid.UUID, int] = field(default_factory=dict)
    daily: dict[uuid.UUID, int | None] = field(default_factory=dict)

    def gap(self, shop: uuid.UUID) -> timedelta:
        return timedelta(minutes=max(1, int(self.spacing.get(shop) or 1)))


@dataclass(frozen=True)
class Change:
    key: str
    old: datetime
    new: datetime
    #: Why it is not where the seller asked: "spacing" | "daily" | "same_minute" | "past" | None.
    moved: str | None = None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def local_day(at: datetime, zone: str) -> date:
    return _aware(at).astimezone(ZoneInfo(zone)).date()


def instant(day: date, at: time, zone: str) -> datetime:
    """The UTC instant of a wall-clock time; a time the clocks skip becomes the next hour's."""
    tz = ZoneInfo(zone)
    naive = datetime.combine(day, at)
    result = naive.replace(tzinfo=tz).astimezone(timezone.utc)
    if result.astimezone(tz).replace(tzinfo=None) != naive:
        result = (naive + timedelta(hours=1)).replace(tzinfo=tz).astimezone(timezone.utc)
    return result


def parse_local(value: str, zone: str) -> datetime:
    """``YYYY-MM-DDTHH:MM`` in the seller's zone -> UTC."""
    try:
        naive = datetime.strptime(value.strip()[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        raise EditRefused("choose a date and a time") from None
    return instant(naive.date(), naive.time(), zone)


def propose(items: list[Item], keys: list[str], op: str, params: dict[str, Any], rules: Rules) -> dict[str, datetime]:
    """The times the seller asked for, before the rules are kept."""
    by_key = {i.key: i for i in items}
    chosen = sorted((by_key[k] for k in dict.fromkeys(keys) if k in by_key and by_key[k].movable),
                    key=lambda i: (_aware(i.at), i.shop_name, i.key))
    if not chosen:
        raise EditRefused("choose at least one scheduled listing that has not gone live")
    zone = rules.zone
    if op == "times":
        given = params.get("times") or {}
        return {i.key: parse_local(str(given[i.key]), zone) for i in chosen if i.key in given}
    if op == "set_time":
        start = parse_local(str(params.get("local_time") or ""), zone)
        step = timedelta(minutes=max(0, int(params.get("minutes_between") or 0)))
        return {i.key: start + n * step for n, i in enumerate(chosen)}
    if op == "shift":
        delta = timedelta(minutes=int(params.get("minutes") or 0))
        if not delta:
            raise EditRefused("shift by at least a minute")
        return {i.key: _aware(i.at) + delta for i in chosen}
    if op == "spread":
        start = parse_local(str(params.get("from") or ""), zone)
        end = parse_local(str(params.get("to") or ""), zone)
        if end <= start:
            raise EditRefused("the end must be after the start")
        if len(chosen) == 1:
            return {chosen[0].key: start}
        step = (end - start) / (len(chosen) - 1)
        return {i.key: (start + n * step).replace(second=0, microsecond=0) for n, i in enumerate(chosen)}
    if op == "move_day":
        try:
            day = date.fromisoformat(str(params.get("date") or ""))
        except ValueError:
            raise EditRefused("choose a day") from None
        tz = ZoneInfo(zone)
        return {i.key: instant(day, _aware(i.at).astimezone(tz).time().replace(second=0, microsecond=0), zone) for i in chosen}
    raise EditRefused(f"unknown action {op}")


def settle(items: list[Item], desired: dict[str, datetime], rules: Rules, now: datetime) -> list[Change]:
    """New times for ``desired``, keeping the rules against every other scheduled listing and
    against each other; earlier asks are placed first."""
    by_key = {i.key: i for i in items}
    floor = (now + LEAD).replace(second=0, microsecond=0) + timedelta(minutes=1)
    placed: list[tuple[uuid.UUID, uuid.UUID | None, datetime]] = [
        (i.shop_id, i.content_id, _aware(i.at)) for i in items if i.key not in desired and _aware(i.at) >= now - timedelta(days=1)
    ]
    tz = ZoneInfo(rules.zone)
    out: list[Change] = []
    for key in sorted(desired, key=lambda k: (_aware(desired[k]), k)):
        item = by_key[key]
        asked = _aware(desired[key]).replace(second=0, microsecond=0)
        t, why = asked, None
        if t < floor:
            t, why = floor, "past"
        gap = rules.gap(item.shop_id)
        limit = rules.daily.get(item.shop_id)
        for _ in range(MAX_STEPS):
            day = t.astimezone(tz).date()
            if limit:
                same_day = sum(1 for s, _, at in placed if s == item.shop_id and at.astimezone(tz).date() == day)
                if same_day >= limit:
                    wall = t.astimezone(tz).time()
                    t, why = instant(day + timedelta(days=1), wall, rules.zone), why or "daily"
                    continue
            clash = [at for s, _, at in placed if s == item.shop_id and abs(at - t) < gap]
            if clash:
                t, why = max(clash) + gap, why or "spacing"
                continue
            if item.content_id is not None and any(c == item.content_id and s != item.shop_id and abs(at - t) < timedelta(minutes=1)
                                                    for s, c, at in placed):
                t, why = t + timedelta(minutes=1), why or "same_minute"
                continue
            break
        else:
            raise EditRefused("there is no room for these listings within a year; allow more a day")
        placed.append((item.shop_id, item.content_id, t))
        out.append(Change(key, _aware(item.at), t, why if t != asked else None))
    return out


def moved_line(changes: list[Change], items: list[Item], rules: Rules) -> str | None:
    """One line when keeping the rules moved anything: "Saving moves 2 more: …"."""
    by_key = {i.key: i for i in items}
    moved = [c for c in changes if c.moved]
    if not moved:
        return None
    parts: list[str] = []
    for why in ("spacing", "daily", "same_minute", "past"):
        hits = [c for c in moved if c.moved == why]
        if not hits:
            continue
        shops = sorted({by_key[c.key].shop_name for c in hits if by_key[c.key].shop_name})
        where = f" in {', '.join(shops)}" if shops else ""
        n = len(hits)
        if why == "spacing":
            mins = sorted({max(1, int(rules.spacing.get(by_key[c.key].shop_id) or 1)) for c in hits})
            parts.append(f"{n} later to keep {mins[0]} minute{'s' if mins[0] != 1 else ''} between listings{where}")
        elif why == "daily":
            caps = sorted({rules.daily.get(by_key[c.key].shop_id) for c in hits} - {None})
            parts.append(f"{n} to the next day{where}: {caps[0] if caps else 'its'} a day at most")
        elif why == "same_minute":
            parts.append(f"{n} a minute later so a design never goes live in two shops at once")
        else:
            parts.append(f"{n} to the next few minutes: that time has passed")
    return f"Keeping the rules moves {', '.join(parts)}."


def reflow(items: list[Item], shop_order: list[uuid.UUID], settings: dict[str, Any], rules: Rules, now: datetime) -> dict[str, datetime]:
    """A group's settings applied to its shops' upcoming listings: per shop, in their current
    order, from today, ``per_shop_per_day`` inside the window, ``spacing_minutes`` apart, the
    shops staggered by ``stagger_minutes``. :func:`settle` then keeps every rule."""
    per_day = max(1, int(settings.get("per_shop_per_day") or 1))
    start = time.fromisoformat(str(settings.get("window_start") or "09:00"))
    end = time.fromisoformat(str(settings.get("window_end") or "17:00"))
    spacing = max(1, int(settings.get("spacing_minutes") or 1))
    stagger = max(0, int(settings.get("stagger_minutes") or 0))
    if end <= start:
        raise EditRefused("the daily window must end after it starts")
    tz = ZoneInfo(rules.zone)
    out: dict[str, datetime] = {}
    for n, shop in enumerate(shop_order):
        mine = sorted((i for i in items if i.shop_id == shop and i.movable), key=lambda i: (_aware(i.at), i.key))
        limit = min(per_day, rules.daily.get(shop) or per_day)
        times: list[time] = []
        first = datetime.combine(date(2000, 1, 3), start) + timedelta(minutes=n * stagger)
        for k in range(limit):
            at = first + timedelta(minutes=k * spacing)
            if at.time() >= end or at.date() != first.date():
                break
            times.append(at.time())
        if not times:
            raise EditRefused("the window is too short for these settings")
        day = now.astimezone(tz).date()
        queue = list(mine)
        for _ in range(400):
            if not queue:
                break
            for wall in times:
                if not queue:
                    break
                at = instant(day, wall, rules.zone)
                if at < now + LEAD:
                    continue
                out[queue.pop(0).key] = at
            day += timedelta(days=1)
    return out
