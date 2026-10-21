"""Calculated shipping: a draft carries the reference listing's weight and packed size.

Production (2026-10-22): every draft for the profile "Regular - PCS" failed with "Could not
set a calculated shipping profile to the listing. The listing is missing 'item_weight',
'item_length', 'item_width', 'item_height', 'item_weight_unit' or 'item_dimensions_unit'."
The reference listing on Etsy had them (shipping profile "free66", calculated; 0 lb 4 oz;
10 x 8 x 2 in). Both halves were missing: the profile refresh never stored the six fields
and createDraftListing never sent them.

Now the refresh stores them exactly as Etsy gives them (one number, one unit) with the
shipping profile's kind; every draft sends them (generated, edited, hand-written, another
shop); a calculated profile with none is never sent and says what to add; profiles read
before are read again once, at most two upkeep requests each.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from app.db.models import EtsyConnection, GeneratedContent, ListingProfile, ProfileShopLink
from app.etsy.errors import EtsyClientError, explain
from app.etsy.publisher import PublishImage, publish_content
from app.pipeline import links, reference, unfinished
from app.workers import profiles as worker
from tests.test_publisher import CONFIG, REFERENCE, FakeEtsy
from tests.test_publisher import _seed as publisher_seed
from tests.test_workers_profiles import FakeEtsy as RefreshEtsy
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_workers_profiles import _patch, _seed as worker_seed

#: What Etsy's getListing gives for "0 lb 4 oz" and a 10 x 8 x 2 in packed size.
ETSY_SIZE = {"item_weight": 4, "item_weight_unit": "oz", "item_length": 10, "item_width": 8, "item_height": 2,
             "item_dimensions_unit": "in"}
CALCULATED = {**REFERENCE, **ETSY_SIZE, "shipping_profile_type": "calculated"}
SENT = {"item_weight": 4.0, "item_weight_unit": "oz", "item_length": 10.0, "item_width": 8.0, "item_height": 2.0,
        "item_dimensions_unit": "in"}


async def _draft(async_sm, reference_payload: dict[str, Any], *, edit=None) -> FakeEtsy:
    ids = await publisher_seed(async_sm)
    fake = FakeEtsy()
    async with async_sm() as s:
        content = await s.get(GeneratedContent, ids[2])
        if edit:
            edit(content)
            await s.commit()
        await publish_content(
            s, job_id=ids[3], content=content, connection=await s.get(EtsyConnection, ids[1]), sku="BR5475",
            thumbnail=PublishImage(b"t", "cover.jpg"), extra_images=[], client=fake, access_token="tok", config=CONFIG,
            reference=reference_payload, theme="", occasion="", vision={}, tenant_limit=2000,
        )
    return fake


# --- reading -------------------------------------------------------------------------------------


def test_the_profile_stores_the_weight_and_size_exactly() -> None:
    payload = reference.build_profile_payload({**ETSY_SIZE, "shipping_profile_id": 55}, {}, {"results": []})
    assert {k: payload[k] for k in reference.SIZE_FIELDS} == ETSY_SIZE
    # A fraction of a pound stays a fraction, in pounds; a zero is never a weight.
    assert reference.shipping_size({**ETSY_SIZE, "item_weight": 0.25, "item_weight_unit": "lb"})["item_weight"] == 0.25
    assert "item_weight" not in reference.shipping_size({**ETSY_SIZE, "item_weight": 0})
    assert "item_length" not in reference.shipping_size({**ETSY_SIZE, "item_height": None})
    assert reference.shipping_size({**ETSY_SIZE, "item_weight_unit": "stone"}).get("item_weight") is None


async def test_the_refresh_reads_the_shipping_kind_and_the_size(async_sm, monkeypatch) -> None:
    tenant_id, profile_id = await worker_seed(async_sm)

    class Etsy(RefreshEtsy):
        async def get_listing(self, listing_id: int, **_: Any) -> dict[str, Any]:
            return {**await super().get_listing(listing_id), **ETSY_SIZE, "shipping_profile_id": 66, "shop_id": 900}

        async def get_shop_shipping_profile(self, shop_id: int, shipping_profile_id: int, **_: Any) -> dict[str, Any]:
            return {"shipping_profile_id": shipping_profile_id, "title": "free66", "profile_type": "calculated"}

    _patch(monkeypatch, tenant_id, Etsy())
    assert await worker.refresh_profile({"sessionmaker": async_sm, "bucket": None, "quota": None}, str(profile_id)) == "refreshed"
    async with async_sm() as s:
        payload = (await s.get(ListingProfile, profile_id)).cached_payload
    assert payload["shipping_profile_type"] == "calculated"
    assert reference.shipping_size(payload) == SENT and reference.size_problem(payload) is None


# --- sending: every path -------------------------------------------------------------------------


async def test_a_generated_listing_sends_the_weight_and_size(async_sm) -> None:
    fake = await _draft(async_sm, CALCULATED)
    assert {k: fake.last_listing[k] for k in SENT} == SENT


async def test_an_edited_and_saved_listing_sends_them(async_sm) -> None:
    def edit(content: GeneratedContent) -> None:
        content.title, content.tags = content.title.replace("Adventure", "Toad"), [*content.tags[:12], "edited tag"]

    fake = await _draft(async_sm, CALCULATED, edit=edit)
    assert "Toad" in fake.last_listing["title"] and {k: fake.last_listing[k] for k in SENT} == SENT


async def test_a_hand_written_listing_sends_them(async_sm) -> None:
    def hand(content: GeneratedContent) -> None:
        content.source, content.model_used, content.attributes = "manual", None, None
        content.title, content.tags = "Frog Reading Club Shirt", ["frog shirt", "book lover", "pond life", "reader", "cottage"]

    fake = await _draft(async_sm, CALCULATED, edit=hand)
    assert {k: fake.last_listing[k] for k in SENT} == SENT


async def test_the_same_listing_in_another_shop_sends_them(async_sm) -> None:
    # Another shop's reference: its own ids, the same weight and size; the main shop's
    # shipping kind is not taken for this shop's own profile.
    link = ProfileShopLink(profile_id=uuid.uuid4(), connection_id=uuid.uuid4(), shipping_profile_id=77,
                           return_policy_id=78, readiness_state_id=79, production_partner_ids=[7], status="ready")
    other = links.shop_reference(CALCULATED, link)
    assert other["shipping_profile_id"] == 77 and "shipping_profile_type" not in other
    fake = await _draft(async_sm, other)
    assert fake.last_listing["shipping_profile_id"] == 77
    assert {k: fake.last_listing[k] for k in SENT} == SENT


# --- no weight or size on a calculated reference ----------------------------------------------------


async def test_a_calculated_profile_without_size_is_never_sent_and_says_what_to_add(async_sm) -> None:
    bare = {**REFERENCE, "shipping_profile_type": "calculated"}
    assert reference.size_problem(bare) == reference.SIZE_FIX
    with pytest.raises(ValueError, match="add them to the reference listing on Etsy, then press Refresh"):
        await _draft(async_sm, bare)


async def test_a_manual_shipping_profile_never_needs_them(async_sm) -> None:
    fake = await _draft(async_sm, {**REFERENCE, "shipping_profile_type": "manual"})
    assert "item_weight" not in fake.last_listing  # none sent that the reference does not have


def test_etsys_refusal_and_old_stored_errors_read_as_the_same_line() -> None:
    refusal = ("Could not set a calculated shipping profile to the listing. The listing is missing 'item_weight', "
               "'item_length', 'item_width', 'item_height', 'item_weight_unit' or 'item_dimensions_unit'.")
    exc = EtsyClientError(400, body=json.dumps({"error": refusal}))
    assert explain(exc) == reference.SIZE_FIX
    stored = f"Etsy refused it while creating the draft: {refusal}"
    said = unfinished.plain_error(stored, "PCS")
    assert "finish the draft" in said and reference.SIZE_FIX in said


async def test_the_profile_card_says_it(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        profile.cached_payload = {**profile.cached_payload, "shipping_profile_type": "calculated",
                                  **{k: None for k in reference.SIZE_FIELDS}}
        await s.commit()
    card = next(p for p in (await world["a"].get("/api/profiles")).json() if p["id"] == str(world["pa1"]))
    assert card["shipping_note"] == reference.SIZE_FIX
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        profile.cached_payload = {**profile.cached_payload, **ETSY_SIZE}
        await s.commit()
    card = next(p for p in (await world["a"].get("/api/profiles")).json() if p["id"] == str(world["pa1"]))
    assert card["shipping_note"] is None


# --- the one-time job ----------------------------------------------------------------------------


async def test_profiles_read_before_get_their_size_once_within_two_requests(async_sm, monkeypatch) -> None:
    tenant_id, profile_id = await worker_seed(async_sm)
    async with async_sm() as s:
        profile = await s.get(ListingProfile, profile_id)
        profile.cached_payload = {**REFERENCE, "shipping_profile_id": 66}  # read before: no kind, no size
        manual = ListingProfile(tenant_id=tenant_id, connection_id=profile.connection_id, name="Manual",
                                reference_listing_id=222, cached_payload={**REFERENCE, "shipping_profile_id": 67})
        fresh = ListingProfile(tenant_id=tenant_id, connection_id=profile.connection_id, name="Fresh", reference_listing_id=333,
                               cached_payload={**CALCULATED})
        s.add_all([manual, fresh])
        await s.commit()
        manual_id = manual.id

    queued: list[tuple] = []

    async def enqueue(ctx, function, *args, **kwargs):  # noqa: ANN001
        queued.append((function, *args))

    monkeypatch.setattr(worker, "_enqueue_job", enqueue)
    monkeypatch.setattr(worker.get_settings(), "etsy_calls_blocked", False)

    class Redis:
        def __init__(self) -> None:
            self.data: dict[str, str] = {}

        async def get(self, key):  # noqa: ANN001
            return self.data.get(key)

        async def set(self, key, value):  # noqa: ANN001
            self.data[key] = value

    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None, "redis": Redis()}
    assert await worker.read_reference_sizes_once(ctx) == "queued:2"
    assert sorted(q[1] for q in queued) == sorted([str(profile_id), str(manual_id)])  # not the one read already
    assert await worker.read_reference_sizes_once(ctx) == "done-before"

    calls: list[str] = []

    class Etsy:
        async def get_shop_shipping_profile(self, shop_id: int, shipping_profile_id: int, **_: Any) -> dict[str, Any]:
            calls.append(f"shipping:{shipping_profile_id}")
            return {"profile_type": "calculated" if shipping_profile_id == 66 else "manual"}

        async def get_listing(self, listing_id: int, **_: Any) -> dict[str, Any]:
            calls.append(f"listing:{listing_id}")
            return {"listing_id": listing_id, **ETSY_SIZE}

    _patch(monkeypatch, tenant_id, Etsy())
    assert await worker.read_reference_size(ctx, str(profile_id)) == "read:calculated"
    assert await worker.read_reference_size(ctx, str(manual_id)) == "read:manual"
    assert calls == ["shipping:66", "listing:111", "shipping:67"]  # two requests, then one
    async with async_sm() as s:
        read = (await s.get(ListingProfile, profile_id)).cached_payload
        other = (await s.get(ListingProfile, manual_id)).cached_payload
    assert reference.shipping_size(read) == SENT and read["taxonomy_id"] == REFERENCE["taxonomy_id"]
    assert other["shipping_profile_type"] == "manual" and reference.size_problem(other) is None


# --- Try again -----------------------------------------------------------------------------------


async def test_try_again_after_the_refusal_makes_one_draft(async_sm) -> None:
    ids = await publisher_seed(async_sm)

    class Refusing(FakeEtsy):
        async def create_draft_listing(self, shop_id: int, *, listing: dict[str, Any], **kw: Any):
            if "item_weight" not in listing:
                raise EtsyClientError(400, body=json.dumps({"error": "Could not set a calculated shipping profile to the listing."}))
            return await super().create_draft_listing(shop_id, listing=listing, **kw)

    fake = Refusing()
    args = dict(job_id=ids[3], sku="BR5475", thumbnail=PublishImage(b"t", "cover.jpg"), extra_images=[], client=fake,
                access_token="tok", config=CONFIG, theme="", occasion="", vision={}, tenant_limit=2000)
    async with async_sm() as s:
        content, connection = await s.get(GeneratedContent, ids[2]), await s.get(EtsyConnection, ids[1])
        with pytest.raises(EtsyClientError):  # the old profile, read before: no size
            await publish_content(s, content=content, connection=connection, reference={**REFERENCE}, **args)
        result = await publish_content(s, content=content, connection=connection, reference=CALCULATED, **args)
    # Etsy refused the first create, so nothing was made: the second is sent at once, and once.
    assert result.listing_id == 555 and fake.calls.count("create") == 1



# --- A3: what a draft costs ------------------------------------------------------------------------


class Answering(FakeEtsy):
    """Etsy answers each write with what it kept, as its API does."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []

    async def update_listing_inventory(self, listing_id, *, inventory, **kw):  # noqa: ANN001
        await super().update_listing_inventory(listing_id, inventory=inventory, **kw)
        return {"products": [{"sku": p["sku"]} for p in inventory["products"]]}

    async def get_listing_inventory(self, listing_id, **kw):  # noqa: ANN001
        self.reads.append("inventory")
        return await super().get_listing_inventory(listing_id, **kw)


