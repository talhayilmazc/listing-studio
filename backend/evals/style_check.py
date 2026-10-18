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
    title = join_prefix(PREFIX, raw["title"])
    return {"title": title, "tags": list(raw["tags"]), "attributes": {}}


def new(design: dict[str, Any]) -> dict[str, Any]:
    raw = design["new"]
    analysis = SimpleNamespace(**design["analysis"])
    rules = sr.SEARCH_TITLE
    title = join_prefix(PREFIX, raw["title"])
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


# --- live: the real content model on the same 20 synthetic analyses -----------------------------


def _analysis(design: dict[str, Any]) -> Any:
    """A vision analysis built from the synthetic set's own fields (no image, no vision call)."""
    from app.pipeline.vision import VisionAnalysis

    a = design["analysis"]
    themes = list(a.get("themes") or [a.get("theme", "")])
    return VisionAnalysis(
        theme=a.get("theme") or themes[0], embedded_text="", style=a.get("style", ""), colors=list(a.get("colors") or []),
        target_audience="", product_type_hints=["t-shirt"], occasion=a.get("occasion", ""),
        meaning="A design about " + " and ".join(themes), themes=themes, profession=a.get("profession", ""),
        season=a.get("season", ""),
    )


async def _live_rows(designs: list[dict[str, Any]], style: str) -> tuple[list[dict[str, Any]], list[Any]]:
    from app.core import ai_meter
    from app.core.config import get_settings
    from app.pipeline.content import AnthropicContentGenerator, ContentValidationError
    from app.pipeline.llm import client_for
    from app.pipeline.templates import load_template

    args: dict[str, Any] = {}
    if style == "search":
        args = {"title_rules": sr.SEARCH_TITLE, "attribute_choices": CHOICES, "category_names": CATEGORY, "max_tokens": 2048}
    generator = AnthropicContentGenerator(
        client_for(get_settings(), "content"),
        template=load_template("content/apparel_search" if style == "search" else "content/apparel"),
        policy=policy_for("apparel"), title_prefix=PREFIX, trademarks=Blocklist(()), **args,
    )
    rows: list[dict[str, Any]] = []
    usages: list[Any] = []

    async def one(design: dict[str, Any]) -> None:
        try:
            with ai_meter.origin("eval"):
                result = await generator.generate(_analysis(design), design["sku"])
            usages.extend(result.usages)
            rows.append({"sku": design["sku"], "title": result.listing.title, "tags": result.listing.tags,
                         "attempts": result.attempts, "warnings": result.warnings, "failed": None})
        except ContentValidationError as exc:
            usages.extend(exc.usages)
            rows.append({"sku": design["sku"], "title": "", "tags": [], "attempts": 2, "warnings": [],
                         "failed": exc.errors})

    for design in designs:  # one at a time: the measurement, not throughput
        await one(design)
    return rows, usages


def live_measure(rows: list[dict[str, Any]], usages: list[Any]) -> dict[str, Any]:
    """Per style: the share of titles meeting each rule of Etsy's title guidance (the
    same rules for both styles: 140 characters at most, fewer than 15 words, …),
    the mean words per title, tags repeating title words, attempts, warnings, cost."""
    from app.core import ai_prices

    guidance = sr.TitleRules(min_length=1, max_length=sr.ETSY_MAX_TITLE_LENGTH, max_words=sr.MAX_TITLE_WORDS)
    written = [r for r in rows if r["title"]]
    reports = [sr.rule_report(r["title"], guidance, PREFIX) for r in written]
    table = ai_prices.merged(None)
    cost = sum((ai_prices.cost(u.model, table, input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                               cache_write_tokens=u.cache_creation_input_tokens - u.cache_creation_1h_input_tokens,
                               cache_write_1h_tokens=u.cache_creation_1h_input_tokens,
                               cache_read_tokens=u.cache_read_input_tokens) or 0) for u in usages)
    tags_all = 0
    tags_some = 0
    for r in written:
        in_title = set(sr.keywords(r["title"]))
        for t in r["tags"]:
            key = set(sr.keywords(t))
            tags_all += bool(key) and key <= in_title
            tags_some += bool(key & in_title)
    n = len(written) or 1
    return {
        "listings": len(rows),
        "written": len(written),
        "failed": sum(1 for r in rows if r["failed"]),
        "rule_share": {rule: round(sum(rep[rule] for rep in reports) / n, 2) for rule in sr.TITLE_RULES},
        "all_rules": round(sum(all(rep.values()) for rep in reports) / n, 2),
        "words_mean": round(mean(sr.word_count(r["title"]) for r in written), 1) if written else None,
        "tags_mean": round(mean(len(r["tags"]) for r in written), 1) if written else None,
        "tags_only_title_words": tags_all,
        "tags_sharing_a_title_word": tags_some,
        "tags_total": sum(len(r["tags"]) for r in written),
        "rewritten_once": sum(1 for r in written if r["attempts"] == 2),
        "kept_with_warning": sum(1 for r in written if r["warnings"]),
        "calls": len(usages),
        "cost_usd": float(round(cost, 4)),
    }


def live(path: Path = SET) -> dict[str, Any]:
    """20 listings per style from the synthetic analyses, written by the real model.
    No Etsy call; the model calls are metered as ``eval`` (no seller)."""
    import asyncio

    designs = json.loads(path.read_text())["designs"]
    out: dict[str, Any] = {}

    async def both() -> None:  # one event loop: the meter's database engine belongs to it
        for style in ("search", "classic"):
            rows, usages = await _live_rows(designs, style)
            out[style] = live_measure(rows, usages)
            out[style + "_examples"] = [
                {"sku": r["sku"], "title": r["title"], "tags": r["tags"], "warnings": r["warnings"], "failed": r["failed"]}
                for r in rows
            ]

    asyncio.run(both())
    return out


if __name__ == "__main__":
    if "--live" in sys.argv:
        print(json.dumps(live(), indent=1, ensure_ascii=False))
    else:
        report = run(Path(sys.argv[1]) if len(sys.argv) > 1 else SET)
        print(json.dumps(report, indent=1, ensure_ascii=False))
