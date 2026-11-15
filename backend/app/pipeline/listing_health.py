"""Listing health, version 2: what sells is left alone, real problems come first (docs/listing-health.md).

Every listing goes into exactly one bucket (:func:`classify`):

* **Fix now**: real problems only. A brand or character name, something Etsy rejects (a title or tag past its
  limits or characters), no main photo, a required attribute missing, an inactive, expired or sold-out listing that
  used to sell, stray characters. Shown whether or not the listing sells.
* **Selling**: a sale within the selling window (``LISTING_HEALTH_SELLING_DAYS``, 60). Nothing about its title or
  tags is ever suggested: only a Fix now problem moves it.
* **Too new to judge**: younger than ``LISTING_HEALTH_NEW_DAYS`` (30) and no sale yet. Quiet, not a warning.
* **Worth improving**: no sale in the window **and** at least one concrete issue with a one-click fix.
* **Nothing to fix**: no sale, and nothing we can fix with one click.
* **Not judged yet**: the shop's sales have not been read, so nobody can say what sells. No title or tag change is
  suggested until they are.

There is no score and no percentage: a share of style checks read as a grade and could not tell a listing that
sells from one that does not (a seller's best seller scored 50%).

The title and tag checks are the rules the app writes by and the free tools copy (``etsy_text``, ``search_rules``,
``compliance/trademarks``); ``free_tool_cases.json`` proves the browser's checkers give the same verdicts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.compliance import trademarks
from app.pipeline import ai_text, etsy_text, search_rules, season_calendar
from app.pipeline.reference import decode_etsy_text

PASS, FAIL, WARN, NOTE = "pass", "fail", "warn", "note"
ETSY, ADVICE = "etsy", "advice"

FIX_NOW, IMPROVE, SELLING, TOO_NEW, NOTHING, NOT_JUDGED = (
    "fix_now", "worth_improving", "selling", "too_new", "nothing_to_fix", "not_judged")
#: The buckets in the order the page lists them: Fix now first.
BUCKETS = (FIX_NOW, IMPROVE, SELLING, TOO_NEW, NOTHING, NOT_JUDGED)

#: Fixes a problem can offer. The first five are the preview kinds (pipeline/listing_fixes.py); "edit" is a link
#: to the listing in Shop Manager (nothing we can change for the seller); "season_planner" opens the planner.
EDIT, SEASON_PLANNER = "edit", "season_planner"

SELLING_NOTE = "Changing the title or tags of a selling listing can lower its search position."
#: Said on a listing whose every brand name the seller kept ("Keep it, I know the risk").
KEPT_NOTE = "Brand name kept by you."
#: The action that keeps a brand name: only the brand problem offers it, never another Fix now kind.
KEEP_BRAND = "keep_brand"
NOT_JUDGED_NOTE = ("Your sales have not been read yet, so we cannot tell which listings sell. Until they are, "
                   "we suggest no title or tag changes. Reports reads them.")
#: States a listing is in when buyers cannot buy it, and it may have sold before.
STOPPED = ("inactive", "expired", "sold_out")


@dataclass(frozen=True)
class Check:
    id: str
    kind: str
    verdict: str
    detail: str


@dataclass(frozen=True)
class Windows:
    """How far back a sale makes a listing "selling", and how young a listing is too new to judge."""
    selling_days: int = 60
    new_days: int = 30


@dataclass(frozen=True)
class Problem:
    id: str
    kind: str
    #: FIX_NOW or IMPROVE.
    level: str
    sentence: str
    fixes: tuple[str, ...] = ()


@dataclass
class Verdict:
    bucket: str
    problems: list[Problem] = field(default_factory=list)
    #: Quiet pointers that move no listing into a bucket (a seasonal listing and the season planner).
    suggestions: list[Problem] = field(default_factory=list)
    brands: list[str] = field(default_factory=list)
    #: Brand names on the listing the seller chose to keep: no longer a Fix now problem.
    kept_brands: list[str] = field(default_factory=list)
    recent_units: int = 0
    past_units: int = 0
    favourites: int = 0
    age_days: int | None = None
    note: str | None = None

    @property
    def fixes(self) -> set[str]:
        """Every fix this listing's problems offer: the only ones a preview may make for it."""
        return {f for p in self.problems for f in p.fixes}

    def sort_key(self) -> tuple:
        """Fix now first; then the listings with the most past sales, then favourites."""
        first = self.recent_units if self.bucket == SELLING else self.past_units
        return (BUCKETS.index(self.bucket), -first, -self.favourites)


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




