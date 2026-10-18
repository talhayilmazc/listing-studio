"""A listing's title for another shop whose prefix makes it too long (pipeline/targets.py).

A listing written for one shop is drafted in another with that shop's title prefix. When
the longer prefix pushes the title past Etsy's 140 characters, the title is **rewritten
once** for that shop, under the same rules the listing was written to (its profile's
style and bounds), and the rewrite is kept for that shop only
(``generated_content.attributes["shop_titles"][<shop id>]``, tied to the title it was
written from, so editing the title drops it). When the rewrite does not fit either, or
cannot be made, the draft for that shop stops before anything is sent to Etsy and the
listing's card says why, like the other title warnings. The title is never cut.

One model call per listing and shop (metered as ``title_fit``); it does not count toward
"Listings generated" (the design was already written).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.db.models import EtsyConnection, GeneratedContent, ListingProfile
from app.pipeline import search_rules
from app.pipeline.content import TITLE_TOO_SHORT, bounds_for, join_prefix, title_length_errors, uses_search_style

SHOP_TITLES = "shop_titles"
TITLE_WARNINGS = "title_warnings"

#: The model's answer.
SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}},
    "required": ["title"],
    "additionalProperties": False,
}

Rewrite = Callable[[str, str, int, ListingProfile], Awaitable[str | None]]


def stored_for(content: GeneratedContent, connection_id: Any) -> str | None:
    """The title already rewritten for this shop, while the listing's title is unchanged."""
    entry = ((content.attributes or {}).get(SHOP_TITLES) or {}).get(str(connection_id))
    if isinstance(entry, dict) and entry.get("for") == (content.title or ""):
        return str(entry.get("title") or "") or None
    return None


def problems(title: str, profile: ListingProfile) -> list[str]:
    """What keeps ``title`` from being sent for ``profile``: Etsy's 140 characters and the
    profile's own rules (the short style's guidance too). A title a little under the
    profile's minimum is still sent, as the shop-prefix swap always allowed: a short title
    beats refusing the shop."""
    rules = search_rules.rules_for(profile) if uses_search_style(profile) else bounds_for(profile)
    errors = [e for e in title_length_errors(title, rules) if not e.startswith(TITLE_TOO_SHORT)]
    if rules.readable:
        errors += search_rules.title_errors(title, rules, prefix=profile.title_prefix)
    return errors


def warning(shop: str, length: int) -> str:
    return (f"With {shop}'s prefix the title is {length} characters; Etsy allows 140. It was kept as written, "
            f"not cut: shorten it here, then create the {shop} draft again.")


async def fit_for_shop(content: GeneratedContent, connection: EtsyConnection, profile: ListingProfile,
                       title: str, rewrite: Rewrite) -> str:
    """The title the draft in ``connection`` is sent with: ``title`` when it fits, else the
    one rewrite (kept for this shop). Raises ValueError with the seller's sentence, and
    flags the card, when no version fits; nothing is cut."""
    if len(title) <= search_rules.ETSY_MAX_TITLE_LENGTH:
        return title
    shop = connection.shop_name or "this shop"
    prefix = (profile.title_prefix or "").strip()
    stored = stored_for(content, connection.id)
    if stored and not problems(stored, profile):
        return stored
    room = search_rules.ETSY_MAX_TITLE_LENGTH - (len(prefix) + 1 if prefix else 0)
    base = search_rules.without_prefix(title, prefix)
    try:
        rewritten = await rewrite(base, prefix, room, profile)
    except Exception:  # noqa: BLE001 - no rewrite this time: flagged, never cut
        rewritten = None
    candidate = join_prefix(prefix, rewritten) if rewritten else None
    attributes = dict(content.attributes or {})
    if candidate and not problems(candidate, profile):
        attributes[SHOP_TITLES] = {**(attributes.get(SHOP_TITLES) or {}),
                                   str(connection.id): {"for": content.title or "", "title": candidate}}
        content.attributes = attributes
        return candidate
    message = warning(shop, len(title))
    attributes[TITLE_WARNINGS] = list(dict.fromkeys([*(attributes.get(TITLE_WARNINGS) or []), message]))
    content.attributes = attributes
    raise ValueError(message)


def model_rewrite(client: Any) -> Rewrite:
    """The one rewrite, by the content model (``client``: pipeline/llm.py)."""
    from app.core import ai_meter

    async def rewrite(base: str, prefix: str, room: int, profile: ListingProfile) -> str | None:
        short = uses_search_style(profile)
        rules = (
            "Keep the item and its key details first, fewer than 15 words in all with the prefix, each word once, "
            "no opinion words, no gift, price, sale or shipping wording."
            if short else
            "Keep it as comma-separated keyword phrases with the most important first; drop the least important "
            "phrase or shorten words before changing what the item is."
        )
        text = (
            f"This Etsy listing title is too long for a shop whose title starts with the prefix \"{prefix}\". "
            f"Rewrite the part after the prefix so it is at most {room} characters (spaces included). "
            f"Keep what the listing is and its words where you can; add nothing new. {rules}\n\nTitle: {base}"
        )
        with ai_meter.purpose("title_fit"):
            result = await client.complete_json(
                system="You shorten Etsy listing titles without changing what they describe. Answer in JSON.",
                content_blocks=[{"type": "text", "text": text}], schema=SCHEMA, max_tokens=300)
        out = str((result.data or {}).get("title") or "").strip()
        return search_rules.without_prefix(out, prefix) or None

    return rewrite
