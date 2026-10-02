"""Listing rules that follow Etsy's current search guidance.

Etsy's Seller Handbook ("New Guidance for Listing Titles, and a Tool to Help",
April 2026; "Keywords 101") moved away from long keyword titles: a title states
clearly what the item is, in fewer than 15 words, without repeated words,
subjective words or anything about price, sales or shipping. The other keywords
belong in the 13 tags (distinct multi-word phrases), the first sentences of the
description, and the category's attributes, all of which Etsy matches on.

Pure checks only; :mod:`app.pipeline.content` decides when they apply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Etsy's own limit; no setting may exceed it.
ETSY_MAX_TITLE_LENGTH = 140
#: The shortest title that can still hold two phrases.
SHORTEST_TITLE = 20


@dataclass(frozen=True)
class TitleRules:
    """What a title must look like. ``readable`` turns on the current guidance:
    no repeated words, the product type named once, no subjective or sales words."""

    min_length: int
    max_length: int
    max_words: int | None = None
    min_phrases: int | None = None
    max_phrases: int | None = None
    readable: bool = False


#: The rule the app shipped with: 110-140 characters of comma-separated keywords.
LEGACY_TITLE = TitleRules(min_length=110, max_length=140)
#: Etsy's guidance: fewer than 15 words, 2-4 phrases. The character bounds are
#: the default a profile may change.
SEARCH_TITLE = TitleRules(
    min_length=40, max_length=100, max_words=14, min_phrases=2, max_phrases=4, readable=True
)


def title_rules(
    min_length: int | None = None, max_length: int | None = None, base: TitleRules = SEARCH_TITLE
) -> TitleRules:
    """``base`` with a profile's own length bounds, kept inside Etsy's limit."""
    high = min(ETSY_MAX_TITLE_LENGTH, max(SHORTEST_TITLE, max_length or base.max_length))
    low = max(SHORTEST_TITLE, min(min_length or base.min_length, high))
    return TitleRules(low, high, base.max_words, base.min_phrases, base.max_phrases, base.readable)


# Opinion words: Etsy asks for these to move to the description.
SUBJECTIVE = (
    "cute", "perfect", "beautiful", "unique", "amazing", "awesome", "adorable", "lovely",
    "gorgeous", "stunning", "premium", "high quality", "trendy", "must have", "stylish",
)
# Price, sale and shipping: Etsy badges these itself.
COMMERCE = (
    "sale", "on sale", "discount", "% off", "percent off", "free shipping", "fast shipping",
    "shipping", "ships", "cheap", "deal", "clearance", "bogo", "coupon", "price", "bestseller",
    "best seller",
)

_STOP = {"a", "an", "and", "for", "in", "of", "on", "or", "the", "to", "with", "at", "by", "from"}
_TYPES = {"shirt", "tshirt", "tee", "sweatshirt", "hoodie", "crewneck", "tank", "pullover"}
#: The kinds of search a tag can serve; a good set covers several.
INTENTS = ("recipient", "occasion", "profession", "season", "style", "humor", "product", "subject")
MIN_INTENTS = 5
MIN_MULTI_WORD_TAGS = 9

OPENING_MIN_SENTENCES = 2
OPENING_MAX_SENTENCES = 3
OPENING_MAX_LENGTH = 500
#: Attributes the design itself can support (the category decides which exist).
ATTRIBUTE_KEYS = ("occasion", "holiday", "style", "primary_color", "theme", "recipient")
MAX_ATTRIBUTE_LENGTH = 40


def words(text: str) -> list[str]:
    """Lowercase words, with every spelling of "t-shirt" as one word."""
    text = re.sub(r"\bt[\s-]?shirts?\b", "tshirt", text.lower())
    return re.findall(r"[a-z0-9]+", text.replace("'", "").replace("’", ""))


def stem(word: str) -> str:
    """Enough to see "nurses" and "nurse" as one word."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def keywords(text: str) -> list[str]:
    return [stem(w) for w in words(text) if w not in _STOP]


def phrases(title: str) -> list[str]:
    return [p.strip() for p in title.split(",") if p.strip()]


def _has(text: str, term: str) -> bool:
    if term.startswith("%"):
        return term in text.lower()
    return re.search(rf"\b{re.escape(term)}\b", text, re.IGNORECASE) is not None


def title_errors(title: str, rules: TitleRules, *, names_type: bool = True) -> list[str]:
    """What keeps ``title`` from following ``rules`` (length is checked by the caller)."""
    errors: list[str] = []
    parts = phrases(title)
    if rules.min_phrases and len(parts) < rules.min_phrases:
        errors.append(
            f"write the title as {rules.min_phrases}-{rules.max_phrases} comma-separated phrases "
            f"(yours has {len(parts)})"
        )
    if rules.max_phrases and len(parts) > rules.max_phrases:
        errors.append(
            f"the title has {len(parts)} phrases; keep {rules.min_phrases}-{rules.max_phrases} and "
            "move the rest to the tags"
        )
    count = len(title.split())
    if rules.max_words and count > rules.max_words:
        errors.append(
            f"the title has {count} words; Etsy asks for fewer than {rules.max_words + 1}. "
            "Move the extra keywords to the tags"
        )
    if not rules.readable:
        return errors

    seen: dict[str, int] = {}
    for w in keywords(title):
        seen[w] = seen.get(w, 0) + 1
    repeated = sorted(w for w, n in seen.items() if n > 1)
    if repeated:
        errors.append(
            "the title repeats " + ", ".join(f"'{w}'" for w in repeated)
            + ": use each word once and put the other phrasing in a tag"
        )
    types = [w for w in words(title) if w in _TYPES]
    if names_type and len(types) > 1 and len(set(types)) > 1:
        errors.append(
            f"the title names the product type {len(types)} times ({', '.join(types)}): name it "
            "once, in the first phrase; other garment words belong in the tags"
        )
    for term in SUBJECTIVE:
        if _has(title, term):
            errors.append(
                f"remove '{term}' from the title: opinion words belong in the description"
            )
    for term in COMMERCE:
        if _has(title, term):
            errors.append(
                f"remove '{term}' from the title: nothing about price, sales or shipping"
            )
    return errors


