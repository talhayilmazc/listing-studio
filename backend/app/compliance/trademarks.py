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
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass(frozen=True)
class Blocklist:
    """Compiled terms. Empty when the filter is off."""

    terms: tuple[tuple[str, re.Pattern[str]], ...] = field(default=())

    def find(self, text: str) -> list[str]:
        """The listed terms that appear in ``text``, as written in the list."""
        if not text or not self.terms:
            return []
        folded = _fold(text)
        return [term for term, pattern in self.terms if pattern.search(folded)]

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
    return Blocklist(tuple(compiled))


def read_terms(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


# (path, modification time) -> compiled list, so an edited file is picked up on
# the next check without a restart, and an unchanged one is not re-read.
_cache: dict[str, tuple[float, Blocklist]] = {}


def configured_blocklist() -> Blocklist:
    """The blocklist in force now: empty when TRADEMARK_FILTER is off."""
    settings = get_settings()
    if not settings.trademark_filter:
        return Blocklist()
    path = Path(settings.trademark_list_path) if settings.trademark_list_path else DEFAULT_LIST_PATH
    try:
        mtime = path.stat().st_mtime
    except OSError:
        # A filter that is on but cannot read its list must not silently pass
        # everything: fall back to the bundled list.
        logger.error("trademark list %s is unreadable; using the bundled list", path)
        path = DEFAULT_LIST_PATH
        mtime = path.stat().st_mtime
    key = str(path)
    cached = _cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    blocklist = compile_blocklist(read_terms(path))
    _cache[key] = (mtime, blocklist)
    return blocklist


def blocklist_for(setting: bool | None) -> Blocklist:
    """The blocklist for an account: its own setting if an admin set one (v7 §A4),
    else TRADEMARK_FILTER."""
    if setting is False:
        return Blocklist()
    if setting is True:
        settings = get_settings()
        path = Path(settings.trademark_list_path) if settings.trademark_list_path else DEFAULT_LIST_PATH
        return compile_blocklist(read_terms(path if path.exists() else DEFAULT_LIST_PATH))
    return configured_blocklist()


async def tenant_blocklist(session, tenant_id) -> Blocklist:  # noqa: ANN001
    """The blocklist in force for this account now."""
    from app.db.models import Tenant

    tenant = await session.get(Tenant, tenant_id)
    return blocklist_for(tenant.trademark_filter if tenant is not None else None)


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
