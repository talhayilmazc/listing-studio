"""Listing health (pipeline/listing_health.py, api/listing_health.py, workers/listing_fixes.py).

Each scoring rule; a preview and an approval before any write; the 50-listing cap; the read-back; Undo for 7
days; a listing changed on Etsy left alone; the allowance rule said before a rewrite; another account 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.api import deps
from app.compliance import trademarks
from app.core import allowance
from app.db.models import (
    AllowanceUse,
    AuditLog,
    EtsyConnection,
    ListingFix,
    ListingProfile,
    ShopListingCache,
    Tenant,
)
from app.etsy.errors import EtsyError
from app.pipeline import listing_fixes as F
from app.pipeline import listing_health as H
from app.pipeline.llm import LLMResult, Usage
from app.workers import listing_fixes as jobs
from app.workers.retention import purge_expired_rows, purge_shop_etsy_content
from tests.test_api import client  # noqa: F401  (fixture)

GOOD_TAGS = ["hiking gear", "mountain lover", "camp tee", "outdoor top", "trail runner", "retro nature", "forest walk",
             "summit view", "park ranger", "weekend trek", "pine tree top", "nature lover", "alpine style"]


def listing(listing_id: int = 901, **over: Any) -> dict[str, Any]:
    return {
        "listing_id": listing_id, "state": "active", "title": "Retro Mountain Shirt, Green Cotton Hiking Top",
        "tags": list(GOOD_TAGS), "description": "A soft green cotton shirt with a retro mountain drawn across the chest. " * 4,
        "materials": ["cotton"], "images": [{"url_170x135": f"https://i.etsystatic.com/{i}.jpg"} for i in range(6)],
        "shop_section_id": 7, "taxonomy_id": 482, "url": f"https://www.etsy.com/listing/{listing_id}/x",
        **over,
    }


def by_id(health: H.Health) -> dict[str, H.Check]:
    return {c.id: c for c in health.checks}


# --- scoring: every rule -----------------------------------------------------------------------------


def test_a_tidy_listing_scores_100_and_says_nothing_to_fix() -> None:
    health = H.score(listing(), properties=[{"property_id": 1, "property_name": "Style", "value_ids": [3], "values": ["Retro"]}])
    assert health.score == 100 and health.to_fix == 0 and health.brands == []


def test_each_title_rule_is_etsys_or_says_it_is_ours() -> None:
    kinds = {c.id: c.kind for c in H.title_checks("Retro Mountain Shirt")}
    assert {k for k, v in kinds.items() if v == H.ETSY} == {"title_length", "title_characters", "title_once", "title_words"}
    assert {k for k, v in kinds.items() if v == H.ADVICE} == {"item_first", "repeats", "subjective", "commerce", "gift", "shorter"}


@pytest.mark.parametrize("title,check,verdict", [
    ("a" * 141, "title_length", H.FAIL),
    ("Frog 😀 Shirt", "title_characters", H.FAIL),
    ("Mom & Dad & Kids Shirt", "title_once", H.FAIL),
    (" ".join(f"w{i}" for i in range(15)), "title_words", H.WARN),
    ("Sunset Lake, Nurse Shirt", "item_first", H.WARN),
    ("Handmade Ceramic Mug", "item_first", H.NOTE),
    ("Nurse Shirt, Retro Nurse Top", "repeats", H.WARN),
    ("Cute Frog Shirt", "subjective", H.WARN),
    ("Frog Shirt, Free Shipping", "commerce", H.WARN),
    ("Frog Shirt, Gift for Her", "gift", H.WARN),
    ("One, Two, Three, Four, Five Shirt", "shorter", H.WARN),
    ("Frog Shirt, Toad Tee", "shorter", H.WARN),
])
def test_each_title_rule_catches_what_it_is_for(title: str, check: str, verdict: str) -> None:
    assert by_id(H.score(listing(title=title)))[check].verdict == verdict


@pytest.mark.parametrize("tags,check,verdict", [
    ([f"tag {i}" for i in range(14)], "tag_count", H.FAIL),
    (["a" * 21], "tag_length", H.FAIL),
    (["tag & more"], "tag_characters", H.FAIL),
    (["-dash first"], "tag_start", H.WARN),
    (["hiking gear", "Hiking Gear"], "tag_duplicates", H.WARN),
    (["hiking shirts", "shirt hiking"], "tag_duplicates", H.WARN),
    (["retro mountain"], "tag_title_words", H.WARN),
    ([f"word{i}" for i in range(9)], "tag_phrases", H.WARN),
    (["hiking gear"], "tag_all", H.WARN),
])
def test_each_tag_rule_catches_what_it_is_for(tags: list[str], check: str, verdict: str) -> None:
    assert by_id(H.score(listing(tags=tags)))[check].verdict == verdict


def test_attributes_materials_photos_description_and_section() -> None:
    bare = by_id(H.score(listing(materials=[], images=[{}], description="Short.", shop_section_id=None)))
    assert bare["materials"].verdict == bare["photos"].verdict == bare["description"].verdict == bare["section"].verdict == H.WARN
    assert "attributes" not in bare  # not read yet: left out, neither met nor missed
    assert by_id(H.score(listing(), properties=[]))["attributes"].verdict == H.WARN
    reference = [{"property_id": 2, "property_name": "Sleeve length", "value_ids": [9], "values": ["Short"]}]
    have = [{"property_id": 1, "property_name": "Style", "value_ids": [3], "values": ["Retro"]}]
    missing = by_id(H.score(listing(), properties=have, reference_attributes=reference))["attributes"]
    assert missing.verdict == H.WARN and "Sleeve length" in missing.detail
    assert by_id(H.score(listing(), properties=[*have, *reference], reference_attributes=reference))["attributes"].verdict == H.PASS


def test_personalization_is_only_asked_of_a_title_that_says_personalized() -> None:
    assert "personalization" not in by_id(H.score(listing(personalization=None)))
    said = listing(title="Personalized Name Shirt, Green Cotton Top")
    assert "personalization" not in by_id(H.score(said))  # not read by the sync: not judged
    assert by_id(H.score({**said, "personalization": None}))["personalization"].verdict == H.WARN
    asked = {**said, "personalization": {"personalization_questions": [{"question_text": "Name?"}]}}
    assert by_id(H.score(asked))["personalization"].verdict == H.PASS


def test_the_trademark_warning_is_the_apps_sentence_and_finds_any_form() -> None:
    health = H.score(listing(title="DisneyWorld Family Shirt", tags=["mick ey tee", *GOOD_TAGS[:12]]))
    assert sorted(health.brands) == ["Disney", "Mickey"]
    brand = by_id(health)["brand"]
    assert brand.verdict == H.WARN and trademarks.BRAND_WARNING.format(brand="Disney") in brand.detail


def test_what_cannot_be_judged_is_not_scored_and_a_long_keyword_listing_is_not_told_off() -> None:
    mug = H.score(listing(title="Handmade Ceramic Mug"), properties=[{"property_id": 1, "value_ids": [3], "values": ["x"]}])
    assert by_id(mug)["item_first"].verdict == H.NOTE and mug.score == 100
    title = "Nurse Shirt, Nurse Gift, Funny Nurse Tee, Nurse Life, Cute Nursing Top, RN Shirt, Hospital Shirt"
    assert by_id(H.score(listing(title=title)))["repeats"].verdict == H.WARN
    chosen = by_id(H.score(listing(title=title), short_style=False))
    assert "repeats" not in chosen and "subjective" not in chosen and "gift" not in chosen and "title_length" in chosen


# --- what a fix proposes ------------------------------------------------------------------------------


def test_added_tags_keep_the_listings_own_and_skip_what_breaks_the_rules() -> None:
    title = "Retro Mountain Shirt"
    added = F.added_tags(title, ["hiking gear"], ["Hiking Gears", "retro mountain", "disney tee", "cute top", "-dash", "a" * 21, "camp tee", "camp tees", "trail top"])
    assert added == ["camp tee", "trail top"]
    assert F.added_tags(title, [f"t {i}" for i in range(13)], ["camp tee"]) == []


def test_missing_attributes_never_overwrite_a_value() -> None:
    reference = [{"property_id": 1, "property_name": "Style", "value_ids": [3], "values": ["Retro"]},
                 {"property_id": 2, "property_name": "Sleeve", "value_ids": [9], "values": ["Short"]},
                 {"property_id": 3, "property_name": "Empty", "value_ids": [], "values": []}]
    have = [{"property_id": 1, "property_name": "Style", "value_ids": [4], "values": ["Modern"]}]
    assert [m["property_id"] for m in F.missing_attributes(have, reference)] == [2]


@pytest.mark.parametrize("data", [
    {"title": "", "tags": ["camp tee"]},
    {"title": "Disney Shirt", "tags": ["camp tee"]},
    {"title": "Cute Shirt", "tags": ["camp tee"]},
    {"title": "Frog Shirt Frog Tee", "tags": ["camp tee"]},
    {"title": "Frog Shirt", "tags": ["disney tee"]},
])
def test_what_the_model_writes_must_meet_the_rules(data: dict) -> None:
    assert F.usable_rewrite(data) is None


def test_a_usable_rewrite_drops_only_the_tags_that_fail() -> None:
    out = F.usable_rewrite({"title": "Retro Mountain Shirt, Green Cotton Top", "tags": ["camp tee", "disney tee", "-dash", "camp tees", "retro mountain", "trail walk"]})
    assert out == {"title": "Retro Mountain Shirt, Green Cotton Top", "tags": ["camp tee", "trail walk"]}


# --- preview, approval, the cap ------------------------------------------------------------------------


class Queue:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def enqueue(self, function: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((function, args))

    def named(self, function: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == function]


class Etsy:
    """A shop on the other end: records every write, and answers with what it holds."""

    def __init__(self, listings: dict[int, dict[str, Any]], properties: dict[int, list[dict]] | None = None) -> None:
        self.listings, self.properties = listings, properties or {}
        self.writes: list[tuple] = []
        self.keep_tags = True

    async def get_listing(self, listing_id: int, **_: Any) -> dict:
        return dict(self.listings[listing_id])

    async def get_listing_properties(self, shop_id: int, listing_id: int, **_: Any) -> dict:
        return {"results": [dict(p) for p in self.properties.get(listing_id, [])]}

    async def update_listing(self, shop_id: int, listing_id: int, *, updates: dict, **_: Any) -> dict:
        self.writes.append(("update", listing_id, dict(updates)))
        kept = dict(updates)
        if not self.keep_tags:
            kept.pop("tags", None)
        self.listings[listing_id].update(kept)
        return {}

    async def update_listing_property(self, shop_id: int, listing_id: int, property_id: int, *, value_ids=None, values=None, scale_id=None, **_: Any) -> dict:  # noqa: ANN001
        self.writes.append(("property", listing_id, property_id, value_ids, values))
        rest = [p for p in self.properties.get(listing_id, []) if p["property_id"] != property_id]
        self.properties[listing_id] = [*rest, {"property_id": property_id, "property_name": "x", "value_ids": value_ids or [], "values": values or []}]
        return {}

    async def delete_listing_property(self, shop_id: int, listing_id: int, property_id: int, **_: Any) -> dict:
        self.writes.append(("delete", listing_id, property_id))
        self.properties[listing_id] = [p for p in self.properties.get(listing_id, []) if p["property_id"] != property_id]
        return {}


def wire(monkeypatch, etsy: Etsy) -> dict:  # noqa: ANN001
    class Service:
        async def get_valid_access_token(self, session, connection):  # noqa: ANN001
            return "tok"

    async def shop_id(session, client, connection, kw):  # noqa: ANN001
        return connection.shop_id

    async def gated(ctx, function, arg, tenant_id, body):  # noqa: ANN001
        return await body()

    monkeypatch.setattr(jobs, "_build_client", lambda *a, **k: etsy)
    monkeypatch.setattr(jobs, "_connection_service", lambda settings: Service())
    monkeypatch.setattr(jobs, "_resolve_shop_id", shop_id)
    monkeypatch.setattr(jobs, "_run_gated", gated)
    return {}


async def world(client: AsyncClient, rows: list[dict] | None = None, *, reference: dict | None = None) -> dict:  # noqa: F811
    queue = Queue()
    client.app.dependency_overrides[deps.get_enqueuer] = lambda: queue  # type: ignore[attr-defined]
    tid = client.tenant_id  # type: ignore[attr-defined]
    async with client.sm() as s:  # type: ignore[attr-defined]
        shop = EtsyConnection(tenant_id=tid, etsy_user_id=31, shop_id=31, shop_name="Frost Tees")
        s.add(shop)
        await s.flush()
        s.add(ShopListingCache(tenant_id=tid, connection_id=shop.id, listing_id=500, payload={
            "listing_id": 500, "state": "active", "taxonomy_id": 482, "title": "Reference Shirt", "tags": ["lake house", "trail walk", "disney tee"],
            "description": "x" * 200, "materials": ["cotton"], "images": [{}] * 6, "shop_section_id": 1,
        }))
        profile = ListingProfile(tenant_id=tid, connection_id=shop.id, name="Tees", reference_listing_id=500, content_template="apparel",
                                 cached_payload={"taxonomy_id": 482, "attributes": [{"property_id": 2, "property_name": "Sleeve", "value_ids": [9], "values": ["Short"]}], **(reference or {})})
        s.add(profile)
        for row in rows if rows is not None else [listing()]:
            s.add(ShopListingCache(tenant_id=tid, connection_id=shop.id, listing_id=row["listing_id"], payload=row))
        await s.commit()
        return {"queue": queue, "shop": shop.id, "profile": profile.id, "tid": tid}


async def _preview(client, w, ids, kinds, **more) -> Any:  # noqa: ANN001, F811
    return await client.post("/api/listing-health/preview", json={"connection_id": str(w["shop"]), "listing_ids": ids, "kinds": kinds, **more})


async def test_the_page_scores_the_shops_active_listings_worst_first_and_reads_attributes_for_those_shown(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(), listing(902, title="Cute Shirt, Free Shipping"), listing(903, state="draft", title="Draft")])
    page = (await client.get("/api/listing-health", params={"shop": str(w["shop"]), "show": "all"})).json()
    assert page["reading"] is False and page["total"] == 3  # the reference listing is active too
    ids = [l["listing_id"] for l in page["listings"]]
    assert 903 not in ids and ids.index(902) < ids.index(901)
    row = next(l for l in page["listings"] if l["listing_id"] == 902)
    assert row["url"].startswith("https://www.etsy.com/listing/902") and row["profile_name"] == "Tees"  # the link back to the listing
    assert row["attributes_read"] is False
    assert w["queue"].named("read_listing_properties")
    # A listing page never writes.
    assert not w["queue"].named("apply_listing_fix")


async def test_a_copy_past_its_six_hours_is_not_scored_it_is_read_again(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client)
    async with client.sm() as s:  # type: ignore[attr-defined]
        for row in (await s.execute(select(ShopListingCache))).scalars():
            row.fetched_at = datetime.now(timezone.utc) - timedelta(hours=7)
        await s.commit()
    page = (await client.get("/api/listing-health", params={"shop": str(w["shop"])})).json()
    assert page["reading"] is True and page["listings"] == []
    assert w["queue"].named("sync_shop_listings")


async def test_a_preview_changes_nothing_on_etsy_and_only_approval_starts_a_write(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(tags=GOOD_TAGS[:5], _properties=[])])
    res = await _preview(client, w, [901], ["tags", "attributes"])
    assert res.status_code == 200, res.text
    fix = res.json()["fixes"][0]
    assert fix["state"] == "proposed" and set(fix["kinds"]) == {"tags", "attributes"}
    assert fix["before"]["tags"] == GOOD_TAGS[:5] and fix["after"]["tags"] == [*GOOD_TAGS[:5], "lake house", "trail walk"]  # from the reference, no brand
    assert fix["after"]["attributes"][0]["property_id"] == 2 and fix["before"]["attributes"][0]["value_ids"] == []
    assert not w["queue"].named("apply_listing_fix")
    # Unconfirmed: refused, nothing started.
    assert (await client.post("/api/listing-health/apply", json={"fix_ids": [fix["id"]]})).status_code == 422
    assert not w["queue"].named("apply_listing_fix")
    ok = await client.post("/api/listing-health/apply", json={"fix_ids": [fix["id"]], "confirm": True})
    assert ok.status_code == 200 and ok.json()["started"] == 1
    assert w["queue"].named("apply_listing_fix") == [("apply_listing_fix", (fix["id"],))]
    # Approved once: pressing again starts nothing.
    assert (await client.post("/api/listing-health/apply", json={"fix_ids": [fix["id"]], "confirm": True})).status_code == 409


async def test_at_most_50_live_listings_per_approval(client: AsyncClient) -> None:  # noqa: F811
    rows = [listing(1000 + i, tags=GOOD_TAGS[:5]) for i in range(51)]
    w = await world(client, rows)
    ids = [r["listing_id"] for r in rows]
    too_many = await _preview(client, w, ids, ["tags"])
    assert too_many.status_code == 422 and "At most 50" in too_many.text
    res = await _preview(client, w, ids[:50], ["tags"])
    assert len(res.json()["fixes"]) == 50
    extra = await _preview(client, w, ids[50:], ["tags"])
    all_ids = [f["id"] for f in res.json()["fixes"]] + [f["id"] for f in extra.json()["fixes"]]
    refused = await client.post("/api/listing-health/apply", json={"fix_ids": all_ids, "confirm": True})
    assert refused.status_code == 422 and "At most 50" in refused.text and not w["queue"].named("apply_listing_fix")
    assert (await client.post("/api/listing-health/apply", json={"fix_ids": all_ids[:50], "confirm": True})).json()["started"] == 50
    undo = await client.post("/api/listing-health/undo", json={"fix_ids": all_ids, "confirm": True})
    assert undo.status_code == 422 and "At most 50" in undo.text


async def test_a_preview_past_six_hours_cannot_be_approved(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(tags=GOOD_TAGS[:5])])
    fix = (await _preview(client, w, [901], ["tags"])).json()["fixes"][0]
    async with client.sm() as s:  # type: ignore[attr-defined]
        row = await s.get(ListingFix, uuid.UUID(fix["id"]))
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=7)
        await s.commit()
    res = await client.post("/api/listing-health/apply", json={"fix_ids": [fix["id"]], "confirm": True})
    assert res.json()["started"] == 0 and res.json()["expired"] == 1 and "preview again" in res.json()["message"]
    assert not w["queue"].named("apply_listing_fix")


async def test_two_profiles_that_fit_are_the_sellers_choice_and_the_choice_settles_only_that(client: AsyncClient) -> None:  # noqa: F811
    other_category = listing(902, tags=GOOD_TAGS[:5], taxonomy_id=99)
    w = await world(client, [listing(tags=GOOD_TAGS[:5]), other_category])
    async with client.sm() as s:  # type: ignore[attr-defined]
        second = ListingProfile(tenant_id=w["tid"], connection_id=w["shop"], name="Other tees", reference_listing_id=501, content_template="apparel", cached_payload={"taxonomy_id": 482})
        s.add(second)
        s.add(ShopListingCache(tenant_id=w["tid"], connection_id=w["shop"], listing_id=501, payload={"listing_id": 501, "state": "draft", "tags": ["river cabin"]}))
        await s.commit()
        second_id = second.id
    row = next(l for l in (await client.get("/api/listing-health", params={"shop": str(w["shop"]), "show": "all"})).json()["listings"] if l["listing_id"] == 901)
    assert row["profile_unclear"] is True and row["profile_id"] is None
    asked = (await _preview(client, w, [901], ["tags"])).json()
    assert asked["fixes"] == [] and "no profile" in asked["skipped"][0]["reason"]
    chosen = (await _preview(client, w, [901, 902], ["tags"], profile_id=str(second_id))).json()
    assert [f["listing_id"] for f in chosen["fixes"]] == [901]  # 902's category fits neither: nothing is copied into it
    assert chosen["fixes"][0]["after"]["tags"][-1] == "river cabin"


# --- the write, the read-back and Undo ----------------------------------------------------------------


async def _approved(client, w, row: dict, kinds=("tags",)) -> str:  # noqa: ANN001, F811
    fix = (await _preview(client, w, [row["listing_id"]], list(kinds))).json()["fixes"][0]
    await client.post("/api/listing-health/apply", json={"fix_ids": [fix["id"]], "confirm": True})
    return fix["id"]


async def test_applying_writes_what_was_previewed_reads_it_back_and_keeps_the_copy(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5], _properties=[])
    w = await world(client, [row])
    etsy = Etsy({901: dict(row)}, {901: []})
    wire(monkeypatch, etsy)
    fix_id = await _approved(client, w, row, ("tags", "attributes"))
    ctx = {"sessionmaker": client.sm}  # type: ignore[attr-defined]
    assert await jobs.apply_listing_fix(ctx, fix_id) == "applied"
    assert ("update", 901, {"tags": [*GOOD_TAGS[:5], "lake house", "trail walk"]}) in etsy.writes
    assert ("property", 901, 2, [9], ["Short"]) in etsy.writes
    assert all("state" not in w_[2] for w_ in etsy.writes if w_[0] == "update")  # a fix never changes a listing's state
    async with client.sm() as s:  # type: ignore[attr-defined]
        fix = await s.get(ListingFix, uuid.UUID(fix_id))
        assert fix.state == "applied" and fix.before["tags"] == GOOD_TAGS[:5]
        until = fix.undo_until if fix.undo_until.tzinfo else fix.undo_until.replace(tzinfo=timezone.utc)
        assert timedelta(days=6) < until - datetime.now(timezone.utc) <= timedelta(days=7)
        assert (await s.execute(select(AuditLog).where(AuditLog.action == "listing.health_fix_applied"))).scalars().first() is not None
        cached = await s.get(ShopListingCache, (w["tid"], 901))
        assert len(cached.payload["tags"]) == 7  # the page shows it as it now is
    listed = (await client.get("/api/listing-health/fixes", params={"shop": str(w["shop"])})).json()["fixes"]
    assert listed[0]["state"] == "applied" and listed[0]["can_undo"] is True


async def test_a_read_back_that_differs_is_said_and_undo_is_still_offered(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    etsy = Etsy({901: dict(row)})
    etsy.keep_tags = False
    wire(monkeypatch, etsy)
    fix_id = await _approved(client, w, row)
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix_id) == "not-kept"  # type: ignore[attr-defined]
    listed = (await client.get("/api/listing-health/fixes", params={"shop": str(w["shop"])})).json()["fixes"][0]
    assert listed["state"] == "failed" and "did not keep the tags" in listed["note"] and listed["can_undo"] is True


async def test_a_listing_changed_on_etsy_after_the_preview_is_left_alone(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    etsy = Etsy({901: {**row, "tags": ["the seller's own edit"]}})
    wire(monkeypatch, etsy)
    fix_id = await _approved(client, w, row)
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix_id) == "changed"  # type: ignore[attr-defined]
    assert etsy.writes == []
    async with client.sm() as s:  # type: ignore[attr-defined]
        fix = await s.get(ListingFix, uuid.UUID(fix_id))
        assert fix.state == "changed" and "left as it is" in fix.note and fix.written_at is None


async def test_a_listing_no_longer_active_is_left_alone(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    etsy = Etsy({901: {**row, "state": "inactive"}})
    wire(monkeypatch, etsy)
    fix_id = await _approved(client, w, row)
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix_id) == "not-active"  # type: ignore[attr-defined]
    assert etsy.writes == []


async def test_a_run_cut_short_repeats_without_writing_twice(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    target = [*GOOD_TAGS[:5], "lake house", "trail walk"]
    etsy = Etsy({901: {**row, "tags": target}})  # the first run got this far
    wire(monkeypatch, etsy)
    fix_id = await _approved(client, w, row)
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix_id) == "applied"  # type: ignore[attr-defined]
    assert etsy.writes == []


async def test_a_job_that_was_not_approved_does_nothing(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    etsy = Etsy({901: dict(row)})
    wire(monkeypatch, etsy)
    fix = (await _preview(client, w, [901], ["tags"])).json()["fixes"][0]
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix["id"]) == "not-approved"  # type: ignore[attr-defined]
    assert etsy.writes == []


async def test_etsys_refusal_is_said_in_a_plain_sentence(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])

    class Refusing(Etsy):
        async def update_listing(self, *a: Any, **k: Any) -> dict:
            raise EtsyError("refused")

    wire(monkeypatch, Refusing({901: dict(row)}))
    fix_id = await _approved(client, w, row)
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix_id) == "failed"  # type: ignore[attr-defined]
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.get(ListingFix, uuid.UUID(fix_id))).note


async def _applied(client, w, row: dict, etsy: Etsy, monkeypatch, kinds=("tags",)) -> str:  # noqa: ANN001, F811
    wire(monkeypatch, etsy)
    fix_id = await _approved(client, w, row, kinds)
    assert await jobs.apply_listing_fix({"sessionmaker": client.sm}, fix_id) == "applied"  # type: ignore[attr-defined]
    etsy.writes.clear()
    return fix_id


async def test_undo_writes_back_the_copy_and_clears_what_was_empty(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5], _properties=[])
    w = await world(client, [row])
    etsy = Etsy({901: dict(row)}, {901: []})
    fix_id = await _applied(client, w, row, etsy, monkeypatch, ("tags", "attributes"))
    assert (await client.post("/api/listing-health/undo", json={"fix_ids": [fix_id]})).status_code == 422  # not confirmed
    res = await client.post("/api/listing-health/undo", json={"fix_ids": [fix_id], "confirm": True})
    assert res.status_code == 200 and w["queue"].named("undo_listing_fix")
    assert await jobs.undo_listing_fix({"sessionmaker": client.sm}, fix_id) == "undone"  # type: ignore[attr-defined]
    assert ("update", 901, {"tags": GOOD_TAGS[:5]}) in etsy.writes and ("delete", 901, 2) in etsy.writes
    assert etsy.listings[901]["tags"] == GOOD_TAGS[:5] and etsy.properties[901] == []
    listed = (await client.get("/api/listing-health/fixes", params={"shop": str(w["shop"])})).json()["fixes"][0]
    assert listed["state"] == "undone" and listed["can_undo"] is False


async def test_undo_restores_a_title_and_leaves_alone_what_the_seller_changed_since(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    etsy = Etsy({901: dict(row)})
    fix_id = await _applied(client, w, row, etsy, monkeypatch)
    etsy.listings[901]["tags"] = ["the seller's later edit"]
    await client.post("/api/listing-health/undo", json={"fix_ids": [fix_id], "confirm": True})
    assert await jobs.undo_listing_fix({"sessionmaker": client.sm}, fix_id) == "changed"  # type: ignore[attr-defined]
    assert etsy.writes == []
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.get(ListingFix, uuid.UUID(fix_id))).state == "undo_failed"


async def test_undo_lasts_7_days(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    fix_id = await _applied(client, w, row, Etsy({901: dict(row)}), monkeypatch)
    async with client.sm() as s:  # type: ignore[attr-defined]
        fix = await s.get(ListingFix, uuid.UUID(fix_id))
        fix.undo_until = datetime.now(timezone.utc) - timedelta(minutes=1)
        await s.commit()
    res = await client.post("/api/listing-health/undo", json={"fix_ids": [fix_id], "confirm": True})
    assert res.status_code == 409 and "7 days" in res.text and not w["queue"].named("undo_listing_fix")


async def test_the_copy_is_deleted_on_its_clock_and_with_the_shop(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    row = listing(tags=GOOD_TAGS[:5])
    w = await world(client, [row])
    fix_id = await _applied(client, w, row, Etsy({901: dict(row)}), monkeypatch)
    now = datetime.now(timezone.utc)
    async with client.sm() as s:  # type: ignore[attr-defined]
        await purge_expired_rows(s, now=now)
        assert await s.get(ListingFix, uuid.UUID(fix_id)) is not None  # within its 7 days
        await purge_expired_rows(s, now=now + timedelta(days=8))
        assert await s.get(ListingFix, uuid.UUID(fix_id)) is None

async def test_a_preview_nobody_approved_goes_after_six_hours_and_everything_goes_with_the_shop(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(tags=GOOD_TAGS[:5])])
    first = (await _preview(client, w, [901], ["tags"])).json()["fixes"][0]
    async with client.sm() as s:  # type: ignore[attr-defined]
        row = await s.get(ListingFix, uuid.UUID(first["id"]))
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=7)
        await s.commit()
        await purge_expired_rows(s, now=datetime.now(timezone.utc))
        assert await s.get(ListingFix, uuid.UUID(first["id"])) is None
        second = ListingFix(tenant_id=w["tid"], connection_id=w["shop"], listing_id=901, kinds=["tags"], state="applied", before={"tags": ["a"]}, after={"tags": ["a", "b"]})
        s.add(second)
        await s.commit()
        await purge_shop_etsy_content(s, w["shop"])
        await s.commit()
        assert await s.get(ListingFix, second.id) is None


# --- the rewrite and the allowance ---------------------------------------------------------------------


class Model:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {"title": "Retro Mountain Shirt, Green Cotton Top", "tags": ["camp tee", "trail walk", "forest hike"]}
        self.calls = 0

    async def complete_json(self, **kwargs: Any) -> LLMResult:
        self.calls += 1
        return LLMResult(data=self.data, usage=Usage(input_tokens=10, output_tokens=10, model="m"))


async def test_a_rewrite_must_be_asked_for_and_the_rule_is_said_first(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client)
    check = (await client.post("/api/listing-health/rewrite-check", json={"connection_id": str(w["shop"]), "listing_ids": [901]})).json()
    assert "rewrite rule" in check["rule"] and check["counted"] == 1 and check["free"] == 0  # the app never wrote it
    res = await _preview(client, w, [901], ["rewrite"])
    assert res.status_code == 422 and "uses your allowance" in res.text
    assert not w["queue"].named("write_listing_fix_text")
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.execute(select(AllowanceUse))).scalars().first() is None


async def test_a_rewrite_becomes_a_preview_and_counts_only_when_written(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    w = await world(client)
    res = await _preview(client, w, [901], ["rewrite"], accept_rewrite=True)
    fix = res.json()["fixes"][0]
    assert fix["state"] == "writing" and w["queue"].named("write_listing_fix_text")
    # Writing it does not approve it.
    assert (await client.post("/api/listing-health/apply", json={"fix_ids": [fix["id"]], "confirm": True})).status_code == 409
    model = Model()
    monkeypatch.setattr(jobs, "client_for", lambda settings, role: model)
    assert await jobs.write_listing_fix_text({"sessionmaker": client.sm}, fix["id"]) == "written"  # type: ignore[attr-defined]
    async with client.sm() as s:  # type: ignore[attr-defined]
        row = await s.get(ListingFix, uuid.UUID(fix["id"]))
        assert row.state == "proposed" and row.after["title"] == model.data["title"] and row.before["title"] == listing()["title"]
        uses = list((await s.execute(select(AllowanceUse))).scalars())
        assert [(u.kind, u.design) for u in uses] == [(allowance.GENERATION, "listing:901")]
    # A second rewrite of the same listing is a free one, while they last.
    check = (await client.post("/api/listing-health/rewrite-check", json={"connection_id": str(w["shop"]), "listing_ids": [901]})).json()
    assert check["free"] == 1 and check["counted"] == 0


async def test_an_unusable_rewrite_counts_nothing_and_says_so(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    w = await world(client)
    fix = (await _preview(client, w, [901], ["rewrite"], accept_rewrite=True)).json()["fixes"][0]
    monkeypatch.setattr(jobs, "client_for", lambda settings, role: Model({"title": "Disney Shirt", "tags": ["camp tee"]}))
    assert await jobs.write_listing_fix_text({"sessionmaker": client.sm}, fix["id"]) == "unusable"  # type: ignore[attr-defined]
    async with client.sm() as s:  # type: ignore[attr-defined]
        row = await s.get(ListingFix, uuid.UUID(fix["id"]))
        assert row.state == "could_not_write" and "Nothing was counted" in row.note
        assert (await s.execute(select(AllowanceUse))).scalars().first() is None


async def test_a_rewrite_past_the_allowance_is_refused_before_it_runs(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client)
    async with client.sm() as s:  # type: ignore[attr-defined]
        tenant = await s.get(Tenant, w["tid"])
        tenant.allowance_amount, tenant.allowance_period = 0, "monthly"
        await s.commit()
    res = await _preview(client, w, [901], ["rewrite"], accept_rewrite=True)
    assert res.status_code == 429 and not w["queue"].named("write_listing_fix_text")


# --- another account ------------------------------------------------------------------------------------


async def test_another_accounts_shop_listing_and_fix_are_404(client: AsyncClient) -> None:  # noqa: F811
    from tests.auth_support import make_tenant

    w = await world(client)
    other = await make_tenant(client.sm, "other@example.com")  # type: ignore[attr-defined]
    async with client.sm() as s:  # type: ignore[attr-defined]
        shop = EtsyConnection(tenant_id=other, etsy_user_id=77, shop_id=77, shop_name="Theirs")
        s.add(shop)
        await s.flush()
        s.add(ShopListingCache(tenant_id=other, connection_id=shop.id, listing_id=777, payload=listing(777, title="Their Secret Shirt")))
        fix = ListingFix(tenant_id=other, connection_id=shop.id, listing_id=777, kinds=["tags"], state="proposed", before={"tags": ["their tag"]}, after={"tags": ["their tag", "x"]})
        s.add(fix)
        await s.commit()
        theirs, their_fix = shop.id, fix.id
    assert (await client.get("/api/listing-health", params={"shop": str(theirs)})).status_code == 404
    assert (await client.post("/api/listing-health/preview", json={"connection_id": str(theirs), "listing_ids": [777], "kinds": ["tags"]})).status_code == 404
    assert (await client.post("/api/listing-health/rewrite-check", json={"connection_id": str(theirs), "listing_ids": [777]})).status_code == 404
    for path in ("apply", "undo", "discard"):
        assert (await client.post(f"/api/listing-health/{path}", json={"fix_ids": [str(their_fix)], "confirm": True})).status_code == 404
    # Their listing id through our own shop is simply not there.
    res = (await _preview(client, w, [777], ["tags"])).json()
    assert res["fixes"] == [] and "Their Secret" not in str(res)
    page = (await client.get("/api/listing-health", params={"shop": str(w["shop"]), "show": "all"})).text
    assert "Their Secret" not in page and "their tag" not in page
    assert not w["queue"].named("apply_listing_fix")


async def test_no_answer_carries_a_token_count_or_a_model(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client)
    text = (await client.get("/api/listing-health", params={"shop": str(w["shop"])})).text
    for word in ("input_tokens", "claude", "cost_usd"):
        assert word not in text
