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

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import anthropic  # here, not at the first call: importing it stops the event loop for a second or two


from app.core import db_release
from app.core import ai_meter, alerts
from app.core.llm_status import classify

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class ModelCapability:
    """How one model family takes the thinking setting. Each model accepts a
    different one, and a setting it does not accept is a 400 for every request."""

    #: What LLM_THINKING=off is on this model: the ``thinking`` value to send.
    #: None: nothing is sent (the model does not think unless asked, or cannot stop).
    off: dict[str, str] | None = None
    #: Thinking cannot be turned off: "off" is the lowest effort instead.
    always_thinks: bool = False
    #: Takes ``thinking: {"type": "adaptive"}``.
    adaptive: bool = True
    #: Takes ``output_config.effort``.
    effort: bool = True
    #: With thinking off, effort may be at most "high" (above it the API answers 400).
    off_needs_high_or_below: bool = False


_DISABLED = {"type": "disabled"}
#: Every model family this app knows, by the start of its id; **the longest match
#: wins**, so ``claude-sonnet-5-5`` is never read as ``claude-sonnet-5`` and a dated
#: snapshot follows its family. The one place a model's thinking setting is decided:
#: a new model is added here, with a test (tests/test_llm_models.py).
MODEL_CAPABILITIES: dict[str, ModelCapability] = {
    # Haiku 4.5 and older: no thinking unless given a token budget, and no effort.
    "claude-haiku": ModelCapability(off=None, adaptive=False, effort=False),
    # Haiku 5.5 thinks by default: off must be said, and only up to high effort.
    "claude-haiku-5-5": ModelCapability(off=_DISABLED, off_needs_high_or_below=True),
    # Sonnet 5 thinks when the field is left out: off is said explicitly.
    "claude-sonnet-5": ModelCapability(off=_DISABLED),
    # Sonnet 5.5 answers 400 to "disabled"; its lowest setting is "between_tools",
    # sent with no other field, and only up to high effort.
    "claude-sonnet-5-5": ModelCapability(off={"type": "between_tools"}, off_needs_high_or_below=True),
    "claude-opus-5": ModelCapability(off=_DISABLED, off_needs_high_or_below=True),
    # These always think; "disabled" is a 400. Off means low effort.
    "claude-opus-5-5": ModelCapability(always_thinks=True),
    "claude-fable": ModelCapability(always_thinks=True),
    "claude-mythos": ModelCapability(always_thinks=True),
}
#: The models listings are written with: run against the real API, and the ones
#: deploy/preflight.sh accepts for VISION_MODEL and CONTENT_MODEL.
SUPPORTED_MODELS = ("claude-sonnet-5-5", "claude-sonnet-5", "claude-haiku-5-5")
#: A model not listed (Opus 4.x, Sonnet 4.6, one newer than this file): said off
#: explicitly. If it refuses that, the call is made again without it (see
#: ``complete_json``), so an unknown model is slower and dearer, never stopped.
_UNLISTED = ModelCapability(off=_DISABLED)
#: Models whose thinking setting the provider refused in this process: they are
#: called without one from then on.
_REFUSED: set[str] = set()
#: Room for thinking plus the JSON answer; thinking counts against max_tokens.
THINKING_MAX_TOKENS = 8000
_ABOVE_HIGH = ("xhigh", "max")


def capability(model: str) -> ModelCapability:
    """The model's entry in :data:`MODEL_CAPABILITIES` (longest id prefix)."""
    matches = [name for name in MODEL_CAPABILITIES if model.startswith(name)]
    return MODEL_CAPABILITIES[max(matches, key=len)] if matches else _UNLISTED


def is_listed(model: str) -> bool:
    return any(model.startswith(name) for name in MODEL_CAPABILITIES)


