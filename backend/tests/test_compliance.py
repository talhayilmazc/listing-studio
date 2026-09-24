"""Title and tag quality, and the trademark blocklist (docs/duzeltmeler-v6.md §B)."""

import os
import time

import pytest

from app.compliance.scanner import TRADEMARK, scan
from app.compliance.trademarks import compile_blocklist, configured_blocklist
from app.db.models import ComplianceSeverity
from app.pipeline.content import (
    AnthropicContentGenerator,
    ContentValidationError,
    GeneratedListing,
    copied_design_text,
    policy_for,
    validate_listing,
)
from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.templates import load_template
from app.pipeline.vision import VisionAnalysis
from tests.support import FakeMessages, fake_response

APPAREL = policy_for("apparel")
TAGS = ["funny nurse shirt", "nurse humor", "flu season", "nurse gift", "healthcare worker",
        "er nurse", "nurses week", "nursing student", "rn gift", "medical humor",
        "coworker gift", "hospital staff", "nurse tee"]
GOOD_TITLE = (
    "Funny Nurse Shirt, Flu Season Humor Tee, Hand Washing Nurse Gift, "
    "Healthcare Worker Humor, ER Nurse Sweatshirt, Nurses Week"
)


def _listing(title: str = GOOD_TITLE, tags: list[str] | None = None, description: str = "A tee.") -> GeneratedListing:
    return GeneratedListing(title, list(tags if tags is not None else TAGS), description)


# --- the blocklist ------------------------------------------------------------------
def test_blocklist_matches_whole_words_ignoring_case_and_accents() -> None:
    bl = compile_blocklist(["Disney", "Pokemon", "Star Wars", "Marvel", "Lilo and Stitch", "Spider-Man"])
    assert bl.find("DISNEY princess tee") == ["Disney"]
    assert bl.find("Pokémon trainer") == ["Pokemon"]
    assert bl.find("starwars fan") == ["Star Wars"]
    assert bl.find("star-wars fan") == ["Star Wars"]
    assert bl.find("Lilo & Stitch") == ["Lilo and Stitch"]
    assert bl.find("spiderman hoodie") == ["Spider-Man"]
    assert bl.find("a marvelous morning") == []  # whole words only
    assert bl.find("Disneyland") == []


def test_the_bundled_list_has_the_names_from_the_spec() -> None:
    bl = configured_blocklist()
    for name in ("Disney", "Mickey", "Marvel", "Nintendo", "Pokémon", "Star Wars",
                 "Harry Potter", "Barbie", "Nike", "Adidas"):
        assert bl.find(f"{name} shirt"), name
    # Blank-garment brands a seller names truthfully are not trademarks here.
    assert bl.find("Comfort Colors® Funny Nurse Shirt") == []


def test_a_trademark_is_rejected_in_title_tags_and_description() -> None:
    title = GOOD_TITLE.replace("Nurses Week", "Disney Fans")
    tags = ["mickey ears", *TAGS[1:]]
    errors = validate_listing(_listing(title, tags, "Great for a Marvel fan."))
    assert any("'Disney' from the title" in e for e in errors), errors
    assert any("'Mickey' from these tags" in e and "mickey ears" in e for e in errors), errors
    assert any("'Marvel' from the description" in e for e in errors), errors


def test_the_filter_off_lets_trademarks_through(test_settings) -> None:
    test_settings.trademark_filter = False
    title = GOOD_TITLE.replace("Nurses Week", "Disney Fans")
    assert validate_listing(_listing(title)) == []
    assert scan(title, TAGS, "d") == []


def test_the_list_file_is_extended_without_a_restart(tmp_path, test_settings) -> None:
    path = tmp_path / "marks.txt"
    path.write_text("# ours\nAcme\n", encoding="utf-8")
    test_settings.trademark_list_path = str(path)
    assert configured_blocklist().find("Acme Rocket tee") == ["Acme"]
    assert configured_blocklist().find("Nurse Humor tee") == []

    path.write_text("Acme\nNurse Humor\n", encoding="utf-8")
    later = time.time() + 5
    os.utime(path, (later, later))  # a new modification time, even on coarse clocks
    assert configured_blocklist().find("Nurse Humor tee") == ["Nurse Humor"]


