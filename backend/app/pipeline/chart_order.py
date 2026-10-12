"""Where a listing's size charts sit among its photos.

A profile's size charts (its ``fixed_image_ids``) go on every draft. Where they
go is the profile's choice (``listing_profile.size_chart_position``):

* ``after_cover``: right after the cover (2nd),
* ``third``: after the cover and one more photo (3rd),
* ``last``: after every photo (what drafts always did; the default).

Those three are presets. On the profile the seller can also drag each chart to
its own place (``listing_profile.size_chart_slots``), and a listing group can
override the profile by dragging a chart in its own strip
(``listing_group_setting.chart_slots``). Either is a *placement*: the charts in
the order they appear, each with a *slot*, the number of photos before it, or
:data:`END` for "after every photo" (so a chart dragged to the end stays last
when photos are added). The cover is always first: a slot is at least 1. Two
charts in the same slot keep the placement's order, so each chart has its own
position on the listing.

Precedence, in one place (:func:`resolve`): the group's placement, else the
profile's, else the profile's preset. **A listing with fewer photos than a
chart's slot gets that chart last** (:func:`arrange`).

Drafts in every shop, "Replace images" and the review strip all use
:func:`resolve` and :func:`arrange`, so the order is the same everywhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

POSITIONS = ("after_cover", "third", "last")
DEFAULT_POSITION = "last"
#: A chart after every photo.
END = -1
#: The most images Etsy keeps on a listing: the profile editor shows this many places.
MAX_IMAGES = 20

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


def _keyed(saved: Any) -> list[tuple[int, int]] | None:
    """A stored placement (``[{"id": chart image id, "slot": n}, ...]``, in the
    charts' order) as pairs; None when ``saved`` is not one."""
    if not isinstance(saved, list) or not saved or not all(isinstance(x, dict) for x in saved):
        return None
    out: list[tuple[int, int]] = []
    for item in saved:
        try:
            chart, slot = int(item["id"]), int(item["slot"])
        except (KeyError, TypeError, ValueError):
            return None
        if chart not in {c for c, _ in out}:
            out.append((chart, END if slot == END else max(1, slot)))
    return out


def _lay(base: list[tuple[int, int]], saved: Any, canonical: Sequence[int]) -> list[tuple[int, int]] | None:
    """``saved`` laid over ``base`` (charts in order, each with its slot); None when
    nothing is saved. A chart the saved placement does not mention keeps its base
    slot, after the ones it does; a chart no longer attached is dropped."""
    if saved is None:
        return None
    keyed = _keyed(saved)
    if keyed is None:
        # One slot per chart, in the charts' own order (how a group's drag was
        # stored before a placement carried the charts' order).
        slots = dict(zip(canonical, [END if s == END or s is None else max(1, int(s)) for s in saved or []]))
        return [(chart, slots.get(chart, slot)) for chart, slot in base]
    have = dict(base)
    mentioned = [(chart, slot) for chart, slot in keyed if chart in have]
    named = {chart for chart, _ in mentioned}
    return mentioned + [(chart, slot) for chart, slot in base if chart not in named]


def resolve(
    chart_ids: Sequence[int], *, group: Any = None, profile: Any = None, position: str | None = None
) -> tuple[list[int], list[int], str]:
    """The charts in listing order, a slot for each, and who decided:
    ``"group"`` (dragged on the listing group), ``"profile"`` (dragged on the
    profile) or ``"preset"`` (the profile's after cover / 3rd / last).

    ``chart_ids`` are the profile's size charts in their own order; ``group`` and
    ``profile`` are the stored placements (or None).
    """
    ids = [int(c) for c in chart_ids]
    placed = list(zip(ids, default_slots(position, len(ids))))
    source = "preset"
    for name, saved in (("profile", profile), ("group", group)):
        laid = _lay(placed, saved, ids)
        if laid is not None:
            placed, source = laid, name
    return [c for c, _ in placed], [s for _, s in placed], source


def check_placement(raw: Sequence[Any], chart_ids: Sequence[int]) -> list[dict[str, int]]:
    """A placement to store, or ValueError saying what is wrong with it. Each of
    the profile's charts at most once; never before the cover; at most
    :data:`MAX_IMAGES` places."""
    known = {int(c) for c in chart_ids}
    out: list[dict[str, int]] = []
    for item in raw:
        chart, slot = int(item["listing_image_id"]), int(item["slot"])
        if chart not in known:
            raise ValueError("That image is not one of this profile's size charts.")
        if any(p["id"] == chart for p in out):
            raise ValueError("A size chart can only be placed once.")
        if slot != END and slot < 1:
            raise ValueError("A size chart cannot come before the cover.")
        if slot != END and slot > MAX_IMAGES - 1:
            raise ValueError(f"A listing has at most {MAX_IMAGES} images.")
        out.append({"id": chart, "slot": slot})
    return out


def positions(photo_count: int, slots: Sequence[int]) -> list[int]:
    """1-based ranks the charts get on the listing (for the read-back check)."""
    ranks = arrange(list(range(photo_count)), [f"c{i}" for i in range(len(slots))], slots)
    return [i + 1 for i, x in enumerate(ranks) if isinstance(x, str)]
