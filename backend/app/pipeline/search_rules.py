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
#: "Etsy recommended (short)": at most 15 words (the prefix included) and 140
#: characters, 1-4 phrases (Part C; docs/etsy-growth-research.md). The character
#: bounds are the default a profile may change.
SEARCH_TITLE = TitleRules(
    min_length=40, max_length=140, max_words=15, min_phrases=1, max_phrases=4, readable=True
)


def title_rules(
    min_length: int | None = None, max_length: int | None = None, base: TitleRules = SEARCH_TITLE
) -> TitleRules:
    """``base`` with a profile's own length bounds, kept inside Etsy's limit."""
    high = min(ETSY_MAX_TITLE_LENGTH, max(SHORTEST_TITLE, max_length or base.max_length))
    low = max(SHORTEST_TITLE, min(min_length or base.min_length, high))
    return TitleRules(low, high, base.max_words, base.min_phrases, base.max_phrases, base.readable)


STYLES = ("classic", "search")


def rules_for(profile: object | None) -> TitleRules:
    """The rules a profile's listings are WRITTEN to (its style and its bounds)."""
    if profile is None or getattr(profile, "listing_style", "classic") != "search":
        return LEGACY_TITLE
    return title_rules(getattr(profile, "title_min_length", None), getattr(profile, "title_max_length", None))


def bounds_for(profile: object | None) -> TitleRules:
    """The title length a profile's listings are CHECKED against after a seller
    edits them or before a draft is made. Only the length: the style rules guide
    what the app writes, they do not stop a seller wording a title their way."""
    rules = rules_for(profile)
    return TitleRules(rules.min_length, rules.max_length)


# Opinion words: Etsy asks for these to move to the description.
SUBJECTIVE = (
    "cute", "perfect", "beautiful", "unique", "amazing", "awesome", "adorable", "lovely",
    "gorgeous", "stunning", "premium", "high quality", "trendy", "must have", "stylish",
)
# Gifting and aspirational phrases: Etsy asks for none in the title; they go in
# the tags. A recipient or profession word that defines the item ("Nurse Shirt")
# is not one of these; "for <someone>" and "gift" are.
GIFT = (
    "gift", "gifts", "gift idea", "gift ideas", "present", "presents", "stocking stuffer",
    "stocking stuffers", "for her", "for him", "for mom", "for mum", "for dad", "for women",
    "for men", "for kids", "for girls", "for boys", "for wife", "for husband", "for grandma",
    "for grandpa", "for friends", "for friend", "for sister", "for brother", "for teachers",
    "for nurses", "for them", "for everyone",
)
# Price, sale and shipping: Etsy badges these itself.
COMMERCE = (
    "sale", "on sale", "discount", "% off", "percent off", "free shipping", "fast shipping",
    "shipping", "ships", "cheap", "deal", "clearance", "bogo", "coupon", "price", "bestseller",
    "best seller",
)

# Words that pad a title phrase without describing the design ("Script Heart
# Design", "Castle Rainbow Graphic"). "Leopard Print" is a pattern, not filler.
TITLE_FILLER = ("design", "graphic", "graphics", "print", "prints")
_PATTERNS = {"leopard", "cheetah", "animal", "floral", "zebra", "paw", "tiger", "cow", "snake", "camo", "plaid"}
# Opinion words say nothing a buyer searches by; in a tag they waste the slot.
TAG_OPINION = (*SUBJECTIVE, "best")
_BEST_IS_SUBJECT = re.compile(r"\bbest\s+(friend|friends|dad|mom|man|buds|bud)\b", re.IGNORECASE)

_STOP = {"a", "an", "and", "for", "in", "of", "on", "or", "the", "to", "with", "at", "by", "from"}
_TYPES = {"shirt", "tshirt", "tee", "sweatshirt", "hoodie", "crewneck", "tank", "pullover"}
#: The kinds of search a tag can serve; a good set covers several.
INTENTS = ("recipient", "occasion", "profession", "season", "style", "humor", "product", "subject")
MIN_INTENTS = 5
MIN_MULTI_WORD_TAGS = 9

OPENING_MIN_SENTENCES = 1
OPENING_MAX_SENTENCES = 2
OPENING_MAX_LENGTH = 500


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


def without_prefix(title: str, prefix: str | None) -> str:
    """``title`` after the profile's fixed prefix (which is the seller's, not ours)."""
    prefix = (prefix or "").strip()
    if prefix and title.strip().casefold().startswith(prefix.casefold()):
        return title.strip()[len(prefix):].lstrip(" ,")
    return title


def gift_in(title: str) -> list[str]:
    """The gifting phrases in ``title``."""
    return [term for term in GIFT if _has(title, term)]


def title_errors(
    title: str, rules: TitleRules, *, names_type: bool = True, prefix: str | None = None
) -> list[str]:
    """What keeps ``title`` from following ``rules`` (length is checked by the caller).
    The word count includes ``prefix``; the repeated-word check does not."""
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
            f"the title has {count} words; keep it to {rules.max_words} or fewer. "
            "Move the extra keywords to the tags"
        )
    if not rules.readable:
        return errors

    seen: dict[str, int] = {}
    for w in keywords(without_prefix(title, prefix)):
        seen[w] = seen.get(w, 0) + 1
    repeated = sorted(w for w, n in seen.items() if n > 1)
    if repeated:
        errors.append(
            "the title repeats " + ", ".join(f"'{w}'" for w in repeated)
            + ": use each word once and put the other phrasing in a tag"
        )
    for part in parts:
        last = words(part)[-2:]
        if last and last[-1] in TITLE_FILLER and not (
            last[-1].startswith("print") and len(last) == 2 and last[0] in _PATTERNS
        ):
            errors.append(
                f"the phrase '{part}' ends in the filler word '{part.split()[-1]}': end it on what "
                "the design shows or its style, or drop the word"
            )
    types = [w for w in words(title) if w in _TYPES]
    if names_type and len(types) > 1:
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
    for term in gift_in(title):
        errors.append(
            f"remove '{term}' from the title: gift, occasion and recipient phrases go in the tags"
        )
    return errors


