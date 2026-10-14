"""The search-readiness checklist on each review card.

A check of one listing against rules the app already has and Etsy's own published
seller guidance (docs/etsy-growth-research.md, S1-S4). It is computed from the
listing's own title, tags, description and item options, its profile's settings
and the account's trademark list: nothing is read from Etsy and no model is
called, so it can be worked out again on every keystroke. It never blocks
approval or a draft; only the existing rules do that (content.validate_listing,
the compliance scanner).

Every check reuses an existing rule; none is written a second time here. Each
names its source in a comment. A check with no source in the code or in Etsy's
guidance is not added.

``fail`` means an Etsy limit or a rule the app already enforces is not met (so
approval or the draft is already stopped by that rule); ``warn`` means Etsy's
guidance suggests otherwise, and the seller decides.

Wording: a "search-readiness checklist", never a score, a rank or a promise.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.compliance.scanner import scan
from app.compliance.trademarks import Blocklist
from app.db.models import ComplianceSeverity
from app.pipeline import creativity, search_rules
from app.pipeline.content import (
    REQUIRED_TAG_COUNT,
    MAX_TAG_LENGTH,
    TAG_COMMA,
    TAG_COUNT,
    TAG_DUPLICATE,
    TAG_EMPTY,
    TAG_TOO_LONG,
    tag_problems,
    title_length_errors,
)
from app.pipeline.search_rules import TitleRules

#: Raised whenever a check is added, removed or judged differently.
CHECKLIST_VERSION = 1

HEADING = "Search-readiness checklist"
NOTE = "A check against Etsy's published guidance. It does not predict how Etsy ranks your listing."

PASS, WARN, FAIL = "pass", "warn", "fail"


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    status: str
    #: One sentence, shown to the seller.
    reason: str
    #: Where the rule comes from: a rule in the code, or Etsy's guidance cited in the repo.
    source: str


@dataclass(frozen=True)
class Checklist:
    checks: list[Check]
    version: int = CHECKLIST_VERSION

    @property
    def passed(self) -> int:
        return sum(1 for c in self.checks if c.status == PASS)

    @property
    def summary(self) -> str:
        return f"{self.passed} of {len(self.checks)} checks pass"


@dataclass(frozen=True)
class OptionState:
    """One Occasion, Holiday or shop Section the listing can set (pipeline/item_options.py)."""

    #: "Occasion", "Holiday", "Section in Frost Tees".
    name: str
    values: list[str] = field(default_factory=list)
    #: The seller chose on the card, possibly "none".
    decided: bool = False


def _quote(items: Sequence[str]) -> str:
    return ", ".join(f"“{i}”" for i in items)


def _title_length(title: str, rules: TitleRules) -> Check:
    # Source: content.title_length_errors, the length validate_listing enforces,
    # with the profile's bounds (content.bounds_for); Etsy allows 140 characters.
    source = "pipeline/content.py::title_length_errors, bounds_for"
    n = len(title)
    if not title_length_errors(title, rules):
        reason = f"{n} characters, within this profile's {rules.min_length}–{rules.max_length}."
        return Check("title_length", "Title length", PASS, reason, source)
    if not title.strip():
        reason = "The title is empty."
    elif n < rules.min_length:
        reason = f"{n} characters; this profile asks for at least {rules.min_length}."
    else:
        reason = f"{n} characters; the most is {rules.max_length}."
    return Check("title_length", "Title length", FAIL, reason, source)


def _title_repeats(title: str, prefix: str | None) -> Check:
    # Source: search_rules.repeated_words (the check title_errors makes when the
    # app writes); Etsy, "New Guidance for Listing Titles" (S2): don't repeat words.
    source = "pipeline/search_rules.py::repeated_words; Etsy Seller Handbook S2"
    label = "No repeated words in the title"
    if not title.strip():
        return Check("title_repeats", label, WARN, "There is no title yet.", source)
    repeated = search_rules.repeated_words(title, prefix)
    if repeated:
        reason = f"Used more than once: {_quote(repeated)}. Etsy suggests using each word once."
        return Check("title_repeats", label, WARN, reason, source)
    return Check("title_repeats", label, PASS, "Each word appears once, leaving the profile's prefix aside.", source)


def _tag_count(tags: list[str], kinds: set[str]) -> Check:
    # Source: content.tag_problems (REQUIRED_TAG_COUNT); Etsy, "Keywords 101" (S3): use all 13.
    source = "pipeline/content.py::tag_problems; Etsy Seller Handbook S3"
    label = f"All {REQUIRED_TAG_COUNT} tags used"
    reason = f"{len(tags)} of {REQUIRED_TAG_COUNT} tags."
    if TAG_COUNT in kinds:
        if len(tags) > REQUIRED_TAG_COUNT:
            reason = f"{len(tags)} tags; Etsy takes {REQUIRED_TAG_COUNT}."
        return Check("tag_count", label, FAIL, reason, source)
    return Check("tag_count", label, PASS, reason, source)


def _tag_format(problems: list[tuple[str, str, str]]) -> Check:
    # Source: content.tag_problems (MAX_TAG_LENGTH, no comma, none empty); Etsy (S3): 20 characters each.
    source = "pipeline/content.py::tag_problems; Etsy Seller Handbook S3"
    label = f"Tags are {MAX_TAG_LENGTH} characters or fewer, without commas"
    long = [tag for kind, tag, _ in problems if kind == TAG_TOO_LONG]
    comma = [tag for kind, tag, _ in problems if kind == TAG_COMMA]
    empty = sum(1 for kind, _, _ in problems if kind == TAG_EMPTY)
    parts = []
    if long:
        parts.append(f"over {MAX_TAG_LENGTH} characters: {_quote(long)}")
    if comma:
        parts.append(f"with a comma: {_quote(comma)}")
    if empty:
        parts.append(f"{empty} empty")
    if parts:
        reason = "Tags " + "; ".join(parts) + "."
        return Check("tag_format", label, FAIL, reason[0].upper() + reason[1:], source)
    return Check("tag_format", label, PASS, f"Every tag is {MAX_TAG_LENGTH} characters or fewer, with no commas.", source)


def _tag_duplicates(tags: list[str], problems: list[tuple[str, str, str]]) -> Check:
    # Source: content.tag_problems (the same tag twice, which validate_listing refuses)
    # and search_rules.same_search_tags (the root-word check select_tags applies);
    # Etsy (S3): don't repeat tags, and Etsy matches root words, so plural and
    # singular are one search.
    source = "pipeline/content.py::tag_problems, search_rules.py::same_search_tags; Etsy Seller Handbook S3"
    label = "No duplicate tags"
    twice = list(dict.fromkeys(tag.strip() for kind, tag, _ in problems if kind == TAG_DUPLICATE))
    if twice:
        return Check("tag_duplicates", label, FAIL, f"Used twice: {_quote(twice)}.", source)
    same = search_rules.same_search_tags([t for t in tags if t.strip()])
    if same:
        first, again = same[0]
        more = f" (and {len(same) - 1} more)" if len(same) > 1 else ""
        reason = f"“{first}” and “{again}” are the same search to Etsy{more}; one could be a different phrase."
        return Check("tag_duplicates", label, WARN, reason, source)
    return Check("tag_duplicates", label, PASS, "Every tag is a different search.", source)


def _description_opening(title: str, description: str, prefix: str | None) -> Check:
    # Source: Etsy, "Keywords 101" and "Anatomy of a Well-Crafted Listing" (S3, S4):
    # keywords naturally in the first few sentences of the description. "The first
    # few" is the opening the app writes (search_rules.OPENING_MAX_SENTENCES), and
    # words are compared as search_rules.keywords compares them (root words, no
    # stop words, the profile's prefix left out).
    source = "Etsy Seller Handbook S3, S4; search_rules.py::sentences, keywords, OPENING_MAX_SENTENCES"
    label = "The title's key words early in the description"
    if not description.strip():
        # validate_listing refuses an empty description.
        return Check("description_opening", label, FAIL, "The description is empty.", source)
    wanted = list(dict.fromkeys(search_rules.keywords(search_rules.without_prefix(title, prefix))))
    if not wanted:
        return Check("description_opening", label, WARN, "There is no title yet to compare with.", source)
    first = " ".join(search_rules.sentences(description)[: search_rules.OPENING_MAX_SENTENCES])
    found = [w for w in wanted if w in set(search_rules.keywords(first))]
    if found:
        return Check("description_opening", label, PASS, f"The first sentences mention {_quote(found[:4])}.", source)
    reason = (
        f"The first {search_rules.OPENING_MAX_SENTENCES} sentences mention none of the title's words "
        f"({_quote(wanted[:4])}); Etsy suggests naming the item there."
    )
    return Check("description_opening", label, WARN, reason, source)


def _item_options(options: Sequence[OptionState]) -> Check | None:
    # Source: pipeline/item_options.py (Occasion and Holiday from Etsy's own list
    # for the category; the section chosen per shop, else the suggested existing
    # one); Etsy, "How Etsy Search Works" and S4: add every attribute that applies.
    source = "pipeline/item_options.py::chosen, suggest_section; Etsy Seller Handbook S1, S4"
    if not options:
        return None
    label = "Occasion, Holiday and Section"
    unset = [o.name for o in options if not o.values and not o.decided]
    if unset:
        reason = f"Not set: {', '.join(unset)}. Leave any that does not apply to this design empty."
        return Check("item_options", label, WARN, reason, source)
    said = [f"{o.name}: {', '.join(o.values)}" if o.values else f"{o.name}: none, as you chose" for o in options]
    return Check("item_options", label, PASS, "; ".join(said) + ".", source)


def _trademarks(
    title: str, tags: list[str], description: str, blocklist: Blocklist, characters: list[str], filter_on: bool
) -> Check:
    # Source: compliance/scanner.py::scan with the account's own list
    # (trademarks.tenant_blocklist): the same scan that stops a draft.
    source = "compliance/scanner.py::scan, trademarks.py::tenant_blocklist"
    label = "No trademark findings"
    findings = scan(title, tags, description, blocklist, characters=characters)
    blocking = [f for f in findings if f.severity is ComplianceSeverity.blocking]
    if blocking:
        return Check("trademarks", label, FAIL, " ".join(f.detail for f in blocking), source)
    if findings:
        return Check("trademarks", label, WARN, " ".join(f.detail for f in findings), source)
    if not filter_on:
        reason = "Your trademark filter is off, so brand and character names are not checked."
        return Check("trademarks", label, WARN, reason, source)
    return Check("trademarks", label, PASS, "No brand or character names in the title, tags or description.", source)


def _production_partner(profile: Any) -> Check | None:
    # Source: pipeline/creativity.py (Etsy's Creativity Standards: a listing made
    # by a production partner names that partner). Only for profiles whose items
    # look made by a partner.
    source = "pipeline/creativity.py::partner_expected, production_note"
    if profile is None or not creativity.partner_expected(profile):
        return None
    label = "Production partner named"
    if creativity.production_note(profile):
        reason = (
            "This profile's reference listing names no production partner; if one makes the item, "
            "Etsy's Creativity Standards ask the listing to say so."
        )
        return Check("production_partner", label, WARN, reason, source)
    return Check("production_partner", label, PASS, "This profile's reference listing names a production partner.", source)


def build(
    title: str,
    tags: list[str],
    description: str,
    *,
    title_rules: TitleRules,
    prefix: str | None = None,
    blocklist: Blocklist,
    filter_on: bool,
    characters: Sequence[str] = (),
    options: Sequence[OptionState] = (),
    profile: Any = None,
) -> Checklist:
    """The checklist for one listing. Pure: nothing is read or written, nothing called."""
    tags = list(tags)
    problems = tag_problems(tags)
    kinds = {kind for kind, _, _ in problems}
    checks = [
        _title_length(title, title_rules),
        _title_repeats(title, prefix),
        _tag_count(tags, kinds),
        _tag_format(problems),
        _tag_duplicates(tags, problems),
        _description_opening(title, description, prefix),
        _item_options(options),
        _trademarks(title, tags, description, blocklist, list(characters), filter_on),
        _production_partner(profile),
    ]
    return Checklist([c for c in checks if c is not None])