def thinking_params(model: str, mode: str, effort: str = "") -> dict[str, Any]:
    """The ``thinking`` / ``output_config.effort`` request fields for this model."""
    can = capability(model)
    out: dict[str, Any] = {}
    if can.always_thinks:
        effort = effort or ("low" if mode != "adaptive" else "")
    elif mode == "adaptive":
        if can.adaptive:
            out["thinking"] = {"type": "adaptive"}
    elif can.off is not None:
        out["thinking"] = dict(can.off)
        if can.off_needs_high_or_below and effort in _ABOVE_HIGH:
            effort = "high"  # thinking off is what was asked for; it caps the effort
    if effort and can.effort:
        out["effort"] = effort
    return out


def _about_thinking(status: int | None, message: str) -> bool:
    """A 400 that names the thinking or effort setting: the request shape, not the content."""
    return status == 400 and bool(re.search(r"thinking|effort", message, re.IGNORECASE))


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
        self._always_thinks = capability(model).always_thinks
        if messages_client is not None:
            self._messages = messages_client
        else:
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
        # A model that refused its thinking setting is called without one.
        thinking = {} if self.model in _REFUSED else self._thinking
        if "effort" in thinking:
            output_config["effort"] = thinking["effort"]
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
        if "thinking" in thinking:
            params["thinking"] = thinking["thinking"]
        if thinking.get("thinking", {}).get("type") == "adaptive" or self._always_thinks or self.model in _REFUSED:
            params["max_tokens"] = max(params["max_tokens"], THINKING_MAX_TOKENS)
        return params

    async def _without_thinking_setting(self, exc: Exception, kwargs: dict[str, Any]) -> dict[str, Any]:
        """The provider refused the thinking setting this model was sent (a 400).

        That would fail every listing, with nothing wrong with any of them. So
        the call is made again with no thinking setting at all, which every
        model accepts (it then thinks as it does by default: slower and dearer,
        but the listing is written), the model is remembered for this process,
        and the operator is told once. Nothing is hidden: the refusal is logged
        and metered like any failed call.
        """
        first = self.model not in _REFUSED
        _REFUSED.add(self.model)
        if first:
            logger.error("the provider refused the thinking setting sent to %s: %s", self.model, str(exc)[:300])
            await alerts.send(
                f"Listyro: the AI model {self.model} refused the thinking setting this app sends "
                f"({self._thinking.get('thinking') or self._thinking or 'none'}). Listings are still being written, without a thinking "
                "setting, which is slower and costs more. Add the model to MODEL_CAPABILITIES (backend/app/pipeline/llm.py) "
                "or set VISION_MODEL / CONTENT_MODEL to a supported model."
            )
        return self.build_params(**kwargs)

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
        await db_release.before_slow()  # tens of seconds: no database connection held for them
        started = time.monotonic()
        try:
            try:
                response = await self._messages.create(**params)
            except Exception as exc:  # noqa: BLE001 - only to tell a refused setting apart
                status = getattr(exc, "status_code", None)
                if classify(status, str(exc)) is not None or not _about_thinking(status, str(exc)) or self.model in _REFUSED:
                    raise
                # The refused attempt is on record; the call goes again without the setting.
                await ai_meter.record(model=self.model, usage=None, ok=False, started=started, error="http_400_thinking")
                params = await self._without_thinking_setting(
                    exc, {"system": system, "content_blocks": content_blocks, "schema": schema, "max_tokens": max_tokens}
                )
                started = time.monotonic()
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


@lru_cache(maxsize=16)
def _shared_client(loop: asyncio.AbstractEventLoop, api_key: str, model: str, thinking: str, effort: str) -> AnthropicLLMClient:
    return AnthropicLLMClient(api_key=api_key, model=model, thinking=thinking, effort=effort)


def client_for(settings: Any, role: str) -> AnthropicLLMClient:
    """The client for ``role`` ("vision" or "content"), per VISION_MODEL /
    CONTENT_MODEL (each falls back to LLM_MODEL). One per event loop and model: making one builds a
    TLS context, about 20 ms of CPU, and a shared one keeps its connections to the provider."""
    model = (settings.vision_model if role == "vision" else settings.content_model) or settings.llm_model
    return _shared_client(asyncio.get_running_loop(), settings.llm_api_key, model, settings.llm_thinking, settings.llm_effort)


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
