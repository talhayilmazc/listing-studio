"""How it's made: Etsy's Creativity Standards questions, answered once per profile.

Etsy's listing editor asks "How does your shop produce this item?" (one answer) and "What
tools are used to make this item?" (any of them, or none). Etsy's Open API has no field for
either (checked against the OpenAPI document; the request for them, etsy/open-api discussion
#1630 of June 2026, has no answer from Etsy), and Listyro never automates Etsy's website. So:

* the seller answers both once on each profile (``listing_profile.how_made_production`` and
  ``how_made_tools``); "none" stands alone;
* every review card and "Where and when" says what to tick in Etsy after publishing;
* "Finish in Etsy" (api/finish_in_etsy.py) lists every listing Listyro created whose answers
  are not marked done, grouped by the answers to tick, with the editor link; the seller marks
  each done (the two Shop Manager ticks of etsy/manual_fields.py, for that listing);
* the code is ready to send them: once Etsy publishes the fields, fill :data:`FIELD_NAMES` and
  :data:`VALUES` from Etsy's schema and set ``ETSY_HOW_ITS_MADE_FIELDS=true``; every draft path
  (they all build the draft in ``etsy/publisher.py``) then sends them in every shop.
  Today :func:`draft_fields` returns nothing. Steps: docs/etsy-how-its-made.md.
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
#: The two Shop Manager ticks of etsy/manual_fields.py: "done" means both, for that listing.
DONE_KEYS = ("creativity_production", "creativity_tools")

#: Etsy's field names, once Etsy publishes them ({"production": ..., "tools": ...}); empty today.
FIELD_NAMES: dict[str, str] = {}
#: Our answer key -> Etsy's value, per field, once published; empty today.
VALUES: dict[str, dict[str, Any]] = {"production": {}, "tools": {}}


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


def after_publishing(profile: Any) -> str:
    """The review card's and Where and when's line."""
    words = short_words(profile)
    if words:
        return f"After publishing, tick in Etsy: {words}"
    name = getattr(profile, "name", None)
    return (f'How it\'s made is not set on the profile "{name}": set it on the profile first, '
            "then tick the same in Etsy after publishing") if name else "Choose a profile, then set How it's made on it"


def tick(profile: Any, key: str) -> str | None:
    """Etsy's wording to tick for one of the two questions, or None when not set."""
    if not is_set(profile):
        return None
    if key == "creativity_production":
        return PRODUCTION[profile.how_made_production][0]
    return ", ".join(TOOLS[t][0] for t in profile.how_made_tools if t in TOOLS)


def is_done(publication: Any) -> bool:
    keys = publication.manual_done_keys()
    return all(k in keys for k in DONE_KEYS)


def mark_done(publication: Any) -> None:
    """Both ticks, for this listing (a new dict, so the change is saved)."""
    ticks = dict(publication.manual_done or {})
    for key in DONE_KEYS:
        ticks[key] = publication.etsy_listing_id
    publication.manual_done = ticks


def draft_fields(profile: Any) -> dict[str, Any]:
    """What a draft sends for these questions: nothing until Etsy publishes the fields, they are
    filled in above and ETSY_HOW_ITS_MADE_FIELDS is on."""
    if not get_settings().etsy_how_its_made_fields or not FIELD_NAMES or not is_set(profile):
        return {}
    out: dict[str, Any] = {}
    production = VALUES.get("production", {}).get(profile.how_made_production)
    tools = [VALUES.get("tools", {}).get(t) for t in profile.how_made_tools]
    if FIELD_NAMES.get("production") and production is not None:
        out[FIELD_NAMES["production"]] = production
    if FIELD_NAMES.get("tools") and tools and all(v is not None for v in tools):
        out[FIELD_NAMES["tools"]] = tools
    return out
