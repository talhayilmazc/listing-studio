"""Junk in AI-written text never reaches a saved listing (fix/onboarding-followups, Part A).

The two real samples are the content step's own answers for our example designs on 8 November 2026:
an opening sentence ending in ``}]`` and one ending in an invisible zero-width space. The fuzz set wraps
ordinary text in every residue pattern the sanitiser knows; ordinary seller text (brackets, quotes,
sizes) is left exactly as it is.
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from sqlalchemy import select

from app.db.models import (
    AuditLog, ConnectionStatus, EtsyConnection, GeneratedContent, ListingPublication, Tenant, UploadBatch, UploadBatchStatus,
)
from app.pipeline import ai_text, content, listing_fixes, listing_health
from app.pipeline.content import AnthropicContentGenerator, ContentValidationError, search_schema
from app.pipeline.vision import AnthropicVisionAnalyzer
from tests.support import VALID_TITLE, fake_client, fake_response

#: The content step's own answers, 8 November 2026 (our example designs; nothing of a seller's).
REAL_SAMPLES = [
    ("This tomato season t-shirt celebrates the joy of growing your own vegetables, with bold typographic art "
     "honoring backyard gardening pride. A fun pick for home gardeners who love showing off their summer harvest "
     "of homegrown tomatoes.}]",
     "This tomato season t-shirt celebrates the joy of growing your own vegetables, with bold typographic art "
     "honoring backyard gardening pride. A fun pick for home gardeners who love showing off their summer harvest "
     "of homegrown tomatoes."),
    ("Slow mornings club tee for anyone whose best part of the day starts whenever they're ready.​",
     "Slow mornings club tee for anyone whose best part of the day starts whenever they're ready."),
]

BASE = ["Retro Mountain Shirt, Sunset Hiking Tee", "A soft cotton tee with a faded sunset print.", "hiking gift"]
SUFFIXES = ["}]", "]}", "}", "]", '"}', '"]', '"}]', "```", "\n```", "​", "﻿", "⁠", "‍", " ‌ ", "\x07", '"']
PREFIXES = ["```json\n", "```", "[", "{", '"', "﻿", "​", '["']

#: Ordinary text a seller (or the model) means: never changed, never called junk.
ORDINARY = [
    "Sizes [S], [M] and [L] in stock",
    'The "Slow Mornings" print, in cream',
    "Tee (unisex), 100% cotton",
    "Line one.\n\nLine two: care instructions.\n- Wash cold\n- Hang dry",
    "Café au lait ☕ mug",
    "Kids' tee {age 4–6}",
    "Our cotton tee.\n\n[Size guide]\nS 34in · M 38in",
]


@pytest.mark.parametrize(("bad", "good"), REAL_SAMPLES)
def test_the_real_samples_come_out_clean(bad: str, good: str) -> None:
    assert ai_text.junk(bad)
    assert ai_text.clean(bad) == good
    assert ai_text.junk(ai_text.clean(bad)) == []


@pytest.mark.parametrize(("text", "suffix", "prefix"), list(itertools.product(BASE, SUFFIXES, ["", *PREFIXES]))[:300])
def test_every_residue_pattern_is_found_and_removed(text: str, suffix: str, prefix: str) -> None:
    dirty = prefix + text + suffix
    assert ai_text.junk(dirty), repr(dirty)
    assert ai_text.clean(dirty) == text, repr(dirty)


@pytest.mark.parametrize("text", ORDINARY)
def test_ordinary_text_is_left_alone(text: str) -> None:
    lines = "\n" in text
    assert ai_text.junk(text, lines=lines) == []
    assert ai_text.clean(text, lines=lines) == text


def test_whitespace_is_normalised_and_cleaning_twice_changes_nothing() -> None:
    assert ai_text.clean("  Retro   Tee \t") == "Retro Tee"
    assert ai_text.clean("One.\r\n\r\n\r\n\r\nTwo.  ", lines=True) == "One.\n\nTwo."
    for text in [*BASE, *ORDINARY, *(b for b, _ in REAL_SAMPLES)]:
        once = ai_text.clean(text, lines="\n" in text)
        assert ai_text.clean(once, lines="\n" in text) == once


def test_tags_are_cleaned_one_by_one_and_empty_or_repeated_ones_go() -> None:
    assert ai_text.clean_tags(["hiking gift}]", "​", "Hiking Gift", "trail tee​"]) == ["hiking gift", "trail tee"]


def test_residue_in_the_middle_is_not_cut_out_but_written_again() -> None:
    assert ai_text.leftover("Retro tee }] for hikers")
    assert ai_text.clean("Retro tee }] for hikers") == "Retro tee }] for hikers"
    listing = content.GeneratedListing(title="Retro tee }] for hikers", tags=["a"], description="x")
    assert content.junk_errors(listing)


# --- The source --------------------------------------------------------------------------------------
def test_the_opening_is_no_longer_the_last_string_of_the_answer() -> None:
    names = list(search_schema({})["properties"])
    assert names.index("opening") < names.index("tags")
    assert names[-1] == "tags"
    assert list(search_schema({"Primary color": ["Red"]})["properties"])[-1] == "attributes"


def test_every_content_prompt_asks_for_plain_text() -> None:
    from pathlib import Path

    for name in ("apparel", "apparel_search", "digital_products"):
        text = (Path(content.__file__).parent / "prompts" / "content" / name / "system.md").read_text(encoding="utf-8")
        assert "Every value is plain text" in text, name


def test_the_content_step_saves_clean_fields() -> None:
    listing = content._to_listing({  # noqa: SLF001
        "title": "Retro Mountain Shirt}]",
        "tags": [{"tag": "hiking gift​", "intent": "occasion"}, {"tag": "​", "intent": "occasion"}],
        "opening": REAL_SAMPLES[0][0],
        "attributes": {},
    })
    assert listing.title == "Retro Mountain Shirt"
    assert listing.tags == ["hiking gift"]
    assert listing.opening == REAL_SAMPLES[0][1] and listing.description == REAL_SAMPLES[0][1]
    classic = content._to_listing({"title": '"' + VALID_TITLE, "tags": ["a}]"], "description": "Body.\n\nMore.​"})  # noqa: SLF001
    assert classic.title == VALID_TITLE and classic.tags == ["a"] and classic.description == "Body.\n\nMore."


VISION = {"theme": "mountains", "embedded_text": "", "style": "retro", "colors": ["orange"], "target_audience": "hikers",
          "product_type_hints": ["printable"]}


async def _analysis():
    vision = await AnthropicVisionAnalyzer(fake_client([fake_response(VISION)])).analyze(b"x", "image/png")
    return vision.analysis


async def test_junk_left_in_the_middle_is_written_once_more_then_refused_never_saved() -> None:
    tags = [f"tag{i}" for i in range(13)]
    junky = {"title": VALID_TITLE[:60] + " }] " + VALID_TITLE[60:120], "tags": tags, "description": "A print."}
    clean = {"title": VALID_TITLE, "tags": tags, "description": "A print."}
    analysis = await _analysis()
    # Junk, then clean: the second write is kept.
    result = await AnthropicContentGenerator(fake_client([fake_response(junky), fake_response(clean)])).generate(analysis, "SKU1")
    assert result.attempts == 2 and result.listing.title == VALID_TITLE
    # Junk twice: refused with the reason (the listing is marked for the seller; nothing is saved).
    with pytest.raises(ContentValidationError) as refused:
        await AnthropicContentGenerator(fake_client([fake_response(junky), fake_response(junky)])).generate(analysis, "SKU1")
    assert any("leftover code characters" in e for e in refused.value.errors)


# --- scan-ai-text -----------------------------------------------------------------------------------
async def _seed(sm) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:  # noqa: ANN001
    from app.db.models import Asset, AssetStatus

    async with sm() as s:
        tenant = Tenant(email=f"{uuid.uuid4()}@example.com", password_hash="!")
        s.add(tenant)
        await s.flush()
        shop = EtsyConnection(tenant_id=tenant.id, status=ConnectionStatus.active, etsy_user_id=1, shop_id=1)
        batch = UploadBatch(tenant_id=tenant.id, status=UploadBatchStatus.ready, file_count=3)
        s.add_all([shop, batch])
        await s.flush()
        ids = []
        for i, (title, description) in enumerate([
            ("Tomato Season Shirt", REAL_SAMPLES[0][0]),  # junk, not sent
            ("Slow Mornings Shirt", REAL_SAMPLES[1][0]),  # junk, sent
            ("Kindness Shirt", "A clean description."),  # clean
        ]):
            asset = Asset(batch_id=batch.id, tenant_id=tenant.id, original_filename=f"{i}.png", status=AssetStatus.processed)
            s.add(asset)
            await s.flush()
            c = GeneratedContent(tenant_id=tenant.id, batch_id=batch.id, asset_id=asset.id, title=title, tags=["tag"], description=description)
            s.add(c)
            await s.flush()
            ids.append(c.id)
        s.add(ListingPublication(tenant_id=tenant.id, connection_id=shop.id, content_id=ids[1], etsy_listing_id=4242, state="draft"))
        await s.commit()
        return tenant.id, ids[0], ids[1], ids[2]


async def test_scan_lists_counts_and_ids_only_and_fixes_only_what_is_not_on_etsy(async_sm) -> None:  # noqa: ANN001
    from app.ops import ai_text_scan

    tenant_id, unsent, sent, fine = await _seed(async_sm)
    out = await ai_text_scan.run(async_sm, fix=False)
    assert f"account {tenant_id}: not yet sent 1, already sent to Etsy 1" in out
    assert str(unsent) in out and str(sent) in out and "Etsy 4242" in out and str(fine) not in out
    assert "tomatoes" not in out and "Slow" not in out  # no text
    async with async_sm() as s:
        assert (await s.get(GeneratedContent, unsent)).description == REAL_SAMPLES[0][0]  # read only
    fixed = await ai_text_scan.run(async_sm, fix=True)
    assert "Cleaned 1 not-yet-sent listing(s)" in fixed
    async with async_sm() as s:
        assert (await s.get(GeneratedContent, unsent)).description == REAL_SAMPLES[0][1]
        assert (await s.get(GeneratedContent, sent)).description == REAL_SAMPLES[1][0]  # on Etsy: never changed here
        audits = (await s.execute(select(AuditLog).where(AuditLog.action == "listing.ai_text_cleaned"))).scalars().all()
        assert len(audits) == 1
    again = await ai_text_scan.run(async_sm, fix=False)
    assert "not yet sent 0, already sent to Etsy 1" in again


# --- Listing health: "Fix now: description has stray characters" -------------------------------------
def test_listing_health_marks_stray_characters_fix_now_and_offers_the_cleaned_text() -> None:
    listing = {"title": "Slow Mornings Club Shirt", "tags": ["cozy tee"], "description": REAL_SAMPLES[1][0], "images": []}
    check = next(c for c in listing_health.score(listing).checks if c.id == "stray_characters")
    assert check.verdict == listing_health.FAIL and check.detail == "Description has stray characters."
    before, after = listing_fixes.stray(listing)
    assert before == {"description": REAL_SAMPLES[1][0]} and after == {"description": REAL_SAMPLES[1][1]}
    assert listing_fixes.CLEAN in listing_fixes.KINDS
    fine = {**listing, "description": "A clean one."}
    assert listing_fixes.stray(fine) is None
    assert next(c for c in listing_health.score(fine).checks if c.id == "stray_characters").verdict == listing_health.PASS


async def test_an_approved_clean_text_fix_writes_the_description_and_nothing_else() -> None:
    from app.workers import listing_fixes as W

    sent: list[dict] = []

    class Client:
        async def update_listing(self, shop_id, listing_id, *, updates, **kw):  # noqa: ANN001, ANN003
            sent.append(updates)

    current = {"title": "Slow Mornings Club Shirt", "tags": ["cozy tee"], "description": REAL_SAMPLES[1][0], "attributes": {}}
    wrote = await W._write(Client(), 1, 2, current, {"description": REAL_SAMPLES[1][1]}, {})  # noqa: SLF001
    assert wrote and sent == [{"description": REAL_SAMPLES[1][1]}]
    # Read back as sent: nothing reported as not kept.
    assert W._not_kept({**current, "description": REAL_SAMPLES[1][1]}, {"description": REAL_SAMPLES[1][1]}) == []  # noqa: SLF001
