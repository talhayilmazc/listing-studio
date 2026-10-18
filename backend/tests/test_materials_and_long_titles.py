"""Part A: the long keyword style's titles are rewritten once and never cut, and the
reference listing's materials reach new drafts (pipeline/content.py,
pipeline/reference.py, etsy/publisher.py, api/profiles.py).

Model and Etsy calls are faked; nothing reaches Etsy or the AI provider.
"""

from __future__ import annotations

import pytest

from app.db.models import EtsyConnection, GeneratedContent, ListingProfile
from app.etsy.publisher import PublishImage, publish_content
from app.pipeline import search_rules as sr
from app.pipeline.content import AnthropicContentGenerator, ContentValidationError, policy_for
from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.reference import MAX_MATERIAL_LENGTH, MAX_MATERIALS, build_profile_payload, clean_materials
from app.pipeline.templates import load_template
from app.pipeline.vision import VisionAnalysis
from tests.support import FakeMessages, fake_response
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_publisher import CONFIG, REFERENCE, FakeEtsy, _seed

# --- long keyword titles: rewritten once, never cut -------------------------------------------------------

BASE = "Christmas Nurse Sweatshirt, Funny Nurse Holiday Tee, Nurse Christmas Gift, RN Xmas Crewneck, Winter Nursing Shirt"
LONG = BASE + ", Warm Hospital Staff Jumper"  # 141 characters
TAGS = ["shirt", "christmas nurse", *[f"holiday nurse tag{i}" for i in range(11)]]
ANALYSIS = VisionAnalysis(theme="christmas", themes=["christmas", "nurse"], embedded_text="", style="",
                          colors=[], target_audience="nurses", product_type_hints=[])


def _classic(*titles: str) -> tuple[AnthropicContentGenerator, FakeMessages]:
    messages = FakeMessages([fake_response({"title": t, "tags": TAGS, "description": "d"}) for t in titles])
    client = AnthropicLLMClient(api_key="t", model="claude-sonnet-5-5", messages_client=messages)
    return AnthropicContentGenerator(client, load_template("content/apparel"), policy=policy_for("apparel")), messages


def test_the_trim_is_gone() -> None:
    import app.pipeline.content as content

    assert not hasattr(content, "fit_title")


async def test_a_long_keyword_title_over_140_is_rewritten_once() -> None:
    assert len(LONG) == 141
    gen, messages = _classic(LONG, BASE)
    result = await gen.generate(ANALYSIS)
    assert result.attempts == 2 and result.listing.title == BASE and result.warnings == []
    retry = "\n".join(b["text"] for b in messages.calls[1]["messages"][0]["content"] if b["type"] == "text")
    assert "141" in retry  # told what was wrong, not trimmed


async def test_still_too_long_is_kept_as_written_and_flagged() -> None:
    gen, messages = _classic(LONG, LONG)
    result = await gen.generate(ANALYSIS)
    assert len(messages.calls) == 2
    assert result.listing.title == LONG  # every phrase still there
    assert result.warnings == ["The title is 141 characters; the most is 140. Shorten it before creating the draft."]


async def test_the_long_style_is_not_warned_about_the_short_styles_rules() -> None:
    # Long keyword titles repeat words and carry gift phrases on purpose; only length is a rule.
    assert sr.title_warnings(BASE, sr.LEGACY_TITLE) == []
    assert sr.title_warnings("Nurse Shirt", sr.LEGACY_TITLE) == [
        "The title is 11 characters; this profile asks for at least 110."
    ]


async def test_replace_images_still_refuses_a_flagged_long_title() -> None:
    gen, _ = _classic(LONG, LONG)
    with pytest.raises(ContentValidationError):
        await gen.generate(ANALYSIS, allow_warnings=False)


# --- materials ---------------------------------------------------------------------------------------------


def test_materials_are_cleaned_to_etsys_limits_and_never_invented() -> None:
    raw = ["Cotton", "cotton", "  Ring-spun   cotton ", "100% polyester", "", None, "x" * 60,
           *[f"thread {i}" for i in range(20)]]
    out = clean_materials(raw)
    assert out[:4] == ["Cotton", "Ring spun cotton", "100 polyester", "x" * MAX_MATERIAL_LENGTH]
    assert len(out) == MAX_MATERIALS and all(len(m) <= MAX_MATERIAL_LENGTH for m in out)
    assert clean_materials(None) == [] and clean_materials("cotton") == [] and clean_materials([]) == []


def test_the_reference_payload_carries_its_own_materials_or_none() -> None:
    with_them = build_profile_payload({"materials": ["Cotton", "Polyester"]}, {}, {"results": []})
    assert with_them["materials"] == ["Cotton", "Polyester"]
    assert build_profile_payload({}, {}, {"results": []})["materials"] == []


async def _draft(async_sm, reference: dict, readback_override: dict | None = None) -> FakeEtsy:  # noqa: ANN001
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy(readback_override=readback_override)
    async with async_sm() as s:
        await publish_content(
            s, job_id=job_id, content=await s.get(GeneratedContent, content_id), connection=await s.get(EtsyConnection, conn_id),
            sku="BR5475", thumbnail=PublishImage(b"thumb", "t.jpg"), extra_images=[], client=fake,
            access_token="tok", config=CONFIG, reference=reference, theme="", tenant_limit=2000,
        )
    return fake


async def test_the_draft_gets_the_references_materials(async_sm) -> None:  # noqa: ANN001
    fake = await _draft(async_sm, {**REFERENCE, "materials": ["Cotton", "Polyester", "cotton"]})
    assert fake.last_listing["materials"] == ["Cotton", "Polyester"]


async def test_no_materials_on_the_reference_means_none_on_the_draft(async_sm) -> None:  # noqa: ANN001
    fake = await _draft(async_sm, {**REFERENCE, "materials": []})
    assert "materials" not in fake.last_listing


async def test_a_payload_read_before_materials_were_copied_adds_none(async_sm) -> None:  # noqa: ANN001
    fake = await _draft(async_sm, dict(REFERENCE))
    assert "materials" not in fake.last_listing


async def test_materials_that_did_not_save_are_said(async_sm) -> None:  # noqa: ANN001
    with pytest.raises(ValueError, match="materials"):
        await _draft(async_sm, {**REFERENCE, "materials": ["Cotton"]}, readback_override={"materials": []})


async def test_the_profile_card_shows_materials_to_its_own_account_only(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        profile.cached_payload = {**(profile.cached_payload or {}), "materials": ["Cotton", "Polyester"]}
        other = await s.get(ListingProfile, world["pa2"])
        other.cached_payload = {**(other.cached_payload or {}), "materials": []}
        await s.commit()
    mine = (await world["a"].get(f"/api/profiles/{world['pa1']}")).json()
    assert mine["materials"] == ["Cotton", "Polyester"]
    assert (await world["a"].get(f"/api/profiles/{world['pa2']}")).json()["materials"] == []
    # A profile whose payload predates materials says so (None), not "none".
    assert (await world["b"].get(f"/api/profiles/{world['pb1']}")).json()["materials"] is None
    # Another account sees nothing of it.
    assert (await world["b"].get(f"/api/profiles/{world['pa1']}")).status_code == 404
    assert all(p["id"] != str(world["pa1"]) for p in (await world["b"].get("/api/profiles")).json())
