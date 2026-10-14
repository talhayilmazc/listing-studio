"""The search-readiness checklist on the review card (pipeline/checklist.py).

* Each check passes, warns or fails from the listing's own text and settings.
* No check contradicts the profile's style: a listing the app writes correctly
  for a classic or a search profile gets no warning for following that style.
* It reuses the existing rules: the checklist and validate_listing agree.
* It makes no Etsy call and no model call, queues nothing and stores nothing.
* It is on the content endpoints for AI-written and write-it-yourself listings,
  follows unsaved text, and never blocks approval.
* Its words promise nothing: no score, rank or boost.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.compliance.trademarks import Blocklist, compile_blocklist
from app.db.models import AiCall, ComplianceFinding, GeneratedContent, ListingProfile
from app.etsy.api import EtsyApiClient
from app.pipeline import checklist
from app.pipeline.checklist import FAIL, PASS, WARN, OptionState, build
from app.pipeline.content import GeneratedListing, policy_for, validate_listing
from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.reference import replace_title_block, with_opening
from app.pipeline.search_rules import LEGACY_TITLE, SEARCH_TITLE, TitleRules, title_rules
from tests.test_multi_shop import BODY, PREFIX_A, world  # noqa: F401  (fixture)

SHORT = TitleRules(40, 140)
TITLE = "Frog Reading Club Shirt, Cottagecore Book Lover Top, Pond Life Crewneck"
TAGS = [
    "frog shirt", "book lover top", "cottagecore tee", "pond life", "reading club", "librarian top",
    "bookish crewneck", "nature lover", "frog mom", "toad shirt", "green frog", "vintage frog", "lily pad",
]
DESCRIPTION = "A frog reading club shirt for book lovers. Soft cotton.\n\nSizes and care."


def _check(result: checklist.Checklist, key: str) -> checklist.Check:
    return next(c for c in result.checks if c.key == key)


def _build(title: str = TITLE, tags: list[str] = TAGS, description: str = DESCRIPTION, **kwargs) -> checklist.Checklist:
    """A listing of a short-style ("search") profile unless ``search_style=False``."""
    args = {"title_rules": SHORT, "blocklist": compile_blocklist(["Disney"]), "filter_on": True, "search_style": True, **kwargs}
    return build(title, tags, description, **args)


def test_a_listing_that_follows_everything_passes_every_check() -> None:
    result = _build()
    assert [c.status for c in result.checks] == [PASS] * len(result.checks)
    assert [c.key for c in result.checks] == [
        "title_length", "title_repeats", "tag_count", "tag_format", "tag_duplicates", "description_opening", "trademarks",
    ]
    assert result.summary == "7 of 7 checks pass"
    assert result.version == checklist.CHECKLIST_VERSION == 2
    assert all(c.source and c.reason.endswith(".") for c in result.checks)


@pytest.mark.parametrize(
    "title, status, reason",
    [
        ("", FAIL, "The title is empty."),
        ("Frog Shirt", FAIL, "10 characters; this profile asks for at least 40."),
        ("x" * 141, FAIL, "141 characters; the most is 140."),
        (TITLE, PASS, f"{len(TITLE)} characters, within this profile's 40–140."),
    ],
)
def test_title_length_follows_the_profiles_bounds(title: str, status: str, reason: str) -> None:
    check = _check(_build(title=title), "title_length")
    assert (check.status, check.reason) == (status, reason)


def test_title_length_uses_the_classic_bounds_for_a_classic_profile() -> None:
    assert _check(_build(title_rules=LEGACY_TITLE), "title_length").status == FAIL  # 71 characters < 110


def test_repeated_title_words_warn_and_the_prefix_is_left_aside() -> None:
    check = _check(_build(title="Frog Shirt, Retro Frog Tee, Pond Life Crewneck Shirts For Readers"), "title_repeats")
    assert check.status == WARN
    assert check.reason == "Used more than once: “frog”, “shirt”. Etsy suggests using each word once."
    prefixed = _check(_build(title="Comfort Colors Frog Tee, Retro Frog Reading Club", prefix="Comfort Colors"), "title_repeats")
    assert prefixed.status == WARN and "“frog”" in prefixed.reason and "comfort" not in prefixed.reason
    assert _check(_build(title="Comfort Colors Frog Reading Club Shirt", prefix="Comfort Colors"), "title_repeats").status == PASS
    assert _check(_build(title=""), "title_repeats").status == WARN


def test_repeated_title_words_are_not_checked_for_the_long_keyword_style() -> None:
    """The classic style repeats words by design: the check is left out, not replaced."""
    classic = _build(title="Frog Shirt, Retro Frog Tee, Pond Life Crewneck Shirts For Readers", search_style=False)
    assert "title_repeats" not in [c.key for c in classic.checks]
    assert len(classic.checks) == len(_build().checks) - 1


def test_tag_count_fails_below_and_above_thirteen() -> None:
    assert (_check(_build(tags=TAGS[:9]), "tag_count").status, _check(_build(tags=TAGS[:9]), "tag_count").reason) == (FAIL, "9 of 13 tags.")
    assert _check(_build(tags=[*TAGS, "extra one"]), "tag_count").reason == "14 tags; Etsy takes 13."
    assert _check(_build(), "tag_count").reason == "13 of 13 tags."


def test_tag_format_fails_on_long_comma_or_empty_tags() -> None:
    tags = ["a tag far longer than twenty", "frog, toad", "", *TAGS[3:]]
    check = _check(_build(tags=tags), "tag_format")
    assert check.status == FAIL
    assert check.reason == "Tags over 20 characters: “a tag far longer than twenty”; with a comma: “frog, toad”; 1 empty."


def test_duplicate_tags_fail_and_the_same_search_warns() -> None:
    twice = _check(_build(tags=[*TAGS[:12], "Frog Shirt"]), "tag_duplicates")
    assert (twice.status, twice.reason) == (FAIL, "Used twice: “Frog Shirt”.")
    same = _check(_build(tags=[*TAGS[:12], "frog shirts"]), "tag_duplicates")
    assert same.status == WARN
    assert same.reason == "“frog shirt” and “frog shirts” are the same search to Etsy; one could be a different phrase."
    # The classic style's writer does not apply the root-word rule: only a real repeat counts.
    classic = _check(_build(tags=[*TAGS[:12], "frog shirts"], search_style=False), "tag_duplicates")
    assert (classic.status, classic.reason) == (PASS, "No tag is used twice.")
    assert _check(_build(tags=[*TAGS[:12], "Frog Shirt"], search_style=False), "tag_duplicates").status == FAIL


def test_the_descriptions_first_sentences_should_name_the_item() -> None:
    assert _check(_build(), "description_opening").reason == "The first sentences mention “frog”, “reading”, “club”, “shirt”."
    late = "Thanks for visiting. Soft cotton, made to order. A frog reading club shirt."
    check = _check(_build(description=late), "description_opening")
    assert check.status == WARN and check.reason.startswith("The first 2 sentences mention none of the title's words")
    assert _check(_build(description="  "), "description_opening").status == FAIL
    # The profile's prefix is the seller's brand, not the item: it does not count.
    branded = _check(_build(title="Comfort Colors Frog Shirt", prefix="Comfort Colors", description="Comfort Colors quality."), "description_opening")
    assert branded.status == WARN


def test_occasion_and_holiday_warn_only_when_nobody_decided() -> None:
    undecided = [OptionState("Occasion", ["Birthday"]), OptionState("Holiday", [])]
    check = _check(_build(options=undecided), "item_options")
    assert (check.status, check.reason) == (WARN, "Not set: Holiday. Leave any that does not apply to this design empty.")
    for chosen_by, said in (
        ("you", "none, as you chose"),
        ("writer", "none, as written for this design"),
        ("profile", "none, the profile's default"),
    ):
        check = _check(_build(options=[OptionState("Occasion", ["Birthday"]), OptionState("Holiday", [], chosen_by=chosen_by)]), "item_options")
        assert (check.status, check.reason) == (PASS, f"Occasion: Birthday; Holiday: {said}.")
    assert check.label == "Occasion and Holiday"
    assert "item_options" not in [c.key for c in _build().checks]  # nothing offered: no check


def test_trademarks_fail_with_the_filter_on_and_say_so_when_it_is_off() -> None:
    check = _check(_build(title="Disney Frog Reading Club Shirt, Pond Life Top"), "trademarks")
    assert (check.status, check.reason) == (FAIL, "Remove: Disney (title)")
    characters = _check(_build(characters=["Mickey Mouse"]), "trademarks")
    assert characters.status == FAIL and "Mickey Mouse" in characters.reason
    # Filter off (the seller accepted the risk): no check, unless the scanner still warns.
    off = _build(title="Disney Frog Reading Club Shirt, Pond Life Top", blocklist=Blocklist(), filter_on=False)
    assert "trademarks" not in [c.key for c in off.checks]
    off_characters = _check(_build(blocklist=Blocklist(), filter_on=False, characters=["Mickey Mouse"]), "trademarks")
    assert off_characters.status == WARN and "Mickey Mouse" in off_characters.reason


def test_production_partner_only_for_profiles_that_look_print_on_demand() -> None:
    pod = ListingProfile(name="Tees", content_template="apparel", cached_payload={"who_made": "i_did"})
    check = _check(_build(profile=pod), "production_partner")
    assert check.status == WARN and "Creativity Standards" in check.reason
    pod.cached_payload = {"who_made": "i_did", "production_partner_ids": [7]}
    assert _check(_build(profile=pod), "production_partner").status == PASS
    made_here = ListingProfile(name="Prints", content_template="digital_products", cached_payload={"who_made": "i_did"})
    assert "production_partner" not in [c.key for c in _build(profile=made_here).checks]
    unread = ListingProfile(name="New", content_template="apparel", cached_payload=None)
    assert "production_partner" not in [c.key for c in _build(profile=unread).checks]


def test_the_checklist_and_validate_listing_agree_on_the_limits_they_share() -> None:
    """The structural checks are the very rules validate_listing enforces, not a copy."""
    cases = [
        (TITLE, TAGS),
        ("", TAGS),
        ("Frog", TAGS),
        (TITLE, TAGS[:12]),
        (TITLE, [*TAGS[:12], "a tag far longer than twenty"]),
        (TITLE, [*TAGS[:12], "frog, toad"]),
        (TITLE, [*TAGS[:12], "frog shirt"]),
    ]
    for title, tags in cases:
        errors = validate_listing(GeneratedListing(title, tags, DESCRIPTION), trademarks=Blocklist(), title_rules=SHORT)
        failed = [c for c in _build(title=title, tags=tags, blocklist=Blocklist()).checks if c.status == FAIL]
        assert bool(errors) == bool(failed), (title, tags, errors, failed)


def test_the_search_rules_title_check_uses_the_same_repeated_words() -> None:
    from app.pipeline import search_rules

    title = "Frog Shirt, Retro Frog Tee"
    errors = search_rules.title_errors(title, SEARCH_TITLE)
    assert any("repeats 'frog'" in e for e in errors)
    assert search_rules.repeated_words(title) == ["frog"]


# --- on the content endpoints --------------------------------------------------------------------


class _NoCalls:
    """Fails the test if anything reaches Etsy, a model or the network."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []

        def refuse(name):
            async def _refuse(*args, **kwargs):
                self.calls.append(name)
                raise AssertionError(f"{name} was called")

            return _refuse

        monkeypatch.setattr(EtsyApiClient, "_request", refuse("Etsy API"))
        monkeypatch.setattr(AnthropicLLMClient, "complete_json", refuse("model"))
        # The real network transport (the test client talks to the app in-process).
        monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse("network"))


