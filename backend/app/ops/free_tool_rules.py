"""The app's listing rules as data for the free tools, which run in the visitor's browser.

The tools have no server behind them, so the rules are in TypeScript too (``frontend/lib/listingRules.ts``,
``frontend/lib/brandSpot.ts``). The words and limits are not typed twice: this writes them from
``pipeline/search_rules.py`` and ``compliance/trademarks.py`` into ``frontend/lib/listingRuleData.ts``.
``tests/test_free_tool_rules.py`` fails when the file and the rules differ, and says how to write it again:

    docker compose exec -T api python -m app.cli free-tool-rules > frontend/lib/listingRuleData.ts

The trademark notes (who holds a name) stay out: a browser has no use for them.
"""

from __future__ import annotations

import json
from typing import Any

from app.compliance import trademarks
from app.pipeline import search_rules


def rule_data() -> dict[str, Any]:
    return {
        "SUBJECTIVE": list(search_rules.SUBJECTIVE),
        "GIFT": list(search_rules.GIFT),
        "COMMERCE": list(search_rules.COMMERCE),
        "GARMENT_WORDS": sorted(search_rules._TYPES),
        "STOP_WORDS": sorted(search_rules._STOP),
        "MAX_TITLE_WORDS": search_rules.MAX_TITLE_WORDS,
        "MAX_TITLE_PHRASES": search_rules.SEARCH_TITLE.max_phrases,
        "MIN_MULTI_WORD_TAGS": search_rules.MIN_MULTI_WORD_TAGS,
        "TRADEMARKS": trademarks.read_terms(trademarks.DEFAULT_LIST_PATH),
        "TRADEMARKS_REVIEWED": trademarks.list_date(),
        "BRAND_WARNING": trademarks.BRAND_WARNING,
        "ORDINARY_WORDS": sorted(trademarks.ORDINARY_WORDS),
        "JOINED_AFTER_FROM": trademarks.JOINED_AFTER_FROM,
        "JOINED_BEFORE_FROM": trademarks.JOINED_BEFORE_FROM,
        "LOOSE_FROM": trademarks.LOOSE_FROM,
    }


def render() -> str:
    lines = [
        "// Written by `python -m app.cli free-tool-rules` from backend/app/pipeline/search_rules.py and",
        "// backend/app/compliance/trademarks.py. Do not edit: tests/test_free_tool_rules.py compares them.",
    ]
    for name, value in rule_data().items():
        text = json.dumps(value, ensure_ascii=False)
        lines.append(f"export const {name} = {text}{' as const' if isinstance(value, list) else ''};")
    return "\n".join(lines) + "\n"
