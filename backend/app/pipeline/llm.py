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
import time
from dataclasses import dataclass
from typing import Any, Protocol


from app.core import ai_meter
from app.core.llm_status import classify


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
    #: The part of ``cache_creation_input_tokens`` written with the 1-hour
    #: lifetime, which is billed at a higher rate than the 5-minute default.
    cache_creation_1h_input_tokens: int = 0


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


# How each model family treats thinking (see the Claude API model table):
# - Haiku 4.5 runs without thinking unless given a token budget, and takes no
#   ``effort``: send neither.
# - Fable, Mythos and Opus 5.5 always think; ``{"type": "disabled"}`` is a 400,
#   so "off" means leaving the parameter out and asking for low effort.
# - The rest (Sonnet 5, Opus 5, Opus 4.x, Sonnet 4.6) think adaptively when the
#   parameter is omitted, so "off" must be said explicitly.
_NO_THINKING_PARAM = ("claude-haiku",)
_ALWAYS_THINKING = ("claude-fable", "claude-mythos", "claude-opus-5-5")
#: Room for thinking plus the JSON answer; thinking counts against max_tokens.
THINKING_MAX_TOKENS = 8000


def thinking_params(model: str, mode: str, effort: str = "") -> dict[str, Any]:
    """The ``thinking`` / ``output_config.effort`` request fields for this model."""
    if model.startswith(_NO_THINKING_PARAM):
        return {}
    out: dict[str, Any] = {}
    if model.startswith(_ALWAYS_THINKING):
        out["effort"] = effort or ("low" if mode != "adaptive" else "")
    elif mode == "adaptive":
        out["thinking"] = {"type": "adaptive"}
        if effort:
            out["effort"] = effort
    else:
        out["thinking"] = {"type": "disabled"}
        if effort:
            out["effort"] = effort
    if not out.get("effort"):
        out.pop("effort", None)
    return out


class AnthropicLLMClient:
    """Concrete client backed by the Anthropic Messages API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        messages_client: MessagesClient | None = None,
        max_tokens: int = 1024,
        thinking: str = "off",
        effort: str = "",
    ) -> None:
        self.model = model
        self._max_tokens = max_tokens
        self._thinking = thinking_params(model, thinking, effort)
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
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if "effort" in self._thinking:
            output_config["effort"] = self._thinking["effort"]
        params: dict[str, Any] = {
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
            "output_config": output_config,
        }
        if "thinking" in self._thinking:
            params["thinking"] = self._thinking["thinking"]
        if self._thinking.get("thinking", {}).get("type") == "adaptive" or self.model.startswith(_ALWAYS_THINKING):
            params["max_tokens"] = max(params["max_tokens"], THINKING_MAX_TOKENS)
        return params

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
        # Every call is metered here, where every call passes: the ones that
        # worked, the ones refused, the ones whose answer could not be used.
        started = time.monotonic()
        try:
            response = await self._messages.create(**params)
        except Exception as exc:  # noqa: BLE001 - only to tell an account refusal apart
            outage = classify(getattr(exc, "status_code", None), str(exc))
            status = getattr(exc, "status_code", None)
            await ai_meter.record(
                model=self.model, usage=None, ok=False, started=started,
                error=outage.kind if outage is not None else f"http_{status}" if status else type(exc).__name__,
            )
            if outage is not None:
                # Our account, not this request: the caller pauses instead of failing.
                raise outage from None
            raise
        # The model that answered, as the provider names it (what its console shows).
        answered = str(getattr(response, "model", None) or self.model)
        billed = _billed(response)
        try:
            data = _parse_json(response)
            usage = _usage(response, self.model)
        except LLMError as exc:
            # Billed all the same: a refusal or an unusable answer still used tokens.
            await ai_meter.record(model=answered, usage=billed, ok=False, started=started, error=_kind(exc))
            raise
        await ai_meter.record(model=answered, usage=usage, ok=True, started=started)
        return LLMResult(data=data, usage=usage)


def client_for(settings: Any, role: str) -> AnthropicLLMClient:
    """The client for ``role`` ("vision" or "content"), per VISION_MODEL /
    CONTENT_MODEL (each falls back to LLM_MODEL)."""
    model = (settings.vision_model if role == "vision" else settings.content_model) or settings.llm_model
    return AnthropicLLMClient(
        api_key=settings.llm_api_key,
        model=model,
        thinking=settings.llm_thinking,
        effort=settings.llm_effort,
    )


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


def _kind(exc: LLMError) -> str:
    text = str(exc)
    if "refused" in text:
        return "refusal"
    if "JSON" in text or "text block" in text:
        return "unusable_answer"
    return "no_usage" if "usage" in text else "error"


def _billed(response: Any) -> Usage | None:
    """The tokens a response was billed for, whatever became of its content."""
    try:
        return _usage(response, "")
    except (LLMError, AttributeError, TypeError, ValueError):
        return None


def _one_hour_writes(usage: Any) -> int:
    split = getattr(usage, "cache_creation", None)
    if isinstance(split, dict):
        return int(split.get("ephemeral_1h_input_tokens") or 0)
    return int(getattr(split, "ephemeral_1h_input_tokens", 0) or 0) if split is not None else 0


def _usage(response: Any, model: str) -> Usage:
    usage = getattr(response, "usage", None)
    if usage is None:
        raise LLMError("model response carried no usage")
    return Usage(
        model=model,
        input_tokens=int(usage.input_tokens),
        output_tokens=int(usage.output_tokens),
        cache_creation_1h_input_tokens=_one_hour_writes(usage),
        cache_creation_input_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
    )
