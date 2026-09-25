"""v7 §A1 (several themes) and §A3 (a 141-character title is fitted, not failed)."""

from __future__ import annotations

from app.pipeline.content import (
    MAX_TITLE_LENGTH,
    MIN_TITLE_LENGTH,
    AnthropicContentGenerator,
    GeneratedListing,
    fit_title,
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


# --- A3: fitting a title into 110-140 -----------------------------------------------------
BASE = "Christmas Nurse Sweatshirt, Funny Nurse Holiday Tee, Nurse Christmas Gift, RN Xmas Crewneck, Winter Nursing Shirt"


def test_a_141_character_title_loses_its_last_phrase_not_the_listing() -> None:
    title = BASE + ", Warm Hospital Staff Jumper"
    assert len(title) == 141
    fitted = fit_title(title)
    assert fitted == BASE and MIN_TITLE_LENGTH <= len(fitted) <= MAX_TITLE_LENGTH


def test_when_dropping_goes_under_110_the_phrase_is_cut_word_by_word() -> None:
    short = "Christmas Nurse Sweatshirt, Funny Nurse Holiday Tee, Nurse Christmas Gift"  # 74
    long_tail = "Warm Winter Crewneck for Registered Nurses and Nursing Students Who Love the Holidays"
    fitted = fit_title(f"{short}, {long_tail}")
    assert MIN_TITLE_LENGTH <= len(fitted) <= MAX_TITLE_LENGTH
    assert fitted.startswith(short + ", Warm Winter Crewneck")
    assert not fitted.endswith(" ")


def test_the_prefix_is_kept_when_the_title_is_fitted() -> None:
    prefixed = "Comfort Colors® " + BASE + ", Hospital Xmas Top"
    assert len(prefixed) > MAX_TITLE_LENGTH
    fitted = fit_title(prefixed)
    assert fitted.startswith("Comfort Colors® Christmas Nurse") and len(fitted) <= MAX_TITLE_LENGTH


def test_a_title_that_cannot_fit_is_left_for_validation() -> None:
    impossible = "Word" * 40  # one 160-character word
    assert fit_title(impossible) == impossible
    assert fit_title(BASE) == BASE  # already in range: untouched


async def test_the_generator_no_longer_fails_a_title_one_character_over() -> None:
    title = BASE + ", Warm Hospital Staff Jumper"
    assert len(title) == 141
    msgs = FakeMessages([fake_response({"title": title, "tags": TAGS, "description": "d"})])
    client = AnthropicLLMClient(api_key="t", model="claude-haiku-4-5-20251001", messages_client=msgs)
    gen = AnthropicContentGenerator(client, load_template("content/apparel"), policy=APPAREL)
    analysis = VisionAnalysis(theme="christmas", themes=["christmas", "nurse"], embedded_text="", style="",
                              colors=[], target_audience="nurses", product_type_hints=[])
    result = await gen.generate(analysis)
    assert result.attempts == 1 and result.listing.title == BASE


def test_a_trademark_theme_is_never_demanded_in_the_title() -> None:
    # Vision may name the character a design shows; the title can never carry it.
    title = "Christmas Nurse Sweatshirt, Funny Nurse Holiday Tee, Nurse Christmas Gift, RN Xmas Crewneck, Winter Nursing Shirt"
    assert theme_errors(_listing(title), ["christmas", "disney", "nurse"]) == []
    assert theme_errors(_listing("Christmas Sweatshirt"), ["christmas", "minnie mouse"]) == []
