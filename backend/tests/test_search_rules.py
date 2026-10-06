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
    assert "6 phrases" in errors
    assert "19 words; keep it to 15 or fewer" in errors
    assert "remove 'gift' from the title" in errors
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


def test_a_tag_that_only_repeats_the_title_is_used_only_when_no_distinct_phrase_is_left() -> None:
    title = "Funny Nurse Shirt, Flu Season Humor"
    candidates = ["funny nurse shirt", "flu season", *TAGS]
    tags, _ = sr.select_tags(title, candidates, ["humor", "subject", *INTENTS])
    assert "funny nurse shirt" not in tags and "flu season" not in tags and tags == TAGS
    # Too few distinct phrases: the title's own phrase fills the 13th place.
    tags, _ = sr.select_tags(title, ["flu season", *TAGS[:12]], ["subject", *INTENTS[:12]])
    assert tags == [*TAGS[:12], "flu season"]


def test_reordered_and_plural_tags_are_one_search() -> None:
    tags = ["gift for nurses", "nurse gift", *TAGS[2:]]
    assert "are the same search" in " | ".join(check(listing(tags=tags)))


def test_tags_must_be_phrases_and_cover_several_kinds_of_search() -> None:
    singles = ["nurse", "rn", "hospital", "medical", "gift", "tee", "winter", *TAGS[:6]]
    assert "only 6 tags are phrases" in " | ".join(check(listing(tags=singles)))
    narrow = ["profession"] * 12 + ["product"]
    assert "cover only 2 kinds of search" in " | ".join(check(listing(tag_intents=narrow)))


def test_the_opening_is_one_or_two_sentences_and_not_the_title() -> None:
    assert check(listing(opening="A funny nurse shirt about flu season.", description="x")) == []
    three = "A nurse shirt. It is about flu. It is funny."
    assert "3 sentence(s)" in " | ".join(check(listing(opening=three, description=three)))
    copied = "Funny Nurse Shirt, Flu Season Humor, Hand Washing Joke. It is great."
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


def test_a_spare_tag_of_a_missing_kind_replaces_one_of_a_crowded_kind() -> None:
    tags = [f"subject tag {i}" for i in range(4)] + ["style a", "gift mom", "july 4th"] + ["tee one", "humor one"]
    kinds = ["subject"] * 4 + ["style", "recipient", "occasion", "product", "humor"]
    kept, kept_kinds = sr.select_tags("Nurse Shirt", tags, kinds, count=7)
    # Seven in order give four kinds; the spares bring product, in place of the last subject tag.
    assert kept == ["subject tag 0", "subject tag 1", "subject tag 2", "style a", "gift mom", "july 4th", "tee one"]
    assert len(set(kept_kinds)) == 5


def test_profile_bounds_stay_inside_etsys_limit() -> None:
    assert (sr.title_rules().min_length, sr.title_rules().max_length) == (40, 140)
    wide = sr.title_rules(60, 400)
    assert (wide.min_length, wide.max_length, wide.max_words) == (60, 140, 15)
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
    {"property_id": 6, "property_name": "Material", "possible_values": [{"value_id": 60, "name": "Cotton"}]},
]


def test_the_choices_are_etsys_own_lists_for_what_a_design_can_answer() -> None:
    from app.pipeline.attributes import attribute_choices

    # Not the required one, not the garment's own (Material), not one without a list.
    assert attribute_choices(PROPS) == {
        "Occasion": ["Birthday"],
        "Holiday": ["Christmas", "Independence Day"],
        "Primary color": ["Red"],
    }


def test_an_attribute_value_must_be_on_etsys_list() -> None:
    choices = {"Holiday": ["Christmas"], "Primary color": ["Red"]}
    assert sr.attribute_errors({"Holiday": "Christmas", "Primary color": "Red"}, choices) == []
    errors = " | ".join(sr.attribute_errors({"Holiday": "4th of July", "Theme": "patriotic"}, choices))
    assert "'4th of July' is not one of Etsy's values for Holiday" in errors
    assert "no 'Theme' attribute" in errors