async def test_the_review_page_gets_a_checklist_without_any_etsy_or_model_call(world, monkeypatch) -> None:  # noqa: F811
    guard = _NoCalls(monkeypatch)
    queued = len(world["queue"].calls)
    res = await world["a"].get(f"/api/batches/{world['batch']}/content")
    assert res.status_code == 200, res.text
    for card in res.json():
        found = card["checklist"]
        assert found["heading"] == "Search-readiness checklist"
        assert found["note"] == "A check against Etsy's published guidance. It does not predict how Etsy ranks your listing."
        assert found["summary"] == f"{found['passed']} of {found['total']} checks pass"
        statuses = {c["key"]: c["status"] for c in found["checks"]}
        # The fixture's classic profile repeats words by design (not checked); its
        # description names none of the title's words, and its apparel profile names
        # no production partner.
        assert "title_repeats" not in statuses
        assert found["version"] == 2
        assert statuses["description_opening"] == "warn"
        assert statuses["production_partner"] == "warn"
        assert statuses["tag_count"] == "pass"
    assert guard.calls == []
    assert len(world["queue"].calls) == queued  # nothing queued (no sections read, no sync)
    async with world["sm"]() as s:
        assert (await s.execute(select(AiCall))).scalars().all() == []


async def test_the_preview_follows_unsaved_text_and_saves_nothing(world, monkeypatch) -> None:  # noqa: F811
    guard = _NoCalls(monkeypatch)
    content_id = world["contents"][0]
    async with world["sm"]() as s:
        before = await s.get(GeneratedContent, content_id)
        saved = (before.title, list(before.tags), before.description)
        findings = len((await s.execute(select(ComplianceFinding))).scalars().all())
    res = await world["a"].post(
        f"/api/content/{content_id}/checklist",
        json={"tags": TAGS[:5], "description": "Retro frog tee for pond lovers. Soft."},
    )
    assert res.status_code == 200, res.text
    statuses = {c["key"]: c for c in res.json()["checks"]}
    assert (statuses["tag_count"]["status"], statuses["tag_count"]["reason"]) == ("fail", "5 of 13 tags.")
    assert statuses["description_opening"]["status"] == "pass"
    assert "title_repeats" not in statuses  # a classic profile
    assert statuses["title_length"]["reason"].startswith(f"{len(BODY) + len(PREFIX_A) + 2} characters")  # the saved title
    async with world["sm"]() as s:
        after = await s.get(GeneratedContent, content_id)
        assert (after.title, list(after.tags), after.description) == saved
        assert len((await s.execute(select(ComplianceFinding))).scalars().all()) == findings
    assert guard.calls == []
    # Another account's listing is not found.
    assert (await world["b"].post(f"/api/content/{content_id}/checklist", json={})).status_code == 404


