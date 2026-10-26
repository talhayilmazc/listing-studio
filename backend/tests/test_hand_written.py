"""Listings the seller writes themselves ("write it yourself").

Production (2026-10-21): a seller made 14 hand-written listings in PCS, did not press
Generate, and pressed approve. The page said "Approved 0 · 14 not approved, fix these
first:" and repeated for every listing "title is empty; expected exactly 13 tags, got 0;
title must name the product type…; at least one tag must name the product type".

Now:

* what is typed is saved as typed, survives a reload and is what approval uses; a save
  that would overwrite another tab's edit is refused (409), never applied;
* a hand-written listing is stopped only by what Etsy itself refuses for a draft
  (pipeline/etsy_text.py, from Etsy's OpenAPI spec) and by the trademark filter; the
  app's quality rules (13 tags, the product type in the title and a tag...) are warnings;
* "Approve all" says one summary line and one line per reason with a count;
* "Generate the empty ones" writes only listings nothing was typed into;
* listings the app wrote keep every rule.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import select

from app.compliance.check import listing_problem
from app.db.models import (
    AllowanceUse,
    Asset,
    AssetStatus,
    GeneratedContent,
    ListingGroupSetting,
    ListingProfile,
    UploadBatch,
    UploadBatchStatus,
)
from app.pipeline import approval, etsy_text, manual
from app.pipeline.content import GeneratedListing, check_listing, policy_for
from tests.support import VALID_TITLE, fake_response
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_publish_api import _shop, ctx  # noqa: F401  (fixture)
from tests.test_regenerate import VISION, _content_reply, llm  # noqa: F401  (fixture)
from tests.test_review_flow import TITLE, _batch

CASES = json.loads((Path(__file__).parent / "fixtures" / "etsy_text_cases.json").read_text(encoding="utf-8"))
FIVE_TAGS = ["frog reading club", "book lover", "pond life", "librarian", "cottagecore"]


async def _hand_written(world, groups=("g1",)) -> tuple[uuid.UUID, list[dict]]:  # noqa: F811
    batch = await _batch(world, groups=groups)
    res = await world["a"].post(f"/api/batches/{batch}/manual", json={})
    assert res.status_code == 200 and res.json()["generated"] == len(groups), res.text
    return batch, (await world["a"].get(f"/api/batches/{batch}/content")).json()


# --- Etsy's own rules ------------------------------------------------------------------------------


def test_etsys_title_and_tag_characters_follow_its_spec() -> None:
    for case in CASES["titles"]:
        assert (not etsy_text.title_errors(case["text"])) is case["ok"], case["text"][:40]
    for case in CASES["tags"]:
        assert (not etsy_text.tag_errors([case["text"]])) is case["ok"], case["text"]
    assert etsy_text.tag_errors(["t"] * 14) == ["more than 13 tags (14); Etsy takes 13 at most"]
    assert etsy_text.draft_errors("Tee", [], "") == ["description is empty"]


def test_a_hand_written_listing_is_held_to_etsys_rules_and_the_rest_are_warnings() -> None:
    policy = policy_for("apparel")
    listing = GeneratedListing(title="Frog Reading Club", tags=FIVE_TAGS, description="Soft and warm.")
    ai = check_listing(listing, policy, trademarks=None)
    mine = check_listing(listing, policy, hand_written=True, trademarks=None)
    # One rule set whoever wrote it (2026-10-26): the same answer for the same text.
    assert (ai.errors, ai.warnings) == (mine.errors, mine.warnings)
    assert mine.valid and mine.errors == []
    joined = " ".join(mine.warnings)
    assert "expected exactly 13 tags, got 5" in joined and "product type" in joined

    # What Etsy refuses still stops it: a character Etsy does not take, a 14th tag.
    refused = check_listing(GeneratedListing(title="Frog Tee $20", tags=["t"] * 14, description="x"),
                            policy, hand_written=True, trademarks=None)
    assert not refused.valid
    assert any("characters Etsy does not allow" in e for e in refused.errors)
    assert any("more than 13 tags" in e for e in refused.errors)
    assert not any(w.startswith("expected exactly") for w in refused.warnings)  # said once, by Etsy's rule

    # No title yet: one reason, not four.
    empty = check_listing(GeneratedListing(title="", tags=[], description="Body"), policy, hand_written=True, trademarks=None)
    assert empty.errors == ["title is empty"]
    assert empty.warnings == []  # nothing written yet: no notes about 13 tags either
    tags_only = check_listing(GeneratedListing(title="", tags=FIVE_TAGS, description="Body"), policy, hand_written=True, trademarks=None)
    assert tags_only.errors == ["title is empty"] and not any("title" in w for w in tags_only.warnings)


def test_reasons_are_grouped_with_a_count() -> None:
    ids = [uuid.uuid4() for _ in range(14)]
    groups = approval.group([(i, True, ["title is empty"]) for i in ids])
    assert [g.line() for g in groups] == ["14 listings have no title yet: write them, or press Generate"]
    assert approval.summary(14, groups) == "14 listings have no title yet: write them, or press Generate"
    one = approval.group([(ids[0], True, ["title is empty", "tag exceeds 20 characters: 'x'"]),
                          (ids[1], False, ["tag exceeds 20 characters: 'y'"])])
    assert [g.line() for g in one] == ["2 listings have a tag over 20 characters", "1 listing has no title yet: write it, or press Generate"]
    assert approval.summary(2, one) == "2 listings were not approved:"
    assert approval.short("title is empty") == "needs a title"


# --- saved as typed, two tabs ---------------------------------------------------------------------


async def test_hand_written_values_survive_reload_and_approve(world) -> None:  # noqa: F811
    batch, [card] = await _hand_written(world)
    cid = card["id"]
    saved = await world["a"].patch(f"/api/content/{cid}", json={
        "title": TITLE, "tags": FIVE_TAGS, "base": {"title": "", "tags": []}})
    assert saved.status_code == 200, saved.text
    description = saved.json()["content"]["description"]

    # A reload reads what was typed.
    [again] = (await world["a"].get(f"/api/batches/{batch}/content")).json()
    assert (again["title"], again["tags"], again["description"]) == (TITLE, FIVE_TAGS, description)
    # The card shows the app's rules as notes, not as reasons it cannot be approved.
    assert again["validation"]["valid"] is True and again["validation"]["errors"] == []
    assert any("13 tags" in w for w in again["validation"]["warnings"])

    # Approve uses the saved text: a title and 5 tags are approved, with notes.
    res = (await world["a"].post(f"/api/batches/{batch}/approve-all")).json()
    assert (res["approved"], res["skipped"], res["summary"]) == (1, [], None)
    assert res["notes"] and res["notes"][0]["line"] == "1 listing does not have 13 tags"
    async with world["sm"]() as s:
        row = await s.get(GeneratedContent, uuid.UUID(cid))
        assert (row.title, row.tags, row.approved) == (TITLE, FIVE_TAGS, True)


async def test_a_save_that_would_overwrite_another_tabs_edit_is_refused(world) -> None:  # noqa: F811
    _, [card] = await _hand_written(world)
    cid = card["id"]
    # Tab one saves a title.
    one = await world["a"].patch(f"/api/content/{cid}", json={"title": TITLE, "base": {"title": ""}})
    assert one.status_code == 200
    # Tab two still thinks the title is empty: its title is refused, the first one kept.
    two = await world["a"].patch(f"/api/content/{cid}", json={"title": "Another Title Tee", "base": {"title": ""}})
    assert two.status_code == 409 and "another tab" in two.json()["detail"]
    async with world["sm"]() as s:
        assert (await s.get(GeneratedContent, uuid.UUID(cid))).title == TITLE
    # A field nobody else changed is saved; so is sending what is already there.
    tags = await world["a"].patch(f"/api/content/{cid}", json={"tags": FIVE_TAGS, "base": {"tags": []}})
    same = await world["a"].patch(f"/api/content/{cid}", json={"title": TITLE, "base": {"title": ""}})
    assert (tags.status_code, same.status_code) == (200, 200)
    # Without a base (older pages, "Keep mine") the save is applied as before.
    mine = await world["a"].patch(f"/api/content/{cid}", json={"title": "Another Title Tee"})
    assert mine.status_code == 200 and mine.json()["content"]["title"] == "Another Title Tee"


# --- approve: one summary line --------------------------------------------------------------------


async def test_empty_hand_written_listings_are_blocked_with_one_summary_line(world) -> None:  # noqa: F811
    batch, cards = await _hand_written(world, groups=tuple(f"g{n}" for n in range(14)))
    res = (await world["a"].post(f"/api/batches/{batch}/approve-all")).json()
    assert res["approved"] == 0 and len(res["skipped"]) == 14
    assert res["summary"] == "14 listings have no title yet: write them, or press Generate"
    assert [(p["line"], p["count"]) for p in res["problems"]] == [(res["summary"], 14)]
    assert sorted(res["problems"][0]["content_ids"]) == sorted(c["id"] for c in cards)
    # Each card is marked with its own short reason; the long rule list is gone.
    assert {tuple(s["reasons"]) for s in res["skipped"]} == {("needs a title",)}
    assert "13 tags" not in json.dumps(res) and "product type" not in json.dumps(res)


async def test_an_ai_listing_rewritten_by_hand_is_held_to_the_same_rules(world) -> None:  # noqa: F811
    """A seller who rewrites an AI listing's title and tags is writing it too: the same rules as a
    listing they typed from scratch (2026-10-26; before, 5 tags or a short title kept it unapproved
    for ever). What Etsy refuses still stops it."""
    batch, [card] = await _hand_written(world)
    async with world["sm"]() as s:
        row = await s.get(GeneratedContent, uuid.UUID(card["id"]))
        row.source, row.title, row.tags = manual.AI, TITLE, FIVE_TAGS
        await s.commit()
    res = (await world["a"].post(f"/api/batches/{batch}/approve-all")).json()
    assert res["approved"] == 1 and res["skipped"] == [], res
    assert any("13 tags" in n["line"] for n in res["notes"])  # said, never a stop
    async with world["sm"]() as s:
        row = await s.get(GeneratedContent, uuid.UUID(card["id"]))
        assert await listing_problem(s, row) is None
        row.approved, row.tags = False, [*FIVE_TAGS, "mom’s gift"]  # a curly apostrophe: Etsy refuses it
        await s.commit()
    refused = await world["a"].post(f"/api/content/{card['id']}/approve", json={"approved": True})
    assert refused.status_code == 422
    assert any("straight apostrophe" in e for e in refused.json()["detail"]["errors"])


async def test_a_hand_written_listing_with_five_tags_can_be_drafted(world) -> None:  # noqa: F811
    _, [card] = await _hand_written(world)
    await world["a"].patch(f"/api/content/{card['id']}", json={"title": TITLE, "tags": FIVE_TAGS})
    approved = await world["a"].post(f"/api/content/{card['id']}/approve", json={"approved": True})
    assert approved.status_code == 200 and approved.json()["validation"]["warnings"]
    async with world["sm"]() as s:
        # Drafting, publishing and schedules use the same decision as approval.
        assert await listing_problem(s, await s.get(GeneratedContent, uuid.UUID(card["id"]))) is None


# --- Generate the empty ones ----------------------------------------------------------------------


async def test_generate_the_empty_ones_touches_only_empty_listings(ctx, llm) -> None:  # noqa: F811
    tenant = ctx["tenant_id"]
    async with ctx["sm"]() as s:
        shop = await _shop(s, tenant)
        from datetime import datetime, timezone

        profile = ListingProfile(tenant_id=tenant, connection_id=shop, name="Regular - PCS", reference_listing_id=555,
                                 content_template="digital_products", confirmed=True,
                                 cached_payload={"price": 25.0, "images": [], "description": "Ref\n\nBody", "payload_version": 2},
                                 updated_at=datetime.now(timezone.utc))
        s.add(profile)
        batch = UploadBatch(tenant_id=tenant, status=UploadBatchStatus.ready, file_count=4, connection_id=shop)
        s.add(batch)
        await s.flush()
        covers = {}
        for key in ("EMPTY", "STARTED", "AI", "NONE"):
            asset = Asset(tenant_id=tenant, batch_id=batch.id, original_filename=f"{key}/front.png", group_key=key, rank=1,
                          status=AssetStatus.processed, storage_key="p/1.png", processed_key="p/1.png", mime_type="image/png")
            s.add(asset)
            s.add(ListingGroupSetting(tenant_id=tenant, batch_id=batch.id, group_key=key, connection_id=shop, profile_id=profile.id))
            await s.flush()
            covers[key] = asset.id
        blank = manual.description_for(profile, "")
        s.add_all([
            GeneratedContent(tenant_id=tenant, batch_id=batch.id, asset_id=covers["EMPTY"], title="", tags=[], description=blank,
                             listing_profile_id=profile.id, source=manual.MANUAL),
            # Started: only tags typed, no title yet. It is the seller's: left alone.
            GeneratedContent(tenant_id=tenant, batch_id=batch.id, asset_id=covers["STARTED"], title="", tags=["my tag"],
                             description=blank, listing_profile_id=profile.id, source=manual.MANUAL),
            GeneratedContent(tenant_id=tenant, batch_id=batch.id, asset_id=covers["AI"], title=VALID_TITLE,
                             tags=[f"tag{i}" for i in range(13)], description="Written.", listing_profile_id=profile.id),
        ])
        await s.commit()
        batch_id, profile_id = batch.id, profile.id
    llm._responses += [fake_response(VISION), fake_response(_content_reply()), fake_response(VISION), fake_response(_content_reply())]

    res = (await ctx["client"].post(f"/api/batches/{batch_id}/generate",
                                    json={"profile_id": str(profile_id), "only_empty": True})).json()
    assert (res["generated"], res["failed"], res["skipped"]) == (2, 0, 2), res

    async with ctx["sm"]() as s:
        rows = (await s.execute(select(GeneratedContent, Asset).join(Asset, Asset.id == GeneratedContent.asset_id)
                                .where(GeneratedContent.batch_id == batch_id))).all()
        by_group = {a.group_key: c for c, a in rows}
        assert set(by_group) == {"EMPTY", "STARTED", "AI", "NONE"}  # one listing each, the empty one replaced
        assert by_group["EMPTY"].source == manual.AI and by_group["EMPTY"].title
        assert by_group["NONE"].source == manual.AI and by_group["NONE"].title
        assert (by_group["STARTED"].source, by_group["STARTED"].tags) == (manual.MANUAL, ["my tag"])
        assert by_group["AI"].title == VALID_TITLE
        # Counted as Generate counts: a new listing is one; writing an empty one is a rewrite of its design.
        kinds = sorted(u.kind for u in (await s.execute(select(AllowanceUse))).scalars())
        assert kinds == ["generation", "rewrite"]
