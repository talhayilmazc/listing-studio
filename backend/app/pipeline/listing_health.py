"""How a seller's own active listing does against the app's rules: pure, from what the shop sync read.

Nothing here is new law. The title and tag checks call the rules the app writes by and the free tools
copy (``etsy_text``, ``search_rules``, ``compliance/trademarks``), and ``free_tool_cases.json`` proves the
browser's checkers give these same verdicts. Each check says whose rule it is: ``etsy`` (quoted with its
page in ``frontend/lib/listingHealth.ts``) or ``advice`` (ours, and labelled so).

The score is the share of checks met, among those that could be judged. What could not be judged (the
attributes before they are read) is left out, not counted as met or missed. It is a tidy-up list, and it
says nothing about how Etsy ranks a listing: Etsy publishes no such figure.

A listing written in the profile's long keyword style is not told off for being long keyword style: the
short-title advice applies only where the seller has not chosen otherwise (``content.uses_search_style``
makes the same decision for what the app writes).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.compliance import trademarks
from app.pipeline import etsy_text, search_rules
from app.pipeline.reference import decode_etsy_text

PASS, FAIL, WARN, NOTE = "pass", "fail", "warn", "note"
ETSY, ADVICE = "etsy", "advice"

#: Our advice, not Etsy's numbers.
MIN_PHOTOS = 5
MIN_DESCRIPTION = 160

_PERSONALIZED = re.compile(r"\b(personali[sz]ed?|personali[sz]ation|custom name)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Check:
    id: str
    kind: str
    verdict: str
    detail: str


@dataclass(frozen=True)
class Health:
    score: int
    checks: list[Check] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)

    @property
    def to_fix(self) -> int:
        return sum(1 for c in self.checks if c.verdict in (FAIL, WARN))


def _quoted(items: list[str]) -> str:
    return ", ".join(f'"{item}"' for item in items)


def _longest(items: list[str]) -> list[str]:
    """Without a word a longer finding already says: "free shipping", not "shipping" as well."""
    return [i for i in items if not any(o != i and i in o for o in items)]


def _plural(n: int, one: str) -> str:
    return f"{n} {one}{'' if n == 1 else 's'}"


def title_checks(title: str, *, short_style: bool = True) -> list[Check]:
    """The title rules, in the order the free title checker shows them (``lib/freeTools/titleCheck.ts``)."""
    text = title.strip()
    size = len(title)
    bad = [ch for ch in title if not etsy_text._title_char_ok(ch)]
    twice = [ch for ch in etsy_text.ONCE if title.count(ch) > 1]
    out = [
        Check("title_length", ETSY, FAIL if size > etsy_text.MAX_TITLE else PASS, f"{size} characters."),
        Check("title_characters", ETSY, FAIL if bad else PASS,
              f"Not allowed: {' '.join(dict.fromkeys(bad))}." if bad else "Every character is allowed."),
        Check("title_once", ETSY, FAIL if twice else PASS,
              f"Uses {' '.join(twice)} more than once." if twice else "None of them is used twice."),
    ]
    if not short_style:
        return out
    count = search_rules.word_count(title)
    out.append(Check("title_words", ETSY, WARN if count > search_rules.MAX_TITLE_WORDS else PASS, f"{_plural(count, 'word')}."))
    parts = search_rules.phrases(title)
    garments = [w for w in search_rules.words(title) if w in search_rules._TYPES]
    first = search_rules.words(parts[0]) if parts else []
    if any(w in search_rules._TYPES for w in first):
        item = Check("item_first", ADVICE, PASS, "The first part names the garment.")
    elif garments:
        item = Check("item_first", ADVICE, WARN, f'"{garments[0]}" comes after the first comma.')
    else:
        item = Check("item_first", ADVICE, NOTE, "We only recognise garment words here. If this is another item, check it is named first.")
    out.append(item)
    repeated = search_rules.repeated_words(title)
    out.append(Check("repeats", ADVICE, WARN if repeated else PASS, f"Repeats {_quoted(repeated)}." if repeated else "Each word is used once."))
    subjective = [t for t in search_rules.SUBJECTIVE if search_rules._has(title, t)]
    out.append(Check("subjective", ADVICE, WARN if subjective else PASS, f"Uses {_quoted(_longest(subjective))}." if subjective else "None found."))
    commerce = [t for t in search_rules.COMMERCE if search_rules._has(title, t)]
    out.append(Check("commerce", ADVICE, WARN if commerce else PASS, f"Uses {_quoted(_longest(commerce))}." if commerce else "None found."))
    gift = search_rules.gift_in(title)
    out.append(Check("gift", ADVICE, WARN if gift else PASS, f"Uses {_quoted(_longest(gift))}." if gift else "None found."))
    if len(parts) > (search_rules.SEARCH_TITLE.max_phrases or 0):
        out.append(Check("shorter", ADVICE, WARN, f"{len(parts)} comma-separated parts."))
    elif len(garments) > 1:
        out.append(Check("shorter", ADVICE, WARN, f"Names the garment {len(garments)} times ({', '.join(garments)})."))
    else:
        out.append(Check("shorter", ADVICE, PASS, f"{_plural(len(parts), 'part')}."))
    if not text:
        return [c for c in out if c.id == "title_length"]
    return out


def tag_checks(tags: list[str], title: str = "") -> list[Check]:
    """The tag rules, in the order the free tag checker shows them (``lib/freeTools/tagCheck.ts``)."""
    long = [t for t in tags if len(t) > etsy_text.MAX_TAG]
    bad = [t for t in tags if any(ch != "," and not etsy_text._tag_char_ok(ch) for ch in t)]
    starts = etsy_text.tags_starting_badly(tags)
    lower = [t.lower() for t in tags]
    exact = list(dict.fromkeys(t for i, t in enumerate(lower) if lower.index(t) != i))
    same = search_rules.same_search_tags(tags)
    in_title = set(search_rules.keywords(title))
    only_title = [t for t in tags if search_rules.keywords(t) and set(search_rules.keywords(t)) <= in_title]
    multi = sum(1 for t in tags if len(t.split()) >= 2)
    wanted = search_rules.MIN_MULTI_WORD_TAGS
    return [
        Check("tag_count", ETSY, FAIL if len(tags) > etsy_text.MAX_TAGS else PASS, f"{len(tags)} tags."),
        Check("tag_length", ETSY, FAIL if long else PASS, f"Over {etsy_text.MAX_TAG}: {_quoted(long)}." if long else "All of them fit."),
        Check("tag_characters", ETSY, FAIL if bad else PASS, f"Not allowed in: {_quoted(bad)}." if bad else "Every character is allowed."),
        Check("tag_start", ETSY, WARN if starts else PASS, f"Start with ' or -: {_quoted(starts)}." if starts else "None of them does."),
        Check("tag_duplicates", ADVICE, WARN if exact or same else PASS,
              " ".join(filter(None, [f"Twice: {_quoted(exact)}." if exact else "",
                                     ("Same words: " + ", ".join(f'"{a}" and "{b}"' for a, b in same) + ".") if same else ""])) or "None found."),
        Check("tag_title_words", ADVICE, WARN if only_title else PASS,
              f"Only the title's words: {_quoted(only_title)}." if only_title else "Each tag adds a word of its own."),
        Check("tag_phrases", ADVICE, WARN if len(tags) >= wanted and multi < wanted else PASS, f"{multi} of {_plural(len(tags), 'tag')}."),
        Check("tag_all", ADVICE, WARN if len(tags) < etsy_text.MAX_TAGS else PASS,
              f"Room for {etsy_text.MAX_TAGS - len(tags)} more." if len(tags) < etsy_text.MAX_TAGS else "All 13 are used."),
    ]


def attribute_check(properties: list[dict[str, Any]] | None, reference: list[dict[str, Any]] | None) -> Check | None:
    """``properties`` is None until they are read: then it cannot be judged, and is left out.

    ``reference``: the matching profile's reference listing's attributes. One it has that this listing
    lacks is a fix we can offer; with no profile to compare, having any at all is what can be said."""
    if properties is None:
        return None
    filled = [p for p in properties if p.get("value_ids") or p.get("values")]
    have = {p.get("property_id") for p in filled}
    missing = [str(p.get("property_name") or p.get("property_id")) for p in reference or []
               if (p.get("value_ids") or p.get("values")) and p.get("property_id") not in have]
    if missing:
        return Check("attributes", ETSY, WARN, f"Your profile's reference listing also has: {', '.join(missing)}.")
    if not filled:
        return Check("attributes", ETSY, WARN, "No attributes are filled.")
    return Check("attributes", ETSY, PASS, f"{_plural(len(filled), 'attribute')} filled.")


def _has_personalization(listing: dict[str, Any]) -> bool | None:
    """None when the sync did not read it (a row from before it asked)."""
    if "personalization" not in listing:
        return None
    value = listing.get("personalization")
    return bool(value and (not isinstance(value, dict) or value.get("personalization_questions") or value.get("questions") or value.get("question_text")))


def score(
    listing: dict[str, Any],
    *,
    properties: list[dict[str, Any]] | None = None,
    reference_attributes: list[dict[str, Any]] | None = None,
    short_style: bool = True,
) -> Health:
    """Every check on one listing, as Etsy's API gave it."""
    title = decode_etsy_text(listing.get("title")) or ""
    tags = [decode_etsy_text(t) or "" for t in listing.get("tags") or []]
    description = decode_etsy_text(listing.get("description")) or ""
    checks = [*title_checks(title, short_style=short_style), *tag_checks(tags, title)]
    attribute = attribute_check(properties, reference_attributes)
    if attribute is not None:
        checks.append(attribute)
    materials = listing.get("materials") or []
    checks.append(Check("materials", ADVICE, PASS if materials else WARN, f"{_plural(len(materials), 'material')}." if materials else "None listed."))
    images = listing.get("images")
    if isinstance(images, list):
        checks.append(Check("photos", ADVICE, PASS if len(images) >= MIN_PHOTOS else WARN, f"{_plural(len(images), 'photo')}."))
    size = len(description.strip())
    checks.append(Check("description", ADVICE, PASS if size >= MIN_DESCRIPTION else WARN, f"{size} characters."))
    section = listing.get("shop_section_id")
    checks.append(Check("section", ADVICE, PASS if section else WARN, "In a section." if section else "In no section."))
    if _PERSONALIZED.search(title):
        has = _has_personalization(listing)
        if has is not None:
            checks.append(Check("personalization", ADVICE, PASS if has else WARN,
                                "It asks the buyer for the personalization." if has else "The title says personalized, and the listing asks the buyer nothing."))
    spotted = trademarks.all_marks()
    brands = list(dict.fromkeys(s.term for text in (title, *tags) for s in spotted.spot(text)))
    checks.append(Check("brand", ADVICE, WARN if brands else PASS,
                        " ".join(trademarks.BRAND_WARNING.format(brand=b) for b in brands) if brands else "None found."))
    judged = [c for c in checks if c.verdict != NOTE]
    met = sum(1 for c in judged if c.verdict == PASS)
    return Health(round(100 * met / len(judged)) if judged else 100, checks, brands)
