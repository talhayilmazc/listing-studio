"""The listing rules that follow Etsy's current search guidance (opt-in)."""

from __future__ import annotations

from typing import Any

from app.compliance.trademarks import Blocklist, compile_blocklist
from app.pipeline import search_rules as sr
from app.pipeline.attributes import resolve_optional_attributes
from app.pipeline.content import GeneratedListing, policy_for, theme_errors, validate_listing
from app.pipeline.reference import with_opening

RULES = sr.SEARCH_TITLE
NONE = Blocklist(())

TAGS = [
    "er nurse gift", "nurses week", "healthcare worker", "rn graduation gift", "sarcastic tee",
    "medical humor", "hospital staff", "coworker gift idea", "nursing student", "graphic tshirt",
    "winter germs", "retro lettering", "gift for her",
]
INTENTS = [
    "recipient", "occasion", "profession", "occasion", "humor", "humor", "profession", "recipient",
    "profession", "product", "season", "style", "recipient",
]
OPENING = (
    "A funny nurse shirt about flu season and the hand washing nobody else remembers. "
    "It makes an easy gift for an ER nurse, a nursing student or a coworker during nurses week."
)
OLD_TITLE = (
    "Funny Nurse Shirt, Flu Season Humor Tee, Hand Washing Nurse Gift, Healthcare Worker Humor, "
    "ER Nurse Sweatshirt, Nurses Week"
)


def listing(title: str = "Funny Nurse Shirt, Flu Season Humor, Hand Washing Joke", **kw: Any) -> GeneratedListing:
    base: dict[str, Any] = dict(
        title=title, tags=list(TAGS), description=OPENING, opening=OPENING, tag_intents=list(INTENTS),
        attributes={"theme": "nursing", "recipient": "nurse", "style": "retro"},
    )
    base.update(kw)
    return GeneratedListing(**base)


def check(item: GeneratedListing) -> list[str]:
    return validate_listing(item, policy_for("apparel"), trademarks=NONE, title_rules=RULES)


def test_a_clear_short_title_with_distinct_tags_passes() -> None:
    assert check(listing()) == []


def test_the_old_keyword_title_is_what_the_new_rules_reject() -> None:
    errors = " | ".join(check(listing(OLD_TITLE)))
    assert "exceeds 100 characters" in errors
    assert "6 phrases" in errors
    assert "fewer than 15" in errors
    assert "repeats 'humor', 'nurse'" in errors
    assert "names the product type 3 times" in errors


def test_the_legacy_rules_are_unchanged() -> None:
    item = GeneratedListing(title=OLD_TITLE, tags=[f"nurse tag {i}" for i in range(12)] + ["nurse shirt"], description="x")
    assert validate_listing(item, policy_for("apparel"), trademarks=NONE) == []


def test_opinion_and_sales_words_are_kept_out_of_the_title() -> None:
    errors = " | ".join(check(listing("Cute Nurse Shirt, Perfect Flu Season Humor, Free Shipping")))
    assert "'cute'" in errors and "'perfect'" in errors and "'free shipping'" in errors
    # "Best Dad" and "Cool Moms Club" are subjects, not opinions.
    assert sr.title_errors("Best Dad Ever Shirt, Cool Moms Club Humor", RULES) == []


def test_every_spelling_of_tshirt_is_one_product_word() -> None:
    assert sr.title_errors("Funny Nurse T-Shirt, Flu Season Humor", RULES) == []
    assert "repeats 'tshirt'" in " ".join(sr.title_errors("Nurse T-Shirt, Flu Season Tshirt", RULES))


def test_a_tag_that_only_repeats_the_title_is_rejected() -> None:
    tags = ["funny nurse shirt", "flu season", *TAGS[2:]]
    errors = " | ".join(check(listing(tags=tags)))
    assert "only repeat words already in the title" in errors
    assert "funny nurse shirt" in errors and "flu season" in errors
    # A tag that adds one new word to a title word is a new search.
    assert "er nurse gift" not in errors


def test_reordered_and_plural_tags_are_one_search() -> None:
    tags = ["gift for nurses", "nurse gift", *TAGS[2:]]
    assert "are the same search" in " | ".join(check(listing(tags=tags)))


def test_tags_must_be_phrases_and_cover_several_kinds_of_search() -> None:
    singles = ["nurse", "rn", "hospital", "medical", "gift", "tee", "winter", *TAGS[:6]]
    assert "only 6 tags are phrases" in " | ".join(check(listing(tags=singles)))
    narrow = ["profession"] * 12 + ["product"]
    assert "cover only 2 kinds of search" in " | ".join(check(listing(tag_intents=narrow)))


