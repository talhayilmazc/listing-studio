"""Content generator tests: validation, one-retry, and request shape."""

import pytest

from app.pipeline.content import (
    AnthropicContentGenerator,
    ContentValidationError,
    GeneratedListing,
    policy_for,
    validate_listing,
)
from app.pipeline.vision import VisionAnalysis
from tests.support import VALID_TITLE, FakeMessages, fake_response
from app.pipeline.llm import AnthropicLLMClient

# A valid apparel title: 110-140 chars, names the product type ("Shirt"), no file words.
APPAREL_TITLE = (
    "Patriotic 4th of July American Flag Shirt Retro Distressed Eagle "
    "Gift for Men and Women Independence Day USA Patriot Apparel"
)

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
# --- profile-scoped content policy (spec §C) -------------------------------
APPAREL = policy_for("apparel")
DIGITAL = policy_for("digital_products")


def test_apparel_prompt_template_loads() -> None:
    from app.pipeline.templates import load_template

    tpl = load_template("content/apparel")
    assert "110 and 140" in tpl.system
    assert "digital download" in tpl.system.lower()  # names the banned words
    assert "$theme" in tpl.user


def test_apparel_policy_is_scoped_by_content_template() -> None:
    assert APPAREL.forbidden_terms and APPAREL.required_type_terms
    assert APPAREL.forbidden_tag_terms  # generic-adjective tags are banned (v4 §H)
    # Digital sellers legitimately use "digital download"/"SVG": no bans.
    assert DIGITAL.forbidden_terms == () and DIGITAL.required_type_terms == ()
    assert DIGITAL.forbidden_tag_terms == ()
    assert policy_for("unknown").forbidden_terms == ()


def test_apparel_valid_listing_passes() -> None:
    assert 110 <= len(APPAREL_TITLE) <= 140  # self-check the fixture
    tags = ["shirt", *[f"tag{i}" for i in range(12)]]
    assert validate_listing(GeneratedListing(APPAREL_TITLE, tags, "A comfy tee."), APPAREL) == []


def test_apparel_rejects_forbidden_word_in_title() -> None:
    title = APPAREL_TITLE[:120] + " SVG"  # inject a file word, keep length valid
    tags = ["shirt", *[f"tag{i}" for i in range(12)]]
    errors = validate_listing(GeneratedListing(title, tags, "d"), APPAREL)
    assert any("svg" in e.lower() and "title" in e for e in errors), errors


def test_apparel_rejects_forbidden_word_in_tags() -> None:
    tags = ["digital download", "shirt", *[f"tag{i}" for i in range(11)]]
    errors = validate_listing(GeneratedListing(APPAREL_TITLE, tags, "d"), APPAREL)
    assert any("digital download" in e and "tag" in e for e in errors), errors


def test_apparel_rejects_generic_design_tags() -> None:
    # "illustrated design" reached a real draft; the validator must now stop it.
    tags = ["illustrated design", "shirt", *[f"tag{i}" for i in range(11)]]
    errors = validate_listing(GeneratedListing(APPAREL_TITLE, tags, "d"), APPAREL)
    assert any("illustrated design" in e and "search value" in e for e in errors), errors


def test_apparel_generic_tags_are_case_insensitive() -> None:
    tags = ["Graphic Design", "shirt", *[f"tag{i}" for i in range(11)]]
    errors = validate_listing(GeneratedListing(APPAREL_TITLE, tags, "d"), APPAREL)
    assert any("graphic design" in e.lower() for e in errors), errors


def test_apparel_requires_product_type_in_title_and_tags() -> None:
    no_type_title = APPAREL_TITLE.replace("Shirt ", "")  # drop the only product word
    tags = [f"tag{i}" for i in range(13)]  # no product-type tag either
    errors = validate_listing(GeneratedListing(no_type_title, tags, "d"), APPAREL)
    assert any("title must name the product type" in e for e in errors), errors
    assert any("at least one tag must name the product type" in e for e in errors), errors


def test_digital_policy_allows_download_words() -> None:
    # VALID_TITLE contains "Printable" and "Digital Download" -> fine for a digital seller.
    tags = [f"tag{i}" for i in range(13)]
    assert validate_listing(GeneratedListing(VALID_TITLE, tags, "d"), DIGITAL) == []


def test_digital_policy_allows_generic_design_tags() -> None:
    # The generic-tag ban is apparel-scoped; digital sellers are unaffected.
    tags = ["graphic design", *[f"tag{i}" for i in range(12)]]
    assert validate_listing(GeneratedListing(VALID_TITLE, tags, "d"), DIGITAL) == []


async def test_generator_retry_carries_generic_tag_error() -> None:
    # A generic-adjective tag must fail validation and be corrected on the retry.
    bad_tags = ["illustrated design", "shirt", *[f"tag{i}" for i in range(11)]]
    good_tags = ["shirt", *[f"tag{i}" for i in range(12)]]
    messages = FakeMessages(
        [
            fake_response(_payload(title=APPAREL_TITLE, tags=bad_tags)),  # invalid
            fake_response(_payload(title=APPAREL_TITLE, tags=good_tags)),  # valid
        ]
    )
    client = AnthropicLLMClient(api_key="t", model="claude-haiku-4-5-20251001", messages_client=messages)
    gen = AnthropicContentGenerator(client, policy=APPAREL)

    result = await gen.generate(ANALYSIS, sku="SKU1")
    assert result.attempts == 2
    blocks = messages.calls[1]["messages"][0]["content"]
    retry_text = "\n".join(b["text"] for b in blocks if b["type"] == "text")
    assert "illustrated design" in retry_text.lower()


async def test_generator_retry_carries_forbidden_word_error() -> None:
    bad_tags = ["svg", "shirt", *[f"tag{i}" for i in range(11)]]  # 'svg' forbidden
    good_tags = ["shirt", *[f"tag{i}" for i in range(12)]]
    messages = FakeMessages(
        [
            fake_response(_payload(title=APPAREL_TITLE, tags=bad_tags)),  # invalid (svg)
            fake_response(_payload(title=APPAREL_TITLE, tags=good_tags)),  # valid
        ]
    )
    client = AnthropicLLMClient(api_key="t", model="claude-haiku-4-5-20251001", messages_client=messages)
    gen = AnthropicContentGenerator(client, policy=APPAREL)

    result = await gen.generate(ANALYSIS, sku="SKU1")
    assert result.attempts == 2
    blocks = messages.calls[1]["messages"][0]["content"]
    retry_text = "\n".join(b["text"] for b in blocks if b["type"] == "text")
    assert "svg" in retry_text.lower()  # the correction names the offending term


def test_title_below_110_is_rejected() -> None:
    listing = GeneratedListing("x" * 109, _tags(13), "A description.")
    assert any("at least 110" in e for e in validate_listing(listing))


def test_title_110_is_accepted() -> None:
    listing = GeneratedListing("x" * 110, _tags(13), "A description.")
    assert validate_listing(listing) == []


def test_title_135_is_accepted() -> None:
    listing = GeneratedListing("x" * 135, _tags(13), "A description.")
    assert validate_listing(listing) == []