def tag_errors(title: str, tags: list[str], intents: list[str] | None = None) -> list[str]:
    """Each tag is its own search phrase: none repeats the title or another tag."""
    errors: list[str] = []
    in_title = set(keywords(title))
    repeats = [t for t in tags if keywords(t) and set(keywords(t)) <= in_title]
    if repeats:
        errors.append(
            f"these tags only repeat words already in the title: {repeats}. The title already "
            "matches those searches; use each tag for a phrase the title does not cover"
        )
    by_words: dict[frozenset[str], str] = {}
    for tag in tags:
        key = frozenset(keywords(tag))
        if not key:
            continue
        if key in by_words and by_words[key].strip().lower() != tag.strip().lower():
            errors.append(f"the tags {by_words[key]!r} and {tag!r} are the same search; keep one")
        by_words.setdefault(key, tag)
    multi = sum(1 for t in tags if len(t.split()) >= 2)
    if len(tags) >= MIN_MULTI_WORD_TAGS and multi < MIN_MULTI_WORD_TAGS:
        errors.append(
            f"only {multi} tags are phrases; make at least {MIN_MULTI_WORD_TAGS} of them two or "
            "more words (buyers type phrases, and one phrase matches more searches than one word)"
        )
    if intents is not None:
        kinds = {i for i in intents if i in INTENTS}
        if len(kinds) < MIN_INTENTS:
            errors.append(
                f"the tags cover only {len(kinds)} kinds of search ({', '.join(sorted(kinds)) or 'none'}); "
                f"cover at least {MIN_INTENTS} of: {', '.join(INTENTS)}"
            )
    return errors


def select_tags(
    title: str, candidates: list[str], intents: list[str], count: int = 13, max_length: int = 20
) -> tuple[list[str], list[str]]:
    """The first ``count`` candidate tags that are usable, with their intents.

    The model is asked for a few more tags than Etsy takes, best first. One that
    is a character too long, only repeats the title, or is another tag's words
    reordered is passed over rather than costing the whole listing a retry.
    """
    in_title = set(keywords(title))
    kept: list[str] = []
    kept_intents: list[str] = []
    seen: set[frozenset[str]] = set()
    for i, raw in enumerate(candidates):
        tag = " ".join(raw.split())
        key = frozenset(keywords(tag))
        if not tag or len(tag) > max_length or "," in tag:
            continue
        if key and (key <= in_title or key in seen):
            continue
        seen.add(key)
        kept.append(tag)
        kept_intents.append(intents[i] if i < len(intents) else "")
        if len(kept) == count:
            break
    return kept, kept_intents


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def opening_errors(opening: str, title: str) -> list[str]:
    """The design-specific sentences that go above the shop's own description."""
    text = opening.strip()
    if not text:
        return ["write the opening: two or three sentences about what the design shows, who it is for and the occasion"]
    errors: list[str] = []
    n = len(sentences(text))
    if not OPENING_MIN_SENTENCES <= n <= OPENING_MAX_SENTENCES:
        errors.append(
            f"the opening has {n} sentence(s); write {OPENING_MIN_SENTENCES} or {OPENING_MAX_SENTENCES}"
        )
    if len(text) > OPENING_MAX_LENGTH:
        errors.append(f"the opening is {len(text)} characters; keep it under {OPENING_MAX_LENGTH}")
    if "\n" in text:
        errors.append("the opening is one paragraph: no line breaks or lists")
    if title.strip() and title.strip().lower() in text.lower():
        errors.append("the opening copies the title; write sentences a person would say instead")
    for term in COMMERCE:
        if term not in ("price", "deal", "ships", "shipping") and _has(text, term):
            errors.append(f"remove '{term}' from the opening: the shop's own text covers sales and shipping")
    return errors


def attribute_errors(attributes: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for key, value in attributes.items():
        if key not in ATTRIBUTE_KEYS:
            errors.append(f"unknown attribute '{key}'")
        elif len(value) > MAX_ATTRIBUTE_LENGTH:
            errors.append(f"the {key} attribute is a short value, not a sentence: {value!r}")
    return errors


def clean_attributes(raw: object) -> dict[str, str]:
    """The filled attributes only, as short strings."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        text = str(value or "").strip()
        if text and text.lower() not in ("none", "n/a", "(none)", "null"):
            out[str(key)] = text
    return out