async def test_a_draft_reads_nothing_etsys_answers_already_said(async_sm) -> None:
    """A draft read back what each write had just answered and the shop's sections on every
    draft. Each write's answer is used now, and the sections the review card offered."""
    from datetime import datetime, timezone

    ids = await publisher_seed(async_sm)
    fake = Answering()
    async with async_sm() as s:
        content, connection = await s.get(GeneratedContent, ids[2]), await s.get(EtsyConnection, ids[1])
        connection.sections, connection.sections_at = [{"id": 10, "title": "Tees"}], datetime.now(timezone.utc)
        await s.commit()
        await publish_content(
            s, job_id=ids[3], content=content, connection=connection, sku="BR5475",
            thumbnail=PublishImage(b"t", "cover.jpg"), extra_images=[], client=fake, access_token="tok", config=CONFIG,
            reference=CALCULATED, theme="", occasion="", vision={}, tenant_limit=2000,
        )
    assert "sections" not in fake.calls  # the sections the card offered (kept 24 hours)
    assert fake.reads == []  # the inventory's answer said what Etsy kept


async def test_stale_sections_and_silent_answers_are_still_read(async_sm) -> None:
    from datetime import datetime, timezone

    from app.etsy.publisher import _shop_sections

    ids = await publisher_seed(async_sm)
    fake = FakeEtsy()  # answers {} to writes: the reads stay
    async with async_sm() as s:
        connection = await s.get(EtsyConnection, ids[1])
        connection.sections, connection.sections_at = [{"id": 10, "title": "Tees"}], datetime(2020, 1, 1, tzinfo=timezone.utc)
        await _shop_sections(fake, connection, 900, {})
    assert "sections" in fake.calls
