"""Listing personalization, copied from the reference (v7 §D4).

Etsy moved personalization out of the listing fields: createDraftListing no
longer takes ``personalization_instructions`` or ``personalization_char_count_max``.
It is now a separate resource, read with getListingPersonalization and written
with updateListingPersonalization as a list of questions; Etsy currently
supports a single ``text_input`` question.

A profile copies the reference listing's question on refresh. The seller can
override it on the profile card (turn it off, or change the question); the
override wins until reset.
"""

from __future__ import annotations

from typing import Any

MAX_QUESTION_TEXT = 45  # Etsy: 1-45 characters
MAX_INSTRUCTIONS = 256
MAX_CHARACTERS = 1024


def from_reference(resp: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The reference's text questions, as the payload stores them."""
    out = []
    for q in (resp or {}).get("personalization_questions") or []:
        if q.get("question_type") != "text_input":
            continue  # only text questions can be written today
        out.append(
            {
                "question_text": str(q.get("question_text") or "")[:MAX_QUESTION_TEXT],
                "instructions": q.get("instructions") or "",
                "required": bool(q.get("required")),
                "max_allowed_characters": q.get("max_allowed_characters"),
            }
        )
    return out


def effective(override: dict[str, Any] | None, payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """What a draft built with this profile gets: the seller's override, else the
    reference's question. None when the reference has not been read for it yet."""
    if override is not None:
        return override
    payload = payload or {}
    if "personalization" not in payload:
        return None
    questions = payload.get("personalization") or []
    if not questions:
        return {"enabled": False}
    return {"enabled": True, **questions[0]}


def validate(override: dict[str, Any]) -> dict[str, Any]:
    """A seller's override, cleaned; ValueError with the reason if it cannot be sent."""
    if not override.get("enabled"):
        return {"enabled": False}
    text = str(override.get("question_text") or "").strip()
    if not 1 <= len(text) <= MAX_QUESTION_TEXT:
        raise ValueError(f"the question must be 1 to {MAX_QUESTION_TEXT} characters")
    instructions = str(override.get("instructions") or "").strip()
    if len(instructions) > MAX_INSTRUCTIONS:
        raise ValueError(f"instructions are limited to {MAX_INSTRUCTIONS} characters")
    limit = override.get("max_allowed_characters")
    if limit is not None and not 1 <= int(limit) <= MAX_CHARACTERS:
        raise ValueError(f"the character limit must be between 1 and {MAX_CHARACTERS}")
    return {
        "enabled": True,
        "question_text": text,
        "instructions": instructions,
        "required": bool(override.get("required")),
        "max_allowed_characters": int(limit) if limit is not None else None,
    }


def questions_for(setting: dict[str, Any]) -> list[dict[str, Any]]:
    """The updateListingPersonalization body for an enabled setting."""
    q: dict[str, Any] = {
        "question_text": setting["question_text"],
        "question_type": "text_input",
        "required": bool(setting.get("required")),
    }
    if setting.get("instructions"):
        q["instructions"] = setting["instructions"]
    if setting.get("max_allowed_characters"):
        q["max_allowed_characters"] = int(setting["max_allowed_characters"])
    return [q]
