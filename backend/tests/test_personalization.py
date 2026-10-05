"""Listing personalization copied from the reference (v7 §D4).

Etsy moved it to its own endpoints (getListingPersonalization /
updateListingPersonalization); createDraftListing no longer takes it.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import EtsyConnection, GeneratedContent
from app.etsy.publisher import PublishImage, publish_content
from app.pipeline.personalization import effective, from_reference, questions_for, validate
from tests.test_publisher import CONFIG, REFERENCE, FakeEtsy, _seed
from tests.test_publish_api import ctx  # noqa: F401  (fixture)

REF = {"personalization_questions": [
    {"question_type": "text_input", "question_text": "Name to print", "instructions": "Up to 12 letters",
     "required": True, "max_allowed_characters": 12, "question_id": 7},
    {"question_type": "dropdown", "question_text": "Colour", "options": ["red"]},
]}


def test_the_reference_question_is_copied() -> None:
    assert from_reference(REF) == [{"question_text": "Name to print", "instructions": "Up to 12 letters",
                                    "required": True, "max_allowed_characters": 12}]
    assert from_reference({"personalization_questions": []}) == []


def test_the_sellers_override_wins_and_unknown_stays_unknown() -> None:
    payload = {"personalization": from_reference(REF)}
    assert effective(None, payload)["question_text"] == "Name to print"
    assert effective({"enabled": False}, payload) == {"enabled": False}
    assert effective(None, {"personalization": []}) == {"enabled": False}
    assert effective(None, {"taxonomy_id": 1}) is None  # reference not read for it yet: leave drafts alone


def test_an_override_is_checked_against_etsys_limits() -> None:
    with pytest.raises(ValueError, match="1 to 45"):
        validate({"enabled": True, "question_text": "x" * 46})
    with pytest.raises(ValueError, match="between 1 and 1024"):
        validate({"enabled": True, "question_text": "Name", "max_allowed_characters": 0})
    ok = validate({"enabled": True, "question_text": " Name ", "required": 1, "max_allowed_characters": "20"})
    assert ok == {"enabled": True, "question_text": "Name", "instructions": "", "required": True,
                  "max_allowed_characters": 20}
    assert questions_for(ok) == [{"question_text": "Name", "question_type": "text_input", "required": True,
                                  "max_allowed_characters": 20}]


class PersonalizingEtsy(FakeEtsy):
    def __init__(self, *, saves: bool = True, **kw: Any) -> None:
        super().__init__(**kw)
        self.saves = saves
        self.personalization: list[dict[str, Any]] = []

    async def update_listing_personalization(self, shop_id, listing_id, *, questions, **_):  # noqa: ANN001, ANN201
        self.calls.append("update_listing_personalization")
        if self.saves:
            self.personalization = questions
        return {"personalization_questions": self.personalization}

    async def get_listing_personalization(self, listing_id, **_):  # noqa: ANN001, ANN201
        return {"personalization_questions": self.personalization}


async def _publish(async_sm, fake, setting):  # noqa: ANN001, ANN202
    _, conn_id, content_id, job_id = await _seed(async_sm, taxonomy_id=None)
    async with async_sm() as s:
        return await publish_content(
            s, job_id=job_id, content=await s.get(GeneratedContent, content_id),
            connection=await s.get(EtsyConnection, conn_id), sku="BR5475",
            thumbnail=PublishImage(b"thumb", "t.jpg"), client=fake, access_token="tok",
            config=CONFIG, reference=REFERENCE, tenant_limit=2000, personalization=setting,
        )


async def test_a_draft_gets_the_profiles_question(async_sm: async_sessionmaker) -> None:
    fake = PersonalizingEtsy()
    await _publish(async_sm, fake, effective(None, {"personalization": from_reference(REF)}))
    assert fake.personalization == [{"question_text": "Name to print", "question_type": "text_input",
                                     "required": True, "instructions": "Up to 12 letters",
                                     "max_allowed_characters": 12}]


async def test_personalization_off_sends_nothing(async_sm: async_sessionmaker) -> None:
    fake = PersonalizingEtsy()
    await _publish(async_sm, fake, {"enabled": False})
    assert "update_listing_personalization" not in fake.calls


async def test_a_question_that_did_not_save_is_reported(async_sm: async_sessionmaker) -> None:
    with pytest.raises(ValueError, match="personalization did not save as set \(question\)"):
        await _publish(async_sm, PersonalizingEtsy(saves=False), {"enabled": True, "question_text": "Name"})


async def test_the_profile_shows_and_takes_the_sellers_setting(ctx) -> None:  # noqa: F811
    from app.db.models import ListingProfile

    from tests.test_publish_api import _add_content

    content_id = await _add_content(ctx["sm"], ctx["tenant_id"])
    async with ctx["sm"]() as s:
        profile = (await s.get(GeneratedContent, content_id)).listing_profile_id
        p = await s.get(ListingProfile, profile)
        p.cached_payload = {**(p.cached_payload or {}), "personalization": from_reference(REF)}
        await s.commit()
    got = (await ctx["client"].get(f"/api/profiles/{profile}")).json()
    assert got["personalization_source"] == "reference"
    assert got["personalization"]["question_text"] == "Name to print"

    url = f"/api/profiles/{profile}"
    bad = await ctx["client"].patch(url, json={"personalization": {"enabled": True, "question_text": ""}})
    assert bad.status_code == 422
    off = (await ctx["client"].patch(url, json={"personalization": {"enabled": False}})).json()
    assert off["personalization"] == {"enabled": False, "question_text": None, "instructions": None,
                                      "required": False, "max_allowed_characters": None}
    assert off["personalization_source"] == "custom"
    back = (await ctx["client"].patch(url, json={"personalization": None})).json()
    assert back["personalization_source"] == "reference" and back["personalization"]["enabled"] is True


# --- per listing (v8 §D) -------------------------------------------------------------------------

from app.pipeline import personalization as P  # noqa: E402
from tests.auth_support import authenticate, make_tenant, open_session  # noqa: E402
from tests.test_publish_api import _add_content, ctx  # noqa: E402,F401  (fixture)


def test_a_listing_setting_wins_over_its_profiles_in_every_shop() -> None:
    profile_q = {"enabled": True, "question_text": "Name", "required": False}
    assert P.for_listing(None, profile_q, None) == (profile_q, "profile")
    own = {"enabled": False}
    assert P.for_listing(own, profile_q, None) == (own, "listing")
    assert P.for_listing(None, None, {}) == (None, "unknown")  # the profile's reference not read for it yet


def test_the_read_back_names_what_etsy_did_not_keep() -> None:
    sent = {"enabled": True, "question_text": "Name", "required": True, "max_allowed_characters": 20, "instructions": "Up to 20"}
    kept = {"personalization_questions": [{"question_text": "Name", "required": True, "max_allowed_characters": 20,
                                           "instructions": "Up to 20"}]}
    assert P.differs(sent, kept) == []
    lost = {"personalization_questions": [{"question_text": "Name", "required": False, "max_allowed_characters": 256}]}
    assert P.differs(sent, lost) == ["required", "character limit", "instructions"]
    assert P.differs(sent, {"personalization_questions": []}) == ["question"]


async def test_the_review_card_sets_a_listings_personalization_within_etsys_limits(ctx) -> None:  # noqa: F811
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"])
    url = f"/api/content/{content_id}"
    res = await ctx["client"].patch(url, json={"personalization": {
        "enabled": True, "question_text": "Name to print", "required": True, "max_allowed_characters": 20,
        "instructions": "Up to 20 letters"}})
    assert res.status_code == 200, res.text
    out = res.json()["content"]
    assert out["personalization_source"] == "listing"
    assert out["personalization"] == {"enabled": True, "question_text": "Name to print", "instructions": "Up to 20 letters",
                                      "required": True, "max_allowed_characters": 20}
    # Etsy's limits: a question of 1-45 characters; the character limit and instructions as the app allows.
    for bad in ({"enabled": True, "question_text": "x" * 46}, {"enabled": True, "question_text": "Name", "max_allowed_characters": 0},
                {"enabled": True, "question_text": "Name", "instructions": "x" * 257}):
        assert (await ctx["client"].patch(url, json={"personalization": bad})).status_code == 422
    # Turned on with no question: Etsy needs one, so it gets a plain default.
    res = await ctx["client"].patch(url, json={"personalization": {"enabled": True, "required": False}})
    assert res.json()["content"]["personalization"]["question_text"] == P.DEFAULT_QUESTION
    # null follows the profile again.
    res = await ctx["client"].patch(url, json={"personalization": None})
    assert res.json()["content"]["personalization_source"] != "listing"


async def test_set_for_all_reaches_every_listing_and_only_the_owners(ctx) -> None:  # noqa: F811
    first = await _add_content(ctx["sm"], ctx["tenant_id"])
    async with ctx["sm"]() as s:
        batch_id = (await s.get(GeneratedContent, first)).batch_id
    res = await ctx["client"].post(f"/api/batches/{batch_id}/personalization", json={"personalization": {"enabled": False}})
    assert res.status_code == 200 and all(c["personalization"] == {"enabled": False, "question_text": None, "instructions": None,
                                                                    "required": False, "max_allowed_characters": None}
                                          for c in res.json())
    other = await make_tenant(ctx["sm"], "someone@example.com")
    authenticate(ctx["client"], await open_session(ctx["redis"], other))
    res = await ctx["client"].post(f"/api/batches/{batch_id}/personalization", json={"personalization": None})
    assert res.status_code == 404
