"""What Etsy itself refuses in a draft's title, tags and description.

From Etsy's OpenAPI spec for ``createDraftListing`` (read 21 October 2026):

* ``title`` and ``description`` are required;
* a title holds only letters, numbers, punctuation, mathematical symbols,
  whitespace, ™, © and ® (``/[^\\p{L}\\p{Nd}\\p{P}\\p{Sm}\\p{Zs}™©®]/u``), and the
  %, :, & and + characters at most once each;
* a tag holds only letters, numbers, whitespace, -, ', ™, © and ®
  (``/[^\\p{L}\\p{Nd}\\p{Zs}\\-'™©®]/u``).

The spec does not state lengths; Etsy's documented limits are a title of 140
characters at most and 13 tags of 20 characters at most, and the app already
held listings to them (``content.MAX_TITLE_LENGTH``, ``MAX_TAG_LENGTH``).

A listing the seller writes themselves is held only to these (the app's own
quality rules are warnings on it, ``content.check_listing``). The TypeScript
copy is ``frontend/lib/etsyText.ts``; both read ``tests/fixtures/etsy_text_cases.json``.
"""

from __future__ import annotations

import functools
import unicodedata

#: Etsy's title limit and tag limits (documented; not in the spec's text).
MAX_TITLE = 140
MAX_TAGS = 13
MAX_TAG = 20
#: Characters a title may use only once each.
ONCE = ("%", ":", "&", "+")
_MARKS = {"™", "©", "®"}

TITLE_EMPTY = "title is empty"
DESCRIPTION_EMPTY = "description is empty"
TAG_EMPTY = "a tag is empty"


def _title_char_ok(ch: str) -> bool:
    if ch in _MARKS:
        return True
    cat = unicodedata.category(ch)
    return cat[0] in ("L", "P") or cat in ("Nd", "Sm", "Zs")


@functools.lru_cache(maxsize=2048)
def _tag_char_ok(ch: str) -> bool:
    if ch in _MARKS or ch in ("-", "'"):
        return True
    cat = unicodedata.category(ch)
    return cat[0] == "L" or cat in ("Nd", "Zs")


def _shown(chars: list[str]) -> str:
    out = []
    for ch in dict.fromkeys(chars):
        out.append(repr(ch) if ch.isprintable() and not ch.isspace() else f"U+{ord(ch):04X}")
    return " ".join(out)


#: Characters people paste from documents and phones, and what Etsy takes instead.
_CURLY = {"’", "‘", "ʼ"}
_DASHES = {"–", "—", "‒"}


def _fix(bad: list[str], *, tag: bool) -> str:
    """The plain fix for characters Etsy refuses (said after the refusal)."""
    chars = set(bad)
    hints: list[str] = []
    if chars & _CURLY:
        hints.append("use a straight apostrophe (') instead of a curly one")
    if tag and chars & _DASHES:
        hints.append("use a hyphen (-) instead of a dash")
    if tag and "&" in chars:
        hints.append("write \"and\" instead of &")
    if chars - _CURLY - (_DASHES if tag else set()) - ({"&"} if tag else set()):
        hints.append("remove the others (emoji, symbols, tabs)" if hints else "remove them (emoji, symbols, tabs)")
    return "; ".join(hints)


def title_errors(title: str) -> list[str]:
    """Why Etsy would refuse this title (empty list: it would not)."""
    if not title.strip():
        return [TITLE_EMPTY]
    errors: list[str] = []
    if len(title) > MAX_TITLE:
        errors.append(f"title is over Etsy's {MAX_TITLE} characters (yours is {len(title)})")
    bad = [ch for ch in title if not _title_char_ok(ch)]
    if bad:
        errors.append(f"title has characters Etsy does not allow: {_shown(bad)}: {_fix(bad, tag=False)}")
    twice = [ch for ch in ONCE if title.count(ch) > 1]
    if twice:
        fix = " (write \"and\" for the other &)" if "&" in twice else ""
        errors.append(f"title uses {' '.join(twice)} more than once; Etsy allows %, :, & and + once each{fix}")
    return errors


def tag_errors(tags: list[str]) -> list[str]:
    """Why Etsy would refuse these tags (empty list: it would not)."""
    errors: list[str] = []
    if len(tags) > MAX_TAGS:
        errors.append(f"more than {MAX_TAGS} tags ({len(tags)}); Etsy takes {MAX_TAGS} at most")
    for tag in tags:
        if not tag.strip():
            errors.append(TAG_EMPTY)
            continue
        if len(tag) > MAX_TAG:
            # The same words as content.tag_problems, so the two lists merge.
            errors.append(f"tag exceeds {MAX_TAG} characters: {tag!r}")
        if "," in tag:
            errors.append(f"tag contains a comma: {tag!r}")
        bad = [ch for ch in tag if ch != "," and not _tag_char_ok(ch)]
        if bad:
            errors.append(f"tag has characters Etsy does not allow: {tag!r} ({_shown(bad)}): {_fix(bad, tag=True)}")
    return list(dict.fromkeys(errors))


def draft_errors(title: str, tags: list[str], description: str) -> list[str]:
    """Everything in the listing's own text that Etsy would refuse for a draft."""
    errors = title_errors(title) + tag_errors(tags)
    if not description.strip():
        errors.append(DESCRIPTION_EMPTY)
    return errors
