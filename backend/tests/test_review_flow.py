"""Customer feedback on the review flow.

* Manual mode: the seller writes the title and tags; no AI is called and nothing
  is metered, everything else comes from the profile, it counts as one listing.
* The approve step chooses shops only: the ticks are kept on each listing, the
  profile is the listing's own in every shop, a shop it is not set up in is set
  up as "Use in" does, and what a ticked shop still needs is said per shop.
* Bug a: the cards keep one order whatever is applied to all of them.
* Bug b: a written listing follows the profile chosen on its group, and the group
  card says when that profile is not set up in the group's shop.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import (
    AiCall,
    AllowanceUse,
    Asset,
    AssetStatus,
    ContentVersion,
    EtsyConnection,
    GeneratedContent,
    ListingGroupSetting,
    ListingProfile,
    ListingPublication,
    ProfileShopLink,
    ShopListingCache,
    UploadBatch,
    UploadBatchStatus,
)
from app.etsy.publisher import PublishImage, publish_content
from app.pipeline import group_state, manual, versions
from app.pipeline.targets import resolve_target
from tests.test_multi_shop import PREFIX_A, _profile, world  # noqa: F401  (fixture)
from tests.test_publisher import CONFIG, REFERENCE, FakeEtsy
from tests.test_publisher import _seed as publisher_seed

TITLE = "Vintage Frog Reading Club Shirt, Cottagecore Book Lover Tee, Librarian Gift Top, Pond Life Reader Crewneck, Frog Mom Bookish Present Idea"
TAGS = ["frog shirt", *[f"frog tag {n}" for n in range(12)]]  # the apparel policy wants the product type in a tag


async def _batch(world, groups=("g1", "g2"), profile="pa1", shop="a1") -> uuid.UUID:  # noqa: F811
    """A batch of unwritten groups in one shop, each with a cover and a second photo."""
    async with world["sm"]() as s:
        batch = UploadBatch(tenant_id=world["alice"], status=UploadBatchStatus.ready, file_count=2 * len(groups),
                            connection_id=world[shop])
        s.add(batch)
        await s.flush()
        for key in groups:
            for rank, name in enumerate(("front.png", "back.png"), start=1):
                s.add(Asset(tenant_id=world["alice"], batch_id=batch.id, original_filename=f"{key}/{name}", group_key=key, rank=rank,
                            status=AssetStatus.processed, storage_key=f"k/{key}/{name}", processed_key=f"k/{key}/{name}"))
            s.add(ListingGroupSetting(tenant_id=world["alice"], batch_id=batch.id, group_key=key,
                                      connection_id=world[shop], profile_id=world[profile]))
        await s.commit()
        return batch.id


# --- manual mode -----------------------------------------------------------------------------------


async def test_writing_it_myself_calls_no_ai_and_counts_as_one_listing(world) -> None:  # noqa: F811
    batch = await _batch(world)
    res = await world["a"].post(f"/api/batches/{batch}/manual", json={"group_key": "g1"})
    assert res.status_code == 200, res.text
    assert (res.json()["generated"], res.json()["failed"]) == (1, 0)

    [card] = (await world["a"].get(f"/api/batches/{batch}/content")).json()
    # The title and the 13 tags start empty; the rest is the profile's.
    assert (card["source"], card["title"], card["tags"], card["approved"]) == ("manual", "", [], False)
    assert card["description"] == "Sizes for shop one."  # the reference's body, without the reference's own title
    assert (card["profile_id"], card["profile_name"], card["connection_id"]) == (str(world["pa1"]), "Standard Tee", str(world["a1"]))
    assert card["written_from_asset_id"] is None  # nothing was read from the cover
    async with world["sm"]() as s:
        assert (await s.execute(select(AiCall))).scalars().all() == []  # no vision call, no content call, no cost
        content = (await s.execute(select(GeneratedContent).where(GeneratedContent.batch_id == batch))).scalars().one()
        assert (content.source, content.model_used, content.vision_model, content.input_tokens) == ("manual", None, None, None)
        assert len((await s.execute(select(AllowanceUse))).scalars().all()) == 1
    assert (await world["a"].get("/api/account/allowance")).json()["used"] == 1

    # Asked again for the same group: it is already written, nothing more is counted.
    again = await world["a"].post(f"/api/batches/{batch}/manual", json={"group_key": "g1"})
    assert (again.json()["generated"], again.json()["skipped"]) == (0, 1)
    # For the whole batch: only the group not written yet.
    rest = await world["a"].post(f"/api/batches/{batch}/manual", json={})
    assert (rest.json()["generated"], rest.json()["skipped"]) == (1, 1)
    assert (await world["a"].get("/api/account/allowance")).json()["used"] == 2
    # Another account's batch is not there.
    assert (await world["b"].post(f"/api/batches/{batch}/manual", json={})).status_code == 404


async def test_a_manual_listing_is_checked_like_any_other_and_its_description_follows_the_title(world) -> None:  # noqa: F811
    batch = await _batch(world, groups=("g1",))
    await world["a"].post(f"/api/batches/{batch}/manual", json={"group_key": "g1"})
    [card] = (await world["a"].get(f"/api/batches/{batch}/content")).json()

    # Empty: it cannot be approved, and the reasons are the same rules as an AI listing's.
    refused = await world["a"].post(f"/api/content/{card['id']}/approve", json={"approved": True})
    assert refused.status_code == 422
    errors = " ".join(refused.json()["detail"]["errors"]).lower()
    assert "title" in errors and "13" in errors

    # A tag over 20 characters and a duplicate are refused too.
    bad = await world["a"].patch(f"/api/content/{card['id']}", json={
        "title": TITLE, "tags": ["this tag is far too long for etsy", "frog", "Frog", *TAGS[:10]]})
    said = " ".join(bad.json()["validation"]["errors"]).lower()
    assert bad.json()["validation"]["valid"] is False and "20" in said and "duplicate" in said

    # The long style opens the description with the title: it follows what the seller types.
    good = await world["a"].patch(f"/api/content/{card['id']}", json={"title": TITLE, "tags": TAGS, "description": card["description"]})
    assert good.json()["validation"]["valid"] is True, good.json()["validation"]
    assert good.json()["content"]["description"] == f"{TITLE}\n\nSizes for shop one."
    retitled = await world["a"].patch(f"/api/content/{card['id']}", json={
        "title": TITLE.replace("Vintage", "Retro"), "description": good.json()["content"]["description"]})
    assert retitled.json()["content"]["description"].startswith("Retro Frog Reading Club Shirt")
    # Once the seller edits the description it is theirs: the title no longer touches it.
    own = await world["a"].patch(f"/api/content/{card['id']}", json={"description": "My own words.\n\nSizes."})
    again = await world["a"].patch(f"/api/content/{card['id']}", json={"title": TITLE, "description": own.json()["content"]["description"]})
    assert again.json()["content"]["description"] == "My own words.\n\nSizes."
    assert (await world["a"].post(f"/api/content/{card['id']}/approve", json={"approved": True})).json()["content"]["approved"] is True


async def test_starting_from_the_reference_listing_reads_the_sellers_own_cache(world) -> None:  # noqa: F811
    batch = await _batch(world, groups=("g1",))
    await world["a"].post(f"/api/batches/{batch}/manual", json={"group_key": "g1"})
    [card] = (await world["a"].get(f"/api/batches/{batch}/content")).json()
    reference = 101 * 10  # the profile's reference listing (tests/test_multi_shop.py)

    # Not in the cache: the shop is read again first, nothing is made up.
    missing = await world["a"].get(f"/api/content/{card['id']}/reference-text")
    assert missing.status_code == 409 and ("sync_shop_listings", (str(world["a1"]),)) in world["queue"].calls
    async with world["sm"]() as s:
        row = ShopListingCache(tenant_id=world["alice"], connection_id=world["a1"], listing_id=reference,
                               payload={"listing_id": reference, "title": "Reference &amp; Title", "tags": ["one", "two"]},
                               fetched_at=datetime.now(timezone.utc))
        s.add(row)
        await s.commit()
    got = await world["a"].get(f"/api/content/{card['id']}/reference-text")
    assert got.status_code == 200
    assert (got.json()["title"], got.json()["tags"], got.json()["listing_id"]) == ("Reference & Title", ["one", "two"], reference)
    # Nothing was saved: the card fills its fields and the seller saves.
    assert (await world["a"].get(f"/api/batches/{batch}/content")).json()[0]["title"] == ""
    # Older than the six hours listing content may be shown: read again first.
    async with world["sm"]() as s:
        (await s.get(ShopListingCache, (world["alice"], reference))).fetched_at = datetime.now(timezone.utc) - timedelta(hours=7)
        await s.commit()
    assert (await world["a"].get(f"/api/content/{card['id']}/reference-text")).status_code == 409
    assert (await world["b"].get(f"/api/content/{card['id']}/reference-text")).status_code == 404


async def test_a_manual_listings_draft_records_no_model_and_a_manual_source(async_sm) -> None:
    ids = await publisher_seed(async_sm)
    fake = FakeEtsy()
    async with async_sm() as s:
        content = await s.get(GeneratedContent, ids[2])
        content.source, content.model_used, content.vision_model, content.attributes = "manual", None, None, None
        await s.commit()
        # Nothing in the draft needs what a vision call would have read.
        result = await publish_content(
            s, job_id=ids[3], content=content, connection=await s.get(EtsyConnection, ids[1]), sku="BR5475",
            thumbnail=PublishImage(b"t", "cover.jpg"), extra_images=[], client=fake, access_token="tok", config=CONFIG,
            reference=REFERENCE, theme="", occasion="", vision={}, tenant_limit=2000,
        )
        version = (await s.execute(select(ContentVersion))).scalars().one()
    assert result.listing_id and fake.last_listing["title"] == content.title
    assert (version.source, version.vision_model, version.content_model) == ("manual", None, None)
    # In neither of the app's title styles, so it is left out of their comparison.
    assert (version.title_style, version.reason) == ("manual", "manual")
    assert version.title_style not in versions.WRITTEN_STYLES


def test_what_a_manual_description_is() -> None:
    long_style = ListingProfile(name="Tee", content_template="apparel", listing_style="classic",
                                cached_payload={"description": "The reference title\n\nSizes.\nCare."})
    short_style = ListingProfile(name="Tee", content_template="apparel", listing_style="search",
                                 cached_payload={"description": "The reference title\n\nSizes.\nCare."})
    assert manual.description_for(long_style, "") == "Sizes.\nCare."
    assert manual.description_for(long_style, "My Title") == "My Title\n\nSizes.\nCare."
    # The short style opens with sentences about the design, which only the seller can write here.
    assert manual.description_for(short_style, "My Title") == "Sizes.\nCare."
    assert manual.description_for(None, "T") == "T"


# --- the approve step: shops only, kept on the listing ------------------------------------------------


def _cells(preview: dict, shop: uuid.UUID) -> list[dict]:
    return [next(c for c in row["cells"] if c["connection_id"] == str(shop)) for row in preview["rows"]]


async def test_ticked_shops_are_kept_on_the_listing_and_drafts_go_there(world) -> None:  # noqa: F811
    batch, (first, second) = world["batch"], world["contents"]
    async with world["sm"]() as s:  # the batch is for shop one; one listing is not approved yet
        (await s.get(UploadBatch, batch)).connection_id = world["a1"]
        (await s.get(GeneratedContent, second)).approved = False
        await s.commit()
    before = (await world["a"].post(f"/api/batches/{batch}/publish/preview", json={})).json()
    # By default each listing goes to the shop it was written for, and nowhere else.
    assert [c["wanted"] for c in _cells(before, world["a1"])] == [True, True]
    assert [c["wanted"] for c in _cells(before, world["a2"])] == [False, False]
    assert [r["profile_name"] for r in before["rows"]] == ["Standard Tee", "Standard Tee"]
    assert "profiles" in before["columns"][0]  # still sent; the page no longer offers a profile per shop

    # Tick the second shop for every listing, the unapproved one included.
    ticked = await world["a"].put(f"/api/batches/{batch}/targets", json={"connection_id": str(world["a2"]), "on": True})
    assert ticked.status_code == 200 and ticked.json()["changed"] == 2
    after = (await world["a"].post(f"/api/batches/{batch}/publish/preview", json={})).json()
    cells = _cells(after, world["a2"])
    assert [c["wanted"] for c in cells] == [True, True]
    assert [(c["state"], c["chosen"]) for c in cells] == [("available", True), ("unavailable", False)]
    assert cells[1]["reason"] == "not approved yet"
    assert [c["ticked"] for c in after["columns"]] == [2, 2] and after["drafts"] == 2

    # Approved afterwards: the tick is still there, so the draft goes to both shops.
    await world["a"].post(f"/api/content/{second}/approve", json={"approved": True})
    sent = await world["a"].post(f"/api/batches/{batch}/publish")
    assert sent.status_code == 200, sent.text
    assert sorted((j["content_id"], j["connection_id"]) for j in sent.json()["jobs"]) == sorted(
        (str(c), str(shop)) for c in (first, second) for shop in (world["a1"], world["a2"]))
    # One listing's own "Create draft" sends it to its ticked shops too.
    async with world["sm"]() as s:
        states = await group_state.group_states(s, batch_ids={batch})
        assert {world["a1"], world["a2"]} <= next(iter(states.values())).targets

    # Unticking the shop it was written for is a choice too, and it is kept.
    off = await world["a"].put(f"/api/batches/{batch}/targets", json={
        "connection_id": str(world["a1"]), "on": False, "content_ids": [str(first)]})
    assert off.json()["changed"] == 1
    again = (await world["a"].post(f"/api/batches/{batch}/publish/preview", json={})).json()
    row = next(r for r in again["rows"] if r["content_id"] == str(first))
    assert {c["connection_id"]: c["wanted"] for c in row["cells"]} == {str(world["a1"]): False, str(world["a2"]): True}
    # Another account's shop, or batch, is not there.
    assert (await world["a"].put(f"/api/batches/{batch}/targets", json={"connection_id": str(world["b1"]), "on": True})).status_code == 404
    assert (await world["b"].put(f"/api/batches/{batch}/targets", json={"connection_id": str(world["b1"]), "on": True})).status_code == 404


async def test_a_profile_sent_for_a_shop_is_not_used(world) -> None:  # noqa: F811
    """The approve step no longer chooses a profile: every listing is built with
    its own, whatever a client sends for a shop."""
    async with world["sm"]() as s:
        other = await _profile(s, world["alice"], await s.get(EtsyConnection, world["a1"]), name="Hoodie", prefix="Heavy")
        await s.commit()
        other_id = other.id
    res = await world["a"].post(f"/api/batches/{world['batch']}/publish", json={
        "targets": [{"connection_id": str(world["a1"]), "profile_id": str(other_id)}]})
    assert res.status_code == 200, res.text
    async with world["sm"]() as s:
        from app.db.models import Job

        jobs = (await s.execute(select(Job))).scalars().all()
        assert {j.payload["profile_id"] for j in jobs} == {str(world["pa1"])}


async def test_ticking_a_shop_sets_the_profile_up_there_and_says_what_the_shop_needs(world) -> None:  # noqa: F811
    batch, (first, _second) = world["batch"], world["contents"]
    async with world["sm"]() as s:
        # A profile that lives in shop one only (no same-named profile in shop two).
        hoodie = await _profile(s, world["alice"], await s.get(EtsyConnection, world["a1"]), name="Hoodie", prefix=PREFIX_A,
                                body="Hoodie sizes.")
        hoodie.cached_payload = {**hoodie.cached_payload, "shipping_profile_id": 11, "return_policy_id": 22}
        (await s.get(UploadBatch, batch)).connection_id = world["a1"]
        for cid in world["contents"]:
            (await s.get(GeneratedContent, cid)).listing_profile_id = hoodie.id
        await s.commit()
        hoodie_id = hoodie.id

    res = await world["a"].put(f"/api/batches/{batch}/targets", json={"connection_id": str(world["a2"]), "on": True})
    assert res.status_code == 200, res.text
    # As "Use in" does: one link, one job that reads the shops and links exact matches.
    assert [p["name"] for p in res.json()["setting_up"]] == ["Hoodie"]
    assert world["queue"].calls.count(("link_profile", (str(hoodie_id),))) == 1
    async with world["sm"]() as s:
        link = (await s.execute(select(ProfileShopLink).where(
            ProfileShopLink.profile_id == hoodie_id, ProfileShopLink.connection_id == world["a2"]))).scalars().one()
        assert link.status == "checking"

    def needs(preview: dict) -> list[dict]:
        return next(c for c in preview["columns"] if c["connection_id"] == str(world["a2"]))["needs"]

    # While it is being set up: said per shop, and nothing is created there.
    checking = (await world["a"].post(f"/api/batches/{batch}/publish/preview", json={})).json()
    assert [(n["kind"], n["listings"], n["blocking"], n["checking"]) for n in needs(checking)] == [("checking", 2, True, True)]
    assert [c["state"] for c in _cells(checking, world["a2"])] == ["unavailable", "unavailable"]
    # Ticked again: nothing is started twice.
    await world["a"].put(f"/api/batches/{batch}/targets", json={"connection_id": str(world["a2"]), "on": True})
    assert world["queue"].calls.count(("link_profile", (str(hoodie_id),))) == 1

    # The shop has no matching shipping profile or return policy: each is named, per shop, before approving.
    async with world["sm"]() as s:
        link = (await s.execute(select(ProfileShopLink).where(
            ProfileShopLink.profile_id == hoodie_id, ProfileShopLink.connection_id == world["a2"]))).scalars().one()
        link.status = "incomplete"
        link.notes = {"shipping_profile": 'no shipping profile named "Standard" in this shop'}
        await s.commit()
    incomplete = (await world["a"].post(f"/api/batches/{batch}/publish/preview", json={})).json()
    said = {n["kind"]: n for n in needs(incomplete)}
    assert set(said) == {"shipping_profile", "return_policy"}
    assert 'no shipping profile named "Standard"' in said["shipping_profile"]["message"]
    assert all(n["blocking"] and n["listings"] == 2 and n["profile_name"] == "Hoodie" for n in said.values())
    # "Create drafts" skips those with the reason: nothing fails silently, the own shop still goes.
    sent = await world["a"].post(f"/api/batches/{batch}/publish")
    assert {j["connection_id"] for j in sent.json()["jobs"]} == {str(world["a1"])}
    assert {s["connection_id"] for s in sent.json()["skipped"]} == {str(world["a2"])}
    assert all("Hoodie" in s["reason"] for s in sent.json()["skipped"])

    # Linked: the cells can be created and the shop needs nothing more.
    async with world["sm"]() as s:
        link = (await s.execute(select(ProfileShopLink).where(
            ProfileShopLink.profile_id == hoodie_id, ProfileShopLink.connection_id == world["a2"]))).scalars().one()
        link.status, link.notes, link.shipping_profile_id, link.return_policy_id = "ready", None, 911, 922
        await s.commit()
    ready = (await world["a"].post(f"/api/batches/{batch}/publish/preview", json={})).json()
    assert needs(ready) == [] and [c["state"] for c in _cells(ready, world["a2"])] == ["available", "available"]

    # A section chosen in shop one that shop two does not have: the draft is still made, and it is said.
    now = datetime.now(timezone.utc)
    async with world["sm"]() as s:
        one, two = await s.get(EtsyConnection, world["a1"]), await s.get(EtsyConnection, world["a2"])
        one.sections, one.sections_at = [{"id": 5, "title": "Frogs"}], now
        two.sections, two.sections_at = [{"id": 9, "title": "Mugs"}], now
        (await s.get(GeneratedContent, first)).item_options = {"sections": {str(world["a1"]): {"id": 5, "title": "Frogs"}}}
        await s.commit()
    with_section = (await world["a"].post(f"/api/batches/{batch}/publish/preview", json={})).json()
    [section] = needs(with_section)
    assert (section["kind"], section["blocking"], section["listings"]) == ("section", False, 1)
    assert 'Section "Frogs" is not in this shop' in section["message"]


# --- bug a: applying something to every listing ---------------------------------------------------------


async def test_the_cards_keep_one_order_whatever_is_applied_to_all(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        batch = UploadBatch(tenant_id=world["alice"], status=UploadBatchStatus.ready, file_count=4, connection_id=world["a1"])
        s.add(batch)
        await s.flush()
        # Written in an order that is not the groups' order; every cover has rank 1.
        for key in ("NW1047", "NW1042", "NW1045", "NW1043"):
            asset = Asset(tenant_id=world["alice"], batch_id=batch.id, original_filename=f"{key}/front.png", group_key=key, rank=1,
                          status=AssetStatus.processed, storage_key=f"k/{key}", processed_key=f"k/{key}")
            s.add(asset)
            await s.flush()
            s.add(GeneratedContent(tenant_id=world["alice"], batch_id=batch.id, asset_id=asset.id, listing_profile_id=world["pa1"],
                                   title="t", tags=[], description="d"))
        await s.commit()
        batch_id = batch.id

    async def order() -> list[str]:
        return [c["group_key"] for c in (await world["a"].get(f"/api/batches/{batch_id}/content")).json()]

    expected = ["NW1042", "NW1043", "NW1045", "NW1047"]
    assert await order() == expected
    setting = {"enabled": True, "question_text": "Name on the shirt", "instructions": "", "required": False, "max_allowed_characters": None}
    res = await world["a"].post(f"/api/batches/{batch_id}/personalization", json={"personalization": setting})
    # The answer and every later read are in the same order as before.
    assert [c["group_key"] for c in res.json()] == expected and await order() == expected
    assert all(c["personalization"]["question_text"] == "Name on the shirt" for c in res.json())
    await world["a"].post(f"/api/batches/{batch_id}/approve-all")
    assert await order() == expected


# --- bug b: a profile chosen on a group --------------------------------------------------------------------


async def test_a_written_listing_follows_the_profile_chosen_on_its_group(world) -> None:  # noqa: F811
    batch = await _batch(world, groups=("g1",))
    async with world["sm"]() as s:
        hoodie = await _profile(s, world["alice"], await s.get(EtsyConnection, world["a1"]), name="Hoodie", prefix="Heavy Blend",
                                body="Hoodie sizes.")
        cover = (await s.execute(select(Asset).where(Asset.batch_id == batch, Asset.rank == 1))).scalars().one()
        content = GeneratedContent(
            tenant_id=world["alice"], batch_id=batch, asset_id=cover.id, listing_profile_id=world["pa1"], approved=True,
            title=f"{PREFIX_A} Retro Frog Tee, Cottagecore Shirt, Vintage Frog Graphic Top, Nature Lover Gift, Pond Life Crewneck Tee, Frog Mom",
            tags=["shirt", *[f"tag{n}" for n in range(12)]], description="Old title\n\nSizes for shop one.",
        )
        s.add(content)
        await s.commit()
        hoodie_id, content_id = hoodie.id, content.id

    res = await world["a"].put(f"/api/batches/{batch}/groups", json={"group_key": "g1", "profile_id": str(hoodie_id)})
    assert res.status_code == 200, res.text
    group = res.json()[0]
    # The card and the listing agree: the draft is built with the profile the card shows.
    assert (group["profile_id"], group["listing_profile_id"], group["listing_profile_name"]) == (str(hoodie_id), str(hoodie_id), "Hoodie")
    [card] = (await world["a"].get(f"/api/batches/{batch}/content")).json()
    assert (card["profile_id"], card["profile_name"]) == (str(hoodie_id), "Hoodie")
    # Its prefix and its description are the new profile's; the title's own words and the tags stay.
    assert card["title"].startswith("Heavy Blend Retro Frog Tee, Cottagecore Shirt") and PREFIX_A not in card["title"]
    assert card["description"] == f"{card['title']}\n\nHoodie sizes."
    assert card["tags"][0] == "shirt" and card["approved"] is False  # what would be sent changed: approve it again
    async with world["sm"]() as s:
        target = await resolve_target(s, await s.get(GeneratedContent, content_id), await s.get(EtsyConnection, world["a1"]))
        assert target.ok and target.profile.id == hoodie_id
        assert (await s.execute(select(AiCall))).scalars().all() == []  # no AI, nothing generated
        assert (await s.execute(select(AllowanceUse))).scalars().all() == []

    # Once it is on Etsy the listing keeps the profile it was built with, and says so.
    async with world["sm"]() as s:
        s.add(ListingPublication(tenant_id=world["alice"], content_id=content_id, connection_id=world["a1"], etsy_listing_id=77, state="draft"))
        await s.commit()
    refused = await world["a"].put(f"/api/batches/{batch}/groups", json={"group_key": "g1", "profile_id": str(world["pa1"])})
    assert refused.status_code == 409 and "already on Etsy" in refused.json()["detail"]
    assert (await world["a"].get(f"/api/batches/{batch}/groups")).json()[0]["profile_id"] == str(hoodie_id)


async def test_the_group_card_says_when_its_profile_is_not_set_up_in_its_shop(world) -> None:  # noqa: F811
    batch = await _batch(world, groups=("g1",))
    async with world["sm"]() as s:
        hoodie = await _profile(s, world["alice"], await s.get(EtsyConnection, world["a1"]), name="Hoodie")
        await s.commit()
        hoodie_id = hoodie.id

    async def card(body: dict) -> dict:
        res = await world["a"].put(f"/api/batches/{batch}/groups", json={"group_key": "g1", **body})
        assert res.status_code == 200, res.text
        return res.json()[0]

    # In its own shop, or in a shop a same-named profile lives in: nothing to say.
    assert (await card({"profile_id": str(hoodie_id)}))["profile_setup"] is None
    assert (await card({"profile_id": str(world["pa1"]), "connection_id": str(world["a2"])}))["profile_setup"] is None
    # A profile that lives in shop one, on a group that goes to shop two: said where it is chosen.
    note = (await card({"profile_id": str(hoodie_id)}))["profile_setup"]
    assert (note["profile_name"], note["shop_name"], note["setup"]) == ("Hoodie", "Frost Mugs", True)
    assert "not set up in this shop" in note["reason"]
    # Being set up, then missing something: the card follows.
    async with world["sm"]() as s:
        link = ProfileShopLink(tenant_id=world["alice"], profile_id=hoodie_id, connection_id=world["a2"], status="checking")
        s.add(link)
        await s.commit()
    note = (await world["a"].get(f"/api/batches/{batch}/groups")).json()[0]["profile_setup"]
    assert (note["checking"], note["setup"]) == (True, False)
    async with world["sm"]() as s:
        link = (await s.execute(select(ProfileShopLink).where(ProfileShopLink.profile_id == hoodie_id))).scalars().first()
        link.status = "ready"
        await s.commit()
    assert (await world["a"].get(f"/api/batches/{batch}/groups")).json()[0]["profile_setup"] is None


async def test_the_card_names_the_shop_the_listing_goes_to_not_its_profiles_shop(world) -> None:  # noqa: F811
    """"Written for" on the review card and the matrix's own shop were two different
    answers once a group was moved to another shop."""
    batch = await _batch(world, groups=("g1",), shop="a2")  # the group is in shop two; its profile lives in shop one
    await world["a"].post(f"/api/batches/{batch}/manual", json={"group_key": "g1"})
    [card] = (await world["a"].get(f"/api/batches/{batch}/content")).json()
    preview = (await world["a"].post(f"/api/batches/{batch}/publish/preview", json={})).json()
    assert card["connection_id"] == preview["rows"][0]["own_connection_id"] == str(world["a2"])


async def test_a_listings_own_text_is_not_listed_as_something_a_shop_needs(world) -> None:  # noqa: F811
    """An empty manual listing ticked for a shop served by a same-named profile:
    what is wrong is its title and tags, said on its cell, not a need of the shop."""
    batch = await _batch(world, groups=("g1",))
    await world["a"].post(f"/api/batches/{batch}/manual", json={"group_key": "g1"})
    await world["a"].put(f"/api/batches/{batch}/targets", json={"connection_id": str(world["a2"]), "on": True})
    preview = (await world["a"].post(f"/api/batches/{batch}/publish/preview", json={})).json()
    column = next(c for c in preview["columns"] if c["connection_id"] == str(world["a2"]))
    cell = _cells(preview, world["a2"])[0]
    assert column["needs"] == [] and cell["wanted"] is True and cell["state"] == "unavailable" and cell["by_shop"] is False