def test_optional_attributes_are_looked_up_again_in_the_drafts_own_category() -> None:
    proposed = {"Holiday": "christmas", "Primary color": "Red", "Occasion": "Retirement", "Style": "Retro", "Neckline": "Crew neck"}
    resolved, unmatched = resolve_optional_attributes(PROPS, proposed)
    assert [(a.property_name, a.value_ids, a.values) for a in resolved] == [
        ("Holiday", [30], ["Christmas"]),
        ("Primary color", [40], ["Red"]),
    ]
    # Offered here but not with that value: reported, never guessed. A property this
    # category does not have (Style) and a required one (Neckline) are left alone.
    assert unmatched == ["Occasion: Retirement"]


def test_optional_attributes_never_touch_what_is_already_set() -> None:
    resolved, _ = resolve_optional_attributes(PROPS, {"Holiday": "Christmas"}, already_set={3})
    assert resolved == []


def test_filler_endings_and_a_second_garment_word_are_rejected() -> None:
    errors = " | ".join(sr.title_errors("Girls Trip Shirt, Friend Group Tee, Script Heart Design", RULES))
    assert "names the product type 2 times" in errors
    assert "ends in the filler word 'Design'" in errors
    assert sr.title_errors("Leopard Print Shirt, Retro Safari Style", RULES) == []
    assert "filler word 'Graphic'" in " ".join(sr.title_errors("Nurse Shirt, Castle Rainbow Graphic", RULES))


def test_tags_carry_no_opinion_and_do_not_restate_category_or_attributes() -> None:
    already = ["Clothing", "T-shirts", "Christmas"]
    errors = " | ".join(sr.tag_errors("Funny Nurse Shirt", ["cute nurse gift", "tshirt", "christmas", "best friend gift", "christmas party"], None, already))
    assert "opinion word 'cute'" in errors
    assert "'tshirt' only restates the category or attribute 'T-shirts'" in errors
    assert "'christmas' only restates the category or attribute 'Christmas'" in errors
    assert "best friend gift" not in errors and "christmas party" not in errors
    kept, _ = sr.select_tags("Funny Nurse Shirt", ["cute nurse gift", "tshirt", "er nurse gift", "hand drawn nurse"], [], already=already, banned=("hand drawn",))
    assert kept == ["er nurse gift"]


def test_a_profile_chooses_its_style_and_its_bounds() -> None:
    from types import SimpleNamespace as P

    from app.pipeline.content import bounds_for, content_template_for, search_style

    classic = P(listing_style="classic", content_template="apparel", title_min_length=None, title_max_length=None, cached_payload={})
    assert content_template_for(classic) == "content/apparel" and search_style(classic) == {}
    assert (bounds_for(classic).min_length, bounds_for(classic).max_length) == (110, 140)
    new = P(listing_style="search", content_template="apparel", title_min_length=50, title_max_length=None,
            cached_payload={"category_attributes": {"Holiday": ["Christmas"]}, "category_names": ["Clothing", "T-shirts"]})
    assert content_template_for(new) == "content/apparel_search"
    style = search_style(new)
    assert (style["title_rules"].min_length, style["title_rules"].max_length, style["title_rules"].readable) == (50, 140, True)
    assert style["attribute_choices"] == {"Holiday": ["Christmas"]} and style["category_names"] == ["Clothing", "T-shirts"]
    # A seller's edit is held to the length only, never to the writing rules.
    assert bounds_for(new).readable is False and bounds_for(new).min_length == 50
    # Digital profiles have no search prompt: they stay classic whatever is stored.
    digital = P(listing_style="search", content_template="digital_products", title_min_length=None, title_max_length=None, cached_payload={})
    assert content_template_for(digital) == "content/digital_products" and search_style(digital) == {}
