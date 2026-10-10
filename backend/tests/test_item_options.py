"""Occasion, Holiday and Section on the review card (pipeline/item_options.py): only
Etsy's values, multi-value respected, a section per shop, nothing created without
the seller's confirmation, and the draft's read-back catching a mismatch."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.api import deps
from app.db.models import (
    Asset,
    EtsyConnection,
    GeneratedContent,
    ListingGroupSetting,
    ListingProfile,
    ListingPublication,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.etsy.publisher import PublishImage, publish_content
from app.pipeline import item_options as opts
from app.pipeline.attributes import resolve_optional_attributes
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_grouping import _upload
from tests.test_publisher import CONFIG, REFERENCE, FakeEtsy, _seed

PAYLOAD = {
    "category_attributes": {"Occasion": ["Birthday", "Graduation", "Retirement"], "Holiday": ["Christmas", "Halloween", "Easter"]},
    "category_attribute_limits": {"Occasion": 1, "Holiday": 2},
}

# --- the rules -----------------------------------------------------------------------------------


def test_only_etsys_values_and_no_more_than_the_property_takes() -> None:
    occasion = opts.options_for(PAYLOAD, "occasion")
    holiday = opts.options_for(PAYLOAD, "Holiday")
    assert occasion and occasion.max_values == 1 and holiday and holiday.max_values == 2
    assert opts.validate(["birthday"], occasion, "Occasion") == ["Birthday"]  # Etsy's own spelling
    with pytest.raises(ValueError, match="not one of Etsy's Occasion values"):
        opts.validate(["Wedding day"], occasion, "Occasion")
    with pytest.raises(ValueError, match="takes one value"):
        opts.validate(["Birthday", "Graduation"], occasion, "Occasion")
    assert opts.validate(["Christmas", "Easter"], holiday, "Holiday") == ["Christmas", "Easter"]
    with pytest.raises(ValueError, match="up to 2"):
        opts.validate(["Christmas", "Easter", "Halloween"], holiday, "Holiday")
    assert opts.validate([], occasion, "Occasion") == []  # cleared
    assert opts.options_for({}, "Occasion") is None
    limits = opts.attribute_limits([
        {"property_name": "Holiday", "is_multivalued": True, "max_values_allowed": 2, "possible_values": [{}] * 5},
        {"property_name": "Occasion", "is_multivalued": False},
    ])
    assert limits == {"Holiday": 2, "Occasion": 1}


def test_the_profiles_default_unless_the_design_clearly_shows_another() -> None:
    occasion = opts.options_for(PAYLOAD, "Occasion")
    # No default: the writer's choice stands.
    assert opts.apply_profile_default({"Occasion": "Graduation"}, "Occasion", None, "", occasion) == {"Occasion": "Graduation"}
    # A default: used when the design shows none...
    assert opts.apply_profile_default({"Occasion": "Graduation"}, "Occasion", "Retirement", "", occasion) == {"Occasion": "Retirement"}
    assert opts.apply_profile_default({}, "Occasion", "Retirement", "", occasion) == {"Occasion": "Retirement"}
    # ...not when it clearly shows one.
    assert opts.apply_profile_default({"Occasion": "Graduation"}, "Occasion", "Retirement", "graduation", occasion) == {"Occasion": "Graduation"}
    # "none" clears it; a default Etsy does not list is not used.
    assert opts.apply_profile_default({"Occasion": "Graduation"}, "Occasion", "", "", occasion) == {}
    assert opts.apply_profile_default({}, "Occasion", "Wedding day", "", occasion) == {}


def test_the_section_is_an_existing_one_that_fits_and_says_why() -> None:
    titles = ["Nurse Collection", "Teacher Tees", "Funny Shirts"]
    picked = opts.suggest_section(titles, theme_words=["nurse life", "healthcare"])
    assert (picked.title, picked.reason) == ("Nurse Collection", "matches: nurse")
    # Nothing fits clearly: none, and never a new name.
    assert opts.suggest_section(titles, theme_words=["fishing"]).title is None
    assert opts.suggest_section([], theme_words=["nurse"]).title is None
    # Plural and singular are the same word; a tie is not a clear fit.
    assert opts.suggest_section(["Teachers"], theme_words=["teacher"]).title == "Teachers"
    assert opts.suggest_section(["Cat Mom", "Cat Dad"], theme_words=["cat"]).title is None
    # The profile's rule when the words do not decide.
    assert opts.suggest_section(["Comfort Colors", "Other"], theme_words=["x"], profile_name="Comfort Colors Tee").title == "Comfort Colors"


def test_what_a_draft_gets() -> None:
    attrs = {"listing": {"Occasion": "Birthday", "Primary color": "Blue"}}
    assert opts.draft_attributes(attrs, None) == {"Occasion": "Birthday", "Primary color": "Blue"}
    assert opts.draft_attributes(attrs, {"Occasion": []}) == {"Primary color": "Blue"}  # cleared
    assert opts.draft_attributes(attrs, {"Holiday": ["Christmas", "Easter"]})["Holiday"] == ["Christmas", "Easter"]
    own = {"sections": {"a": {"id": 5, "title": "Nurse Collection"}}}
    assert opts.section_choice(own, "a") == {"id": 5, "title": "Nurse Collection", "explicit": True}
    assert opts.section_choice(own, "b") == {"id": None, "title": "Nurse Collection", "explicit": False}
    assert opts.section_choice(None, "b") is None


def test_several_values_only_where_etsy_takes_several() -> None:
    props = [
        {"property_id": 1, "property_name": "Holiday", "is_multivalued": True, "max_values_allowed": 2,
         "possible_values": [{"value_id": 11, "name": "Christmas"}, {"value_id": 12, "name": "Easter"}, {"value_id": 13, "name": "Halloween"}]},
        {"property_id": 2, "property_name": "Occasion", "is_multivalued": False,
         "possible_values": [{"value_id": 21, "name": "Birthday"}, {"value_id": 22, "name": "Graduation"}]},
    ]
    chosen, unmatched = resolve_optional_attributes(
        props, {"Holiday": ["Christmas", "Easter", "Halloween"], "Occasion": ["Birthday", "Graduation"]}
    )
    assert {a.property_name: a.value_ids for a in chosen} == {"Holiday": [11, 12], "Occasion": [21]}
    _, unmatched = resolve_optional_attributes(props, {"Occasion": "Wedding"})
    assert unmatched == ["Occasion: Wedding"]


# --- drafts ------------------------------------------------------------------------------------

SECTIONS = {"results": [{"shop_section_id": 10, "title": "Nurse Collection"}, {"shop_section_id": 11, "title": "Teacher Tees"}]}
PROPS = {"results": [
    {"property_id": 7, "property_name": "Holiday", "is_multivalued": True, "max_values_allowed": 2,
     "possible_values": [{"value_id": 71, "name": "Christmas"}, {"value_id": 72, "name": "Easter"}]},
]}


class Shop(FakeEtsy):
    def __init__(self, *, lose: bool = False, **kw: Any) -> None:
        super().__init__(sections=SECTIONS, properties=PROPS, **kw)
        self.lose = lose

    async def get_listing_properties(self, shop_id: int, listing_id: int, **_: Any) -> dict[str, Any]:
        kept = [p for p in self.properties_set if not self.lose]
        return {"results": [{"property_id": p["property_id"], "value_ids": p["value_ids"]} for p in kept]}


async def _draft(sm, ids: tuple, shop: FakeEtsy, **kw: Any):  # noqa: ANN001
    _, conn_id, content_id, job_id = ids
    async with sm() as s:
        return await publish_content(
            s, job_id=job_id, content=await s.get(GeneratedContent, content_id), connection=await s.get(EtsyConnection, conn_id),
            sku="BR5475", thumbnail=PublishImage(b"t", "t.jpg"), client=shop, access_token="tok", config=CONFIG,
            reference=REFERENCE, theme="x", tenant_limit=2000, **kw,
        )


async def test_the_draft_gets_the_sellers_section_and_values_and_reads_them_back(async_sm) -> None:  # noqa: ANN001
    ids = await _seed(async_sm)
    shop = Shop()
    result = await _draft(async_sm, ids, shop, section_choice={"id": 11, "title": "Teacher Tees", "explicit": True},
                          optional_attributes={"Holiday": ["Christmas", "Easter"]})
    assert shop.last_listing["shop_section_id"] == 11 and result.section_id == 11
    assert shop.properties_set == [{"property_id": 7, "value_ids": [71, 72], "values": ["Christmas", "Easter"]}]
    assert "create_section" not in shop.calls  # never created by a draft
    async with async_sm() as s:
        from app.db.models import ContentVersion

        version = (await s.execute(select(ContentVersion))).scalars().one()
        assert version.section == "Teacher Tees" and version.attributes["Holiday"] == "Christmas, Easter"


async def test_the_read_back_catches_values_etsy_did_not_keep(async_sm) -> None:  # noqa: ANN001
    ids = await _seed(async_sm)
    with pytest.raises(ValueError, match="Holiday did not save as set"):
        await _draft(async_sm, ids, Shop(lose=True), optional_attributes={"Holiday": ["Christmas"]})


async def test_the_read_back_catches_a_section_etsy_did_not_keep(async_sm) -> None:  # noqa: ANN001
    ids = await _seed(async_sm)
    with pytest.raises(ValueError, match="shop section"):
        await _draft(async_sm, ids, Shop(readback_override={"shop_section_id": 99}),
                     section_choice={"id": 10, "title": "Nurse Collection", "explicit": True})


async def test_a_chosen_section_gone_from_the_shop_is_not_guessed(async_sm) -> None:  # noqa: ANN001
    ids = await _seed(async_sm)
    with pytest.raises(ValueError, match="not in this shop any more"):
        await _draft(async_sm, ids, Shop(), section_choice={"id": 55, "title": "Old Section", "explicit": True})


async def test_another_shops_title_is_used_when_this_shop_has_it_else_the_fit(async_sm) -> None:  # noqa: ANN001
    ids = await _seed(async_sm)
    shop = Shop()
    await _draft(async_sm, ids, shop, section_choice={"id": None, "title": "teacher tees", "explicit": False})
    assert shop.last_listing["shop_section_id"] == 11
    ids2 = await _seed_other(async_sm)
    shop2 = Shop()
    await _draft(async_sm, ids2, shop2, section_choice={"id": None, "title": "Not Here", "explicit": False},
                 theme_words=["nurse", "scrubs"])
    assert shop2.last_listing["shop_section_id"] == 10 and "create_section" not in shop2.calls


async def _seed_other(sm):  # noqa: ANN001
    async with sm() as s:
        for c in (await s.execute(select(EtsyConnection))).scalars():
            c.etsy_user_id = (c.etsy_user_id or 0) + 1000  # another Etsy account for the next shop
        await s.commit()
    return await _seed(sm)


# --- the review card ---------------------------------------------------------------------------


class _Queue:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def enqueue(self, function: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((function, args))


async def _world(client: AsyncClient) -> dict[str, Any]:  # noqa: F811
    queue = _Queue()
    client.app.dependency_overrides[deps.get_enqueuer] = lambda: queue  # type: ignore[attr-defined]
    batch = (await client.post("/api/batches")).json()["id"]
    for name in ("NU1001-1.png", "TE2002-1.png"):
        await _upload(client, batch, name)
    await client.post(f"/api/batches/{batch}/finalize")
    now = datetime.now(timezone.utc)
    async with client.sm() as s:  # type: ignore[attr-defined]
        tid = client.tenant_id  # type: ignore[attr-defined]
        a = EtsyConnection(tenant_id=tid, etsy_user_id=1, shop_id=1, shop_name="Shop A", scopes=["shops_r", "shops_w"],
                           sections=[{"id": 10, "title": "Nurse Collection"}, {"id": 11, "title": "Teacher Tees"}], sections_at=now)
        b = EtsyConnection(tenant_id=tid, etsy_user_id=2, shop_id=2, shop_name="Shop B", scopes=["shops_r"],
                           sections=[{"id": 20, "title": "Teacher Tees"}], sections_at=now - timedelta(hours=1))
        stale = EtsyConnection(tenant_id=tid, etsy_user_id=3, shop_id=3, shop_name="Shop C", scopes=["shops_w"],
                               sections=[{"id": 30, "title": "Old"}], sections_at=now - timedelta(hours=25))
        s.add_all([a, b, stale])
        await s.flush()
        profile = ListingProfile(tenant_id=tid, connection_id=a.id, name="Tee", reference_listing_id=1, confirmed=True,
                                 cached_payload=dict(PAYLOAD))
        s.add(profile)
        await s.flush()
        assets = {x.group_key: x for x in (await s.execute(select(Asset).where(Asset.batch_id == uuid.UUID(batch)))).scalars()}
        contents = {}
        for key, theme in (("NU1001", "nurse"), ("TE2002", "teacher")):
            s.add(ListingGroupSetting(tenant_id=tid, batch_id=uuid.UUID(batch), group_key=key, connection_id=a.id, profile_id=profile.id))
            c = GeneratedContent(tenant_id=tid, batch_id=uuid.UUID(batch), asset_id=assets[key].id, title="t", tags=[], description="d",
                                 listing_profile_id=profile.id,
                                 attributes={"listing": {"Occasion": "Birthday"}, "vision": {"theme": theme, "themes": [theme]}})
            s.add(c)
            await s.flush()
            contents[key] = c.id
        # The nurse listing also goes to shops B and C (it has drafts there).
        for shop, lid in ((b, 900), (stale, 901)):
            s.add(ListingPublication(tenant_id=tid, content_id=contents["NU1001"], connection_id=shop.id, etsy_listing_id=lid, state="draft"))
        await s.commit()
        return {"batch": batch, "a": a.id, "b": b.id, "c": stale.id, "nurse": contents["NU1001"], "teacher": contents["TE2002"],
                "profile": profile.id, "queue": queue}


async def test_the_card_shows_etsys_values_and_a_section_per_shop(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    out = (await client.get(f"/api/content/{w['nurse']}/options")).json()
    assert out["occasion"]["values"] == ["Birthday", "Graduation", "Retirement"]
    assert (out["occasion"]["selected"], out["occasion"]["source"], out["occasion"]["max_values"]) == (["Birthday"], "writer", 1)
    assert out["holiday"]["max_values"] == 2 and out["holiday"]["selected"] == []
    shops = {s["shop_name"]: s for s in out["shops"]}
    assert set(shops) == {"Shop A", "Shop B", "Shop C"}
    assert (shops["Shop A"]["selected_title"], shops["Shop A"]["reason"]) == ("Nurse Collection", "matches: nurse")
    assert shops["Shop B"]["selected_id"] is None and shops["Shop B"]["can_create"] is False
    # Shop C's sections are past 24 hours: not shown, read again through the queue.
    assert shops["Shop C"]["sections"] is None
    assert ("sync_shop_sections", (str(w["c"]),)) in w["queue"].calls


async def test_setting_values_only_etsys_and_multi_value_respected(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    url = f"/api/content/{w['nurse']}/options"
    assert (await client.put(url, json={"occasion": ["Wedding day"]})).status_code == 422
    assert (await client.put(url, json={"occasion": ["Birthday", "Graduation"]})).status_code == 422
    out = (await client.put(url, json={"holiday": ["christmas", "Easter"], "occasion": []})).json()
    assert (out["holiday"]["selected"], out["holiday"]["source"]) == (["Christmas", "Easter"], "seller")
    assert (out["occasion"]["selected"], out["occasion"]["source"]) == ([], "seller")  # cleared
    out = (await client.put(url, json={"occasion": None})).json()
    assert out["occasion"]["selected"] == ["Birthday"] and out["occasion"]["source"] == "writer"  # back to the writer's
    async with client.sm() as s:  # type: ignore[attr-defined]
        content = await s.get(GeneratedContent, w["nurse"])
        assert opts.draft_attributes(content.attributes, content.item_options) == {"Occasion": "Birthday", "Holiday": ["Christmas", "Easter"]}


async def test_a_section_per_shop_and_one_missing_is_offered_not_made(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    url = f"/api/content/{w['nurse']}/options"
    out = (await client.put(url, json={"sections": {str(w["a"]): 11}})).json()
    shops = {s["shop_name"]: s for s in out["shops"]}
    assert (shops["Shop A"]["selected_title"], shops["Shop A"]["source"], shops["Shop A"]["reason"]) == ("Teacher Tees", "seller", "")
    # Shop B has a section by that name: the same one, said so.
    assert (shops["Shop B"]["selected_id"], shops["Shop B"]["source"]) == (20, "carried")
    out = (await client.put(url, json={"sections": {str(w["a"]): 10}})).json()
    shop_b = next(s for s in out["shops"] if s["shop_name"] == "Shop B")
    # Shop B has no "Nurse Collection": offered (create or pick), never made by itself.
    assert shop_b["missing_title"] == "Nurse Collection"
    assert not any(c[0] == "create_shop_section" for c in w["queue"].calls)
    # Only the shop's own sections; only shops it goes to; only this account's shops.
    assert (await client.put(url, json={"sections": {str(w["a"]): 20}})).status_code == 422
    assert (await client.put(f"/api/content/{w['teacher']}/options", json={"sections": {str(w["b"]): 20}})).status_code == 422
    assert (await client.put(url, json={"sections": {str(uuid.uuid4()): 1}})).status_code == 404
    out = (await client.put(url, json={"sections": {str(w["b"]): None}})).json()
    assert next(s for s in out["shops"] if s["shop_name"] == "Shop B")["reason"].startswith("no section")


async def test_nothing_is_created_without_the_sellers_confirmation(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    body = {"title": "Nurse Collection", "content_ids": [str(w["nurse"])]}
    # Shop B has not allowed creating sections.
    assert (await client.post(f"/api/shops/{w['b']}/sections", json=body)).status_code == 409
    # Shop A already has it: chosen, nothing made.
    out = (await client.post(f"/api/shops/{w['a']}/sections", json=body)).json()
    assert out["queued"] is False and out["requests"] == 0
    body = {"title": "Scrub Life", "content_ids": [str(w["nurse"])]}
    asked = (await client.post(f"/api/shops/{w['a']}/sections", json=body)).json()
    assert asked["queued"] is False and "Create the section" in asked["message"] and asked["requests"] == 3
    assert not any(c[0] == "create_shop_section" for c in w["queue"].calls)
    done = (await client.post(f"/api/shops/{w['a']}/sections", json={**body, "confirm": True})).json()
    assert done["queued"] is True
    assert [c[0] for c in w["queue"].calls].count("create_shop_section") == 1
    assert (await client.post(f"/api/shops/{w['a']}/sections", json={"title": "x" * 25, "confirm": True})).status_code == 422


async def test_set_for_selected(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    res = await client.post(f"/api/batches/{w['batch']}/options", json={
        "content_ids": [str(w["nurse"]), str(w["teacher"])], "holiday": ["Halloween"],
        "section_connection_id": str(w["b"]), "section_id": 20,
    })
    out = res.json()
    # The teacher listing does not go to Shop B: left as it is, and said why.
    assert out["updated"] == 1 and out["skipped"][0]["content_id"] == str(w["teacher"])
    async with client.sm() as s:  # type: ignore[attr-defined]
        nurse = await s.get(GeneratedContent, w["nurse"])
        assert nurse.item_options["Holiday"] == ["Halloween"] and nurse.item_options["sections"][str(w["b"])]["id"] == 20


async def test_profile_defaults(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    url = f"/api/profiles/{w['profile']}"
    assert (await client.patch(url, json={"default_occasion": "Wedding day"})).status_code == 422
    out = (await client.patch(url, json={"default_occasion": "retirement", "default_holiday": ""})).json()
    assert (out["default_occasion"], out["default_holiday"]) == ("Retirement", "")
    assert out["occasion_values"] == ["Birthday", "Graduation", "Retirement"]
    assert (await client.patch(url, json={"default_occasion": None})).json()["default_occasion"] is None


async def test_another_account_gets_404(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    async with client.sm() as s:  # type: ignore[attr-defined]
        other = Tenant(email=f"{uuid.uuid4()}@e.com", password_hash="x")
        s.add(other)
        await s.flush()
        batch = UploadBatch(tenant_id=other.id, status=UploadBatchStatus.ready, file_count=1)
        shop = EtsyConnection(tenant_id=other.id, etsy_user_id=77, shop_id=77, scopes=["shops_w"])
        s.add_all([batch, shop])
        await s.flush()
        asset = Asset(batch_id=batch.id, tenant_id=other.id, original_filename="x.png", storage_key="k")
        s.add(asset)
        await s.flush()
        theirs = GeneratedContent(tenant_id=other.id, batch_id=batch.id, asset_id=asset.id, title="t", tags=[])
        s.add(theirs)
        await s.commit()
    assert (await client.get(f"/api/content/{theirs.id}/options")).status_code == 404
    assert (await client.put(f"/api/content/{theirs.id}/options", json={"occasion": []})).status_code == 404
    assert (await client.post(f"/api/batches/{batch.id}/options", json={"content_ids": [str(theirs.id)]})).status_code == 404
    assert (await client.post(f"/api/shops/{shop.id}/sections", json={"title": "Mine", "confirm": True})).status_code == 404
    # Their listing through my batch: skipped, not touched.
    res = (await client.post(f"/api/batches/{w['batch']}/options", json={"content_ids": [str(theirs.id)], "occasion": []})).json()
    assert res["updated"] == 0


# --- the section jobs --------------------------------------------------------------------------


async def test_creating_a_section_reads_first_and_never_makes_it_twice(client: AsyncClient, monkeypatch) -> None:  # noqa: F811
    import app.workers.sections as jobs

    w = await _world(client)
    made: list[str] = []
    listed = [{"shop_section_id": 10, "title": "Nurse Collection"}]

    class Etsy:
        async def get_shop_sections(self, shop_id: int, **_: Any) -> dict:
            return {"results": list(listed)}

        async def create_shop_section(self, shop_id: int, *, title: str, **_: Any) -> dict:
            made.append(title)
            listed.append({"shop_section_id": 50 + len(made), "title": title})
            return {"shop_section_id": 50 + len(made), "title": title}

    class Service:
        async def get_valid_access_token(self, session, connection):  # noqa: ANN001
            return "tok"

    async def shop_id(session, client, connection, kw):  # noqa: ANN001
        return connection.shop_id

    async def gated(ctx, function, arg, tenant_id, body):  # noqa: ANN001
        return await body()

    monkeypatch.setattr(jobs, "_build_client", lambda *a, **k: Etsy())
    monkeypatch.setattr(jobs, "_connection_service", lambda settings: Service())
    monkeypatch.setattr(jobs, "_resolve_shop_id", shop_id)
    monkeypatch.setattr(jobs, "_run_gated", gated)
    ctx = {"sessionmaker": client.sm}  # type: ignore[attr-defined]
    spec = jobs.create_spec(w["a"], "Scrub Life", [w["nurse"]])
    assert (await jobs.create_shop_section(ctx, spec)).endswith(":made:51")
    assert await jobs.create_shop_section(ctx, spec)  # again: found, not made
    assert made == ["Scrub Life"]
    async with client.sm() as s:  # type: ignore[attr-defined]
        nurse = await s.get(GeneratedContent, w["nurse"])
        shop = await s.get(EtsyConnection, w["a"])
    assert nurse.item_options["sections"][str(w["a"])] == {"id": 51, "title": "Scrub Life"}
    assert {"id": 51, "title": "Scrub Life"} in shop.sections
