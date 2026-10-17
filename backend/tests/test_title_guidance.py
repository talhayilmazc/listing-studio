"""Etsy's title guidance for the "Short, clear title" style (pipeline/search_rules.py,
pipeline/content.py, pipeline/generation.py, api/content.py).

The deterministic check after writing (fewer than 15 words, no repeated, subjective,
price/shipping or gift words, the item first), one rewrite and then a warning the
seller reads (the title is never cut), Etsy's tag limits (20 characters, never only
the title's words), and the seller copy (no performance claim anywhere).
Model calls are faked; nothing reaches Etsy or the AI provider.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.compliance.trademarks import Blocklist, compile_blocklist
from app.pipeline import search_rules as sr
from app.pipeline.content import (
    AnthropicContentGenerator,
    ContentValidationError,
    policy_for,
    tag_count_warning,
)
from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.templates import load_template
from app.pipeline.vision import VisionAnalysis
from tests.support import FakeMessages, fake_response
from tests.test_search_rules import INTENTS, OPENING, TAGS

PREFIX = "Comfort Colors®"
RULES = sr.SEARCH_TITLE
GOOD = "Funny Nurse Shirt, Flu Season Humor, Hand Washing Joke"
ANALYSIS = VisionAnalysis(
    theme="nurse", embedded_text="", style="retro lettering", colors=["red"], target_audience="nurses",
    product_type_hints=["t-shirt"], themes=["nurse", "flu season"], profession="nurse", season="winter",
)


# --- the rule check -----------------------------------------------------------------------------------


def test_fewer_than_15_words_counting_the_prefix_as_the_buyer_reads_it() -> None:
    assert RULES.max_words == sr.MAX_TITLE_WORDS == 14
    fourteen = f"{PREFIX} Funny Nurse Shirt, Flu Season Humor, Hand Washing Joke, Retro Red Lettering"
    assert sr.word_count(fourteen) == 14 and sr.rule_report(fourteen, RULES, PREFIX)["words"]
    assert not sr.rule_report(fourteen + " Style", RULES, PREFIX)["words"]
    assert sr.word_count("Salt & Pepper Shirt - Retro") == 4  # "&" and "-" are not words


@pytest.mark.parametrize(("title", "rule"), [
    ("Funny Nurse Shirt, Nurses Week Humor", "repeats"),  # plural counts as the same word
    ("Cute Nurse Shirt, Flu Season Humor", "subjective"),
    ("Perfect Nurse Shirt, Flu Season", "subjective"),
    ("Funny Nurse Shirt, Free Shipping", "commerce"),
    ("Funny Nurse Shirt, Flu Season Sale", "commerce"),
    ("Nurse Shirt 20% Off, Flu Season", "commerce"),
    ("Funny Nurse Shirt, Gift for Her", "gift"),
    ("Flu Season Humor, Funny Nurse Shirt", "item_first"),
])
def test_each_rule_is_checked_on_its_own(title: str, rule: str) -> None:
    rules = sr.title_rules(20, 140)  # these short examples are judged on the one rule each
    report = sr.rule_report(title, rules)
    assert report[rule] is False
    assert all(ok for name, ok in report.items() if name != rule), report
    if rule != "item_first":  # a judgement of order: measured, not a warning
        assert sr.title_warnings(title, rules)  # the seller is told


def test_a_title_meeting_the_guidance_passes_every_rule() -> None:
    assert all(sr.rule_report(f"{PREFIX} {GOOD}", RULES, PREFIX).values())
    assert sr.title_warnings(f"{PREFIX} {GOOD}", RULES, PREFIX) == []
    # The prefix's own words may come back; no other word may.
    assert sr.rule_report(f"{PREFIX} Comfort Food Shirt, Retro Diner Humor", RULES, PREFIX)["repeats"]


def test_the_warnings_are_plain_sentences_for_the_seller() -> None:
    long = "Cute Funny Nurse Shirt, Nurse Gift for Her, Free Shipping, Retro Lettering Winter Germs Hospital Staff Joke"
    text = " ".join(sr.title_warnings(long, RULES))
    assert "words; Etsy's guidance is fewer than 15" in text
    assert "repeats “nurse”" in text and "“cute”" in text and "“free shipping”" in text and "“gift”" in text
    over = "Funny Nurse Shirt, " + "Flu Season Humor " * 9
    assert "characters; the most is 140" in " ".join(sr.title_warnings(over.strip(), RULES))


# --- one rewrite, then a warning; never cut ------------------------------------------------------------


def _search_payload(title: str, tags: list[str] | None = None) -> dict[str, Any]:
    tags = list(TAGS if tags is None else tags)
    intents = (INTENTS * 2)[: len(tags)]
    return {"title_draft": title, "title_check": "", "title": title,
            "tags": [{"tag": t, "intent": i} for t, i in zip(tags, intents)], "opening": OPENING}


def _generator(responses: list[dict[str, Any]], trademarks: Blocklist | None = None) -> tuple[AnthropicContentGenerator, FakeMessages]:
    messages = FakeMessages([fake_response(r) for r in responses])
    client = AnthropicLLMClient(api_key="t", model="claude-sonnet-5-5", messages_client=messages)
    gen = AnthropicContentGenerator(
        client, template=load_template("content/apparel_search"), policy=policy_for("apparel"),
        title_prefix=PREFIX, trademarks=trademarks or Blocklist(()), title_rules=RULES,
    )
    return gen, messages


LONG = "Funny Nurse Shirt, Flu Season Humor, Hand Washing Joke, Retro Red Cream Lettering, Bold Colors"


async def test_a_title_breaking_a_rule_is_rewritten_once_and_the_rewrite_is_kept() -> None:
    gen, messages = _generator([_search_payload(LONG), _search_payload(GOOD)])
    result = await gen.generate(ANALYSIS, "BR1")
    assert result.attempts == 2 and result.warnings == [] and result.listing.title == f"{PREFIX} {GOOD}"
    retry = "\n".join(b["text"] for b in messages.calls[1]["messages"][0]["content"] if b["type"] == "text")
    assert "keep it to 14 or fewer" in retry


async def test_still_breaking_after_the_rewrite_keeps_the_title_as_written_with_a_warning() -> None:
    gen, messages = _generator([_search_payload(LONG), _search_payload(LONG)])
    result = await gen.generate(ANALYSIS, "BR1")
    assert len(messages.calls) == 2  # exactly one rewrite
    assert result.listing.title == f"{PREFIX} {LONG}"  # not cut: every phrase is still there
    assert any("Etsy's guidance is fewer than 15" in w for w in result.warnings)


async def test_a_title_over_140_characters_is_never_cut() -> None:
    over = "Funny Nurse Shirt, Flu Season Humor, Hand Washing Joke, Retro Red Cream Lettering on Heavyweight Garment Dyed Ringspun Cotton Fabric"
    gen, _ = _generator([_search_payload(over), _search_payload(over)])
    result = await gen.generate(ANALYSIS, "BR1")
    assert result.listing.title == f"{PREFIX} {over}" and len(result.listing.title) > 140
    assert any("the most is 140" in w for w in result.warnings)


async def test_a_short_tag_set_after_the_rewrite_says_how_many_are_missing() -> None:
    gen, _ = _generator([_search_payload(GOOD, TAGS[:12]), _search_payload(GOOD, TAGS[:12])])
    result = await gen.generate(ANALYSIS, "BR1")
    assert result.listing.tags == TAGS[:12] and result.warnings == [tag_count_warning(TAGS[:12])]
    assert "Add 1 before creating the draft" in result.warnings[0]


async def test_anything_but_the_title_guidance_still_fails_the_listing() -> None:
    marked = "Disney Nurse Shirt, Flu Season Humor"
    gen, _ = _generator([_search_payload(marked), _search_payload(marked)], trademarks=compile_blocklist(["disney"]))
    with pytest.raises(ContentValidationError):
        await gen.generate(ANALYSIS, "BR1")


async def test_replace_images_never_takes_a_title_with_warnings() -> None:
    gen, _ = _generator([_search_payload(LONG), _search_payload(LONG)])
    with pytest.raises(ContentValidationError):
        await gen.generate(ANALYSIS, "BR1", allow_warnings=False)


# --- tags ------------------------------------------------------------------------------------------------


def test_tags_follow_etsys_limits_and_never_only_repeat_the_title() -> None:
    title = f"{PREFIX} {GOOD}"
    candidates = ["funny nurse shirt", "a tag far over twenty characters", "comma, tag", *TAGS, "spare phrase"]
    tags, _ = sr.select_tags(title, candidates, ["x"] * len(candidates))
    assert len(tags) == 13 and all(len(t) <= 20 and "," not in t for t in tags)
    assert all(not (set(sr.keywords(t)) <= set(sr.keywords(title))) for t in tags)
    assert "gift for her" in tags  # shopper phrases live in the tags


def test_the_prompt_asks_for_fewer_than_15_words_and_shopper_phrases_in_tags() -> None:
    system = load_template("content/apparel_search").system
    assert "fewer than 15 words in all (14 at most)" in system
    assert "`gift for her`" in system and "nobody shortens it for you" in system
    assert "15 words in all" not in system.replace("fewer than 15 words in all", "")


# --- stored with the listing, shown on its card, gone once the seller edits ----------------------------


async def test_the_warning_is_stored_shown_and_cleared_when_the_seller_edits_the_title(async_sm, make_image) -> None:  # noqa: ANN001
    from app.api import content as content_api
    from app.api import schemas
    from app.db.models import Asset, GeneratedContent, ListingProfile, Tenant
    from app.pipeline.generation import TITLE_WARNINGS, generate_listing_content
    from tests.test_generation import VISION_DATA, _seed

    tenant_id, batch_id, asset_id, profile_id = await _seed(async_sm)
    vision = fake_response({**VISION_DATA, "themes": ["nurse", "flu season"], "embedded_text": ""})
    messages = FakeMessages([vision, fake_response(_search_payload(LONG)), fake_response(_search_payload(LONG))])
    client = AnthropicLLMClient(api_key="t", model="claude-sonnet-5-5", messages_client=messages)
    from app.pipeline.vision import AnthropicVisionAnalyzer

    gen = AnthropicContentGenerator(client, template=load_template("content/apparel_search"), policy=policy_for("apparel"),
                                    title_prefix=PREFIX, trademarks=Blocklist(()), title_rules=RULES)
    async with async_sm() as session:
        outcome = await generate_listing_content(
            session, tenant_id=tenant_id, batch_id=batch_id, asset_id=asset_id, image_data=make_image(400, 300),
            media_type="image/png", sku="BR1", analyzer=AnthropicVisionAnalyzer(client), generator=gen,
            profile=await session.get(ListingProfile, profile_id),
        )
    assert outcome.status == "generated"
    async with async_sm() as session:
        row = await session.get(GeneratedContent, outcome.generated_content_id)
        assert row.title == f"{PREFIX} {LONG}" and row.attributes[TITLE_WARNINGS]
        asset = await session.get(Asset, asset_id)
        shown = (await content_api.contents_out(session, [(row, asset)]))[0]
        assert any("fewer than 15" in w for w in shown.title_warnings)
        tenant = await session.get(Tenant, tenant_id)
        out = await content_api.update_content(row.id, schemas.ContentUpdate(title=f"{PREFIX} {GOOD}"), session, tenant)
        assert out.content.title_warnings == []
        assert TITLE_WARNINGS not in ((await session.get(GeneratedContent, row.id)).attributes or {})
    # Another account cannot reach it.
    async with async_sm() as session:
        other = Tenant(email=f"{uuid.uuid4()}@example.com", password_hash="x")
        session.add(other)
        await session.commit()
        with pytest.raises(Exception) as caught:
            await content_api.update_content(row.id, schemas.ContentUpdate(title="x"), session, other)
        assert getattr(caught.value, "status_code", None) == 404
