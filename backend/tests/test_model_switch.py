"""Changing the model that writes listings (Sonnet 5 -> Sonnet 5.5, Haiku 5.5).

Each model takes a different thinking setting and answers 400 to one it does
not accept, on every request. So: one map says what each model is sent, a
refused setting is retried without it and reported (never a silent stop), each
model has its own price, and each listing and version records who wrote it.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.core import ai_meter, ai_prices
from app.core.config import Settings, set_settings_override
from app.core.llm_status import LLMUnavailable
from app.db.models import ContentVersion, GeneratedContent, ListingProfile, ListingPublication
from app.pipeline import llm, versions
from app.pipeline.content import AnthropicContentGenerator
from app.pipeline.generation import generate_listing_content
from app.pipeline.llm import SUPPORTED_MODELS, THINKING_MAX_TOKENS, AnthropicLLMClient, capability, thinking_params
from app.pipeline.vision import AnthropicVisionAnalyzer
from tests.support import FakeMessages, fake_response
from tests.test_generation import VISION_DATA, _content, _seed

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _no_model_is_remembered_as_refused():
    llm._REFUSED.clear()  # noqa: SLF001
    yield
    llm._REFUSED.clear()  # noqa: SLF001


def _body(model: str, **kw: Any) -> dict[str, Any]:
    client = AnthropicLLMClient(api_key="k", model=model, messages_client=object(), **kw)
    return client.build_params(system="s", content_blocks=[{"type": "text", "text": "x"}], schema={"type": "object"})


# --- what each model is sent ---------------------------------------------------------------


def test_sonnet_5_keeps_todays_request_thinking_disabled() -> None:
    body = _body("claude-sonnet-5")
    assert body["thinking"] == {"type": "disabled"}
    assert "effort" not in body["output_config"] and body["max_tokens"] == 1024


def test_sonnet_5_5_is_sent_between_tools_because_it_rejects_disabled() -> None:
    body = _body("claude-sonnet-5-5")
    assert body["thinking"] == {"type": "between_tools"}  # and no other field beside "type"
    assert "effort" not in body["output_config"] and body["max_tokens"] == 1024
    # A dated id is the same model, never read as Sonnet 5.
    assert _body("claude-sonnet-5-5-20261001")["thinking"] == {"type": "between_tools"}
    assert capability("claude-sonnet-5-5") is not capability("claude-sonnet-5")


def test_haiku_5_5_is_told_disabled_explicitly_because_it_thinks_by_default() -> None:
    body = _body("claude-haiku-5-5")
    assert body["thinking"] == {"type": "disabled"}
    assert "effort" not in body["output_config"] and body["max_tokens"] == 1024
    # The Haiku before it takes neither field, as before.
    old = _body("claude-haiku-4-5-20251001", thinking="adaptive", effort="low")
    assert "thinking" not in old and "effort" not in old["output_config"]


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-haiku-5-5"])
def test_thinking_off_caps_the_effort_these_models_would_refuse(model: str) -> None:
    # Off is only accepted up to high effort: xhigh or max with it is a 400.
    assert thinking_params(model, "off", "max")["effort"] == "high"
    assert thinking_params(model, "off", "xhigh")["effort"] == "high"
    assert thinking_params(model, "off", "low")["effort"] == "low"
    assert thinking_params(model, "adaptive", "low") == {"thinking": {"type": "adaptive"}, "effort": "low"}


def test_models_that_cannot_stop_thinking_are_never_sent_a_thinking_field() -> None:
    for model in ("claude-opus-5-5", "claude-fable-5-1", "claude-mythos-5-1"):
        assert thinking_params(model, "off") == {"effort": "low"}
        assert _body(model)["max_tokens"] == THINKING_MAX_TOKENS and "thinking" not in _body(model)


def test_every_supported_model_is_in_the_map_and_a_stranger_gets_the_explicit_off() -> None:
    assert SUPPORTED_MODELS == ("claude-sonnet-5-5", "claude-sonnet-5", "claude-haiku-5-5")
    assert all(llm.is_listed(m) for m in SUPPORTED_MODELS)
    assert not llm.is_listed("claude-next-9") and thinking_params("claude-next-9", "off") == {"thinking": {"type": "disabled"}}


# --- a refused thinking setting never silently stops generation ----------------------------------


class Refused(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class Provider:
    """Answers 400 to any request that carries a thinking setting, as a model would."""

    def __init__(self, message: str = '"thinking.type.disabled" is not supported for this model.') -> None:
        self.calls: list[dict[str, Any]] = []
        self.message = message

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if "thinking" in kwargs or "effort" in kwargs["output_config"]:
            raise Refused(self.message)
        return fake_response({"answer": "ok"})


@pytest.fixture()
def told(monkeypatch) -> list[str]:
    sent: list[str] = []

    async def send(text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(llm.alerts, "send", send)
    return sent


@pytest.fixture()
def metered(monkeypatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    async def record(**kwargs: Any) -> None:
        rows.append(kwargs)

    monkeypatch.setattr(ai_meter, "record", record)
    return rows


async def _ask(client: AnthropicLLMClient) -> dict[str, Any]:
    result = await client.complete_json(system="s", content_blocks=[{"type": "text", "text": "x"}], schema={"type": "object"})
    return result.data


async def test_a_refused_thinking_setting_is_retried_without_it_and_the_operator_is_told_once(told, metered) -> None:
    provider = Provider()
    client = AnthropicLLMClient(api_key="k", model="claude-next-9", messages_client=provider)

    assert await _ask(client) == {"answer": "ok"}  # the listing is still written
    first, second = provider.calls
    assert first["thinking"] == {"type": "disabled"}
    # The second request carries no thinking setting at all, with room for the model to think.
    assert "thinking" not in second and "effort" not in second["output_config"]
    assert second["max_tokens"] == THINKING_MAX_TOKENS and second["output_config"]["format"]["type"] == "json_schema"
    # Nothing is hidden: the refusal is a metered failed call, and the operator hears of it.
    assert [(r["ok"], r.get("error")) for r in metered] == [(False, "http_400_thinking"), (True, None)]
    assert len(told) == 1 and "claude-next-9" in told[0] and "MODEL_CAPABILITIES" in told[0]

    # From then on the model is called without the setting: one request, no second alert.
    assert await _ask(AnthropicLLMClient(api_key="k", model="claude-next-9", messages_client=provider)) == {"answer": "ok"}
    assert len(provider.calls) == 3 and "thinking" not in provider.calls[2] and len(told) == 1


async def test_an_effort_the_model_refuses_is_dropped_the_same_way(told, metered) -> None:
    provider = Provider("output_config.effort 'xhigh' is not supported when thinking is disabled on this model.")
    client = AnthropicLLMClient(api_key="k", model="claude-sonnet-5", messages_client=provider, effort="xhigh")
    assert await _ask(client) == {"answer": "ok"}
    assert "effort" in provider.calls[0]["output_config"] and "effort" not in provider.calls[1]["output_config"]


async def test_any_other_400_is_not_retried_and_fails_as_before(told, metered) -> None:
    class Other:
        calls = 0

        async def create(self, **kwargs: Any) -> Any:
            type(self).calls += 1
            raise Refused("messages.0.content.1.image.source.base64: image exceeds 5 MB maximum")

    with pytest.raises(Refused):
        await _ask(AnthropicLLMClient(api_key="k", model="claude-sonnet-5-5", messages_client=Other()))
    assert Other.calls == 1 and told == [] and "claude-sonnet-5-5" not in llm._REFUSED  # noqa: SLF001


async def test_our_account_being_refused_still_pauses_instead_of_being_retried(told, metered) -> None:
    class Limit:
        calls = 0

        async def create(self, **kwargs: Any) -> Any:
            type(self).calls += 1
            raise Refused("You have reached your specified API usage limits. You will regain access on 2026-11-01.")

    with pytest.raises(LLMUnavailable):
        await _ask(AnthropicLLMClient(api_key="k", model="claude-sonnet-5-5", messages_client=Limit()))
    assert Limit.calls == 1 and told == []


async def test_if_the_request_is_refused_without_the_setting_too_it_fails_loudly(told, metered) -> None:
    class Always:
        calls = 0

        async def create(self, **kwargs: Any) -> Any:
            type(self).calls += 1
            raise Refused("thinking is not supported")

    with pytest.raises(Refused):
        await _ask(AnthropicLLMClient(api_key="k", model="claude-next-9", messages_client=Always()))
    assert Always.calls == 2 and len(told) == 1  # tried once more, told once, then the error stands


# --- prices ---------------------------------------------------------------------------------------


def _usd(model: str, table=None, **tokens: int) -> Decimal:
    return ai_prices.cost(model, table or ai_prices.merged(None), **{"input_tokens": 0, "output_tokens": 0, **tokens})


def test_sonnet_5_5_has_its_own_price_and_sonnet_5_keeps_its_own() -> None:
    million = 1_000_000
    assert _usd("claude-sonnet-5-5", input_tokens=million) == 2
    assert _usd("claude-sonnet-5-5", output_tokens=million) == 10
    assert _usd("claude-sonnet-5-5", cache_write_tokens=million) == Decimal("2.5")
    assert _usd("claude-sonnet-5-5", cache_read_tokens=million) == Decimal("0.10")
    assert _usd("claude-sonnet-5", cache_read_tokens=million) == Decimal("0.20")  # unchanged
    assert _usd("claude-sonnet-5", input_tokens=million) == 2 and _usd("claude-sonnet-5", output_tokens=million) == 10
    # A dated Sonnet 5.5 id is priced as Sonnet 5.5, not as the shorter "claude-sonnet-5".
    assert _usd("claude-sonnet-5-5-20261001", cache_read_tokens=million) == Decimal("0.10")
    assert "platform.claude.com/docs/en/about-claude/pricing" in Path(ai_prices.__file__).read_text(encoding="utf-8")


def test_haiku_5_5_is_priced_by_the_length_of_the_prompt() -> None:
    # Up to 100K prompt tokens: 0.10 in, 0.50 out, 0.01 cache read, 0.125 cache write.
    assert _usd("claude-haiku-5-5", input_tokens=100_000, output_tokens=1_000) == Decimal("0.0105")
    assert _usd("claude-haiku-5-5", input_tokens=10_000, cache_read_tokens=80_000, cache_write_tokens=10_000) == Decimal("0.00305")
    # Over 100K (cached tokens count as prompt): 0.50 in, 2.50 out, 0.05 cache read.
    assert _usd("claude-haiku-5-5", input_tokens=100_001, output_tokens=1_000) == Decimal("0.0525005")
    assert _usd("claude-haiku-5-5", input_tokens=1, cache_read_tokens=100_000) == Decimal("0.0050005")
    # The admin panel changes the standard rates; the long-prompt card stays the published one.
    table = ai_prices.merged({"claude-haiku-5-5": {"input": "0.2", "output": "1", "cache_write": "0.25", "cache_write_1h": "0.4", "cache_read": "0.02"}})
    assert _usd("claude-haiku-5-5", table, input_tokens=1_000_000) == Decimal("0.5")  # one call of a million: long card
    assert _usd("claude-haiku-5-5", table, input_tokens=50_000) == Decimal("0.01")
    # Models with one rate card are unaffected by prompt length.
    assert _usd("claude-sonnet-5-5", input_tokens=500_000) == 1


# --- defaults and preflight -----------------------------------------------------------------------


def test_the_default_model_is_sonnet_5_5_everywhere_it_is_written_down() -> None:
    assert Settings.model_fields["llm_model"].default == "claude-sonnet-5-5"
    if not (ROOT / "deploy").exists():
        pytest.skip("deploy/ is not here (the dev container mounts only backend/)")
    production = (ROOT / "deploy" / "env.production.example").read_text(encoding="utf-8")
    for line in ("VISION_MODEL=claude-sonnet-5-5", "CONTENT_MODEL=claude-sonnet-5-5", "LLM_MODEL=claude-sonnet-5-5", "LLM_THINKING=off"):
        assert f"\n{line}\n" in production, line
    assert "\nLLM_MODEL=claude-sonnet-5-5\n" in (ROOT / ".env.example").read_text(encoding="utf-8")


def _preflight(tmp_path: Path, env_text: str) -> str:
    script = ROOT / "deploy" / "preflight.sh"
    if shutil.which("bash") is None or not script.exists():
        pytest.skip("no bash, or deploy/ is not here (the dev container mounts only backend/)")
    env_file = tmp_path / "production.env"
    env_file.write_text(env_text)
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "ENV_FILE": str(env_file), "COMPOSE_FILE": str(compose),
                   "OPS_ENV": str(tmp_path / "none.env"), "CREDS_DIR": str(tmp_path / "creds"), "BACKUP_DIR": str(tmp_path)}
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, env=environment, timeout=60).stdout


@pytest.mark.parametrize("model", SUPPORTED_MODELS)
def test_preflight_accepts_each_supported_model(tmp_path, model: str) -> None:
    out = _preflight(tmp_path, f"APP_ENV=production\nVISION_MODEL={model}\nCONTENT_MODEL={model}\nLLM_THINKING=off\n")
    assert f"  ok    VISION_MODEL is {model}" in out and f"  ok    CONTENT_MODEL is {model}" in out, out
    assert "not one of the supported models" not in out and "  ok    LLM_THINKING=off" in out


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-haiku-4-5-20251001", "claude-sonnet-4-6", "gpt-x"])
def test_preflight_warns_on_any_other_model_and_does_not_fail_on_it(tmp_path, model: str) -> None:
    out = _preflight(tmp_path, f"APP_ENV=production\nVISION_MODEL={model}\nCONTENT_MODEL=claude-sonnet-5-5\nLLM_THINKING=off\n")
    assert f"  warn  VISION_MODEL is {model}, which is not one of the supported models" in out, out
    assert f"  FAIL  VISION_MODEL" not in out and "  ok    CONTENT_MODEL is claude-sonnet-5-5" in out


def test_preflight_names_the_model_an_unset_line_falls_back_to(tmp_path) -> None:
    out = _preflight(tmp_path, "APP_ENV=production\nLLM_THINKING=off\n")
    assert "VISION_MODEL is not set; it falls back to LLM_MODEL (claude-sonnet-5-5)" in out
    script = (ROOT / "deploy" / "preflight.sh").read_text(encoding="utf-8")
    assert "claude-sonnet-5|claude-sonnet-5-5|claude-haiku-5-5)" in script  # the same three as SUPPORTED_MODELS
    assert set(SUPPORTED_MODELS) == {"claude-sonnet-5", "claude-sonnet-5-5", "claude-haiku-5-5"}


# --- who wrote it ------------------------------------------------------------------------------------


async def test_a_listing_records_the_model_that_read_the_design_and_the_one_that_wrote_it(async_sm, make_image) -> None:
    tenant_id, batch_id, asset_id, profile_id = await _seed(async_sm)
    messages = FakeMessages([fake_response(VISION_DATA), fake_response(_content([f"tag{i}" for i in range(13)]))])
    analyzer = AnthropicVisionAnalyzer(AnthropicLLMClient(api_key="t", model="claude-haiku-5-5", messages_client=messages))
    generator = AnthropicContentGenerator(AnthropicLLMClient(api_key="t", model="claude-sonnet-5-5", messages_client=messages))
    async with async_sm() as s:
        outcome = await generate_listing_content(
            s, tenant_id=tenant_id, batch_id=batch_id, asset_id=asset_id, image_data=make_image(400, 300),
            media_type="image/png", sku="SKU1", analyzer=analyzer, generator=generator, profile=await s.get(ListingProfile, profile_id),
        )
    assert outcome.status == "generated"
    assert [c["model"] for c in messages.calls] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert messages.calls[0]["thinking"] == {"type": "disabled"} and messages.calls[1]["thinking"] == {"type": "between_tools"}

    async with async_sm() as s:
        content = await s.get(GeneratedContent, outcome.generated_content_id)
        assert (content.vision_model, content.model_used) == ("claude-haiku-5-5", "claude-sonnet-5-5")

        # Each version of the text on Etsy says who wrote it.
        conn = (await s.get(ListingProfile, profile_id)).connection_id
        publication = ListingPublication(tenant_id=tenant_id, content_id=content.id, connection_id=conn, etsy_listing_id=4242, state="draft", title=content.title)
        s.add(publication)
        await s.flush()
        versions.drafted(s, publication, content, title=content.title, tags=content.tags, description=content.description, attributes={})
        await s.flush()
        await versions.sku_changed(s, publication, datetime.now(timezone.utc), "NEW-SKU")  # same text: same writers
        await versions.replaced(s, publication, datetime.now(timezone.utc), title="New", tags=["a"], description="d",
                                vision_model="claude-sonnet-5", content_model="claude-sonnet-5")
        await s.commit()
        rows = (await s.execute(select(ContentVersion).order_by(ContentVersion.created_at, ContentVersion.reason))).scalars().all()
        wrote = {r.reason: (r.vision_model, r.content_model) for r in rows}
        assert wrote == {
            "generated": ("claude-haiku-5-5", "claude-sonnet-5-5"),
            "sku_edited": ("claude-haiku-5-5", "claude-sonnet-5-5"),
            "replaced": ("claude-sonnet-5", "claude-sonnet-5"),
        }
        # A draft made with nothing to say who wrote it (no content) records nothing, not a guess.
        assert versions.drafted(s, publication, None, title="x", tags=[], description="", attributes={}).content_model is None


# --- the check an operator runs after changing the model ---------------------------------------------


async def test_llm_check_makes_one_vision_and_one_content_call_per_model_and_says_if_any_was_refused(test_settings, monkeypatch, told) -> None:
    from app import cli

    set_settings_override(test_settings.model_copy(update={
        "llm_api_key": "k", "llm_model": "claude-sonnet-5-5", "vision_model": "", "content_model": "", "llm_thinking": "off"}))
    asked: list[dict[str, Any]] = []

    class Accepting:
        async def create(self, **kwargs: Any) -> Any:
            asked.append(kwargs)
            data = VISION_DATA if any(b.get("type") == "image" for b in kwargs["messages"][0]["content"]) else {
                "title": "Retro Sun Plant Lady Shirt, Botanical Gardening Tee, Houseplant Lover T-Shirt, Green Thumb Graphic Top, Garden Club Tshirt",
                "tags": [f"plant tag {i}" for i in range(13)], "description": "A sun and two words."}
            return fake_response(data, input_tokens=1000, output_tokens=200)

    real = llm.AnthropicLLMClient
    monkeypatch.setattr(llm, "AnthropicLLMClient", lambda **kw: real(messages_client=Accepting(), **kw))
    monkeypatch.setattr(ai_meter, "UNSCOPED_WRITES", False)

    report, fine = await cli.llm_check(list(SUPPORTED_MODELS))
    assert fine and "Every call was accepted as sent." in report
    assert [a["model"] for a in asked if any(b.get("type") == "image" for b in a["messages"][0]["content"])] == list(SUPPORTED_MODELS)
    assert "claude-sonnet-5-5: thinking setting sent: {'type': 'between_tools'}" in report
    assert "claude-haiku-5-5: thinking setting sent: {'type': 'disabled'}" in report
    assert report.count("vision   ok") == 3 and report.count("content  ok") == 3 and "$" in report
    assert "k" != report and "llm_api_key" not in report

    # With no model named it checks what is configured: here, one model for both steps.
    asked.clear()
    report, fine = await cli.llm_check()
    assert fine and {a["model"] for a in asked} == {"claude-sonnet-5-5"}

    # A model that refuses its setting is reported as such, even though the calls went through.
    monkeypatch.setattr(llm, "AnthropicLLMClient", lambda **kw: real(messages_client=Provider(), **kw))
    report, fine = await cli.llm_check(["claude-next-9"])
    assert not fine and "REFUSED" in report and "not one of the supported models" in report
