"""v7 §A1 (several themes) and §A3 (a 141-character title is fitted, not failed)."""

from __future__ import annotations

from app.pipeline.content import (
    AnthropicContentGenerator,
    GeneratedListing,
    policy_for,
    theme_errors,
)
from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.templates import load_template
from app.pipeline.vision import VisionAnalysis, _to_analysis
from tests.support import FakeMessages, fake_response

APPAREL = policy_for("apparel")
TAGS = ["christmas nurse", "nurse sweatshirt", "rn christmas gift", "holiday nurse tee", "xmas scrubs",
        "nursing student", "er nurse gift", "winter sweatshirt", "christmas shirt", "funny nurse",
        "nurse holiday", "hospital xmas", "healthcare gift"]


# --- A1: the vision analysis carries every theme ------------------------------------------
def test_vision_returns_every_theme_most_dominant_first() -> None:
    a = _to_analysis({
        "themes": ["christmas", "nurse"], "embedded_text": "", "style": "flat", "colors": [],
        "target_audience": "nurses", "product_type_hints": [], "profession": "nurse",
        "season": "winter", "humor_type": "nurse humor", "occasion": "christmas",
    })
    assert a.themes == ["christmas", "nurse"] and a.theme == "christmas"
    assert (a.profession, a.season, a.humor) == ("nurse", "winter", "nurse humor")


def test_an_old_single_theme_answer_still_reads() -> None:
    a = _to_analysis({"theme": "fishing", "embedded_text": "", "style": "", "colors": [],
                      "target_audience": "", "product_type_hints": []})
    assert a.themes == ["fishing"] and a.theme == "fishing"


def _listing(title: str, tags=TAGS) -> GeneratedListing:
    return GeneratedListing(title, list(tags), "A cosy sweatshirt.")


def test_a_title_without_the_second_theme_is_rejected() -> None:
    wrong = "Christmas Sweatshirt, Holiday Graphic Tee, Winter Crewneck, Xmas Gift Idea, Festive Holiday Sweater Top"
    errors = theme_errors(_listing(wrong), ["christmas", "nurse"])
    assert any("second theme, 'nurse'" in e for e in errors), errors
    right = "Christmas Nurse Sweatshirt, Funny Nurse Holiday Tee, Nurse Christmas Gift, RN Xmas Crewneck, Winter Nursing Shirt"
    assert theme_errors(_listing(right), ["christmas", "nurse"]) == []


def test_inflections_count_and_tags_must_cover_both_themes() -> None:
    title = "Christmas Sweatshirt for Nursing Staff, Holiday Tee, Winter Crewneck, Xmas Gift, Festive Hospital Top"
    assert theme_errors(_listing(title), ["christmas", "nurse"]) == []  # "Nursing" names it
    no_nurse_tags = [t for t in TAGS if "nurs" not in t]
    no_nurse_tags += [f"xmas tag {i}" for i in range(13 - len(no_nurse_tags))]
    errors = theme_errors(_listing(title, no_nurse_tags), ["christmas", "nurse"])
    assert errors == ["add tags for the theme 'nurse': none of the tags names it"]


def test_one_theme_asks_for_nothing_more() -> None:
    assert theme_errors(_listing("Fishing Shirt"), ["fishing"]) == []
    assert theme_errors(_listing("Fishing Shirt"), ["fishing", "retro"]) == []  # a style, not a theme


async def test_the_generator_retries_a_title_that_drops_the_second_theme() -> None:
    wrong = "Christmas Sweatshirt, Holiday Graphic Tee, Winter Crewneck, Xmas Gift Idea, Festive Holiday Sweater Top"
    right = "Christmas Nurse Sweatshirt, Funny Nurse Holiday Tee, Nurse Christmas Gift, RN Xmas Crewneck, Winter Nursing Shirt"
    msgs = FakeMessages([
        fake_response({"title": wrong, "tags": TAGS, "description": "d"}),
        fake_response({"title": right, "tags": TAGS, "description": "d"}),
    ])
    client = AnthropicLLMClient(api_key="t", model="claude-haiku-4-5-20251001", messages_client=msgs)
    gen = AnthropicContentGenerator(client, load_template("content/apparel"), policy=APPAREL)
    analysis = VisionAnalysis(theme="christmas", themes=["christmas", "nurse"], embedded_text="", style="",
                              colors=[], target_audience="nurses", product_type_hints=[])
    result = await gen.generate(analysis)
    assert result.attempts == 2 and result.listing.title == right
    first = "\n".join(b["text"] for b in msgs.calls[0]["messages"][0]["content"] if b["type"] == "text")
    retry = "\n".join(b["text"] for b in msgs.calls[1]["messages"][0]["content"] if b["type"] == "text")
    assert "Themes, most dominant first: christmas, nurse" in first
    assert "second theme, 'nurse'" in retry


# --- A3: a long title is rewritten once, never cut (tests/test_classic_title.py) ---------------


def test_a_trademark_theme_is_never_demanded_in_the_title() -> None:
    # Vision may name the character a design shows; the title can never carry it.
    title = "Christmas Nurse Sweatshirt, Funny Nurse Holiday Tee, Nurse Christmas Gift, RN Xmas Crewneck, Winter Nursing Shirt"
    assert theme_errors(_listing(title), ["christmas", "disney", "nurse"]) == []
    assert theme_errors(_listing("Christmas Sweatshirt"), ["christmas", "minnie mouse"]) == []
