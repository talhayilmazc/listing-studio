"""What the new prompt's FIRST attempt trips on (for tuning the prompt, not a score).

    python -m evals.first_attempt
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter

from app.pipeline import search_rules as sr
from app.pipeline.content import (
    MAX_TAG_LENGTH,
    REQUIRED_TAG_COUNT,
    copied_text_errors,
    fit_title,
    theme_errors,
    validate_listing,
)
from app.pipeline.vision import VisionAnalysis
from evals.content_eval import SET, _gather, _generators, usable


async def main() -> None:
    generator = _generators()["new"]
    designs = [usable(d) for d in json.loads(SET.read_text(encoding="utf-8"))["designs"]]
    kinds: Counter[str] = Counter()

    async def one(design: dict) -> None:
        analysis = VisionAnalysis(**design["analysis"])
        listing, _ = await generator._generate_once(analysis, design["id"])
        raw = list(listing.tags)
        listing.title = fit_title(listing.title, sr.SEARCH_TITLE.min_length, sr.SEARCH_TITLE.max_length)
        listing.tags, listing.tag_intents = sr.select_tags(
            listing.title, listing.tags, listing.tag_intents, REQUIRED_TAG_COUNT, MAX_TAG_LENGTH
        )
        errors = validate_listing(listing, generator._policy, title_rules=sr.SEARCH_TITLE)
        errors += copied_text_errors(listing.title, analysis.embedded_text)
        errors += theme_errors(listing, analysis.themes, in_title=False)
        print(f"\n{design['id']}: {listing.title}\n  {len(raw)} candidates -> {len(listing.tags)} kept")
        for e in errors:
            kinds[e.split(":")[0][:48]] += 1
            print("  !", e[:240])

    await _gather([one(d) for d in designs])
    print("\nfirst-attempt errors by kind:")
    for kind, n in kinds.most_common():
        print(f"  {n:2d}  {kind}")


if __name__ == "__main__":
    asyncio.run(main())
