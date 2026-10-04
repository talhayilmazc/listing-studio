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
from evals.content_eval import SET, STAND_IN_CATEGORY, STAND_IN_CHOICES, _gather, _generators, usable


async def main() -> None:
    generator = _generators()["new"]
    designs = [usable(d) for d in json.loads(SET.read_text(encoding="utf-8"))["designs"]]
    kinds: Counter[str] = Counter()
    passed: list[bool] = []
    drops: Counter[str] = Counter()

    async def one(design: dict) -> None:
        analysis = VisionAnalysis(**design["analysis"])
        listing, _ = await generator._generate_once(analysis, design["id"])
        raw = list(listing.tags)
        listing.title = fit_title(listing.title, sr.SEARCH_TITLE.min_length, sr.SEARCH_TITLE.max_length)
        listing.tags, listing.tag_intents = sr.select_tags(
            listing.title, listing.tags, listing.tag_intents, REQUIRED_TAG_COUNT, MAX_TAG_LENGTH,
            already=[*STAND_IN_CATEGORY, *listing.attributes.values()], banned=generator.banned_in_tags(),
        )
        errors = validate_listing(
            listing, generator._policy, title_rules=sr.SEARCH_TITLE, category_names=STAND_IN_CATEGORY
        )
        errors += sr.attribute_errors(listing.attributes, STAND_IN_CHOICES)
        errors += copied_text_errors(listing.title, analysis.embedded_text)
        errors += theme_errors(listing, analysis.themes, in_title=False)
        print(f"\n{design['id']}: {listing.title}\n  {len(raw)} candidates -> {len(listing.tags)} kept; {listing.attributes}")
        passed.append(not errors)
        for t in raw:
            if t not in listing.tags:
                why = ("long" if len(t) > 20 else "title-only" if set(sr.keywords(t)) <= set(sr.keywords(listing.title))
                       else "opinion" if sr.opinion_in(t) else "restates" if sr.restates(t, [*STAND_IN_CATEGORY, *listing.attributes.values()])
                       else "banned" if any(sr._has(t, b) for b in generator.banned_in_tags()) else "duplicate/spare")
                drops[why] += 1
                if why != "duplicate/spare":
                    print(f"  - dropped ({why}): {t}")
        for e in errors:
            kinds[e.split(":")[0][:48]] += 1
            print("  !", e[:240])

    await _gather([one(d) for d in designs])
    print(f"\nfirst attempt passed: {sum(passed)}/{len(passed)}")
    print("dropped candidates:", dict(drops))
    print("first-attempt errors by kind:")
    for kind, n in kinds.most_common():
        print(f"  {n:2d}  {kind}")


if __name__ == "__main__":
    # Our own runs spend on the same key: metered as "eval", with no seller.
    from app.core import ai_meter

    with ai_meter.origin("eval"):
        asyncio.run(main())