async def test_a_write_it_yourself_listing_gets_the_checklist_too(world) -> None:  # noqa: F811
    from tests.test_review_flow import _batch

    batch = await _batch(world)
    assert (await world["a"].post(f"/api/batches/{batch}/manual", json={"group_key": "g1"})).status_code == 200
    [card] = (await world["a"].get(f"/api/batches/{batch}/content")).json()
    assert card["source"] == "manual"
    statuses = {c["key"]: c["status"] for c in card["checklist"]["checks"]}
    assert statuses["title_length"] == "fail" and statuses["tag_count"] == "fail"  # empty until the seller writes
    # Saving text returns the card with its checklist worked out again.
    res = await world["a"].patch(f"/api/content/{card['id']}", json={"title": TITLE, "tags": TAGS})
    assert res.status_code == 200, res.text
    statuses = {c["key"]: c["status"] for c in res.json()["content"]["checklist"]["checks"]}
    assert statuses["tag_count"] == "pass"


async def test_warnings_do_not_block_approval(world) -> None:  # noqa: F811
    content_id = world["contents"][0]
    [card] = [c for c in (await world["a"].get(f"/api/batches/{world['batch']}/content")).json() if c["id"] == str(content_id)]
    assert "warn" in {c["status"] for c in card["checklist"]["checks"]}
    assert "fail" not in {c["status"] for c in card["checklist"]["checks"]}
    res = await world["a"].post(f"/api/content/{content_id}/approve", json={"approved": True})
    assert res.status_code == 200, res.text
    assert res.json()["content"]["approved"] is True


