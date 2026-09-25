"""Separate vision/content models and per-model thinking (v7 §A2)."""

from __future__ import annotations

from app.pipeline.cost import CostCalculator
from app.pipeline.llm import THINKING_MAX_TOKENS, AnthropicLLMClient, Usage, client_for, thinking_params


def _params(model: str, **kw) -> dict:
    client = AnthropicLLMClient(api_key="k", model=model, messages_client=object(), **kw)
    return client.build_params(system="s", content_blocks=[{"type": "text", "text": "x"}], schema={"type": "object"})


def test_haiku_gets_no_thinking_or_effort_fields() -> None:
    p = _params("claude-haiku-4-5-20251001", thinking="adaptive", effort="low")
    assert "thinking" not in p and "effort" not in p["output_config"] and p["max_tokens"] == 1024


def test_sonnet_5_thinking_off_is_said_out_loud() -> None:
    # Omitting `thinking` on Sonnet 5 would run adaptive thinking inside a 1024-token cap.
    p = _params("claude-sonnet-5")
    assert p["thinking"] == {"type": "disabled"} and p["max_tokens"] == 1024


def test_adaptive_thinking_gets_room_and_effort() -> None:
    p = _params("claude-sonnet-5", thinking="adaptive", effort="low")
    assert p["thinking"] == {"type": "adaptive"}
    assert p["output_config"]["effort"] == "low" and p["max_tokens"] == THINKING_MAX_TOKENS


def test_models_that_always_think_never_get_disabled() -> None:
    assert thinking_params("claude-opus-5-5", "off") == {"effort": "low"}
    assert thinking_params("claude-fable-5-1", "off") == {"effort": "low"}


class _S:
    llm_api_key = "k"
    llm_model = "claude-haiku-4-5-20251001"
    vision_model = "claude-sonnet-5"
    content_model = ""
    llm_thinking = "off"
    llm_effort = ""


def test_vision_and_content_models_are_separate_settings() -> None:
    assert client_for(_S(), "vision").model == "claude-sonnet-5"
    assert client_for(_S(), "content").model == "claude-haiku-4-5-20251001"  # falls back to LLM_MODEL


def test_sonnet_5_is_priced() -> None:
    cost = CostCalculator().cost_for(Usage(model="claude-sonnet-5", input_tokens=1_000_000, output_tokens=100_000))
    assert str(cost) == "3.00"
