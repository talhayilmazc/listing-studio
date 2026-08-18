"""Anthropic Messages API client for the pipeline's LLM calls.

Design goals baked in here:

- **Prompt caching**: the ``system`` prompt (stable instructions) carries a
  ``cache_control`` breakpoint; the per-call content (image / analysis) goes in
  the ``messages`` turn *after* that breakpoint, so only the volatile part
  changes between calls. NOTE: caching only takes effect once the cached prefix
  exceeds the model's minimum cacheable size (4096 tokens for Haiku 4.5); short
  prompts are structured correctly but won't cache until instructions grow or on
  a model with a lower minimum.
- **Structured outputs**: responses are constrained with ``output_config.format``
  so the first text block is always schema-valid JSON.
- **Batch-API ready**: :meth:`build_params` returns the exact request body, so a
  later Batch-API path can wrap it as a batch request without re-deriving it.
  Jobs already flow through our queue, so batching is a drop-in there.

The provider is injected (a `.messages` object with an async ``create``), which
keeps the concrete Anthropic SDK out of the unit tests — they pass a fake.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


class LLMError(Exception):
    """The model returned something we can't use (refusal, malformed JSON)."""


@dataclass(frozen=True)
class Usage:
    """Token usage for a single model call — the basis for cost instrumentation."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class LLMResult:
    data: dict[str, Any]
    usage: Usage


class MessagesClient(Protocol):
    """Minimal surface we use from ``anthropic.AsyncAnthropic().messages``."""

    async def create(self, **kwargs: Any) -> Any: ...


class LLMClient(Protocol):
    model: str

    def build_params(
        self,
        *,
        system: str,
        content_blocks: list[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]: ...

    async def complete_json(
        self,
        *,
        system: str,
        content_blocks: list[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> LLMResult: ...


class AnthropicLLMClient:
    """Concrete client backed by the Anthropic Messages API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        messages_client: MessagesClient | None = None,
        max_tokens: int = 1024,
    ) -> None:
        self.model = model
        self._max_tokens = max_tokens
        if messages_client is not None:
            self._messages = messages_client
        else:
            # Imported lazily so tests (which inject a fake) don't need the SDK.
            import anthropic

            self._messages = anthropic.AsyncAnthropic(api_key=api_key).messages

    def build_params(
        self,
        *,
        system: str,
        content_blocks: list[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": max_tokens or self._max_tokens,
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": content_blocks}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }

    async def complete_json(
        self,
        *,
        system: str,
        content_blocks: list[dict[str, Any]],
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> LLMResult:
        params = self.build_params(
            system=system,
            content_blocks=content_blocks,
            schema=schema,
            max_tokens=max_tokens,
        )
        response = await self._messages.create(**params)
        return LLMResult(data=_parse_json(response), usage=_usage(response, self.model))


def _parse_json(response: Any) -> dict[str, Any]:
    if getattr(response, "stop_reason", None) == "refusal":
        raise LLMError("model refused the request")
    text: str | None = None
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            text = block.text
            break
    if text is None:
        raise LLMError("no text block in model response")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - guarded by structured outputs
        raise LLMError("model response was not valid JSON") from exc
    if not isinstance(data, dict):
        raise LLMError("model response was not a JSON object")
    return data


def _usage(response: Any, model: str) -> Usage:
    usage = getattr(response, "usage", None)
    if usage is None:
        raise LLMError("model response carried no usage")
    return Usage(
        model=model,
        input_tokens=int(usage.input_tokens),
        output_tokens=int(usage.output_tokens),
        cache_creation_input_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
    )