# --- a listing written correctly for its style -----------------------------------------------------

REFERENCE_DESCRIPTION = "Reference title\n\nSizes S to 3XL. Wash cold."
CATEGORY = {"Occasion": ["Birthday", "Graduation"], "Holiday": ["Christmas", "Halloween"]}

CLASSIC_TITLE = "Vintage Frog Reading Club Shirt, Cottagecore Book Lover Tee, Frog Mom Gift, Pond Life Reader Crewneck Tee, Bookish Frog Present"
# Plural and singular, which the classic writer does not avoid.
CLASSIC_TAGS = ["frog shirt", "frog shirts", "book lover tee", "cottagecore tee", "pond life", "reading club", "librarian top",
                "bookish crewneck", "nature lover", "frog mom", "toad shirt", "green frog", "lily pad"]

SEARCH_TITLE_TEXT = "Frog Reading Club Shirt, Cottagecore Book Lover Top"
SEARCH_TAGS = ["frog shirt", "librarian gift", "bookworm present", "back to school", "funny frog tee", "cottagecore style",
               "reading lover", "toad lover top", "pond life", "book nerd", "birthday idea", "nature tee", "teacher appreciation"]
SEARCH_INTENTS = ["product", "profession", "recipient", "season", "humor", "style", "subject", "subject", "subject", "humor",
                  "occasion", "style", "occasion"]
SEARCH_OPENING = "A frog reading club shirt for anyone who loves books and quiet ponds."


