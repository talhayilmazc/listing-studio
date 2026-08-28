"""Content generator tests: validation, one-retry, and request shape."""

import pytest

from app.pipeline.content import (
    AnthropicContentGenerator,
    ContentValidationError,
    GeneratedListing,
    validate_listing,
)
from app.pipeline.vision import VisionAnalysis
from tests.support import VALID_TITLE, FakeMessages, fake_response
from app.pipeline.llm import AnthropicLLMClient

ANALYSIS = VisionAnalysis(
    theme="cozy autumn coffee",
    embedded_text="but first, coffee",
    style="hand-lettered",
    colors=["rust", "cream"],
    target_audience="coffee lovers",
    product_type_hints=["mug", "printable"],
)


def _tags(n: int) -> list[str]:
    return [f"tag{i}" for i in range(n)]


def _payload(title: str = VALID_TITLE, tags: list[str] | None = None) -> dict:
    return {
        "title": title,
        "tags": tags if tags is not None else _tags(13),
        "description": "A warm hand-lettered design. Instant digital download.",
    }


def _generator(messages: FakeMessages) -> AnthropicContentGenerator:
    client = AnthropicLLMClient(api_key="t", model="claude-haiku-4-5-20251001", messages_client=messages)
    return AnthropicContentGenerator(client)


# --- validate_listing unit tests -------------------------------------------
def test_valid_listing_has_no_errors() -> None:
    listing = GeneratedListing(VALID_TITLE, _tags(13), "A description.")
    assert validate_listing(listing) == []


@pytest.mark.parametrize(
    ("listing", "needle"),
    [
        (GeneratedListing("x" * 141, _tags(13), "d"), "title exceeds"),
        (GeneratedListing("t", _tags(12), "d"), "exactly 13 tags"),
        (GeneratedListing("t", _tags(14), "d"), "exactly 13 tags"),
        (GeneratedListing("t", ["x" * 21, *_tags(12)], "d"), "exceeds 20 characters"),
        (GeneratedListing("t", ["dup", "dup", *_tags(11)], "d"), "duplicate tag"),
        (GeneratedListing("t", ["a,b", *_tags(12)], "d"), "contains a comma"),
        (GeneratedListing("", _tags(13), "d"), "title is empty"),
        (GeneratedListing("t", _tags(13), "  "), "description is empty"),
    ],
)
def test_invalid_listings(listing: GeneratedListing, needle: str) -> None:
    errors = validate_listing(listing)
    assert any(needle in e for e in errors), errors


def test_duplicate_detection_is_case_insensitive() -> None:
    listing = GeneratedListing("t", ["Coffee", "coffee", *_tags(11)], "d")
    assert any("duplicate" in e for e in validate_listing(listing))


# --- generator behavior -----------------------------------------------------
async def test_generate_succeeds_first_try() -> None:
    gen = _generator(FakeMessages([fake_response(_payload())]))
    result = await gen.generate(ANALYSIS, sku="SKU1")
    assert result.attempts == 1
    assert len(result.usages) == 1
    assert len(result.listing.tags) == 13


async def test_generate_regenerates_once_then_succeeds() -> None:
    messages = FakeMessages(
        [
            fake_response(_payload(tags=_tags(12)), input_tokens=200, output_tokens=40),  # invalid
            fake_response(_payload(tags=_tags(13)), input_tokens=210, output_tokens=45),  # valid
        ]
    )
    gen = _generator(messages)

    result = await gen.generate(ANALYSIS, sku="SKU1")

    assert result.attempts == 2
    assert len(result.usages) == 2  # failed attempt still counted for cost
    assert len(messages.calls) == 2


async def test_retry_prompt_includes_specific_validation_errors() -> None:
    # First attempt: too few tags + a short title -> both should surface in the retry.
    short_title = "Too short"
    messages = FakeMessages(
        [
            fake_response(_payload(title=short_title, tags=_tags(12))),  # invalid
            fake_response(_payload(tags=_tags(13))),  # valid
        ]
    )
    gen = _generator(messages)

    result = await gen.generate(ANALYSIS, sku="SKU1")
    assert result.attempts == 2

    # The second call's user turn must spell out what to fix and quote the reject.
    blocks = messages.calls[1]["messages"][0]["content"]
    retry_text = "\n".join(b["text"] for b in blocks if b["type"] == "text")
    assert "REJECTED" in retry_text
    assert "at least 110" in retry_text  # title bound
    assert f"(yours was {len(short_title)})" in retry_text  # actual length
    assert "exactly 13 tags" in retry_text  # tag-count error
    assert short_title in retry_text  # previous output echoed back

    # The first call must NOT carry any correction block.
    first_text = "\n".join(
        b["text"] for b in messages.calls[0]["messages"][0]["content"] if b["type"] == "text"
    )
    assert "REJECTED" not in first_text


async def test_generate_fails_after_two_invalid_attempts() -> None:
    messages = FakeMessages(
        [fake_response(_payload(tags=_tags(11))), fake_response(_payload(tags=_tags(10)))]
    )
    gen = _generator(messages)

    with pytest.raises(ContentValidationError) as exc:
        await gen.generate(ANALYSIS, sku="SKU1")

    assert len(exc.value.usages) == 2
    assert any("exactly 13 tags" in e for e in exc.value.errors)
    assert len(messages.calls) == 2  # exactly one retry, no more


async def test_user_turn_includes_analysis_and_sku() -> None:
    messages = FakeMessages([fake_response(_payload())])
    gen = _generator(messages)
    await gen.generate(ANALYSIS, sku="SKU-42")

    user_text = messages.calls[0]["messages"][0]["content"][0]["text"]
    assert "cozy autumn coffee" in user_text
    assert "SKU-42" in user_text
    # System prompt is cached and does not carry the volatile analysis.
    assert messages.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "SKU-42" not in messages.calls[0]["system"][0]["text"]


# --- title length bounds (spec §3) -----------------------------------------
def test_title_below_110_is_rejected() -> None:
    listing = GeneratedListing("x" * 109, _tags(13), "A description.")
    assert any("at least 110" in e for e in validate_listing(listing))


def test_title_110_is_accepted() -> None:
    listing = GeneratedListing("x" * 110, _tags(13), "A description.")
    assert validate_listing(listing) == []


def test_title_135_is_accepted() -> None:
    listing = GeneratedListing("x" * 135, _tags(13), "A description.")
    assert validate_listing(listing) == []
