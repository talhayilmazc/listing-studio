"""Vision analyzer tests (provider mocked)."""

import base64
from typing import Callable

from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.vision import VISION_SCHEMA, AnthropicVisionAnalyzer
from tests.support import FakeMessages, fake_client, fake_response

_ANALYSIS = {
    "theme": "vintage mountain sunrise",
    "embedded_text": "ADVENTURE AWAITS",
    "style": "retro flat vector",
    "colors": ["burnt orange", "cream", "navy"],
    "target_audience": "hikers and campers",
    "product_type_hints": ["t-shirt", "sticker", "wall art print"],
}


async def test_analyze_parses_structured_output(make_image: Callable[..., bytes]) -> None:
    image = make_image(400, 300)
    client = fake_client([fake_response(_ANALYSIS, input_tokens=1200, output_tokens=90)])
    analyzer = AnthropicVisionAnalyzer(client)

    result = await analyzer.analyze(image, "image/png")

    assert result.analysis.theme == "vintage mountain sunrise"
    assert result.analysis.colors == ["burnt orange", "cream", "navy"]
    assert result.usage.model == "claude-haiku-4-5-20251001"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (1200, 90)


async def test_request_caches_system_and_sends_downscaled_image(
    make_image: Callable[..., bytes],
) -> None:
    image = make_image(400, 300)
    messages = FakeMessages([fake_response(_ANALYSIS)])
    client = AnthropicLLMClient(
        api_key="t", model="claude-haiku-4-5-20251001", messages_client=messages
    )
    analyzer = AnthropicVisionAnalyzer(client)

    await analyzer.analyze(image, "image/png")

    params = messages.calls[0]
    # System prompt carries the cache breakpoint.
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    # Structured output schema is attached.
    assert params["output_config"]["format"]["schema"] == VISION_SCHEMA
    # The image is the (only) volatile block, base64 of exactly what we passed.
    blocks = params["messages"][0]["content"]
    image_block = next(b for b in blocks if b["type"] == "image")
    assert image_block["source"]["media_type"] == "image/png"
    assert base64.standard_b64decode(image_block["source"]["data"]) == image


async def test_only_image_varies_between_calls(make_image: Callable[..., bytes]) -> None:
    a = make_image(400, 300, color=(200, 30, 30))
    b = make_image(400, 300, color=(30, 30, 200))
    messages = FakeMessages([fake_response(_ANALYSIS), fake_response(_ANALYSIS)])
    client = AnthropicLLMClient(api_key="t", model="m", messages_client=messages)
    analyzer = AnthropicVisionAnalyzer(client)

    await analyzer.analyze(a, "image/png")
    await analyzer.analyze(b, "image/png")

    first, second = messages.calls
    assert first["system"] == second["system"]  # cached prefix is byte-identical
    img1 = next(x for x in first["messages"][0]["content"] if x["type"] == "image")
    img2 = next(x for x in second["messages"][0]["content"] if x["type"] == "image")
    assert img1["source"]["data"] != img2["source"]["data"]