async def test_a_listing_written_correctly_for_its_style_gets_no_warning(world) -> None:  # noqa: F811
    """The app's own output for a classic and for a search profile, each valid under
    validate_listing, gets no warning from a check that contradicts its style."""
    from app.api.checklist import checklists_for

    classic = GeneratedListing(CLASSIC_TITLE, CLASSIC_TAGS, replace_title_block(REFERENCE_DESCRIPTION, CLASSIC_TITLE))
    assert 110 <= len(CLASSIC_TITLE) <= 140
    assert validate_listing(classic, policy_for("apparel"), trademarks=compile_blocklist(["Disney"]), title_rules=LEGACY_TITLE) == []

    search = GeneratedListing(
        SEARCH_TITLE_TEXT, SEARCH_TAGS, with_opening(REFERENCE_DESCRIPTION, SEARCH_OPENING),
        opening=SEARCH_OPENING, tag_intents=SEARCH_INTENTS,
    )
    rules = title_rules(40, 140)
    assert validate_listing(search, policy_for("apparel"), trademarks=compile_blocklist(["Disney"]), title_rules=rules) == []

    async with world["sm"]() as s:
        pa1, pa2 = await s.get(ListingProfile, world["pa1"]), await s.get(ListingProfile, world["pa2"])
        for profile, style in ((pa1, "classic"), (pa2, "search")):
            profile.listing_style = style
            profile.title_prefix = None
            # A partner named and the category's Occasion / Holiday read, so only style matters here.
            profile.cached_payload = {**(profile.cached_payload or {}), "description": REFERENCE_DESCRIPTION,
                                      "production_partner_ids": [7], "category_attributes": CATEGORY}
        written = []
        for profile, listing in ((pa1, classic), (pa2, search)):
            content = await s.get(GeneratedContent, world["contents"][len(written)])
            content.listing_profile_id = profile.id
            content.title, content.tags, content.description = listing.title, listing.tags, listing.description
            content.attributes = {"listing": {}, **({"search": True} if profile is pa2 else {})}  # no Occasion / Holiday: the design shows none
            written.append(content)
        await s.commit()
        lists = await checklists_for(s, world["alice"], written, {pa1.id: pa1, pa2.id: pa2})

    for content, style in zip(written, ("classic", "search")):
        found = lists[content.id]
        statuses = {c.key: (c.status, c.reason) for c in found.checks}
        assert all(status == "pass" for status, _ in statuses.values()), (style, statuses)
        assert statuses["item_options"] == ("pass", "Occasion: none, as written for this design; Holiday: none, as written for this design.")
        assert found.version == 2
    classic_keys = [c.key for c in lists[written[0].id].checks]
    search_keys = [c.key for c in lists[written[1].id].checks]
    assert "title_repeats" not in classic_keys and "title_repeats" in search_keys
    # The same classic listing judged by the short style's rules would be told to change.
    as_search = build(CLASSIC_TITLE, CLASSIC_TAGS, classic.description, title_rules=LEGACY_TITLE, blocklist=Blocklist(),
                      filter_on=True, search_style=True)
    assert {c.key for c in as_search.checks if c.status == WARN} == {"title_repeats", "tag_duplicates"}


async def test_a_listing_the_seller_writes_is_undecided_without_a_profile_default(world) -> None:  # noqa: F811
    from app.api.checklist import checklists_for

    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        profile.cached_payload = {**(profile.cached_payload or {}), "category_attributes": CATEGORY}
        profile.default_holiday = ""  # "none" by default
        content = await s.get(GeneratedContent, world["contents"][0])
        content.source = "manual"
        content.attributes = {}
        await s.commit()
        found = (await checklists_for(s, world["alice"], [content], {profile.id: profile}))[content.id]
    check = next(c for c in found.checks if c.key == "item_options")
    assert (check.status, check.reason) == ("warn", "Not set: Occasion. Leave any that does not apply to this design empty.")


# --- the words ------------------------------------------------------------------------------------

FORBIDDEN = re.compile(r"\branking\b|\brank higher\b|\bguarantee|\bboost|etsy score|seo score", re.IGNORECASE)


def _strings(source: str) -> list[str]:
    return re.findall(r'"((?:[^"\\]|\\.)*)"', source)


def test_the_checklists_words_promise_nothing() -> None:
    texts = [checklist.HEADING, checklist.NOTE]
    for builder in (_build(), _build(title="", tags=[], description=""), _build(title="Frog Shirt, Frog Tee", blocklist=Blocklist(), filter_on=False)):
        for c in builder.checks:
            texts += [c.label, c.reason]
    module = Path(checklist.__file__).read_text(encoding="utf-8")
    texts += _strings(module)
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    component = frontend / "components" / "SearchChecklist.tsx"
    if component.exists():  # not in the backend container
        texts += _strings(component.read_text(encoding="utf-8"))
    for text in texts:
        assert not FORBIDDEN.search(text), text
    assert checklist.HEADING == "Search-readiness checklist"
