"""Whether two drafts in one shop are of the same design (pipeline/unfinished.py, the
operator's ``draft-failures``, the "Finish draft" guard in etsy/publisher.py).

A design is known by what the app still holds about it, most specific first:

1. **same listing**: the same listing text (generated content) in the app;
2. **same design**: the same batch and listing group (the design rewritten keeps both);
3. **SKU**: the listing's SKU (the cover's SKU in the app, the publication's, the SKUs of
   a listing on Etsy);
4. **group**: the listing group's name across batches, only when it reads as a SKU
   (letters and digits, not "design1"): a folder called "New folder" in two batches is
   not one design;
5. **title**: the whole title, spaces and case aside (a draft whose batch was deleted is
   known by little else).

The first kind **both** sides have decides: equal is a match by that kind, different is
no match, and a weaker kind is never consulted (two designs of one batch with the same
generic title stay two designs). The one exception is the design itself: two designs in
different batches say nothing yet (a design redone in a new batch is the case this is
for), so the comparison goes on to the SKU, the group and the title.

Only identifiers and titles the seller's own app wrote; no Etsy call.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.pipeline.grouping import UNSORTED, _is_sku

#: The kinds, most specific first, and how each is said.
KINDS: tuple[str, ...] = ("same listing", "same design", "SKU", "group", "title")


def norm_title(title: str | None) -> str | None:
    text = re.sub(r"\s+", " ", (title or "")).strip().casefold()
    return text or None


def norm_sku(sku: str | None) -> str | None:
    text = (sku or "").strip().casefold()
    return text or None


@dataclass(frozen=True)
class Keys:
    content: str | None = None
    batch: str | None = None
    #: The listing group as stored (with the batch, it is the design).
    design_group: str | None = None
    #: The group's name when it reads as a SKU (compared across batches).
    group: str | None = None
    skus: frozenset[str] = field(default_factory=frozenset)
    title: str | None = None

    @property
    def label(self) -> str:
        """What the app knows this design by, for the operator (identifiers, no title)."""
        parts = []
        if self.batch:
            parts.append(f"batch {self.batch[:8]}")
        if self.design_group:
            parts.append(f"group {self.design_group}")
        if self.skus:
            parts.append("SKU " + "/".join(sorted(self.skus)))
        return ", ".join(parts) or "known by its title only"


def keys(*, content: object = None, batch: object = None, group: str | None = None,
         skus: Iterable[str | None] = (), title: str | None = None) -> Keys:
    stored = (group or "").strip() or None
    if stored == UNSORTED:
        stored = None
    named = stored.casefold() if stored and _is_sku(stored) else None
    return Keys(
        content=str(content) if content else None,
        batch=str(batch) if batch else None,
        design_group=stored,
        group=named,
        skus=frozenset(s for s in (norm_sku(x) for x in skus) if s),
        title=norm_title(title),
    )


@dataclass(frozen=True)
class Match:
    kind: str
    value: str | None = None

    def __str__(self) -> str:
        return f"{self.kind} {self.value}" if self.value else self.kind


def same(a: Keys, b: Keys) -> Match | None:
    """The kind of key that says ``a`` and ``b`` are one design, or None (see the module)."""
    if a.content and b.content and a.content == b.content:
        return Match("same listing")
    if a.batch and b.batch and a.design_group and b.design_group and a.batch == b.batch:
        return Match("same design") if a.design_group == b.design_group else None
    if a.skus and b.skus:
        shared = sorted(a.skus & b.skus)
        return Match("SKU", shared[0].upper()) if shared else None
    if a.group and b.group:
        return Match("group", a.group.upper()) if a.group == b.group else None
    if a.title and b.title:
        return Match("title") if a.title == b.title else None
    return None


def rank(match: Match) -> int:
    return KINDS.index(match.kind)


def etsy_keys(row: dict[str, Any]) -> Keys:
    """A listing as Etsy returns it, compared the same way: its SKUs (a single SKU is also
    its group's name, as folders are named after SKUs) and its title."""
    found = sorted({str(s).strip() for s in row.get("skus") or [] if str(s).strip()})
    return keys(group=found[0] if len(found) == 1 else None, skus=found, title=row.get("title"))


def created_of(row: dict[str, Any]) -> datetime | None:
    """When Etsy says the listing was created."""
    made = row.get("original_creation_timestamp") or row.get("created_timestamp") or row.get("creation_timestamp")
    return datetime.fromtimestamp(int(made), tz=timezone.utc) if made else None
