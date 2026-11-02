"""Write tests/fixtures/free_tool_cases.json from the app's own rules (run in the api container:
``python scripts/free_tool_cases.py``). tests/test_free_tool_rules.py checks the code still gives
these answers; the browser tools' copy (frontend lib/freeToolRules.test.ts) checks it gives the same."""

from __future__ import annotations

import json
from pathlib import Path

from app.compliance import trademarks
from app.pipeline import etsy_text, listing_health, search_rules

#: Listing health's name for each of the title and tag checkers' rules.
TITLE_IDS = {"title_length": "length", "title_characters": "characters", "title_once": "once", "title_words": "words"}
TAG_IDS = {"tag_count": "count", "tag_length": "length", "tag_characters": "characters", "tag_start": "start", "tag_duplicates": "duplicates",
           "tag_title_words": "title_words", "tag_phrases": "phrases", "tag_all": "all"}

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "free_tool_cases.json"

TITLES = [
    "Frog Reading Club Shirt, Book Lover Tee",
    "Nurse Shirt, Retro Nurse Life Tee",
    "Nurses shirt for nurse life",
    "Cute Nurse T-Shirt, Perfect Gift for Her, Free Shipping",
    "Retro Sunset Sweatshirt, Vintage Beach Crewneck, Summer Tee, Soft Cotton Top, Mountain Lake Pullover",
    "Sunset Mountain Lake Design",
    "T Shirt t-shirt Tshirts",
    "Mug for Teachers, High Quality Coffee Cup on Sale, 20% off",
    "Handmade Ceramic Mug",
    "One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Shirt",
    "Café Crème T-Shirt – Women's Größe (S/M)",
    "Cutest Gifted Presenter Shirt",
    "Best Seller Mom's Shirt & Dad's Shirt",
    "Marvelous Scavenger Hunt Shirt",
    "DisneyWorld Family Trip Shirt",
    "Mick Ey Mouse Ears Tee",
    "#Disney Vacation Hoodie",
    "D1sney Castle Tee",
    "Blue Y Neck Tee",
    "Baby-Yoda Lover Shirt",
    "star_wars fan tank",
    "Pokémon Trainer Tee",
    "ILoveDisney Tank Top",
    "NFL Sunday Tee",
    "nhi mib tee",
    "Lilo & Stitch Beach Shirt",
    "Disneyland Castle Shirt",
    "A plain title that names nothing in particular",
]

TAG_SETS = [
    {"title": "Frog Reading Club Shirt", "tags": ["frog shirt", "frogs shirts", "shirt frog", "reading club", "book lover", "club shirt", "frog", "gift for reader", "library tee", "nerdy top", "cute reader", "cottagecore", "toad"]},
    {"title": "", "tags": ["nurse", "nursing", "scrubs", "rn", "doctor", "hospital", "medical", "healthcare", "er nurse tee"]},
    {"title": "Retro Sunset Tee", "tags": ["sunset retro", "retro tee", "sunset", "beach sunset shirt", "summer vibes"]},
    {"title": "", "tags": ["disneyworld shirt", "mick ey tee", "marvelous mom", "star-wars fan", "nfl dad", "plain tag"]},
    {"title": "", "tags": ["a much too long tag for etsy", "tag & more", "- dash first", "'quote first", "curly’s tag", "fine tag"]},
    {"title": "", "tags": [f"tag {i}" for i in range(14)]},
    {"title": "Disney Family Trip Shirt", "tags": ["star-wars fan", "disney tee", "plain tag"]},
]


def _title(text: str) -> dict:
    rules = search_rules.SEARCH_TITLE
    report = search_rules.rule_report(text, rules)
    return {
        "text": text,
        "etsy_ok": not etsy_text.title_errors(text),
        "word_count": search_rules.word_count(text),
        "phrases": search_rules.phrases(text),
        "repeated": search_rules.repeated_words(text),
        "subjective": [t for t in search_rules.SUBJECTIVE if search_rules._has(text, t)],
        "commerce": [t for t in search_rules.COMMERCE if search_rules._has(text, t)],
        "gift": search_rules.gift_in(text),
        "garments": [w for w in search_rules.words(text) if w in search_rules._TYPES],
        "report": {k: report[k] for k in ("words", "repeats", "subjective", "commerce", "gift", "item_first")},
        "brands": sorted({s.term for s in trademarks.bundled_blocklist().spot(text)}),
        "brand_warnings": trademarks.brand_warnings(text, [], "", trademarks.Blocklist()),
        "health": {TITLE_IDS.get(c.id, c.id): c.verdict for c in listing_health.title_checks(text)},
    }


def _tags(case: dict) -> dict:
    tags, title = case["tags"], case["title"]
    in_title = set(search_rules.keywords(title))
    known = trademarks.bundled_blocklist()
    return {
        "title": title,
        "tags": tags,
        "etsy_ok": not etsy_text.tag_errors(tags),
        "same_search": [list(pair) for pair in search_rules.same_search_tags(tags)],
        "only_title_words": [t for t in tags if search_rules.keywords(t) and set(search_rules.keywords(t)) <= in_title],
        "phrases": sum(1 for t in tags if len(t.split()) >= 2),
        "brands": sorted({s.term for tag in tags for s in known.spot(tag)}),
        "brand_warnings": trademarks.brand_warnings(title, tags, "", trademarks.Blocklist()),
        "starting_badly": etsy_text.tags_starting_badly(tags),
        "health": {TAG_IDS[c.id]: c.verdict for c in listing_health.tag_checks(tags, title)},
    }


def build() -> dict:
    return {
        "_about": "What the app's own rules answer (pipeline/search_rules.py, pipeline/etsy_text.py, compliance/trademarks.py). "
                  "Written by scripts/free_tool_cases.py; read by tests/test_free_tool_rules.py and frontend/lib/freeToolRules.test.ts.",
        "titles": [_title(t) for t in TITLES],
        "tag_sets": [_tags(c) for c in TAG_SETS],
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
