"""Shared test doubles for the LLM pipeline — no real API calls."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.pipeline.llm import AnthropicLLMClient

MODEL = "claude-haiku-4-5-20251001"

# A 135-char title that satisfies the 130-140 rule (spec §3).
VALID_TITLE = ("Adventure Awaits Printable Wall Art Digital Download Poster " * 4)[:135]
assert 130 <= len(VALID_TITLE) <= 140


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class FakeResponse:
    content: list[FakeTextBlock]
    usage: FakeUsage
    stop_reason: str = "end_turn"


def fake_response(
    data: dict[str, Any],
    *,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: int = 0,
    cache_write: int = 0,
) -> FakeResponse:
    return FakeResponse(
        content=[FakeTextBlock(json.dumps(data))],
        usage=FakeUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


class FakeMessages:
    """Stands in for ``anthropic.AsyncAnthropic().messages``.

    Returns queued responses in order and records every request's kwargs so tests
    can assert on caching structure, model, image payload, etc.
    """

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessages ran out of queued responses")
        return self._responses.pop(0)


def fake_client(responses: list[FakeResponse], *, model: str = MODEL) -> AnthropicLLMClient:
    return AnthropicLLMClient(api_key="test", model=model, messages_client=FakeMessages(responses))