# --- version 2: the buckets ----------------------------------------------------------------------------------

_HOLIDAY_WORDS = {
    "christmas": "christmas", "xmas": "christmas", "halloween": "halloween", "thanksgiving": "thanksgiving",
    "valentine": "valentines", "valentines": "valentines", "valentine's": "valentines", "easter": "easter",
    "mothers day": "mothers_day", "mother's day": "mothers_day", "fathers day": "fathers_day", "father's day": "fathers_day",
    "4th of july": "july_4", "fourth of july": "july_4", "july 4th": "july_4", "new year": "new_year", "new years": "new_year",
    "new year's": "new_year", "st patricks": "st_patricks", "st patrick's": "st_patricks", "saint patrick": "st_patricks",
    "back to school": "back_to_school",
}
_HOLIDAY = re.compile(r"(?<![a-z])(" + "|".join(sorted((re.escape(w) for w in _HOLIDAY_WORDS), key=len, reverse=True)) + r")(?![a-z])")
_YEAR = re.compile(r"(?<!\d)(20\d\d)(?!\d)")


def holidays_in(text: str) -> list[str]:
    """The season planner's holidays a piece of text names, in order, once each."""
    return list(dict.fromkeys(_HOLIDAY_WORDS[m.group(1)] for m in _HOLIDAY.finditer(text.lower().replace("’", "'"))))


def dated_tags(tags: list[str], today: datetime) -> list[str]:
    """Tags naming a holiday with a year that has passed ("christmas 2024"): out of date whatever the season."""
    out = []
    for tag in tags:
        keys = holidays_in(tag)
        years = [int(y) for y in _YEAR.findall(tag)]
        if keys and years and all(season_calendar._DATES[keys[0]](y) < today.date() for y in years):
            out.append(tag)
    return out


def _photo_missing(listing: dict[str, Any]) -> bool | None:
    """None when the sync did not read the photos."""
    images = listing.get("images")
    if not isinstance(images, list):
        return None
    if not images:
        return True
    first = images[0] if isinstance(images[0], dict) else {}
    return not any(str(v).startswith("http") for k, v in first.items() if k.startswith("url_"))