# --- filler words -------------------------------------------------------------------
@pytest.mark.parametrize(
    "term", ["hand drawn", "hand-drawn", "handdrawn", "illustration", "artwork", "design tee", "graphic print"]
)
def test_filler_is_rejected_in_title_and_tags(term: str) -> None:
    title = GOOD_TITLE.replace("Nurses Week", term.title())
    tags = [term, *TAGS[1:]]
    errors = validate_listing(_listing(title, tags), APPAREL)
    assert any(f"remove '{term}' from the title" in e for e in errors), errors
    assert any(f"'{term}' has no search value" in e for e in errors), errors


# --- describe, don't transcribe -----------------------------------------------------
@pytest.mark.parametrize(
    ("printed", "title"),
    [
        # The two real titles that reached drafts.
        ("So is the flu — wash your hands", "So Is the Flu Wash Your Hands"),
        ("Deliver, Labor and Delivery", "Comfort Colors® Deliver, Labor And Delivery"),
    ],
)
def test_the_real_failures_count_as_copying(printed: str, title: str) -> None:
    assert copied_design_text(title, printed) is not None


def test_naming_the_job_is_not_copying() -> None:
    # Three words of the printed pun that are also what buyers search for.
    title = "Labor and Delivery Nurse Shirt, L&D Nurse Gift, Funny OB Nurse Tee, Delivery Nurse Humor"
    assert copied_design_text(title, "Deliver, Labor and Delivery") is None
    assert copied_design_text(GOOD_TITLE, "So is the flu — wash your hands") is None


FLU = VisionAnalysis(
    theme="nurse flu season joke",
    embedded_text="So is the flu — wash your hands",
    style="bold typographic",
    colors=["black"],
    target_audience="nurses",
    product_type_hints=["t-shirt"],
    meaning="nurse humor about flu season and hand washing",
    recipient="nurse",
    humor="nurse humor",
)


def _gen(messages: FakeMessages) -> AnthropicContentGenerator:
    client = AnthropicLLMClient(api_key="t", model="claude-haiku-4-5-20251001", messages_client=messages)
    return AnthropicContentGenerator(client, load_template("content/apparel"), policy=APPAREL)


def _payload(title: str, tags: list[str] = TAGS) -> dict:
    return {"title": title, "tags": tags, "description": "A funny tee for nurses."}


async def test_a_title_that_copies_the_design_is_regenerated_with_a_correction() -> None:
    copied = (
        "So Is the Flu Wash Your Hands Shirt, Funny Nurse Tee, Nurse Gift, Healthcare Worker Humor, "
        "Flu Season Sweatshirt"
    )
    assert 110 <= len(copied) <= 140
    messages = FakeMessages([fake_response(_payload(copied)), fake_response(_payload(GOOD_TITLE))])
    result = await _gen(messages).generate(FLU)

    assert result.attempts == 2
    assert result.listing.title == GOOD_TITLE
    retry = "\n".join(b["text"] for b in messages.calls[1]["messages"][0]["content"] if b["type"] == "text")
    assert "copies the words printed on the design" in retry
    assert "so is the flu wash your hands" in retry


async def test_the_prompt_gives_the_meaning_and_marks_the_text_as_context() -> None:
    messages = FakeMessages([fake_response(_payload(GOOD_TITLE))])
    await _gen(messages).generate(FLU)
    text = "\n".join(b["text"] for b in messages.calls[0]["messages"][0]["content"] if b["type"] == "text")
    system = str(messages.calls[0]["system"])
    assert "nurse humor about flu season and hand washing" in text
    assert "never copy it into the title or tags" in text
    # The real failures are in the prompt as counter-examples.
    assert "So Is the Flu Wash Your Hands" in system
    assert "Deliver, Labor And Delivery" in system


async def test_a_trademark_that_survives_the_retry_fails_the_listing() -> None:
    bad = GOOD_TITLE.replace("Nurses Week", "Disney Fans")
    messages = FakeMessages([fake_response(_payload(bad)), fake_response(_payload(bad))])
    with pytest.raises(ContentValidationError) as exc:
        await _gen(messages).generate(FLU)
    assert any("trademark 'Disney'" in e for e in exc.value.errors)


# --- the scanner --------------------------------------------------------------------
def test_the_scanner_reports_each_place_as_blocking() -> None:
    findings = scan("Nike Running Tee", ["barbie pink", "tee"], "Not affiliated with Adidas.")
    assert {f.rule for f in findings} == {TRADEMARK}
    assert all(f.severity is ComplianceSeverity.blocking for f in findings)
    assert [f.detail for f in findings] == [
        "trademark 'Nike' in the title",
        "trademark 'Barbie' in the tag 'barbie pink'",
        "trademark 'Adidas' in the description",
    ]
