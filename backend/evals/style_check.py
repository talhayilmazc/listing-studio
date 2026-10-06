"""Deterministic old-vs-new check of the two title styles (Part C, item 10).

    python -m evals.style_check                     # the committed synthetic set
    python -m evals.style_check path/to/set.json    # any set in the same shape

Each design goes through the parts of the pipeline that are deterministic for
each style: the profile's prefix, the title fit, and for "Etsy recommended
(short)" the tag selection (Etsy's tag rules) and the attribute fill from the
analysis. The model's raw output is part of the set, so **titles and tag
candidates measure the set's wording, not the model**: the committed set
(``evals/fixtures/synthetic_designs.json``) is invented and hand-written. To
measure the model, write the set from real generations (``evals.content_eval
run`` needs the LLM and the seller's designs, which are not committed).

Reported, per style: words per title, titles with a repeated word (the prefix's
words excluded), subjective or gift words in titles, tags duplicating an
attribute value or the category, tag pairs sharing their root words, tags made
only of the title's words, design attributes filled, and the new rules'
validation result. Nothing here measures views, clicks or sales.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean
from types import SimpleNamespace
from typing import Any

from app.compliance.trademarks import Blocklist
from app.pipeline import search_rules as sr
from app.pipeline.attribute_fill import fill_design_attributes
from app.pipeline.content import (
    GeneratedListing,
    fit_title,
    join_prefix,
    policy_for,
    validate_listing,
)

SET = Path(__file__).parent / "fixtures" / "synthetic_designs.json"
PREFIX = "Comfort Colors®"
CATEGORY = ["Clothing", "T-shirts"]
#: A stand-in for an apparel category's design attributes (Etsy's lists are read
#: per shop at profile refresh; none is read here).
CHOICES: dict[str, list[str]] = {
    "Primary color": ["Beige", "Black", "Blue", "Bronze", "Brown", "Clear", "Copper", "Gold", "Gray", "Green",
                      "Orange", "Pink", "Purple", "Rainbow", "Red", "Rose gold", "Silver", "White", "Yellow"],
    "Secondary color": ["Beige", "Black", "Blue", "Bronze", "Brown", "Clear", "Copper", "Gold", "Gray", "Green",
                        "Orange", "Pink", "Purple", "Rainbow", "Red", "Rose gold", "Silver", "White", "Yellow"],
    "Occasion": ["Anniversary", "Baby shower", "Back to school", "Birthday", "Graduation", "Retirement", "Wedding"],
    "Holiday": ["Christmas", "Easter", "Father's Day", "Halloween", "Independence Day", "Mother's Day",
                "St Patrick's Day", "Thanksgiving", "Valentine's Day"],
}


def _repeats(title: str) -> list[str]:
    seen: dict[str, int] = {}
    for w in sr.keywords(sr.without_prefix(title, PREFIX)):
        seen[w] = seen.get(w, 0) + 1
    return sorted(w for w, n in seen.items() if n > 1)


def _bad_words(title: str) -> list[str]:
    return [t for t in (*sr.SUBJECTIVE, *sr.GIFT) if sr._has(title, t)]  # noqa: SLF001


def _tag_checks(title: str, tags: list[str], attributes: dict[str, str]) -> dict[str, int]:
    already = [*CATEGORY, *attributes.values()]
    roots = [frozenset(sr.keywords(t)) for t in tags]
    in_title = set(sr.keywords(title))
    return {
        "tag_attribute_or_category_dupes": sum(1 for t in tags if sr.restates(t, already)),
        "tag_root_dupes": len(roots) - len(set(roots)),
        "tags_only_title_words": sum(1 for r in roots if r and r <= in_title),
        "tags": len(tags),
    }


def old(design: dict[str, Any]) -> dict[str, Any]:
    raw = design["old"]
    title = fit_title(join_prefix(PREFIX, raw["title"]), 110, 140)
    return {"title": title, "tags": list(raw["tags"]), "attributes": {}}


def new(design: dict[str, Any]) -> dict[str, Any]:
    raw = design["new"]
    analysis = SimpleNamespace(**design["analysis"])
    rules = sr.SEARCH_TITLE
    title = fit_title(join_prefix(PREFIX, raw["title"]), rules.min_length, rules.max_length)
    attributes, source = fill_design_attributes(dict(raw.get("attributes") or {}), CHOICES, analysis)
    policy = policy_for("apparel")
    banned = (*policy.forbidden_terms, *policy.forbidden_tag_terms, *policy.forbidden_filler_terms)
    candidates = list(raw["tag_candidates"])
    intents = list(raw.get("tag_intents") or [""] * len(candidates))
    tags, intents = sr.select_tags(title, candidates, intents, already=[*CATEGORY, *attributes.values()], banned=banned)
    listing = GeneratedListing(title=title, tags=tags, description=raw["opening"], opening=raw["opening"],
                               tag_intents=intents, attributes=attributes)
    errors = validate_listing(listing, policy, trademarks=Blocklist(()), title_rules=rules,
                              category_names=CATEGORY, title_prefix=PREFIX)
    # The synthetic set carries no tag intents, so the "kinds of search" rule is not judged here.
    errors = [e for e in errors if "kinds of search" not in e]
    return {"title": title, "tags": tags, "attributes": attributes, "attribute_source": source, "errors": errors}


def measure(rows: list[dict[str, Any]]) -> dict[str, Any]:
    per = []
    for r in rows:
        per.append({
            "words": len(r["title"].split()), "chars": len(r["title"]), "repeated": _repeats(r["title"]),
            "bad_words": _bad_words(r["title"]), "attributes": len(r["attributes"]),
            **_tag_checks(r["title"], r["tags"], r["attributes"]),
        })
    return {
        "designs": len(per),
        "words_per_title_mean": round(mean(p["words"] for p in per), 1),
        "words_per_title_max": max(p["words"] for p in per),
        "chars_per_title_mean": round(mean(p["chars"] for p in per)),
        "titles_over_15_words": sum(1 for p in per if p["words"] > 15),
        "titles_with_repeated_word": sum(1 for p in per if p["repeated"]),
        "titles_with_subjective_or_gift_word": sum(1 for p in per if p["bad_words"]),
        "tag_attribute_or_category_dupes": sum(p["tag_attribute_or_category_dupes"] for p in per),
        "tag_root_dupes": sum(p["tag_root_dupes"] for p in per),
        "tags_only_title_words": sum(p["tags_only_title_words"] for p in per),
        "listings_with_13_tags": sum(1 for p in per if p["tags"] == 13),
        "design_attributes_filled_mean": round(mean(p["attributes"] for p in per), 2),
    }


def run(path: Path = SET) -> dict[str, Any]:
    designs = json.loads(path.read_text())["designs"]
    olds, news = [old(d) for d in designs], [new(d) for d in designs]
    return {
        "old": measure(olds),
        "new": measure(news),
        "new_passing_validation": sum(1 for n in news if not n["errors"]),
        "examples": [
            {"sku": d["sku"], "old": o["title"], "new": n["title"], "attributes": n["attributes"], "errors": n["errors"]}
            for d, o, n in zip(designs, olds, news, strict=True)
        ],
    }


if __name__ == "__main__":
    report = run(Path(sys.argv[1]) if len(sys.argv) > 1 else SET)
    print(json.dumps(report, indent=1, ensure_ascii=False))
