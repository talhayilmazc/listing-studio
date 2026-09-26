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
    with pytest.raises(ValueError, match="personalization question did not save"):
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