def opinion_in(tag: str) -> str | None:
    """The opinion word in ``tag``, if any ("best friend gift" names a recipient)."""
    for term in TAG_OPINION:
        if _has(tag, term) and not (term == "best" and _BEST_IS_SUBJECT.search(tag)):
            return term
    return None


def restates(tag: str, already: list[str]) -> str | None:
    """The category name or attribute value that ``tag`` only says again, if any.
    Etsy matches on those already, so such a tag adds no new search."""
    key = frozenset(keywords(tag))
    for text in already:
        if key and key == frozenset(keywords(text)):
            return text
    return None


def tag_errors(
    title: str, tags: list[str], intents: list[str] | None = None, already: list[str] | None = None
) -> list[str]:
    """Each tag is its own search phrase: none repeats the title, another tag,
    the category or an attribute value (``already``), and none is an opinion."""
    errors: list[str] = []
    for tag in tags:
        word = opinion_in(tag)
        if word:
            errors.append(f"remove the opinion word '{word}' from the tag {tag!r}: nobody searches by it")
        said = restates(tag, already or [])
        if said:
            errors.append(
                f"the tag {tag!r} only restates the category or attribute '{said}', which Etsy "
                "already matches on: use the tag for a different search"
            )
    # A tag that only repeats the title is not an error by itself: ``select_tags``
    # takes one only when no distinct phrase is left to fill the 13.
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
    title: str,
    candidates: list[str],
    intents: list[str],
    count: int = 13,
    max_length: int = 20,
    *,
    already: list[str] | None = None,
    banned: tuple[str, ...] = (),
) -> tuple[list[str], list[str]]:
    """The first ``count`` candidate tags that are usable, with their intents.

    The model is asked for a few more tags than Etsy takes, best first. One that
    is a character too long, an opinion, the category or an attribute value
    again, or another tag's root words (reordered, plural/singular) is passed
    over rather than costing the whole listing a retry. One made only of the
    title's words is kept back and used only if the distinct ones run out.
    """
    in_title = set(keywords(title))
    kept: list[str] = []
    kept_intents: list[str] = []
    #: Tags made only of title words: used only when no distinct phrase is left.
    spare: list[tuple[str, str]] = []
    seen: set[frozenset[str]] = set()
    for i, raw in enumerate(candidates):
        tag = " ".join(raw.split())
        # Root words: "nurse shirts" and "nurse shirt" are one search to Etsy.
        key = frozenset(keywords(tag))
        if not tag or len(tag) > max_length or "," in tag:
            continue
        if key in seen:
            continue
        if opinion_in(tag) or restates(tag, already or []) or any(_has(tag, term) for term in banned):
            continue
        seen.add(key)
        if key and key <= in_title:
            spare.append((tag, intents[i] if i < len(intents) else ""))
            continue
        kept.append(tag)
        kept_intents.append(intents[i] if i < len(intents) else "")
    for tag, intent in spare[: max(0, count - len(kept))]:
        kept.append(tag)
        kept_intents.append(intent)
    # The first ``count`` usable ones, best first. If those serve too few kinds
    # of search and a spare serves a kind they lack, the spare takes the place of
    # the last tag of the most crowded kind.
    chosen = list(range(min(count, len(kept))))
    for spare in range(count, len(kept)):
        kinds = [kept_intents[i] for i in chosen]
        if len({k for k in kinds if k in INTENTS}) >= MIN_INTENTS:
            break
        if kept_intents[spare] not in INTENTS or kept_intents[spare] in kinds:
            continue
        crowded = max(set(kinds), key=kinds.count)
        if kinds.count(crowded) < 2:
            break
        chosen.remove(max(i for i in chosen if kept_intents[i] == crowded))
        chosen.append(spare)
    return [kept[i] for i in chosen], [kept_intents[i] for i in chosen]


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def opening_errors(opening: str, title: str) -> list[str]:
    """The design-specific sentences that go above the shop's own description."""
    text = opening.strip()
    if not text:
        return ["write the opening: one or two sentences naming the item and what the design shows"]
    errors: list[str] = []
    n = len(sentences(text))
    if not OPENING_MIN_SENTENCES <= n <= OPENING_MAX_SENTENCES:
        errors.append(
            f"the opening has {n} sentence(s); write {OPENING_MIN_SENTENCES} or {OPENING_MAX_SENTENCES}, "
            "naming the item first"
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


def attribute_errors(attributes: dict[str, str], choices: dict[str, list[str]]) -> list[str]:
    """Every attribute value is one of the values Etsy lists for that property."""
    errors: list[str] = []
    for name, value in attributes.items():
        allowed = choices.get(name)
        if allowed is None:
            errors.append(f"this category has no '{name}' attribute")
        elif value not in allowed:
            errors.append(f"'{value}' is not one of Etsy's values for {name}; choose from its list or leave it empty")
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
