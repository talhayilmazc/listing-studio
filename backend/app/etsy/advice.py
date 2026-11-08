"""What the seller can do about an error, next to what happened (app/onboarding-v1).

The message itself stays the one :func:`app.etsy.errors.explain` (or ``unfinished.plain_error``)
wrote: what happened, in the product's words. This adds the one action that fixes it, and the raw
answer for a "Details" disclosure: Etsy's status, the step and Etsy's own one-line reason, as stored
with the job (``job.payload["etsy_error"]``). Only ever on the listing owner's own card, like the
message (CLAUDE.md, "Hata mesajlarında…").

Every kind of Etsy error the app handles is a rule below, in order; ``tests/test_error_advice.py``
runs each message ``explain`` can write through it and checks none falls to the bottom without a
reason. The frontend reads ``action`` (what the button does) and ``label`` (its words); it never
classifies a message itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: What the button does. "reconnect" and "profiles" go to a page; the rest act on the card.
ACTIONS = ("reconnect", "retry", "edit_title", "edit_tags", "edit_personalization", "profiles", "set_up_shop",
           "photos", "shop_manager", "wait")


@dataclass(frozen=True)
class Advice:
    action: str
    label: str
    #: The page the button opens, for the actions that go somewhere.
    href: str | None = None


#: (pattern on the message, advice). First match wins; the patterns are the app's own sentences.
RULES: tuple[tuple[re.Pattern[str], Advice], ...] = (
    (re.compile(r"reconnect the shop|refused access to this shop|connect it again", re.I),
     Advice("reconnect", "Reconnect the shop", "/connect")),
    (re.compile(r"too fast|daily (request )?budget|resets at|waits? for the daily", re.I),
     Advice("wait", "It carries on by itself")),
    (re.compile(r"problem of its own|did not answer|no answer|timed out|stopped after several tries", re.I),
     Advice("retry", "Try again")),
    (re.compile(r"does not have the (shipping profile|return policy|processing profile|production partner)|set it up in", re.I),
     Advice("set_up_shop", "Set up the shop")),
    (re.compile(r"weight and size|calculated shipping|item_weight", re.I),
     Advice("profiles", "Open the profile", "/profiles")),
    (re.compile(r"how it'?s made|produce this item|tools are used", re.I),
     Advice("shop_manager", "Set it in Shop Manager")),
    (re.compile(r"personali[sz]ation", re.I),
     Advice("edit_personalization", "Fix the personalization")),
    (re.compile(r"\btitle\b", re.I),
     Advice("edit_title", "Fix the title")),
    (re.compile(r"\btags?\b", re.I),
     Advice("edit_tags", "Fix the tags")),
    (re.compile(r"\b(image|photo)s?\b", re.I),
     Advice("photos", "Check the photos")),
    (re.compile(r"could not find the listing|may have been deleted", re.I),
     Advice("retry", "Create the draft again")),
    (re.compile(r"profile", re.I),
     Advice("profiles", "Open the profile", "/profiles")),
)

#: Anything else Etsy refused: trying again is the one thing the seller can always do.
FALLBACK = Advice("retry", "Try again")


def advise(message: str | None) -> Advice | None:
    """The action for this message; None for no message."""
    if not message:
        return None
    for pattern, advice in RULES:
        if pattern.search(message):
            return advice
    return FALLBACK


def details(etsy_error: dict[str, Any] | None, message: str | None = None) -> str | None:
    """The raw answer for "Details": Etsy's status, the request's step and Etsy's own reason.
    Never the request body (``etsy_error["sent"]`` holds the shape only, and is left out too)."""
    if etsy_error:
        parts = []
        if etsy_error.get("status"):
            parts.append(f"HTTP {etsy_error['status']}")
        if etsy_error.get("method") and etsy_error.get("path"):
            parts.append(f"{etsy_error['method']} {etsy_error['path']}")
        if etsy_error.get("step"):
            parts.append(f"while {etsy_error['step']}")
        if etsy_error.get("message"):
            parts.append(f"Etsy: {etsy_error['message']}")
        if parts:
            return " · ".join(parts)
    return message or None
