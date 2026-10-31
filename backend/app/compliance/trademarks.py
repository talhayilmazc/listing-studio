"""The trademark list and how a brand is recognised in a text (docs/duzeltmeler-v6.md §B).

The one place for this: the app and any tool of ours import this module, which needs no database
and no request. Two questions, on purpose:

* :meth:`Blocklist.find`: the name written whole. While the account's filter is on, it stops the
  listing ("Remove: Disney (title)"). Empty when the filter is off.
* :meth:`Blocklist.spot`: the name in any form a seller writes it, joined, spaced or with letters
  swapped too. It never stops anything. It is what :func:`brand_warnings` tells the seller, filter
  on or off, about whatever ``find`` did not already stop: we warn, and the seller decides.

The terms are in a dated text file (``trademarks.txt`` next to this module, or
``TRADEMARK_LIST_PATH``), one per line with a short source note, so the list grows without a code
change; the file is re-read when it changes on disk.

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

#: What the seller reads about a brand the filter did not stop.
BRAND_WARNING = ("{brand} is a trademark. Etsy often removes listings that use it without permission, "
                 "and repeated removals can suspend a shop.")

#: Inside a brand, for :meth:`Blocklist.spot`: "mick ey", "dis-ney", "star_wars", "#".
_INSIDE = r"[\s\-_.'’#]{0,2}"
_PIECE = re.compile(r"[\s\-_.'’#]+")
#: Common letter swaps. "i" and "l" stand in for each other only in longer names, and a short
#: name takes none: "nhi" and "mib" are not the NHL and MLB.
_SWAPS = {"i": "i1", "l": "l1", "o": "o0", "e": "e3", "a": "a4", "s": "s$"}
_SWAPS_LONG = {**_SWAPS, "i": "i1l", "l": "l1i"}
#: From this many letters a name may have words joined after it ("disneyworld").
JOINED_AFTER_FROM = 5
#: From this many letters, words joined before it too ("ilovedisney").
JOINED_BEFORE_FROM = 6
#: Under this many letters a name is only ever whole (NFL, NBA): too short to be sure of.
LOOSE_FROM = 4
#: Ordinary words that begin or end with a listed name. Nothing else protects them: a name found at
#: the edge of a longer word counts as the name unless that word is here.
ORDINARY_WORDS = frozenset({
    "marvelous", "marvelously", "marvelousness", "marvellous", "marvellously", "marvels", "marveled", "marvelled",
    "marveling", "marvelling", "scavengers", "scavenger", "chanelle", "lionkingdom", "lionkingdoms",
})
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_UNSWAP = str.maketrans({"0": "o", "3": "e", "4": "a", "$": "s"})


def _squeezed(term: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", _fold(term)))


def _spotter(term: str) -> re.Pattern[str] | None:
    letters = _squeezed(term)
    if not letters:
        return None
    if len(letters) < LOOSE_FROM:
        return re.compile(r"\.?".join(re.escape(c) for c in letters))
    swaps = _SWAPS_LONG if len(letters) > LOOSE_FROM else _SWAPS
    parts = [f"[{re.escape(swaps[c])}]" if c in swaps else re.escape(c) for c in letters]
    return re.compile(_INSIDE.join(parts))


def _edge(text: str, at: int) -> bool:
    """A word ends here (a swapped letter is part of a word too)."""
    return at < 0 or at >= len(text) or not (text[at].isalnum() or text[at] == "$")


def _is_ordinary_word(word: str) -> bool:
    word = word.translate(_UNSWAP)
    return word.replace("1", "l") in ORDINARY_WORDS or word.replace("1", "i") in ORDINARY_WORDS


def _counts(text: str, m: re.Match[str], size: int) -> bool:
    """Whether what the pattern matched really is the name, by where its edges fall."""
    pieces = _PIECE.split(m.group(0))
    if len(pieces) > 1 and not (all(len(p) >= 2 for p in pieces) or all(len(p) == 1 for p in pieces)):
        return False  # "Blue Y Neck" is not Bluey; "mick ey" and "d i s n e y" are what they say
    left, right = _edge(text, m.start() - 1), _edge(text, m.end())
    if left and right:
        return True
    if size < LOOSE_FROM or len(pieces) > 1 or not (left or right):
        return False
    start, end = m.start(), m.end()
    while not _edge(text, start - 1):
        start -= 1
    while not _edge(text, end):
        end += 1
    if _is_ordinary_word(text[start:end]):
        return False
    if left:
        return size >= JOINED_AFTER_FROM or text[m.end():end] == "s"
    return size >= JOINED_BEFORE_FROM


@dataclass(frozen=True)
class Spotted:
    """A listed name found in a text, and how it was written there."""

    term: str
    written: str


@dataclass(frozen=True)
class Blocklist:
    """Compiled terms. Empty when the filter is off.

    ``find`` is called several times for every listing a page shows (the load test,
    scripts/load_test.py, found it the most expensive thing on the review page): one combined
    pattern answers the usual "none of them" in one search, and answers are remembered."""

    terms: tuple[tuple[str, re.Pattern[str]], ...] = field(default=())
    _any: re.Pattern[str] | None = field(default=None, compare=False, hash=False, repr=False)
    _memo: dict = field(default_factory=dict, compare=False, hash=False, repr=False)
    _spotters: tuple[tuple[str, int, re.Pattern[str]], ...] = field(default=(), compare=False, hash=False, repr=False)
    _spot_any: re.Pattern[str] | None = field(default=None, compare=False, hash=False, repr=False)
    _spot_memo: dict = field(default_factory=dict, compare=False, hash=False, repr=False)

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

    def spot(self, text: str) -> list[Spotted]:
        """The listed names in ``text`` in any form: whole (what :meth:`find` sees), joined to other
        words, with spaces inside, with common letter swaps; any case. Whole-word aware at both
        ends, never a plain substring: "marvelous", "scavengers" and "Blue Y Neck" are not names."""
        if not text or not self.terms:
            return []
        known = self._spot_memo.get(text)
        if known is not None:
            return list(known)
        found: dict[str, Spotted] = {t: Spotted(t, t) for t in self.find(text)}
        split = _CAMEL.sub(" ", text)  # "ILoveDisney", "DisneyPrincess": the seller's own word breaks
        for variant in dict.fromkeys((_fold(text), _fold(split))):
            if self._spot_any is None or self._spot_any.search(variant) is None:
                continue
            spans: list[tuple[int, int, str, str]] = []
            for term, size, pattern in self._spotters:
                for m in pattern.finditer(variant):
                    if _counts(variant, m, size):
                        spans.append((m.start(), m.end(), term, m.group(0)))
            for start, end, term, written in spans:
                # "disneyland" is Disneyland, not Disney as well.
                inside = any(s <= start and end <= e and (e - s) > (end - start) for s, e, other, _ in spans if other != term)
                if not inside and term not in found:
                    found[term] = Spotted(term, written)
        # A name written whole inside a longer listed name was not written at all.
        names = {_squeezed(t): t for t in found}
        for squeezed, term in list(names.items()):
            if any(squeezed != other and squeezed in other for other in names) and not self._alone(term, text):
                found.pop(term, None)
        result = tuple(found.values())
        if len(self._spot_memo) >= MEMO_SIZE:
            self._spot_memo.clear()
        self._spot_memo[text] = result
        return list(result)

    def _alone(self, term: str, text: str) -> bool:
        return term in self.find(text)

    def __bool__(self) -> bool:
        return bool(self.terms)


def compile_blocklist(terms: Iterable[str]) -> Blocklist:
    compiled: list[tuple[str, re.Pattern[str]]] = []
    spotters: list[tuple[str, int, re.Pattern[str]]] = []
    seen: set[str] = set()
    for raw in terms:
        term = raw.strip()
        if not term or term.startswith("#") or _fold(term) in seen:
            continue
        pattern = _pattern(term)
        if pattern is not None:
            seen.add(_fold(term))
            compiled.append((term, pattern))
            spotter = _spotter(term)
            if spotter is not None:
                spotters.append((term, len(_squeezed(term)), spotter))
    combined = re.compile("|".join(f"(?:{p.pattern})" for _, p in compiled)) if compiled else None
    spot_any = re.compile("|".join(f"(?:{s.pattern})" for _, _, s in spotters)) if spotters else None
    return Blocklist(tuple(compiled), combined, _spotters=tuple(spotters), _spot_any=spot_any)


def read_entries(path: Path) -> list[tuple[str, str]]:
    """Each line's term and its source note: ``Disney | The Walt Disney Company``. (A list of bare
    terms still reads: its notes are empty.)"""
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        term, _, note = line.partition("|")
        entries.append((term.strip(), note.strip()))
    return entries


def read_terms(path: Path) -> list[str]:
    return [term for term, _ in read_entries(path)]


def list_date(path: Path | None = None) -> str | None:
    """The day the list was last reviewed: its ``# Reviewed: YYYY-MM-DD`` line."""
    found = re.search(r"^#\s*Reviewed:\s*(\d{4}-\d{2}-\d{2})", (path or DEFAULT_LIST_PATH).read_text(encoding="utf-8"), re.M)
    return found.group(1) if found else None


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


def all_marks() -> Blocklist:
    """The whole list, whatever any filter says: what the seller is warned about."""
    return _loaded(_list_path())


def spot_in_listing(title: str, tags: list[str], description: str) -> dict[str, list[Spotted]]:
    """Every listed name in a listing's text, by where it is."""
    known = all_marks()
    return {"title": known.spot(title), "tags": [s for tag in tags for s in known.spot(tag)],
            "description": known.spot(description)}


def brand_warnings(title: str, tags: list[str], description: str, in_force: Blocklist) -> list[str]:
    """What the seller is told about the brands in this text that ``in_force`` (the account's
    filter) did not already stop. Never stops anything. Each brand once."""
    stopped = {*in_force.find(title), *in_force.find(description), *(t for tag in tags for t in in_force.find(tag))}
    spotted = spot_in_listing(title, tags, description)
    brands = dict.fromkeys(s.term for where in spotted.values() for s in where)
    return [BRAND_WARNING.format(brand=term) for term in brands if term not in stopped]


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
