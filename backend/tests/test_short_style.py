"""Part C: "Etsy recommended (short)" titles, Etsy's tag rules, every known attribute.

docs/etsy-growth-research.md has the guidance and its sources.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import EtsyConnection, GeneratedContent, ListingProfile
from app.etsy.publisher import PublishImage, publish_content
from app.pipeline import search_rules as sr
from app.pipeline.attribute_fill import fill_design_attributes, garment_attributes
from tests.test_publisher import CONFIG, REFERENCE, FakeEtsy, _seed

RULES = sr.SEARCH_TITLE
PREFIX = "Comfort Colors®"


def test_the_short_title_is_at_most_15_words_and_140_characters_prefix_included() -> None:
    assert (RULES.max_words, RULES.max_length, RULES.min_phrases) == (15, 140, 1)
    fifteen = f"{PREFIX} Funny Nurse Shirt, Flu Season Humor, Hand Washing Joke, Retro Red Blue Lettering"
    assert len(fifteen.split()) == 15 and sr.title_errors(fifteen, RULES, prefix=PREFIX) == []
    sixteen = fifteen + " Style"
    assert "16 words; keep it to 15 or fewer" in " ".join(sr.title_errors(sixteen, RULES, prefix=PREFIX))
    # One clear phrase is enough.
    assert sr.title_errors(f"{PREFIX} Funny Nurse Shirt", RULES, prefix=PREFIX) == []


def test_a_word_of_the_prefix_may_appear_again_but_no_other_word_may() -> None:
    title = f"{PREFIX} Comfort Food Shirt, Retro Diner Humor"
    assert sr.title_errors(title, RULES, prefix=PREFIX) == []
    assert "repeats 'diner'" in " ".join(sr.title_errors(f"{PREFIX} Diner Shirt, Retro Diner", RULES, prefix=PREFIX))
    # Without the prefix argument "comfort" counts twice.
    assert "repeats 'comfort'" in " ".join(sr.title_errors(title, RULES))


@pytest.mark.parametrize(
    "title, word",
    [
        ("Funny Nurse Shirt, Gift for Her", "gift"),
        ("Funny Nurse Shirt, Nurse Week Present", "present"),
        ("Retro Camping Sweatshirt for Dad", "for dad"),
        ("Christmas Sweatshirt, Stocking Stuffer", "stocking stuffer"),
        ("Perfect Teacher Shirt, Back to School", "perfect"),
        ("Teacher Shirt, Free Shipping", "free shipping"),
    ],
)
def test_subjective_gift_and_sale_words_stay_out_of_the_title(title: str, word: str) -> None:
    assert f"'{word}'" in " ".join(sr.title_errors(title, RULES))


def test_a_recipient_or_profession_that_defines_the_item_is_allowed() -> None:
    assert sr.title_errors("Nurse Shirt, Flu Season Humor", RULES) == []
    assert sr.title_errors("New Dad Sweatshirt, Retro Lettering", RULES) == []


CATEGORY = ["Clothing", "T-shirts"]
ATTRS = {"Holiday": "Christmas", "Primary color": "Red"}


def test_tags_follow_etsys_rules_deterministically() -> None:
    title = "Christmas Nurse Shirt, Funny Holiday Scrubs"
    candidates = [
        "t-shirts",               # the category again
        "christmas",              # an attribute value again
        "nurse shirt",            # only the title's words: kept back
        "nurse gift for her", "nurse gifts for her",  # plural of the same root: one search
        "a tag much too long for etsy",               # over 20 characters
        "cute nurse tee",                             # an opinion word
        "er nurse tee", "xmas scrubs", "rn christmas gift", "nursing student", "winter germs",
        "coworker gift", "healthcare worker", "secret santa gift", "hospital party",
        "graphic tshirt", "ugly sweater party", "medical humor", "red holiday tee",
    ]
    tags, _ = sr.select_tags(title, candidates, ["x"] * len(candidates), already=[*CATEGORY, *ATTRS.values()])
    assert len(tags) == 13 and all(len(t) <= 20 for t in tags)
    assert "t-shirts" not in tags and "christmas" not in tags and "cute nurse tee" not in tags
    assert "nurse gift for her" in tags and "nurse gifts for her" not in tags
    assert "nurse shirt" not in tags  # a distinct phrase was available
    assert len({frozenset(sr.keywords(t)) for t in tags}) == 13  # no two share their root words


def test_a_tag_that_repeats_the_title_fills_a_place_only_when_nothing_better_is_left() -> None:
    title = "Christmas Nurse Shirt, Funny Holiday Scrubs"
    distinct = [f"distinct tag {n}" for n in range(12)]
    tags, _ = sr.select_tags(title, ["nurse shirt", *distinct], ["x"] * 13)
    assert tags == [*distinct, "nurse shirt"]


CHOICES = {
    "Primary color": ["Beige", "Black", "Blue", "Gray", "Green", "Red", "White"],
    "Secondary color": ["Beige", "Black", "Blue", "Gray", "Green", "Red", "White"],
    "Holiday": ["Christmas", "Independence Day", "Valentine's Day"],
    "Occasion": ["Birthday", "Graduation", "Retirement"],
    "Theme": ["Camping", "Nursing", "Patriotic"],
    "Style": ["Boho", "Minimalist", "Retro", "Vintage"],
}


def _analysis(**kw: object) -> SimpleNamespace:
    base: dict[str, object] = dict(
        colors=["navy blue", "cream", "red"], occasion="4th of july", season="summer",
        themes=["patriotic", "eagle"], theme="patriotic", profession="", style="retro 70s lettering",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_every_attribute_the_design_shows_is_filled_from_etsys_lists() -> None:
    attrs, source = fill_design_attributes({}, CHOICES, _analysis())
    assert attrs == {
        "Primary color": "Blue", "Secondary color": "Beige", "Holiday": "Independence Day",
        "Theme": "Patriotic", "Style": "Retro",
    }
    assert set(source.values()) == {"vision"}
    # Occasion: the analysis names a holiday, not a birthday or graduation, so it stays empty.
    assert "Occasion" not in attrs


def test_nothing_is_guessed_and_the_models_choice_is_kept() -> None:
    attrs, source = fill_design_attributes(
        {"Theme": "Camping"}, CHOICES, _analysis(colors=["teal", "chartreuse"], occasion="", season="", style="flat vector")
    )
    assert attrs == {"Theme": "Camping"} and source == {"Theme": "model"}  # teal: blue or green? left empty


PROPS = [
    {"property_id": 1, "property_name": "Sleeve length", "possible_values": [{"value_id": 10, "name": "Short sleeve"}]},
    {"property_id": 2, "property_name": "Neckline", "possible_values": [{"value_id": 20, "name": "Crew neck"}]},
    {"property_id": 3, "property_name": "Material", "possible_values": [{"value_id": 30, "name": "Cotton"}]},
]


def test_garment_attributes_come_from_the_profile_or_what_the_mockup_shows() -> None:
    reference = [{"property_id": 1, "value_ids": [10], "values": ["Short sleeve"]}]
    out = garment_attributes(PROPS, reference, {"neckline": "crew neck"}, taken=set())
    assert [(a.property_name, a.values) for a in out] == [("Sleeve length", ["Short sleeve"]), ("Neckline", ["Crew neck"])]
    # Material is neither in the profile nor visible: not written.
    assert garment_attributes(PROPS, [], {"neckline": "v-neck"}, taken=set()) == []


async def test_a_draft_reports_every_attribute_it_was_given(async_sm: async_sessionmaker) -> None:
    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy(properties={"results": [
        {"property_id": 100, "property_name": "Neckline", "is_required": True,
         "possible_values": [{"value_id": 11, "name": "Crew Neck"}]},
        {"property_id": 101, "property_name": "Sleeve length", "possible_values": [{"value_id": 12, "name": "Short sleeve"}]},
        {"property_id": 102, "property_name": "Holiday", "possible_values": [{"value_id": 13, "name": "Christmas"}]},
    ]})
    reference = {**REFERENCE, "attributes": [
        {"property_id": 100, "value_ids": [11], "values": ["Crew Neck"]},
        {"property_id": 101, "value_ids": [12], "values": ["Short sleeve"]},
    ]}
    async with async_sm() as s:
        result = await publish_content(
            s, job_id=job_id, content=await s.get(GeneratedContent, content_id),
            connection=await s.get(EtsyConnection, conn_id), sku="BR5475",
            thumbnail=PublishImage(b"t", "t.jpg"), client=fake, access_token="tok", config=CONFIG,
            reference=reference, theme="x", tenant_limit=2000, optional_attributes={"Holiday": "Christmas"},
        )
    assert result.attributes == {"Neckline": "Crew Neck", "Sleeve length": "Short sleeve", "Holiday": "Christmas"}
    assert [p["property_id"] for p in fake.properties_set] == [100, 101, 102]


async def test_new_profiles_start_short_and_existing_ones_keep_their_style(async_sm: async_sessionmaker) -> None:
    tenant_id, conn_id, _, _ = await _seed(async_sm)
    async with async_sm() as s:
        old = (await s.execute(select(ListingProfile))).scalars().first()
        new = ListingProfile(tenant_id=tenant_id, connection_id=conn_id, name="New", reference_listing_id=9,
                             content_template="apparel")
        s.add(new)
        await s.commit()
        await s.refresh(new)
        assert new.listing_style == "search"
        if old is not None:  # seeded with the style it was given
            assert old.listing_style in ("classic", "search")


def test_the_offline_style_check_runs_on_the_synthetic_set() -> None:
    from evals.style_check import run

    report = run()
    assert report["old"]["designs"] == report["new"]["designs"] == 20
    new = report["new"]
    assert new["titles_over_15_words"] == 0 and new["titles_with_subjective_or_gift_word"] == 0
    assert new["tag_attribute_or_category_dupes"] == new["tag_root_dupes"] == new["tags_only_title_words"] == 0
    assert new["listings_with_13_tags"] == 20
