"""The trademark blocklist (docs/duzeltmeler-v6.md §B).

Brand and character names a seller has no right to use are refused in titles,
tags and descriptions while ``TRADEMARK_FILTER`` is on (the default). The terms
are read from a plain text file (``trademarks.txt`` next to this module, or
``TRADEMARK_LIST_PATH``), one per line, so the list grows without a code change;
the file is re-read when it changes on disk.

Why at all: Etsy's API Terms (Section 1) forbid building an application that can
be used to break Etsy's policies, and listing someone else's trademark is the
most common way a generated listing would.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_LIST_PATH = Path(__file__).parent / "trademarks.txt"

# Between the words of a multi-word term: spaces, hyphens, dots, apostrophes or
# nothing, so "Star Wars", "star-wars" and "starwars" are the same term.
_JOIN = r"[\s\-_.'’]*"


def _fold(text: str) -> str:
    """Lowercase with accents removed: "Pokémon" -> "pokemon"."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _pattern(term: str) -> re.Pattern[str] | None:
    words = re.findall(r"[a-z0-9]+", _fold(term))
    if not words:
        return None
    parts = [r"(?:and|&)" if w == "and" else re.escape(w) for w in words]
    return re.compile(r"(?<![a-z0-9])" + _JOIN.join(parts) + r"(?![a-z0-9])")


#: Texts remembered per list (tags and a profile's description repeat across a page's listings).
MEMO_SIZE = 20000


@dataclass(frozen=True)
class Blocklist:
    """Compiled terms. Empty when the filter is off.

    ``find`` is called several times for every listing a page shows (the load test,
    scripts/load_test.py, found it the most expensive thing on the review page): one combined
    pattern answers the usual "none of them" in one search, and answers are remembered."""

    terms: tuple[tuple[str, re.Pattern[str]], ...] = field(default=())
    _any: re.Pattern[str] | None = field(default=None, compare=False, hash=False, repr=False)
    _memo: dict = field(default_factory=dict, compare=False, hash=False, repr=False)

    def find(self, text: str) -> list[str]:
        """The listed terms that appear in ``text``, as written in the list."""
        if not text or not self.terms:
            return []
        known = self._memo.get(text)
        if known is not None:
            return list(known)
        folded = _fold(text)
        if self._any is not None and self._any.search(folded) is None:
            found: tuple[str, ...] = ()
        else:
            found = tuple(term for term, pattern in self.terms if pattern.search(folded))
        if len(self._memo) >= MEMO_SIZE:
            self._memo.clear()
        self._memo[text] = found
        return list(found)

    def __bool__(self) -> bool:
        return bool(self.terms)


def compile_blocklist(terms: Iterable[str]) -> Blocklist:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    seen: set[str] = set()
    for raw in terms:
        term = raw.strip()
        if not term or term.startswith("#") or _fold(term) in seen:
            continue
        pattern = _pattern(term)
        if pattern is not None:
            seen.add(_fold(term))
            compiled.append((term, pattern))
    combined = re.compile("|".join(f"(?:{p.pattern})" for _, p in compiled)) if compiled else None
    return Blocklist(tuple(compiled), combined)


