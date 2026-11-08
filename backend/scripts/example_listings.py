"""Write the example listings once, with the app's own model calls, and save the result as a fixture.

The example designs (scripts/example_designs.py) are read by the same vision step and written by
the same content step a seller's design goes through, in the short-title style new profiles use.
The answer is saved to backend/app/examples/examples.json, and the app shows it from there
(pipeline/examples.py): trying the examples makes no model call and costs nothing.

    docker compose exec -T api python scripts/example_listings.py

Run once by us, on our own designs, metered as ``eval`` (core/ai_meter.py). Needs LLM_API_KEY.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import ai_meter  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.pipeline import llm  # noqa: E402
from app.pipeline.content import AnthropicContentGenerator, content_template_for, policy_for, search_style  # noqa: E402
from app.pipeline.templates import load_template  # noqa: E402
from app.pipeline.vision import AnthropicVisionAnalyzer  # noqa: E402

HERE = Path(__file__).resolve().parents[1] / "app" / "examples"
SLUGS = ["slow-mornings-club", "tomato-season", "small-kind-things"]

#: A profile as a new seller's would be: apparel, the short-title style, no prefix.
PROFILE = SimpleNamespace(
    listing_style="search", content_template="apparel", title_prefix="", title_min_length=40, title_max_length=140,
    cached_payload={},
)


async def main() -> None:
    settings = get_settings()
    if not settings.llm_api_key:
        raise SystemExit("LLM_API_KEY is not set")
    vision_client = llm.AnthropicLLMClient(api_key=settings.llm_api_key, model=settings.vision_model or settings.llm_model,
                                           thinking=settings.llm_thinking, effort=settings.llm_effort)
    content_client = llm.AnthropicLLMClient(api_key=settings.llm_api_key, model=settings.content_model or settings.llm_model,
                                            thinking=settings.llm_thinking, effort=settings.llm_effort)
    generator = AnthropicContentGenerator(content_client, template=load_template(content_template_for(PROFILE)),
                                          policy=policy_for("apparel"), **search_style(PROFILE))
    listings = []
    with ai_meter.origin("eval"):
        for slug in SLUGS:
            vision = await AnthropicVisionAnalyzer(vision_client).analyze((HERE / f"{slug}-light.jpg").read_bytes(), "image/jpeg")
            result = await generator.generate(vision.analysis, None)
            listing = result.listing
            listings.append({
                "slug": slug,
                "title": listing.title,
                "tags": list(listing.tags),
                "opening": listing.opening or "",
                "attributes": dict(listing.attributes),
                "warnings": list(result.warnings),
                "theme": vision.analysis.theme,
                "themes": list(vision.analysis.themes or []),
            })
            print(slug, "->", listing.title)
    out = {
        "written_on": date.today().isoformat(),
        "how": "scripts/example_listings.py: the app's vision and content steps, short-title style, run once",
        "listings": listings,
    }
    (HERE / "examples.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
