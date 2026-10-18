"""Half-made and failed drafts: the read-only diagnosis command, old messages in today's
words, drafts a newer one replaced (listed with their Etsy ids, never deleted on Etsy),
the personalization question as Etsy's schema wants it, and titles kept whole for another
shop's longer prefix (ops/draft_failures.py, pipeline/unfinished.py, api/unfinished.py,
pipeline/personalization.py, pipeline/title_fit.py, etsy/errors.py).

Synthetic rows only; Etsy and the model are faked; nothing is sent anywhere.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.db.models import (
    Asset,
    AssetStatus,
    DraftAttempt,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingPublication,
)
from app.etsy import errors
from app.etsy.api import EtsyApiClient
from app.ops import draft_failures
from app.pipeline import personalization, title_fit, unfinished
from tests.test_multi_shop import PREFIX_A, world  # noqa: F401  (fixture)

NOW = datetime(2026, 10, 16, 17, 18, tzinfo=timezone.utc)
SECRET = "very-secret-token-value"


async def _attempt(world, *, content_id, shop, listing: int, at: datetime, error: str | None = None,  # noqa: ANN001, F811
                   record: dict | None = None):
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, content_id)
        asset = await s.get(Asset, content.asset_id)
        asset.group_key = asset.group_key or asset.original_filename.split(".")[0]  # each listing its own design
        s.add(DraftAttempt(tenant_id=content.tenant_id, content_id=content_id, connection_id=shop,
                           etsy_listing_id=listing, title=content.title, created_at=at))
        if error is not None:
            s.add(Job(tenant_id=content.tenant_id, connection_id=shop, type=JobType.create_draft,
                      status=JobStatus.failed, payload={"content_id": str(content_id), **({"etsy_error": record} if record else {})},
                      last_error=error, created_at=at, finished_at=at + timedelta(minutes=1)))
        await s.commit()


async def _rewritten(world, shop, listing: int, at: datetime) -> uuid.UUID:  # noqa: ANN001, F811
    """The same design written again (a new listing text in the same group), drafted fine later."""
    async with world["sm"]() as s:
        old = await s.get(GeneratedContent, world["contents"][0])
        asset = await s.get(Asset, old.asset_id)
        asset.group_key = asset.group_key or "d0"
        again = Asset(tenant_id=asset.tenant_id, batch_id=asset.batch_id, original_filename="d0-again.png",
                      storage_key="k-again", status=AssetStatus.processed, rank=9, group_key=asset.group_key)
        s.add(again)
        await s.flush()
        newer = GeneratedContent(tenant_id=old.tenant_id, batch_id=old.batch_id, asset_id=again.id,
                                 listing_profile_id=old.listing_profile_id, title=old.title, tags=list(old.tags),
                                 description="d", approved=True)
        s.add(newer)
        await s.flush()
        s.add(ListingPublication(tenant_id=old.tenant_id, content_id=newer.id, connection_id=shop,
                                 etsy_listing_id=listing, title=newer.title, created_at=at))
        await s.commit()
        return newer.id


# --- old messages in today's words ------------------------------------------------------------------


@pytest.mark.parametrize(("stored", "says"), [
    ("Etsy could not find the listing while setting sizes, prices and SKU; it may have been deleted in Shop Manager",
     "BCT does not have"),
    ("Etsy refused it while setting sizes, prices and SKU: Could not find readiness_state_id='1402915464979' associated with shop '54783739'",
     "BCT does not have the processing profile this listing used; set it up in BCT, then finish the draft"),
    ("client_error:400", "it answered 400"),
    ("etsy client error (400)", "it answered 400"),
    ("etsy server error (503)", "a problem of its own"),
    ("max_attempts_exceeded", "carries on where it stopped"),
])
def test_old_stored_messages_are_said_in_todays_words(stored: str, says: str) -> None:
    assert says in unfinished.plain_error(stored, "BCT")
    # Today's own sentences are left as they are.
    today = "BCT does not have the processing profile this listing used; set it up in BCT, then finish the draft"
    assert unfinished.plain_error(today, "BCT") == today


async def test_an_old_message_is_re_explained_with_the_finish_action(world) -> None:  # noqa: F811
    await _attempt(world, content_id=world["contents"][0], shop=world["a1"], listing=4596846429, at=NOW,
                   error="client_error:400")
    [item] = (await world["a"].get("/api/drafts/unfinished")).json()
    assert item["etsy_listing_id"] == 4596846429 and "it answered 400" in item["error"]
    assert item["can_finish"] is True and item["replaced_by"] is None


# --- replaced by a newer draft ------------------------------------------------------------------------


async def test_a_half_made_draft_replaced_by_a_newer_one_is_listed_apart_and_never_finished(world) -> None:  # noqa: F811
    await _attempt(world, content_id=world["contents"][0], shop=world["a2"], listing=4596846505, at=NOW,
                   error="Etsy refused it while setting sizes, prices and SKU (it answered 400) without saying why")
    await _rewritten(world, world["a2"], 4600000001, NOW + timedelta(hours=2))
    [item] = (await world["a"].get("/api/drafts/unfinished")).json()
    assert item["replaced_by"] == 4600000001 and item["etsy_listing_id"] == 4596846505
    assert item["can_finish"] is False and item["replaced_edit_url"].endswith("4600000001")
    res = (await world["a"].post("/api/drafts/unfinished/finish", json={})).json()
    assert res["started"] == 0 and "replaced by a newer draft" in res["skipped"][0]["reason"]
    assert world["queue"].calls == [] if hasattr(world["queue"], "calls") else True


async def test_an_older_listing_of_the_same_text_is_replaced_too_not_hidden(world) -> None:  # noqa: F811
    # The listing's own draft succeeded later as another Etsy listing: the old one is an orphan on Etsy.
    await _attempt(world, content_id=world["contents"][0], shop=world["a1"], listing=4596846491, at=NOW)
    async with world["sm"]() as s:
        c = await s.get(GeneratedContent, world["contents"][0])
        s.add(ListingPublication(tenant_id=c.tenant_id, content_id=c.id, connection_id=world["a1"],
                                 etsy_listing_id=4600000002, title=c.title, created_at=NOW + timedelta(hours=1)))
        await s.commit()
    [item] = (await world["a"].get("/api/drafts/unfinished")).json()
    assert item["replaced_by"] == 4600000002


async def test_a_replaced_draft_is_only_taken_off_the_list_nothing_is_deleted_on_etsy(world) -> None:  # noqa: F811
    await _attempt(world, content_id=world["contents"][0], shop=world["a2"], listing=4596846475, at=NOW)
    await _attempt(world, content_id=world["contents"][1], shop=world["a2"], listing=4596846483, at=NOW)
    await _rewritten(world, world["a2"], 4600000003, NOW + timedelta(hours=2))
    items = {i["etsy_listing_id"]: i for i in (await world["a"].get("/api/drafts/unfinished")).json()}
    replaced, still = items[4596846475], items[4596846483]
    assert replaced["replaced_by"] == 4600000003 and still["replaced_by"] is None
    assert (await world["b"].post(f"/api/drafts/unfinished/{replaced['attempt_id']}/dismiss")).status_code == 404
    assert (await world["a"].post(f"/api/drafts/unfinished/{still['attempt_id']}/dismiss")).status_code == 409
    assert (await world["a"].post(f"/api/drafts/unfinished/{replaced['attempt_id']}/dismiss")).status_code == 204
    left = [i["etsy_listing_id"] for i in (await world["a"].get("/api/drafts/unfinished")).json()]
    assert left == [4596846483]
    async with world["sm"]() as s:  # the newer draft's record is untouched
        from sqlalchemy import select

        pubs = (await s.execute(select(ListingPublication.etsy_listing_id))).scalars().all()
        assert 4600000003 in pubs
    # No code path here talks to Etsy: the API module has no Etsy client at all.
    import app.api.unfinished as module

    assert "EtsyApiClient" not in open(module.__file__, encoding="utf-8").read()


# --- the read-only diagnosis -----------------------------------------------------------------------------


async def test_the_command_lists_steps_answers_shapes_and_superseded_without_tokens(world, monkeypatch) -> None:  # noqa: F811
    async with world["sm"]() as s:  # a token in the database must never reach the output
        for shop in (world["a1"], world["a2"]):
            (await s.get(EtsyConnection, shop)).access_token_enc = SECRET.encode()
        profile = await s.get(ListingProfile, world["pa1"])
        profile.personalization = {"enabled": True, "question_text": "Name to print", "instructions": "", "required": True,
                                   "max_allowed_characters": None}
        await s.commit()
    record = errors.record(errors.EtsyClientError(400, body='{"error": "max_allowed_characters is required"}',
                                                  path="/application/shops/1/listings/4596864790/personalization", method="POST"))
    record["sent"] = errors.shape_of({"personalization_questions": [{"question_text": "Name to print", "required": True}]})
    await _attempt(world, content_id=world["contents"][0], shop=world["a1"], listing=4596864790, at=NOW,
                   error="Etsy refused it while setting the personalization question: max_allowed_characters is required",
                   record=record)
    await _attempt(world, content_id=world["contents"][0], shop=world["a2"], listing=4596846467, at=NOW - timedelta(days=8),
                   error="client_error:400")
    await _rewritten(world, world["a2"], 4600000004, NOW)
    out = await draft_failures.report(world["sm"], "alice@example.com")
    assert "listing 4596864790" in out and "step: setting the personalization question" in out
    assert "Etsy answered: 400 POST /application/shops/1/listings/4596864790/personalization" in out
    assert "Etsy's reason: max_allowed_characters is required" in out
    assert '"question_text": "string(13)"' in out and "Name to print" not in out  # shapes, never the words
    assert '"max_allowed_characters": "int"' in out  # as it would be sent now
    assert "Etsy's rules: it meets them" in out
    assert "stored message (before Etsy's answer was kept): client_error:400" in out
    assert "superseded: yes" in out and "4600000004" in out
    assert SECRET not in out and "token" not in out.lower()
    # --since and another account.
    assert "4596846467" not in await draft_failures.report(world["sm"], "alice@example.com", since=(NOW - timedelta(days=1)).date())
    bob = await draft_failures.report(world["sm"], "bob@example.com")
    assert "4596864790" not in bob and "None." in bob


# --- Etsy's answer and what was sent are kept with a failure -------------------------------------------


async def test_a_refused_request_keeps_its_shape_never_its_text() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "max_allowed_characters is required for text_input"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = EtsyApiClient(client_id="k", shared_secret="s", http_client=http)
    questions = [{"question_text": "Your name", "question_type": "text_input", "required": False}]
    async with http:
        with pytest.raises(errors.EtsyClientError) as caught:
            await client.update_listing_personalization(1, 2, questions=questions, access_token=SECRET)
    kept = errors.record(caught.value)
    assert kept["status"] == 400 and kept["step"] == "setting the personalization question"
    assert kept["message"] == "max_allowed_characters is required for text_input"
    assert kept["sent"] == {"personalization_questions": {"list": 1, "item": {
        "question_text": "string(9)", "question_type": "string(10)", "required": "bool"}}}
    assert SECRET not in str(kept) and "Your name" not in str(kept)


# --- the personalization question as Etsy's schema wants it ---------------------------------------------


def test_a_text_question_always_carries_its_character_limit() -> None:
    [q] = personalization.questions_for({"enabled": True, "question_text": "Name", "required": False})
    assert q == {"question_text": "Name", "question_type": "text_input", "required": False,
                 "max_allowed_characters": personalization.DEFAULT_MAX_CHARACTERS}
    [q] = personalization.questions_for({"enabled": True, "question_text": "Name", "max_allowed_characters": 20,
                                         "instructions": " Add your name "})
    assert q["max_allowed_characters"] == 20 and q["instructions"] == "Add your name"


@pytest.mark.parametrize(("setting", "problem"), [
    ({"question_text": "x" * 46}, "1 to 45"),
    ({"question_text": "Name", "instructions": "x" * 121}, "limited to 120"),
    ({"question_text": "- Name"}, "start with a letter or a number"),
    ({"question_text": "Name", "instructions": "ADD YOUR NAME"}, "only one word in capitals"),
    ({"question_text": "Name", "max_allowed_characters": 2000}, "between 1 and 1024"),
])
def test_etsys_rules_for_the_question(setting: dict, problem: str) -> None:
    assert any(problem in p for p in personalization.etsy_problems({"enabled": True, **setting}))
    with pytest.raises(ValueError):
        personalization.validate({"enabled": True, **setting})
    assert personalization.etsy_problems({"enabled": True, "question_text": "Name", "instructions": "Add the NAME"}) == []


async def test_a_question_etsy_would_refuse_stops_the_draft_before_anything_is_sent(async_sm) -> None:
    from app.etsy.publisher import PublishImage, publish_content
    from tests.test_publisher import CONFIG, REFERENCE, FakeEtsy, _seed

    _, conn_id, content_id, job_id = await _seed(async_sm)
    fake = FakeEtsy()
    async with async_sm() as s:
        with pytest.raises(ValueError, match="personalization question cannot be sent"):
            await publish_content(
                s, job_id=job_id, content=await s.get(GeneratedContent, content_id), connection=await s.get(EtsyConnection, conn_id),
                sku="BR1", thumbnail=PublishImage(b"t", "t.jpg"), extra_images=[], client=fake, access_token="tok",
                config=CONFIG, reference=REFERENCE, theme="", tenant_limit=2000,
                personalization={"enabled": True, "question_text": "Name", "instructions": "x" * 200},
            )
    assert "create" not in fake.calls  # no listing was made, so nothing is left half-made


# --- cross-shop titles: whole, rewritten once, else flagged -------------------------------------------


async def _long_title_world(world) -> tuple[GeneratedContent, EtsyConnection, ListingProfile]:  # noqa: ANN001, F811
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, world["contents"][0])
        content.title = f"{PREFIX_A}, Retro Frog Tee, Cottagecore Shirt, Pond Life Crewneck, Handmade Vintage Frog Graphic Top For Pond And Nature Lovers"
        profile = await s.get(ListingProfile, world["pa2"])
        profile.title_prefix = "Bella Canvas Unisex Jersey"
        await s.commit()
        return content, await s.get(EtsyConnection, world["a2"]), profile


async def test_a_title_too_long_for_another_shop_is_rewritten_once_and_kept_for_that_shop(world) -> None:  # noqa: F811
    from app.pipeline.targets import resolve_target

    content, shop, profile = await _long_title_world(world)
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, content.id)
        target = await resolve_target(s, content, shop)
        assert target.title_fit and len(target.title) > 140
        calls = []

        async def rewrite(base: str, prefix: str, room: int, prof) -> str:  # noqa: ANN001
            calls.append(room)
            return "Retro Frog Tee, Cottagecore Shirt, Pond Life Crewneck, Vintage Frog Graphic Top"

        fitted = await title_fit.fit_for_shop(content, shop, profile, target.title, rewrite)
        assert len(calls) == 1 and fitted.startswith("Bella Canvas Unisex Jersey Retro Frog Tee") and len(fitted) <= 140
        await s.commit()
        again = await resolve_target(s, content, shop)  # kept for that shop: no second rewrite
    assert again.title == fitted and again.title_fit is False


async def test_when_no_rewrite_fits_the_title_stays_whole_and_the_card_says_why(world) -> None:  # noqa: F811
    from app.pipeline.targets import resolve_target

    content, shop, profile = await _long_title_world(world)
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, content.id)
        target = await resolve_target(s, content, shop)
        whole = content.title

        async def rewrite(base: str, prefix: str, room: int, prof) -> str:  # noqa: ANN001
            return base  # the same, still too long

        with pytest.raises(ValueError, match="kept as written, not cut"):
            await title_fit.fit_for_shop(content, shop, profile, target.title, rewrite)
        await s.commit()
    assert content.title == whole  # never cut
    shown = (await world["a"].get(f"/api/batches/{world['batch']}/content")).json()
    card = next(c for c in shown if c["id"] == str(content.id))
    assert any("Etsy allows 140" in w and "not cut" in w for w in card["title_warnings"])
