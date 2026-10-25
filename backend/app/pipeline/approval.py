"""What "Approve all" says when listings are not approved: one line per reason.

Fourteen empty hand-written listings used to give fourteen copies of the same
long list ("title is empty; expected exactly 13 tags, got 0; title must name the
product type…"). Now each listing's problems become short reasons, identical
reasons are counted together, and the page shows one summary line plus one line
per reason ("14 listings have no title yet: write them, or press Generate"); the
cards that need something are marked in place.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: The paths a listing is approved by, as the publish report prints them.
VIA_CARD = "card"
VIA_APPROVE_ALL = "approve_all"


def set_approved(content: Any, approved: bool, via: str = VIA_CARD) -> None:
    """Approve or un-approve a listing, recording when and by which path (migration 0091).
    An approval already given keeps its first time and path."""
    if approved:
        if not content.approved or content.approved_at is None:
            content.approved_at = datetime.now(timezone.utc)
            content.approved_via = via
        content.approved = True
    else:
        content.approved = False
        content.approved_at = None
        content.approved_via = None

#: (start of a validation message, singular, plural). First match wins; the
#: messages are the app's own (pipeline/content.py, pipeline/etsy_text.py).
_KINDS: tuple[tuple[str, str, str], ...] = (
    ("title is empty", "has no title yet", "have no title yet"),
    ("description is empty", "has no description", "have no description"),
    ("more than 13 tags", "has more than 13 tags", "have more than 13 tags"),
    ("expected exactly", "does not have 13 tags", "do not have 13 tags"),
    ("a tag is empty", "has an empty tag", "have an empty tag"),
    ("tag exceeds", "has a tag over 20 characters", "have a tag over 20 characters"),
    ("tag contains a comma", "has a tag with a comma", "have a tag with a comma"),
    ("tag has characters Etsy does not allow", "has a tag with characters Etsy does not allow",
     "have a tag with characters Etsy does not allow"),
    ("duplicate tag", "has the same tag twice", "have the same tag twice"),
    ("title has characters Etsy does not allow", "has characters Etsy does not allow in its title",
     "have characters Etsy does not allow in their titles"),
    ("title uses", "uses %, :, & or + more than once in its title", "use %, :, & or + more than once in their titles"),
    ("title is over Etsy's", "has a title over 140 characters", "have titles over 140 characters"),
    ("title exceeds", "has a title longer than its profile's maximum", "have titles longer than their profile's maximum"),
    ("title must be at least", "has a title shorter than its profile's minimum",
     "have titles shorter than their profile's minimum"),
    ("title must name the product type", "does not name the product type in its title",
     "do not name the product type in their titles"),
    ("at least one tag must name the product type", "has no tag naming the product type",
     "have no tag naming the product type"),
    ("Remove:", "has a trademark to remove (see its card)", "have trademarks to remove (see their cards)"),
    ("compliance:", "has a compliance finding (see its card)", "have compliance findings (see their cards)"),
)

#: Said after "have no title yet" on listings the seller writes.
WRITE_OR_GENERATE = ": write them, or press Generate"
WRITE_OR_GENERATE_ONE = ": write it, or press Generate"


def kind_of(message: str) -> tuple[str, str, str]:
    """(key, singular, plural) for one validation message; unknown ones are their own kind."""
    for start, one, many in _KINDS:
        if message.startswith(start):
            return start, one, many
    return message, f"is not approved: {message}", f"are not approved: {message}"


def short(message: str) -> str:
    """The reason as the card says it ("needs a title", "has no tag naming the product type")."""
    key, one, _ = kind_of(message)
    if key == "title is empty":
        return "needs a title"
    return one


@dataclass
class Group:
    key: str
    singular: str
    plural: str
    hand_written: bool = False
    content_ids: list[uuid.UUID] = field(default_factory=list)

    def line(self) -> str:
        n = len(self.content_ids)
        noun = "listing" if n == 1 else "listings"
        text = self.singular if n == 1 else self.plural
        if self.key == "title is empty" and self.hand_written:
            text += WRITE_OR_GENERATE_ONE if n == 1 else WRITE_OR_GENERATE
        return f"{n} {noun} {text}"


def group(problems: list[tuple[uuid.UUID, bool, list[str]]]) -> list[Group]:
    """``problems``: (listing id, written by the seller, its messages). One group per
    reason, the most common first; a listing appears once in each of its reasons."""
    groups: dict[str, Group] = {}
    for content_id, hand_written, messages in problems:
        seen: set[str] = set()
        for message in messages:
            key, one, many = kind_of(message)
            if key in seen:
                continue
            seen.add(key)
            g = groups.setdefault(key, Group(key, one, many))
            g.hand_written = g.hand_written or hand_written
            g.content_ids.append(content_id)
    order = list(groups)
    return sorted(groups.values(), key=lambda g: (-len(g.content_ids), order.index(g.key)))


def summary(skipped: int, groups: list[Group]) -> str | None:
    """One line for the whole result: the only reason, or how many were not approved."""
    if skipped == 0:
        return None
    if len(groups) == 1:
        return groups[0].line()
    noun = "listing was" if skipped == 1 else "listings were"
    return f"{skipped} {noun} not approved:"
