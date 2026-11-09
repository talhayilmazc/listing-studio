"""The last step for every field the model writes, before it is saved (fix/onboarding-followups, Part A).

Found 8 November 2026 on our own example designs: the opening sentence the content step wrote ended in
``}]`` once and in an invisible zero-width space another time. That text becomes the top of an Etsy
description. Why: with structured output the JSON stays valid, but the *last* string of the answer
(``opening``, when a category has no attribute lists) sometimes carries the closing characters of the
array before it, or an invisible character, inside the string itself; nothing checked the strings.

The source is fixed in the content schema (``opening`` no longer comes last) and the prompts say plain
text. This module is the net under it, used by every place that saves model text (content, title fit,
listing-health rewrites, personalization shortening, the free tool's ideas):

* ``clean`` removes invisible and control characters (zero-width spaces and joiners, word joiner, byte
  order mark, soft hyphen, bidi marks), JSON, markdown and code-fence residue at the ends (``}]``,
  ``]}``, `````json``, unmatched brackets and quotes) and normalises whitespace;
* ``junk`` says what is wrong with a text (for a check, a test or ``scan-ai-text``), without changing it.

A field that still has junk after ``clean`` (residue in the middle, which cannot be removed safely) is a
validation error: the writer tries once more, then the listing is marked for the seller (never saved
with junk). Nothing here is applied to what a seller typed: only to what the model wrote, and to what
the seller approves in Listing health.
"""

from __future__ import annotations

import re
import unicodedata

#: Invisible characters that are never meant in a listing (format characters and the like).
INVISIBLE = frozenset(
    [chr(c) for c in range(0x200B, 0x2010)]  # zero-width space, non-joiner, joiner, LRM, RLM
    + [chr(c) for c in range(0x202A, 0x202F)]  # bidi embeddings and overrides
    + [chr(c) for c in range(0x2060, 0x2070)]  # word joiner, invisible operators, bidi isolates
    + ["﻿", "­", "᠎", "؜", "͏", " ", " "]
)
#: Characters that only ever come from JSON, markdown or code formatting, at the end or start of a field.
_FENCE = re.compile(r"`{3,}(?:json|javascript|js|text|txt|markdown|md|html|python)?", re.IGNORECASE)
#: A run of two or more JSON closers or openers at an edge is always residue ("}]", '"}', '["').
_CLOSERS = re.compile(r'[\s"}\]]*[}\]][\s"}\]]*$')
_OPENERS = re.compile(r'^[\s"{\[]*[{\[][\s"{\[]*')
_EDGE = set('{}[]`"“”')
_PAIRS = {"]": "[", "}": "{", ")": "("}
#: Residue in the middle of a text, which cannot be removed safely: the field is written again.
_MIDDLE = re.compile(r'\}\s*\]|\]\s*\}|"\s*[}\]]|[{\[]\s*"|`{3}|"\s*:\s*["\[{]|\\[nt"]')


def _drop_invisible(text: str) -> str:
    out = []
    for ch in text:
        if ch in INVISIBLE:
            continue
        if unicodedata.category(ch) == "Cc" and ch not in "\n\t":
            continue
        out.append(ch)
    return "".join(out)


def _balanced(text: str, ch: str) -> bool:
    """Whether a bracket or quote at the edge belongs to the text (it has its partner inside)."""
    if ch in _PAIRS:
        return text.count(_PAIRS[ch]) >= text.count(ch)
    if ch in "[{(":
        closing = {v: k for k, v in _PAIRS.items()}[ch]
        return text.count(closing) >= text.count(ch)
    if ch == '"':
        return text.count('"') % 2 == 0
    if ch in "“”":
        return text.count("“") == text.count("”")
    return False  # a backtick at an edge is never meant


def _trim_edges(text: str, *, whole: bool = True) -> str:
    """Residue at either end: code fences, runs of JSON closers or openers, a field wrapped in quotes, and
    single brackets or quotes with no partner in the text."""
    text = _FENCE.sub("", text) if "```" in text else text
    changed = True
    while changed and text:
        changed = False
        stripped = text.strip()
        run = _CLOSERS.search(stripped)
        if run and len(run.group().strip()) >= 2:
            text, changed = stripped[: run.start()], True
            continue
        run = _OPENERS.match(stripped)
        if run and len(run.group().strip()) >= 2:
            text, changed = stripped[run.end():], True
            continue
        if len(stripped) > 1 and stripped[0] == stripped[-1] == '"' and stripped.count('"') == 2:
            text, changed = stripped[1:-1], True  # the whole field in quotes
            continue
        # A one-line field (title, tag, opening) wholly in one bracket pair; never a description's own line.
        if whole and len(stripped) > 1 and (stripped[0], stripped[-1]) in (("[", "]"), ("{", "}"))                 and stripped.count(stripped[0]) == 1 and stripped.count(stripped[-1]) == 1:
            text, changed = stripped[1:-1], True
            continue
        if stripped and stripped[-1] in _EDGE and not _balanced(stripped, stripped[-1]):
            text, changed = stripped[:-1], True
            continue
        if stripped and stripped[0] in _EDGE and not _balanced(stripped, stripped[0]):
            text, changed = stripped[1:], True
            continue
        text = stripped
    return text


def _spaces(text: str, *, lines: bool) -> str:
    if not lines:
        return " ".join(text.split())
    rows = [" ".join(row.split()) for row in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out = "\n".join(rows).strip()
    return re.sub(r"\n{3,}", "\n\n", out)


def clean(text: str | None, *, lines: bool = False) -> str:
    """``text`` without invisible characters, edge residue or odd whitespace. ``lines``: keep line breaks
    (a description); otherwise one line (a title, a tag, an opening sentence, an attribute value)."""
    if not text:
        return ""
    out = _spaces(_drop_invisible(str(text)), lines=lines)
    out = _trim_edges(out, whole=not lines)
    if lines:
        out = "\n".join(_trim_edges(row, whole=False) if row.strip() else row for row in out.split("\n"))
    return _spaces(out, lines=lines)


def clean_tags(tags: list[str] | None) -> list[str]:
    """Each tag cleaned; empty ones dropped; the first of two that clean to the same kept."""
    out: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        value = clean(tag)
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            out.append(value)
    return out


def junk(text: str | None, *, lines: bool = False) -> list[str]:
    """What is wrong with ``text``, in plain words; empty when nothing is. Whitespace alone is not junk."""
    if not text:
        return []
    found = []
    if any(ch in INVISIBLE or (unicodedata.category(ch) == "Cc" and ch not in "\n\t\r") for ch in text):
        found.append("invisible characters")
    if _trim_edges(_spaces(_drop_invisible(text), lines=lines), whole=not lines) != _spaces(_drop_invisible(text), lines=lines):
        found.append("leftover code characters at the start or end")
    if _MIDDLE.search(_drop_invisible(text)):
        found.append("leftover code characters in the text")
    return found


def leftover(text: str | None) -> bool:
    """Residue ``clean`` cannot remove (in the middle of the text): the field is written again."""
    return bool(text and _MIDDLE.search(text))