def read_terms(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


# (path) -> (modification time, when it was last looked at, compiled list), so an edited file
# is picked up within a few seconds without a restart, and an unchanged one is neither re-read
# nor recompiled: a page checking 500 listings asks for the list 1,000 times (B11).
_cache: dict[str, tuple[float, float, Blocklist]] = {}
#: How long a list is used before its file's time is looked at again.
RECHECK_SECONDS = 5.0


def _loaded(path: Path) -> Blocklist:
    """The compiled list in ``path`` (the bundled list when it cannot be read)."""
    key = str(path)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and now - cached[1] < RECHECK_SECONDS:
        return cached[2]
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # A filter that is on but cannot read its list must not silently pass
        # everything: fall back to the bundled list.
        logger.error("trademark list %s is unreadable; using the bundled list", path)
        return _loaded(DEFAULT_LIST_PATH) if path != DEFAULT_LIST_PATH else Blocklist()
    if cached is not None and cached[0] == mtime:
        _cache[key] = (mtime, now, cached[2])
        return cached[2]
    blocklist = compile_blocklist(read_terms(path))
    _cache[key] = (mtime, now, blocklist)
    return blocklist


def _list_path() -> Path:
    settings = get_settings()
    return Path(settings.trademark_list_path) if settings.trademark_list_path else DEFAULT_LIST_PATH


def configured_blocklist() -> Blocklist:
    """The blocklist in force now: empty when TRADEMARK_FILTER is off."""
    if not get_settings().trademark_filter:
        return Blocklist()
    return _loaded(_list_path())


def bundled_blocklist() -> Blocklist:
    """The full list, whatever any account's filter says: for recognising what a
    design shows, not for refusing words."""
    return _loaded(DEFAULT_LIST_PATH)


def characters_seen(characters: list[str], themes: list[str]) -> list[str]:
    """The characters, franchises and parks a design shows (v7 follow-up).

    The image analysis sometimes names them only as themes ("minnie mouse",
    "epcot") and leaves ``characters`` empty, so any theme the bundled list
    recognises counts too.
    """
    seen = [c for c in characters if c.strip()]
    known = bundled_blocklist()
    for theme in themes:
        if known.find(theme) and theme.casefold() not in {c.casefold() for c in seen}:
            seen.append(theme)
    return seen


def blocklist_for(setting: bool | None) -> Blocklist:
    """The blocklist for an account: its own setting if an admin set one (v7 §A4),
    else TRADEMARK_FILTER."""
    if setting is False:
        return Blocklist()
    if setting is True:
        return _loaded(_list_path())
    return configured_blocklist()


def filter_on(tenant: Any) -> bool:
    """Whether the trademark filter is on for this account.

    An admin override wins; otherwise the seller's own choice (on by default).
    TRADEMARK_FILTER=false still turns it off app-wide, as before.
    """
    if tenant is None:
        return get_settings().trademark_filter
    if tenant.trademark_filter is not None:
        return bool(tenant.trademark_filter)
    return bool(tenant.trademark_filter_seller) and get_settings().trademark_filter


def blocklist_for_tenant(tenant: Any) -> Blocklist:
    return blocklist_for(filter_on(tenant))


async def tenant_blocklist(session, tenant_id) -> Blocklist:  # noqa: ANN001
    """The blocklist in force for this account now."""
    from app.db.models import Tenant

    return blocklist_for_tenant(await session.get(Tenant, tenant_id))


def trademark_summary(title: str, tags: list[str], description: str, blocklist: Blocklist) -> str | None:
    """The same findings as :func:`trademark_errors`, once, as the seller reads
    them (v8 §E): "Remove: Disney, Mickey (title), mickey mouse tee (tags)".
    None when there is nothing to remove."""
    parts: list[str] = []
    in_title = list(dict.fromkeys(blocklist.find(title)))
    if in_title:
        parts.append(", ".join(in_title) + " (title)")
    bad_tags = list(dict.fromkeys(tag for tag in tags if blocklist.find(tag)))
    if bad_tags:
        parts.append(", ".join(bad_tags) + (" (tags)" if len(bad_tags) > 1 else " (tag)"))
    in_description = list(dict.fromkeys(blocklist.find(description)))
    if in_description:
        parts.append(", ".join(in_description) + " (description)")
    return "Remove: " + ", ".join(parts) if parts else None


def trademark_errors(
    title: str, tags: list[str], description: str, blocklist: Blocklist
) -> list[str]:
    """Validation errors, worded as corrections the generator can act on."""
    errors: list[str] = []
    advice = (
        "brand and character names are not allowed. Describe the theme in your own "
        "words instead (the subject, occasion, recipient or style), never the brand"
    )
    for term in blocklist.find(title):
        errors.append(f"remove the trademark '{term}' from the title: {advice}")
    by_term: dict[str, list[str]] = {}
    for tag in tags:
        for term in blocklist.find(tag):
            by_term.setdefault(term, []).append(tag)
    for term, bad in by_term.items():
        errors.append(f"remove the trademark '{term}' from these tags: {bad}: {advice}")
    for term in blocklist.find(description):
        errors.append(f"remove the trademark '{term}' from the description: {advice}")
    return errors
