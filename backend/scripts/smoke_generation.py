"""Opt-in manual smoke test against the REAL Anthropic API.

This is NOT run by pytest. It makes a live API call, so it's gated behind
LLM_API_KEY being set. Run from the backend/ directory:

    LLM_API_KEY=sk-ant-... python -m scripts.smoke_generation

It generates a tiny test image locally, runs vision + content generation, prints
the results, and reports the per-listing cost.
"""

from __future__ import annotations

import asyncio
import io

from app.core.config import get_settings
from app.pipeline.content import AnthropicContentGenerator, ContentValidationError
from app.pipeline.cost import CostCalculator
from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.vision import AnthropicVisionAnalyzer


def _sample_png() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (600, 400), (244, 241, 234))
    draw = ImageDraw.Draw(img)
    draw.rectangle((60, 120, 540, 280), outline=(178, 90, 40), width=8)
    draw.text((90, 180), "ADVENTURE AWAITS", fill=(30, 40, 90))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


async def main() -> None:
    settings = get_settings()
    if not settings.llm_api_key:
        raise SystemExit("Set LLM_API_KEY in the environment to run this smoke test.")

    client = AnthropicLLMClient(api_key=settings.llm_api_key, model=settings.llm_model)
    analyzer = AnthropicVisionAnalyzer(client)
    generator = AnthropicContentGenerator(client)
    calc = CostCalculator()

    image = _sample_png()
    print(f"Model: {settings.llm_model}\n")

    vision = await analyzer.analyze(image, "image/png")
    print("=== Vision analysis ===")
    print(vision.analysis)
    print()

    try:
        result = await generator.generate(vision.analysis, sku="SMOKE-1")
    except ContentValidationError as exc:
        print("Content failed validation:", exc.errors)
        return

    print("=== Generated listing ===")
    print("Title:", result.listing.title)
    print("Tags:", result.listing.tags)
    print("Description:", result.listing.description)
    print()

    usages = [vision.usage, *result.usages]
    print(f"Attempts: {result.attempts}")
    print(f"Per-listing cost (USD): {calc.listing_cost(usages):.6f}")


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
