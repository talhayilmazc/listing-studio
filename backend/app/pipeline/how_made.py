"""How it's made: Etsy's Creativity Standards questions, answered once per profile.

Etsy's listing editor asks "How does your shop produce this item?" (one answer) and "What
tools are used to make this item?" (any of them, or none). Etsy's Open API has neither: no
request or response field of getListing, createDraftListing, updateListing or
updateListingProperty, and nothing in the whole OpenAPI document names them (checked
2026-09-24, -09-26, 2026-10-22 and 2026-10-23; docs/etsy-how-its-made.md has the evidence). The
request for them (etsy/open-api discussion #1630, June 2026) has no answer from Etsy, and Listyro
never automates Etsy's website. So:

* the seller answers both once on each profile (``listing_profile.how_made_production`` and
  ``how_made_tools``); "none" stands alone. The profile card says, once, that Etsy does not let
  apps set them yet and where Shop Manager sets them for many listings at once;
* the seller does nothing per listing for them: no tick list, no step on a draft, and nothing
  about them ever holds up a draft, a schedule or a publish (the owner's rule, 2026-10-23);
* the code is ready to send them: once Etsy publishes the fields, fill :data:`FIELD_NAMES` and
  :data:`VALUES` from Etsy's schema and set ``ETSY_HOW_ITS_MADE_FIELDS=true``. Every draft path
  (they all build the draft in ``etsy/publisher.py``) then sends them in every shop: the reference
  listing's own answers first (:data:`REFERENCE_KEY` in the profile's payload, read when the
  profile is refreshed once getListing returns them), else the profile's selection.
  Today :func:`draft_fields` returns nothing. A weekly job watches the document
  (workers/etsy_spec.py). Steps: docs/etsy-how-its-made.md.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings

#: key -> (Etsy's wording, the short words on a profile card)
PRODUCTION: dict[str, tuple[str, str]] = {
    "made_from_scratch": ("Made from scratch", "made from scratch"),
    "assembled": ("Assembled from purchased parts", "assembled from purchased parts"),
    "altered": ("An item my shop alters", "shop alters it"),
    "curated": ("A curated set of purchased goods", "curated set of purchased goods"),
    "natural": ("A natural material", "natural material"),
}
TOOLS: dict[str, tuple[str, str]] = {
    "handheld": ("Handheld or hand-guided tools", "handheld tools"),
    "computerized": ("Computerized tools or machines", "computerized tools"),
    "ai": ("An AI generator", "AI generator"),
    "none": ("None", "no tools"),
}
NONE = "none"

#: Etsy's field names, once Etsy publishes them ({"production": ..., "tools": ...}); empty today.
FIELD_NAMES: dict[str, str] = {}
#: Our answer key -> Etsy's value, per field, once published; empty today.
VALUES: dict[str, dict[str, Any]] = {"production": {}, "tools": {}}
#: Where a profile refresh will keep the reference listing's own answers, once getListing returns
#: them ({"production": <key>, "tools": [<key>, ...]}); nothing writes it today.
REFERENCE_KEY = "how_made"


class NotValid(ValueError):
    """The answers cannot be saved (message is safe to show)."""


def clean(production: str | None, tools: list[str] | None) -> tuple[str | None, list[str] | None]:
    """Both answers, checked: one production answer; at least one tool, "none" only on its own.
    (None, None) clears them."""
    if production is None and not tools:
        return None, None
    if production not in PRODUCTION:
        raise NotValid("choose how your shop produces this item")
    chosen = list(dict.fromkeys(t for t in (tools or [])))
    if not chosen:
        raise NotValid("choose the tools used, or none")
    unknown = [t for t in chosen if t not in TOOLS]
    if unknown:
        raise NotValid(f"unknown tool: {unknown[0]}")
    if NONE in chosen and len(chosen) > 1:
        raise NotValid('"none" cannot be chosen with other tools')
    return production, [t for t in TOOLS if t in chosen]


def is_set(profile: Any) -> bool:
    return bool(profile is not None and profile.how_made_production and profile.how_made_tools)


def short_words(profile: Any) -> str | None:
    """"shop alters it · computerized tools", or None when not set."""
    if not is_set(profile):
        return None
    tools = ", ".join(TOOLS[t][1] for t in profile.how_made_tools if t in TOOLS)
    return f"{PRODUCTION[profile.how_made_production][1]} · {tools}"


def card_line(profile: Any) -> str:
    """The profile card's one line."""
    words = short_words(profile)
    return f"How it's made: {words}" if words else "How it's made: not set"


def in_use(profile: Any) -> tuple[str | None, list[str] | None, str | None]:
    """(production, tools, source) a draft would send: the reference listing's own answers when
    the profile holds them (``"reference"``), else the profile's selection (``"profile"``), else
    nothing (None)."""
    if profile is None:
        return None, None, None
    ref = (getattr(profile, "cached_payload", None) or {}).get(REFERENCE_KEY) or {}
    try:
        production, tools = clean(ref.get("production"), ref.get("tools"))
    except NotValid:
        production, tools = None, None
    if production:
        return production, tools, "reference"
    if is_set(profile):
        return profile.how_made_production, list(profile.how_made_tools), "profile"
    return None, None, None


def draft_fields(profile: Any) -> dict[str, Any]:
    """What a draft sends for these questions: nothing until Etsy publishes the fields, they are
    filled in above and ETSY_HOW_ITS_MADE_FIELDS is on. Never raises: a missing or unmapped
    answer sends nothing and holds up nothing."""
    if not get_settings().etsy_how_its_made_fields or not FIELD_NAMES:
        return {}
    production, tools, _ = in_use(profile)
    out: dict[str, Any] = {}
    value = VALUES.get("production", {}).get(production) if production else None
    if FIELD_NAMES.get("production") and value is not None:
        out[FIELD_NAMES["production"]] = value
    sent = [VALUES.get("tools", {}).get(t) for t in tools or []]
    if FIELD_NAMES.get("tools") and sent and all(v is not None for v in sent):
        out[FIELD_NAMES["tools"]] = sent
    return out
