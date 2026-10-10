"""Where a listing's size charts sit among its photos.

A profile's size charts (its ``fixed_image_ids``) go on every draft. Where they
go is the profile's choice (``listing_profile.size_chart_position``):

* ``after_cover``: right after the cover (2nd),
* ``third``: after the cover and one more photo (3rd),
* ``last``: after every photo (what drafts always did; the default).

A listing group can override it by dragging a chart (``listing_group_setting.
chart_slots``): one *slot* per chart, the number of the group's photos before
it, or :data:`END` for "after every photo" (so a chart dragged to the end stays
last when photos are added). The cover is always first: a slot is at least 1.

Drafts in every shop, "Replace images" and the review strip all use
:func:`arrange`, so the order is the same everywhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

POSITIONS = ("after_cover", "third", "last")
DEFAULT_POSITION = "last"
#: A chart after every photo.
END = -1

T = TypeVar("T")
U = TypeVar("U")


def check_position(position: str) -> str:
    if position not in POSITIONS:
        raise ValueError(f"size chart position must be one of {', '.join(POSITIONS)}")
    return position


def default_slots(position: str | None, charts: int) -> list[int]:
    """The profile's position as one slot per chart (charts stay together)."""
    slot = {"after_cover": 1, "third": 2}.get(position or DEFAULT_POSITION, END)
    return [slot] * charts


def clean_slots(slots: Sequence[int] | None, charts: int, position: str | None) -> list[int]:
    """``slots`` fitted to ``charts`` charts: missing ones take the profile's place,
    extra ones are dropped, nothing goes before the cover."""
    fallback = default_slots(position, charts)
    out = list(slots or [])[:charts]
    out += fallback[len(out):]
    return [END if s == END or s is None else max(1, int(s)) for s in out]


def arrange(photos: Sequence[T], charts: Sequence[U], slots: Sequence[int]) -> list[T | U]:
    """Photos (cover first) and charts in listing order. A slot past the last photo
    is the end; charts in the same slot keep their own order."""
    n = len(photos)
    place = [n if s == END else min(max(1, s), n) for s in clean_slots(slots, len(charts), None)]
    if not photos:
        return list(charts)
    out: list[T | U] = []
    for k, photo in enumerate(photos):
        out.append(photo)
        out.extend(c for c, at in zip(charts, place) if at == k + 1 and k + 1 < n)
    out.extend(c for c, at in zip(charts, place) if at >= n)
    return out


def slots_from_order(order: Sequence[str], chart_ids: Sequence[str]) -> list[int]:
    """The slots a dragged strip means: for each chart (in ``chart_ids`` order), the
    photos before it, or :data:`END` when no photo follows it."""
    charts = set(chart_ids)
    photos_total = sum(1 for x in order if x not in charts)
    before: dict[str, int] = {}
    seen = 0
    for x in order:
        if x in charts:
            before[x] = END if seen >= photos_total else max(1, seen)
        else:
            seen += 1
    return [before.get(c, END) for c in chart_ids]


def positions(photo_count: int, slots: Sequence[int]) -> list[int]:
    """1-based ranks the charts get on the listing (for the read-back check)."""
    ranks = arrange(list(range(photo_count)), [f"c{i}" for i in range(len(slots))], slots)
    return [i + 1 for i, x in enumerate(ranks) if isinstance(x, str)]
