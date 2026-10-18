"""Listing personalization, copied from the reference (v7 §D4).

Etsy moved personalization out of the listing fields: createDraftListing no
longer takes ``personalization_instructions`` or ``personalization_char_count_max``.
It is now a separate resource, read with getListingPersonalization and written
with updateListingPersonalization as a list of questions; Etsy currently
supports a single ``text_input`` question.

A profile copies the reference listing's question on refresh. The seller can
override it on the profile card (turn it off, or change the question); the
override wins until reset.

What Etsy accepts (Open API v3 ``updateListingPersonalization`` and its
personalization migration guide, read 2026-10-18): a JSON body
``{"personalization_questions": [...]}``; each question needs ``question_text``,
``question_type`` and ``required``, and a ``text_input`` question also needs
``max_allowed_characters`` (1-1024). ``question_text`` is 1-45 characters and
``instructions`` at most 120; both must start with a letter or digit and may not
hold more than one word starting with three or more capital letters. A question
that breaks one of these is refused here, before anything is sent, with a sentence
the seller can act on; nothing is shortened for them.
"""

from __future__ import annotations

import re
from typing import Any

MAX_QUESTION_TEXT = 45  # Etsy: 1-45 characters
MAX_INSTRUCTIONS = 120  # Etsy: at most 120 (the app allowed 256 before 2026-10-18)
MAX_CHARACTERS = 1024  # Etsy: 1-1024
#: Etsy requires a character limit on a text question and gives no default; when neither
#: the seller nor the reference set one, this is sent (the app's choice, shown on the card).
DEFAULT_MAX_CHARACTERS = 256
_SHOUTED = re.compile(r"\b[A-Z]{3,}")


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


def etsy_problems(setting: dict[str, Any] | None) -> list[str]:
    """Why Etsy would refuse this question, in the seller's words ([] when it can be sent)."""
    if not setting or not setting.get("enabled"):
        return []
    out: list[str] = []
    text = str(setting.get("question_text") or "").strip()
    instructions = str(setting.get("instructions") or "").strip()
    if not 1 <= len(text) <= MAX_QUESTION_TEXT:
        out.append(f"the question must be 1 to {MAX_QUESTION_TEXT} characters")
    if len(instructions) > MAX_INSTRUCTIONS:
        out.append(f"instructions are limited to {MAX_INSTRUCTIONS} characters (yours are {len(instructions)})")
    for name, value in (("question", text), ("instructions", instructions)):
        if value and not value[0].isalnum():
            out.append(f"the {name} must start with a letter or a number")
        if len(_SHOUTED.findall(value)) > 1:
            out.append(f"the {name} may have only one word in capitals")
    limit = setting.get("max_allowed_characters")
    if limit is not None and not 1 <= int(limit) <= MAX_CHARACTERS:
        out.append(f"the character limit must be between 1 and {MAX_CHARACTERS}")
    return out


def validate(override: dict[str, Any]) -> dict[str, Any]:
    """A seller's override, cleaned; ValueError with the reason if it cannot be sent."""
    if not override.get("enabled"):
        return {"enabled": False}
    problems = etsy_problems({**override, "enabled": True})
    if problems:
        raise ValueError(problems[0])
    text = str(override.get("question_text") or "").strip()
    instructions = str(override.get("instructions") or "").strip()
    limit = override.get("max_allowed_characters")
    return {
        "enabled": True,
        "question_text": text,
        "instructions": instructions,
        "required": bool(override.get("required")),
        "max_allowed_characters": int(limit) if limit is not None else None,
    }


def character_limit(setting: dict[str, Any]) -> int:
    """The character limit a text question is sent with (Etsy requires one)."""
    return int(setting.get("max_allowed_characters") or DEFAULT_MAX_CHARACTERS)


def questions_for(setting: dict[str, Any]) -> list[dict[str, Any]]:
    """The updateListingPersonalization body for an enabled setting. A text question
    always carries ``max_allowed_characters``: Etsy requires it (before 2026-10-18 it was
    sent only when one was set, which Etsy's schema calls invalid)."""
    q: dict[str, Any] = {
        "question_text": str(setting["question_text"]).strip(),
        "question_type": "text_input",
        "required": bool(setting.get("required")),
        "max_allowed_characters": character_limit(setting),
    }
    if setting.get("instructions"):
        q["instructions"] = str(setting["instructions"]).strip()
    return [q]


#: The question a listing gets when the seller turns personalization on and its
#: profile has none to copy (Etsy requires a question of 1-45 characters).
DEFAULT_QUESTION = "Personalization"


def for_listing(own: dict[str, Any] | None, profile_override: dict[str, Any] | None,
                payload: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str]:
    """What a listing's drafts get (v8 §D), and where it comes from: the
    listing's own setting ("listing"), else its profile's ("profile"; None and
    "unknown" while the profile's reference has not been read for it)."""
    if own is not None:
        return own, "listing"
    setting = effective(profile_override, payload)
    return setting, "profile" if setting is not None else "unknown"


def differs(sent: dict[str, Any], back: dict[str, Any] | None) -> list[str]:
    """What Etsy did not keep of the question that was sent (the draft read-back)."""
    questions = [q for q in (back or {}).get("personalization_questions") or []
                 if q.get("question_text") == sent["question_text"]]
    if not questions:
        return ["question"]
    q = questions[0]
    out = []
    if bool(q.get("required")) != bool(sent.get("required")):
        out.append("required")
    if int(q.get("max_allowed_characters") or 0) != character_limit(sent):
        out.append("character limit")
    if (sent.get("instructions") or "").strip() != (q.get("instructions") or "").strip():
        out.append("instructions")
    return out