def test_the_opening_is_two_or_three_sentences_and_not_the_title() -> None:
    assert "1 sentence(s)" in " | ".join(check(listing(opening="A nurse shirt.", description="A nurse shirt.")))
    copied = "Funny Nurse Shirt, Flu Season Humor, Hand Washing Joke. It is great. Buy it."
    assert "copies the title" in " | ".join(check(listing(opening=copied, description=copied)))
    assert "write the opening" in " | ".join(check(listing(opening="", description="")))


def test_a_trademark_in_an_attribute_is_a_trademark_in_the_listing() -> None:
    marks = compile_blocklist(["disney"])
    item = listing(attributes={"theme": "Disney castle"})
    errors = validate_listing(item, policy_for("apparel"), trademarks=marks, title_rules=RULES)
    assert any("disney" in e.lower() for e in errors)


def test_a_second_theme_goes_in_the_tags_not_a_longer_title() -> None:
    item = listing("Christmas Sweatshirt, Funny Holiday Scrubs Humor")
    # Christmas is in the title, so the tags need not say it again; nurse is in the tags.
    assert theme_errors(item, ["christmas", "nurse"], NONE, in_title=False) == []
    item.tags = [t for t in item.tags if "nurs" not in t and "rn " not in t]
    item.attributes = {}
    errors = theme_errors(item, ["christmas", "nurse"], NONE, in_title=False)
    assert len(errors) == 1 and "'nurse' is nowhere in the listing" in errors[0]
    item.attributes = {"recipient": "nurse"}  # an attribute carries it too
    assert theme_errors(item, ["christmas", "nurse"], NONE, in_title=False) == []
    # The old rule still asks for it in the title.
    assert any("name 'nurse' in the title" in e for e in theme_errors(item, ["christmas", "nurse"], NONE))


def test_spare_tags_replace_the_ones_that_do_not_fit() -> None:
    title = "Funny Nurse Shirt, Flu Season Humor"
    candidates = ["nurse shirt", "er nurse gift", "pirate crew sweatshirt", "gift for nurses", "nurse gift",
                  "nurses week", "a,b", "  "]
    intents = ["product", "recipient", "product", "recipient", "recipient", "occasion", "subject", "subject"]
    tags, kinds = sr.select_tags(title, candidates, intents, count=3)
    # Dropped: only title words; 22 characters; (kept "gift for nurses"); its reorder "nurse gift".
    assert tags == ["er nurse gift", "gift for nurses", "nurses week"]
    assert kinds == ["recipient", "recipient", "occasion"]


def test_profile_bounds_stay_inside_etsys_limit() -> None:
    assert (sr.title_rules().min_length, sr.title_rules().max_length) == (40, 100)
    wide = sr.title_rules(60, 400)
    assert (wide.min_length, wide.max_length, wide.max_words) == (60, 140, 14)
    assert sr.title_rules(90, 50).min_length == 50  # never above its own maximum


def test_the_opening_goes_above_the_reference_body_which_is_kept_verbatim() -> None:
    reference = "Old Reference Title\nsecond title line\n\nSIZING\n- runs true to size\n\nCARE\nWash cold."
    out = with_opening(reference, OPENING)
    assert out == OPENING + "\n\nSIZING\n- runs true to size\n\nCARE\nWash cold."
    assert with_opening("SIZING: true to size", OPENING) == OPENING + "\n\nSIZING: true to size"
    assert with_opening("", OPENING) == OPENING


PROPS: list[dict[str, Any]] = [
    {"property_id": 1, "property_name": "Neckline", "is_required": True, "possible_values": [{"value_id": 10, "name": "Crew neck"}]},
    {"property_id": 2, "property_name": "Occasion", "possible_values": [{"value_id": 20, "name": "Birthday"}]},
    {"property_id": 3, "property_name": "Holiday", "possible_values": [{"value_id": 30, "name": "Christmas"}, {"value_id": 31, "name": "Independence Day"}]},
    {"property_id": 4, "property_name": "Primary color", "possible_values": [{"value_id": 40, "name": "Red"}]},
    {"property_id": 5, "property_name": "Recipient", "possible_values": []},
]


def test_optional_attributes_use_only_what_the_category_offers() -> None:
    proposed = {"holiday": "christmas", "primary_color": "Red", "occasion": "retirement", "style": "retro",
                "recipient": "nurse", "theme": ""}
    resolved, unmatched = resolve_optional_attributes(PROPS, proposed)
    assert [(a.property_name, a.value_ids, a.values) for a in resolved] == [
        ("Holiday", [30], ["Christmas"]),
        ("Primary color", [40], ["Red"]),
    ]
    # Offered by the category but not one of its values: reported, never guessed.
    # Style is not offered at all and Recipient is free text: both skipped silently.
    assert unmatched == ["Occasion: retirement"]


def test_optional_attributes_never_touch_what_is_already_set() -> None:
    resolved, _ = resolve_optional_attributes(PROPS, {"holiday": "Christmas"}, already_set={3})
    assert resolved == []