def _age_days(listing: dict[str, Any], now: datetime) -> int | None:
    made = listing.get("original_creation_timestamp") or listing.get("created_timestamp") or listing.get("creation_timestamp")
    if not made:
        return None
    return max(0, int((now - datetime.fromtimestamp(int(made), tz=timezone.utc)).total_seconds() // 86400))


def _filled(properties: list[dict[str, Any]]) -> set[int]:
    return {int(p["property_id"]) for p in properties if p.get("property_id") is not None and (p.get("value_ids") or p.get("values"))}


def brands_in(listing: dict[str, Any]) -> list[str]:
    """The listed brand names in a listing's title and tags, in any form, as our list writes them, once each."""
    spotted = trademarks.all_marks()
    texts = [decode_etsy_text(listing.get("title")) or "", *(decode_etsy_text(t) or "" for t in listing.get("tags") or [])]
    return list(dict.fromkeys(s.term for text in texts for s in spotted.spot(text)))


def brand_sentence(brands: list[str]) -> str:
    """Plain and factual, no legal advice beyond it."""
    if len(brands) == 1:
        return f"Uses the name '{brands[0]}', a trademark of another company. Etsy can remove listings like this and may restrict the shop."
    names = ", ".join(f"'{b}'" for b in brands[:-1]) + f" and '{brands[-1]}'"
    return f"Uses the names {names}, trademarks of other companies. Etsy can remove listings like this and may restrict the shop."


def _sold(n: int) -> str:
    return f"{n} sale{'' if n == 1 else 's'}"


def fix_now_problems(
    listing: dict[str, Any], *, properties: list[dict[str, Any]] | None, required: list[dict[str, Any]] | None,
    reference_attributes: list[dict[str, Any]] | None, past_units: int, kept_brands: set[str] | frozenset[str] = frozenset(),
) -> tuple[list[Problem], list[str]]:
    """The real problems, and the brand names found. Each is either Etsy's (a limit it states or rejects) or a risk.
    ``kept_brands`` (folded): names the seller kept on this listing; only a name not among them is a problem."""
    title = decode_etsy_text(listing.get("title")) or ""
    tags = [decode_etsy_text(t) or "" for t in listing.get("tags") or []]
    description = decode_etsy_text(listing.get("description")) or ""
    out: list[Problem] = []
    state = str(listing.get("state") or "active")
    if state in STOPPED and past_units > 0:
        sentence = {
            "expired": f"It has expired. It made {_sold(past_units)} in the past year; renewing it in Shop Manager lets you sell it again.",
            "inactive": f"It is inactive, so buyers can no longer buy it. It made {_sold(past_units)} in the past year; reactivate it in Shop Manager.",
            "sold_out": f"It is sold out. It made {_sold(past_units)} in the past year; renewing it in Shop Manager lets you sell it again.",
        }[state]
        out.append(Problem("stopped_selling", ETSY, FIX_NOW, sentence, (EDIT,)))
    brands = brands_in(listing)
    warned = [b for b in brands if b.casefold() not in kept_brands]
    if warned:
        out.append(Problem("brand", ADVICE, FIX_NOW, brand_sentence(warned), ("rewrite", EDIT, KEEP_BRAND)))
    if title.strip():
        rejected = [c.detail for c in title_checks(title, short_style=False) if c.verdict == FAIL]
        if rejected:
            out.append(Problem("title_rejected", ETSY, FIX_NOW,
                               f"Etsy does not accept this title (at most {etsy_text.MAX_TITLE} characters, letters, numbers and "
                               f"punctuation, each of % : & + once): {' '.join(rejected)}", ("rewrite", EDIT)))
    tag_fails = [c.detail for c in tag_checks(tags) if c.id in ("tag_count", "tag_length", "tag_characters") and c.verdict == FAIL]
    tag_fails += [c.detail for c in tag_checks(tags) if c.id == "tag_start" and c.verdict == WARN]
    if tag_fails:
        out.append(Problem("tags_rejected", ETSY, FIX_NOW,
                           f"Etsy does not accept some tags (at most {etsy_text.MAX_TAGS} tags of up to {etsy_text.MAX_TAG} characters, "
                           f"letters, numbers, spaces, ' and - inside a tag, never at its start): {' '.join(tag_fails)}", ("fix_tags",)))
    if state == "active" and _photo_missing(listing):
        out.append(Problem("main_photo", ETSY, FIX_NOW, "It has no main photo, or its first photo has no image. Add one in Shop Manager.", (EDIT,)))
    if properties is not None and required:
        have = _filled(properties)
        missing = [r for r in required if r.get("id") is not None and int(r["id"]) not in have]
        if missing:
            from_reference = {int(p["property_id"]) for p in reference_attributes or [] if p.get("value_ids") or p.get("values")}
            fixes = ("attributes",) if any(int(m["id"]) in from_reference for m in missing) else (EDIT,)
            out.append(Problem("required_attribute", ETSY, FIX_NOW,
                               f"Etsy marks {', '.join(str(m.get('name')) for m in missing)} as required in this category, and it is empty.", fixes))
    stray = [name for name, bad in (("title", ai_text.junk(title)), ("tags", any(ai_text.junk(t) for t in tags)),
                                    ("description", ai_text.junk(description, lines=True))) if bad]
    if stray:
        sentence = "Description has stray characters." if stray == ["description"] else f"Stray characters in the {', '.join(stray)}."
        out.append(Problem("stray_characters", ADVICE, FIX_NOW, sentence + " Buyers see them as junk.", ("clean_text",)))
    return out, brands


def improvements(
    listing: dict[str, Any], *, properties: list[dict[str, Any]] | None, reference_attributes: list[dict[str, Any]] | None,
    tags_to_add: list[str], short_style: bool, now: datetime, required_ids: set[int],
) -> list[Problem]:
    """Concrete issues with a one-click fix. Asked only of a listing that has not sold in the window."""
    title = decode_etsy_text(listing.get("title")) or ""
    tags = [decode_etsy_text(t) or "" for t in listing.get("tags") or []]
    out: list[Problem] = []
    unique = len({" ".join(t.lower().split()) for t in tags if t.strip()})
    if unique < etsy_text.MAX_TAGS:
        out.append(Problem("tags_missing", ETSY, IMPROVE,
                           f"{unique} of {etsy_text.MAX_TAGS} tags used. Etsy lets you add up to {etsy_text.MAX_TAGS}.",
                           ("tags",) if tags_to_add else ("rewrite",)))
    if properties is not None and reference_attributes:
        have = _filled(properties)
        lacking = [str(p.get("property_name") or p.get("property_id")) for p in reference_attributes
                   if (p.get("value_ids") or p.get("values")) and p.get("property_id") is not None
                   and int(p["property_id"]) not in have and int(p["property_id"]) not in required_ids]
        if lacking:
            out.append(Problem("attributes_missing", ETSY, IMPROVE,
                               f"Your profile's reference listing also fills {', '.join(lacking)}; this one leaves it empty.", ("attributes",)))
    if short_style and title.strip():
        said: list[str] = []
        kind = ADVICE
        count = search_rules.word_count(title)
        if count > search_rules.MAX_TITLE_WORDS:
            said.append(f"The title has {count} words; Etsy's Help suggests fewer than 15.")
            kind = ETSY
        repeated = search_rules.repeated_words(title)
        if repeated:
            said.append(f"It repeats {_quoted(repeated)}.")
        found = _longest([t for t in (*search_rules.SUBJECTIVE, *search_rules.COMMERCE) if search_rules._has(title, t)] + search_rules.gift_in(title))
        if found:
            said.append(f"It uses {_quoted(found)}, which our advice keeps out of titles.")
        if said:
            out.append(Problem("title_guidance", kind, IMPROVE, " ".join(said), ("rewrite",)))
    dated = dated_tags(tags, now)
    if dated:
        out.append(Problem("dated_tags", ADVICE, IMPROVE, f"Tags for a holiday that has passed: {_quoted(dated)}.", ("rewrite", SEASON_PLANNER)))
    return out


def season_suggestion(listing: dict[str, Any], now: datetime) -> Problem | None:
    """A seasonal listing that is not selling: when its holiday comes next, and the planner that schedules it."""
    title = decode_etsy_text(listing.get("title")) or ""
    tags = [decode_etsy_text(t) or "" for t in listing.get("tags") or []]
    keys = holidays_in(" ".join([title, *tags]))
    if not keys:
        return None
    season = season_calendar.season_of(season_calendar.BY_KEY[keys[0]], now.date())
    return Problem("seasonal", ADVICE, IMPROVE,
                   f"Seasonal: {season.holiday.name} is on {season.on:%b} {season.on.day}. The season planner can schedule it.",
                   (SEASON_PLANNER,))


def classify(
    listing: dict[str, Any],
    *,
    recent_units: int,
    past_units: int,
    sales_known: bool,
    properties: list[dict[str, Any]] | None = None,
    required: list[dict[str, Any]] | None = None,
    reference_attributes: list[dict[str, Any]] | None = None,
    tags_to_add: list[str] | None = None,
    short_style: bool = True,
    now: datetime | None = None,
    windows: Windows = Windows(),
    kept_brands: set[str] | frozenset[str] = frozenset(),
) -> Verdict | None:
    """One listing's bucket. ``None``: not shown (a draft, or an inactive listing that never sold).

    ``recent_units``: units sold within ``windows.selling_days``; ``past_units``: all the shop's kept history (13
    months). ``properties`` is None until the attributes are read; ``required`` is the category's required attributes
    from a profile in the same category, None when no profile knows them."""
    now = now or datetime.now(timezone.utc)
    state = str(listing.get("state") or "active")
    if state != "active" and not (state in STOPPED and past_units > 0):
        return None
    kept = frozenset(k.casefold() for k in kept_brands)
    problems, brands = fix_now_problems(listing, properties=properties, required=required,
                                        reference_attributes=reference_attributes, past_units=past_units, kept_brands=kept)
    verdict = Verdict(bucket=FIX_NOW, brands=brands, recent_units=recent_units, past_units=past_units,
                      favourites=int(listing.get("num_favorers") or 0), age_days=_age_days(listing, now))
    verdict.kept_brands = [b for b in brands if b.casefold() in kept]
    selling = recent_units > 0
    young = verdict.age_days is not None and verdict.age_days < windows.new_days
    judged = sales_known and not selling and not young and state == "active"
    extra = improvements(listing, properties=properties, reference_attributes=reference_attributes, tags_to_add=tags_to_add or [],
                         short_style=short_style, now=now, required_ids={int(r["id"]) for r in required or [] if r.get("id") is not None}) if judged else []
    if judged:
        hint = season_suggestion(listing, now)
        if hint is not None:
            verdict.suggestions.append(hint)
    if problems:
        verdict.problems = problems + extra
        verdict.note = SELLING_NOTE if selling else None
        return verdict
    if selling:
        verdict.bucket, verdict.note = SELLING, SELLING_NOTE
    elif young:
        verdict.bucket = TOO_NEW
    elif not sales_known:
        verdict.bucket, verdict.note = NOT_JUDGED, NOT_JUDGED_NOTE
    elif extra:
        verdict.bucket, verdict.problems = IMPROVE, extra
    else:
        verdict.bucket = NOTHING
    return verdict
