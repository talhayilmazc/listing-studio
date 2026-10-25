"""A shorter personalization text, offered to the seller (2026-10-25).

Etsy refuses instructions over 120 characters (and a question over 45). The content model
writes a version within the limit with the same meaning; the seller sees it beside their
own and nothing changes until they press "Use this" (api/personalization_fix.py). The offer
is checked against Etsy's rules before it is shown: within the limit, at most one word in
capitals (fixed if the model wrote more), starting with a letter or number. One call per
text (metered as ``personalization_fix``), kept for a week by the text's hash, so opening the
card again costs nothing. It does not count toward "Listings generated".
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.pipeline.personalization import MAX_INSTRUCTIONS, MAX_QUESTION_TEXT, fix_caps, shouted

SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}
#: How long an offer is kept (Redis), by the hash of the text and field.
CACHE_SECONDS = 7 * 24 * 3600
TRIES = 2


def limit_for(field: str) -> int:
    return MAX_QUESTION_TEXT if field == "question" else MAX_INSTRUCTIONS


def acceptable(text: str, field: str) -> str | None:
    """The offer made fit for Etsy (capitals fixed), or None when it still is not."""
    out = fix_caps(" ".join((text or "").split()))
    if not out or len(out) > limit_for(field) or len(shouted(out)) > 1 or not out[0].isalnum():
        return None
    return out


def cache_key(field: str, text: str) -> str:
    return "pfix:" + hashlib.sha256(f"{field}\n{text}".encode()).hexdigest()[:32]


async def shorten(client: Any, text: str, field: str = "instructions") -> str | None:
    """A version of ``text`` within Etsy's limit with the same meaning, by the content model,
    or None when two tries did not give one (the seller edits it by hand then)."""
    from app.core import ai_meter

    limit = limit_for(field)
    what = "instructions shown to the buyer" if field == "instructions" else "question the buyer answers"
    for attempt in range(TRIES):
        room = limit - 10 * attempt  # a second try asks for a little less
        prompt = (
            f"These are the personalization {what} on an Etsy listing. Etsy allows at most {limit} characters. "
            f"Rewrite them in at most {room} characters (spaces included) with the same meaning and the same "
            "language. Keep every instruction the buyer needs (what to type, limits, format); drop only filler. "
            "Use normal sentence case: at most one word in capital letters. Start with a letter or a number.\n\n"
            f"Text: {text}"
        )
        with ai_meter.purpose("personalization_fix"):
            result = await client.complete_json(
                system="You shorten short texts for Etsy listings without changing their meaning. Answer in JSON.",
                content_blocks=[{"type": "text", "text": prompt}], schema=SCHEMA, max_tokens=300)
        out = acceptable(str((result.data or {}).get("text") or ""), field)
        if out is not None:
            return out
    return None
