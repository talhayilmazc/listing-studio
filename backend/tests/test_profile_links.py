"""One profile across shops (v8 §C).

* matching: exact name / identical terms link at once; anything else waits for
  the seller (create on confirmation, or pick once); nothing is guessed;
* "Use in: Group A" sets the profile up in every shop of the group;
* nothing is created in a shop without the seller's confirmation;
* a draft in a linked shop uses that shop's ids and a copy of the main shop's
  size charts (uploaded as images, never re-used by another shop's id);
* disconnecting a shop removes only its link; the profile moves or goes;
* "Link these" makes two same-named profiles one;
* another account gets 404 everywhere.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis
from sqlalchemy import select

from app.db.models import (
    AuditLog,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobType,
    ListingGroupSetting,
    ListingProfile,
    ProfileShopLink,
)
from app.etsy.publisher import PublishImage, publish_content
from app.pipeline import links as L
from app.pipeline import profile_shops
from app.pipeline.targets import resolve_target
from app.workers import links as link_jobs
from tests.test_multi_shop import _shop, world  # noqa: F401  (fixture)
from tests.test_publisher import CONFIG, FakeEtsy

SHIP_A = {"shipping_profile_id": 501, "title": "Standard US", "origin_country_iso": "US", "origin_postal_code": "10001",
          "profile_type": "manual",
          "shipping_profile_destinations": [
              {"destination_country_iso": "US", "primary_cost": {"amount": 499, "divisor": 100},
               "secondary_cost": {"amount": 100, "divisor": 100}},
              {"destination_region": "eu", "primary_cost": {"amount": 1200, "divisor": 100},
               "secondary_cost": {"amount": 300, "divisor": 100}}],
          "shipping_profile_upgrades": [
              {"upgrade_name": "Express", "type": 0, "price": {"amount": 900, "divisor": 100},
               "secondary_price": {"amount": 200, "divisor": 100}}]}
RET_A = {"return_policy_id": 601, "accepts_returns": True, "accepts_exchanges": False, "return_deadline": 30}
READY_A = {"readiness_state_id": 701, "readiness_state": "made_to_order", "min_processing_days": 1, "max_processing_days": 3}
PARTNER_A = {"production_partner_id": 801, "partner_name": "Print Co"}
PAYLOAD = {"description": "Ref\n\nBody", "images": [], "payload_version": 2, "currency": "USD",
           "shipping_profile_id": 501, "return_policy_id": 601, "readiness_state_id": 701,
           "production_partner_ids": [801]}


def _main() -> L.ShopSettings:
    return L.ShopSettings(shipping=[SHIP_A], returns=[RET_A], readiness=[READY_A], partners=[PARTNER_A], currency="USD")


# --- matching (pure) --------------------------------------------------------------------------


def test_exact_names_and_identical_terms_link_without_asking() -> None:
    target = L.ShopSettings(
        shipping=[{"shipping_profile_id": 9501, "title": "Standard US"}],
        returns=[{"return_policy_id": 9601, "accepts_returns": True, "accepts_exchanges": False, "return_deadline": 30}],
        readiness=[{"readiness_state_id": 9701, "readiness_state": "made_to_order", "min_processing_days": 1,
                    "max_processing_days": 3}],
        partners=[{"production_partner_id": 9801, "partner_name": "Print Co"}],
    )
    plan = L.plan_shop(PAYLOAD, _main(), target)
    assert {r: o.linked for r, o in plan.items()} == {
        L.SHIPPING: 9501, L.RETURNS: 9601, L.READINESS: 9701, L.PARTNERS: [9801]}
    assert all(o.done and not o.creatable for o in plan.values())


def test_no_match_offers_a_copy_only_where_etsys_api_can_create_it() -> None:
    target = L.ShopSettings(
        shipping=[{"shipping_profile_id": 1, "title": "Standard us"}],  # not the exact name
        returns=[{"return_policy_id": 2, "accepts_returns": True, "accepts_exchanges": False, "return_deadline": 14}],
        partners=[{"production_partner_id": 3, "partner_name": "Other Printer"}],
    )
    plan = L.plan_shop(PAYLOAD, _main(), target)
    ship = plan[L.SHIPPING]
    assert ship.linked is None and ship.creatable and ship.requests == 3  # profile + 1 more destination + 1 upgrade
    assert ship.create["profile"]["title"] == "Standard US" and ship.create["profile"]["primary_cost"] == 4.99
    assert plan[L.RETURNS].creatable and plan[L.RETURNS].create["return_deadline"] == 30
    assert plan[L.READINESS].creatable and plan[L.READINESS].create["min_processing_time"] == 1
    partners = plan[L.PARTNERS]
    assert not partners.creatable and partners.reason == L.NOT_CREATABLE
    assert partners.options == [{"id": 3, "label": "Other Printer"}]  # the seller picks once


def test_two_candidates_with_the_same_name_are_never_guessed() -> None:
    target = L.ShopSettings(shipping=[{"shipping_profile_id": 1, "title": "Standard US"},
                                      {"shipping_profile_id": 2, "title": "Standard US"}])
    outcome = L.plan_resource(L.SHIPPING, 501, _main(), target)
    assert outcome.linked is None and outcome.reason == L.AMBIGUOUS and not outcome.creatable


def test_a_calculated_shipping_profile_is_not_offered_for_creation() -> None:
    main = _main()
    main.shipping = [{**SHIP_A, "profile_type": "calculated"}]
    outcome = L.plan_resource(L.SHIPPING, 501, main, L.ShopSettings())
    assert not outcome.creatable and outcome.reason == L.CALCULATED


def test_a_shop_in_another_currency_is_named_as_the_problem() -> None:
    assert L.currency_problem("USD", "EUR") == "this shop sells in EUR, the profile's prices are in USD"
    assert L.currency_problem("USD", "USD") is None


# --- Etsy, faked: every shop's own lists, and what gets created ---------------------------------


class FakeShops:
    """One fake Etsy per shop id; records every create."""

    def __init__(self, lists: dict[int, L.ShopSettings]) -> None:
        self.lists = lists
        self.created: list[tuple[int, str, dict[str, Any]]] = []
        self.next_id = 50_000

    def for_shop(self, shop_id: int) -> Any:
        fake = self

        class Client:
            async def get_shop_shipping_profiles(self, sid: int, **_: Any) -> dict:
                return {"results": fake.lists[sid].shipping}

            async def get_shop_return_policies(self, sid: int, **_: Any) -> dict:
                return {"results": fake.lists[sid].returns}

            async def get_shop_readiness_state_definitions(self, sid: int, **_: Any) -> dict:
                return {"results": fake.lists[sid].readiness}

            async def get_shop_production_partners(self, sid: int, **_: Any) -> dict:
                return {"results": fake.lists[sid].partners}

            async def get_shop(self, sid: int, **_: Any) -> dict:
                return {"shop_id": sid, "currency_code": fake.lists[sid].currency or "USD"}

            async def _make(self, sid: int, what: str, data: dict, key: str, into: list | None) -> dict:
                fake.next_id += 1
                fake.created.append((sid, what, data))
                row = {key: fake.next_id, **data}
                if into is not None:
                    into.append(row)
                return row

            async def create_shop_shipping_profile(self, sid: int, *, data: dict, **_: Any) -> dict:
                return await self._make(sid, "shipping", data, "shipping_profile_id", fake.lists[sid].shipping)

            async def create_shop_shipping_profile_destination(self, sid: int, pid: int, *, data: dict, **_: Any) -> dict:
                return await self._make(sid, "destination", data, "shipping_profile_destination_id", None)

            async def create_shop_shipping_profile_upgrade(self, sid: int, pid: int, *, data: dict, **_: Any) -> dict:
                return await self._make(sid, "upgrade", data, "upgrade_id", None)

            async def create_shop_return_policy(self, sid: int, *, data: dict, **_: Any) -> dict:
                return await self._make(sid, "return_policy", data, "return_policy_id", fake.lists[sid].returns)

            async def create_shop_readiness_state_definition(self, sid: int, *, data: dict, **_: Any) -> dict:
                return await self._make(sid, "readiness", data, "readiness_state_id", fake.lists[sid].readiness)

        return Client()


@pytest.fixture()
def fake_etsy(monkeypatch) -> FakeShops:
    shops = FakeShops({
        101: _main(),  # Frost Tees: the profile's main shop
        102: L.ShopSettings(shipping=[{"shipping_profile_id": 2501, "title": "Standard US"}],
                            returns=[{"return_policy_id": 2601, "accepts_returns": True, "accepts_exchanges": False,
                                      "return_deadline": 30}],
                            readiness=[{"readiness_state_id": 2701, "readiness_state": "made_to_order",
                                        "min_processing_days": 1, "max_processing_days": 3}],
                            partners=[{"production_partner_id": 2801, "partner_name": "Print Co"}]),
        103: L.ShopSettings(partners=[{"production_partner_id": 3801, "partner_name": "Print Co"}]),
    })

    def client(ctx, http, shop_uuid, *, upkeep):  # noqa: ANN001
        return _Lazy(shops, shop_uuid)

    async def token(session, connection):  # noqa: ANN001
        return "tok"

    async def shop_id(session, client, connection, kw):  # noqa: ANN001
        client.bind(connection.shop_id)
        return connection.shop_id

    monkeypatch.setattr(link_jobs, "_client", client)
    monkeypatch.setattr(link_jobs, "_token", token)
    monkeypatch.setattr(link_jobs, "_shop_id", shop_id)
    return shops


class _Lazy:
    """The client is built before the shop id is known; it binds on first use."""

    def __init__(self, shops: FakeShops, shop_uuid: uuid.UUID) -> None:
        self._shops, self._inner = shops, None

    def bind(self, shop_id: int) -> None:
        self._inner = self._shops.for_shop(shop_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


async def _setup_world(world) -> dict:  # noqa: F811
    """Alice: Frost Tees (main, 101), Frost Mugs (102), Frost Caps (103) in group A with Mugs."""
    async with world["sm"]() as s:
        a3 = await _shop(s, world["alice"], 103, "Frost Caps", 2)
        profile = await s.get(ListingProfile, world["pa1"])
        profile.cached_payload = dict(PAYLOAD)
        await s.delete(await s.get(ListingProfile, world["pa2"]))  # one profile, the account's
        await s.commit()
        a3_id = a3.id
    res = await world["a"].post("/api/shop-groups", json={"name": "Group A", "connection_ids": [str(world["a2"]), str(a3_id)]})
    assert res.status_code == 201, res.text
    group = next(g for g in res.json()["groups"] if g["name"] == "Group A")
    return {"a3": a3_id, "group": group["id"]}


def _ctx(world) -> dict:  # noqa: F811
    return {"sessionmaker": world["sm"], "redis": world["redis"]}


async def _links(world, profile_id) -> dict[uuid.UUID, ProfileShopLink]:  # noqa: F811
    async with world["sm"]() as s:
        rows = await s.execute(select(ProfileShopLink).where(ProfileShopLink.profile_id == profile_id))
        return {link.connection_id: link for link in rows.scalars()}


# --- "Use in: Group A" ------------------------------------------------------------------------


async def test_use_in_a_group_links_every_shop_and_name_matches_ask_nothing(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    res = await world["a"].post(f"/api/profiles/{world['pa1']}/use-in", json={"scope": "group", "group_id": extra["group"]})
    assert res.status_code == 200, res.text
    assert set(res.json()["started"]) == {str(world["a2"]), str(extra["a3"])}
    assert ("link_profile", (str(world["pa1"]),)) in world["queue"].calls

    assert await link_jobs._link_profile(_ctx(world), str(world["pa1"])) == "linked"
    links = await _links(world, world["pa1"])
    mugs, caps = links[world["a2"]], links[extra["a3"]]
    # Frost Mugs has the same names and terms: linked, no question.
    assert (mugs.status, mugs.shipping_profile_id, mugs.return_policy_id, mugs.readiness_state_id,
            mugs.production_partner_ids) == ("ready", 2501, 2601, 2701, [2801])
    # Nothing was created anywhere: only reads.
    assert fake_etsy.created == []

    setup = (await world["a"].get(f"/api/profiles/{world['pa1']}/setup")).json()
    by_shop = {s["connection_id"]: s for s in setup["shops"]}
    caps_open = {o["resource"]: o for o in by_shop[str(extra["a3"])]["open"]}
    assert caps_open["shipping_profile"]["creatable"] is True and caps_open["shipping_profile"]["requests"] == 3
    assert caps_open["shipping_profile"]["create_summary"] == 'Shipping profile "Standard US" with 2 destinations, 1 upgrade'
    assert "production_partners" not in caps_open  # Print Co matched by name in Frost Caps too
    assert caps.production_partner_ids == [3801]

    profile = (await world["a"].get("/api/profiles")).json()[0]
    shops = {link["shop_name"]: link for link in profile["links"]}
    assert shops["Frost Tees"]["main"] is True
    assert shops["Frost Caps"]["ready"] is False and "Shipping profile" in shops["Frost Caps"]["reason"]


async def test_nothing_is_created_in_a_shop_without_the_sellers_confirmation(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    await world["a"].post(f"/api/profiles/{world['pa1']}/use-in", json={"scope": "shops", "connection_ids": [str(extra["a3"])]})
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    assert fake_etsy.created == []
    # The create job itself does nothing unless the seller confirmed something.
    assert await link_jobs._create_link_resources(_ctx(world), str(world["pa1"])) == "nothing"
    assert fake_etsy.created == []

    # The seller confirms the shipping profile only.
    res = await world["a"].post(f"/api/profiles/{world['pa1']}/setup/create",
                                json={"items": [{"connection_id": str(extra["a3"]), "resource": "shipping_profile"}]})
    assert res.status_code == 200 and res.json()["requests"] == 3
    assert ("create_link_resources", (str(world["pa1"]),)) in world["queue"].calls
    await link_jobs._create_link_resources(_ctx(world), str(world["pa1"]))
    made = [(sid, what) for sid, what, _ in fake_etsy.created]
    assert made == [(103, "shipping"), (103, "destination"), (103, "upgrade")]  # nothing else, nowhere else
    caps = (await _links(world, world["pa1"]))[extra["a3"]]
    assert caps.shipping_profile_id is not None and caps.return_policy_id is None
    assert caps.status == "incomplete"

    # A partner cannot be created through the API: asking is refused.
    res = await world["a"].post(f"/api/profiles/{world['pa1']}/setup/create",
                                json={"items": [{"connection_id": str(extra["a3"]), "resource": "production_partners"}]})
    assert res.status_code == 422


async def test_the_seller_picks_once_from_the_shops_own_list(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    fake_etsy.lists[103].returns = [{"return_policy_id": 3601, "accepts_returns": False, "accepts_exchanges": False}]
    await world["a"].post(f"/api/profiles/{world['pa1']}/use-in", json={"scope": "shops", "connection_ids": [str(extra["a3"])]})
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    url = f"/api/profiles/{world['pa1']}/setup/{extra['a3']}"
    # Only an id the shop's own list offered.
    assert (await world["a"].put(url, json={"resource": "return_policy", "ids": [999]})).status_code == 409
    res = await world["a"].put(url, json={"resource": "return_policy", "ids": [3601]})
    assert res.status_code == 200
    assert (await _links(world, world["pa1"]))[extra["a3"]].return_policy_id == 3601


# --- drafting into a linked shop ------------------------------------------------------------


async def test_a_linked_shop_drafts_with_its_own_ids_and_copied_size_charts(world, fake_etsy) -> None:  # noqa: F811
    await _setup_world(world)
    async with world["sm"]() as s:
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=world["pa1"], connection_id=world["a2"],
                              shipping_profile_id=2501, return_policy_id=2601, readiness_state_id=2701,
                              production_partner_ids=[2801], status="ready"))
        await s.commit()
        content = await s.get(GeneratedContent, world["contents"][0])
        target = await resolve_target(s, content, await s.get(EtsyConnection, world["a2"]))
    assert target.ok and target.profile.id == world["pa1"] and target.link.connection_id == world["a2"]
    assert target.title == content.title  # the listing's own text in every shop of its profile

    reference = L.shop_reference(PAYLOAD | {"taxonomy_id": 2078, "price": 25.0, "who_made": "i_did",
                                            "when_made": "made_to_order", "inventory_products": []}, target.link)
    assert (reference["shipping_profile_id"], reference["return_policy_id"], reference["readiness_state_id"],
            reference["production_partner_ids"]) == (2501, 2601, 2701, [2801])

    fake = FakeEtsy()
    async with world["sm"]() as s:
        job = Job(tenant_id=world["alice"], connection_id=world["a2"], type=JobType.create_draft, payload={})
        s.add(job)
        await s.flush()
        await publish_content(
            s, job_id=job.id, content=await s.get(GeneratedContent, world["contents"][0]),
            connection=await s.get(EtsyConnection, world["a2"]), sku="X1",
            thumbnail=PublishImage(b"t", "t.jpg"), fixed_image_ids=[PublishImage(b"chart", "size-chart-901.jpg")],
            client=fake, access_token="tok", config=CONFIG, reference=reference, tenant_limit=2000,
        )
    assert fake.last_listing["shipping_profile_id"] == 2501 and fake.last_listing["production_partner_ids"] == [2801]
    # The chart went up as an image of this shop, not as the main shop's image id.
    assert fake.uploaded[-1] == (2, "size-chart-901.jpg")


async def test_size_charts_for_another_shop_are_fetched_in_memory(world) -> None:  # noqa: F811
    from app.workers.publish import copied_size_charts

    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        profile.fixed_image_ids = [901]
        profile.cached_payload = {**PAYLOAD, "images": [{"listing_image_id": 901, "url": "https://img.example/901.jpg"}]}
        profile.images_updated_at = datetime.now(timezone.utc)
        await s.commit()
        seen: list[str] = []

        async def fetch(url: str) -> tuple[bytes, str]:
            seen.append(url)
            return b"pixels", "image/png"

        images = await copied_size_charts({}, s, profile, fetch)
    assert seen == ["https://img.example/901.jpg"]
    assert [(i.data, i.mime_type) for i in images] == [(b"pixels", "image/png")]


async def test_a_shop_the_profile_is_not_set_up_in_says_so_with_one_action(world) -> None:  # noqa: F811
    await _setup_world(world)
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, world["contents"][0])
        target = await resolve_target(s, content, await s.get(EtsyConnection, world["a2"]))
    assert not target.ok and target.setup and "is not set up in this shop" in target.reason


# --- disconnect, "Link these", isolation ----------------------------------------------------


async def test_disconnecting_the_main_shop_moves_the_profile_and_the_last_shop_takes_it(world) -> None:  # noqa: F811
    from app.workers.retention import purge_shop_etsy_content

    await _setup_world(world)
    async with world["sm"]() as s:
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=world["pa1"], connection_id=world["a2"],
                              shipping_profile_id=2501, status="ready"))
        await s.commit()
    async with world["sm"]() as s:
        counts = await purge_shop_etsy_content(s, world["a1"])
        await s.commit()
    assert counts["profiles_moved"] == 1 and counts["profiles"] == 0
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        # Survives in Frost Mugs; the Etsy data read from Frost Tees went with it.
        assert profile.connection_id == world["a2"] and profile.cached_payload is None
        assert profile.reference_listing_id is None and "Choose a reference" in profile.refresh_error
    async with world["sm"]() as s:
        counts = await purge_shop_etsy_content(s, world["a2"])
        await s.commit()
    assert counts["profiles"] == 1
    async with world["sm"]() as s:
        assert await s.get(ListingProfile, world["pa1"]) is None


async def test_link_these_makes_two_same_named_profiles_one(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        s.add(ListingGroupSetting(tenant_id=world["alice"], batch_id=world["batch"], group_key="g",
                                  profile_id=world["pa2"]))
        await s.commit()
    suggestions = (await world["a"].get("/api/profiles/link-suggestions")).json()
    assert [len(x["profiles"]) for x in suggestions] == [2]
    res = await world["a"].post(f"/api/profiles/{world['pa1']}/link-profile", json={"other_profile_id": str(world["pa2"])})
    assert res.status_code == 200, res.text
    assert {link["shop_name"] for link in res.json()["links"]} == {"Frost Tees", "Frost Mugs"}
    async with world["sm"]() as s:
        assert await s.get(ListingProfile, world["pa2"]) is None
        setting = (await s.execute(select(ListingGroupSetting))).scalar_one()
        assert setting.profile_id == world["pa1"]  # what named the other names this one
        entry = (await s.execute(select(AuditLog).where(AuditLog.action == "profile.merged"))).scalar_one()
        assert entry.details["object_id"] == str(world["pa2"]) and entry.details["shop_id"] == str(world["a2"])


async def test_another_account_gets_404_everywhere(world) -> None:  # noqa: F811
    extra = await _setup_world(world)
    b, pa1, a3 = world["b"], world["pa1"], extra["a3"]
    checks = [
        ("POST", f"/api/profiles/{pa1}/use-in", {"scope": "all"}),
        ("GET", f"/api/profiles/{pa1}/setup", None),
        ("POST", f"/api/profiles/{pa1}/setup/create", {"items": [{"connection_id": str(a3), "resource": "return_policy"}]}),
        ("PUT", f"/api/profiles/{pa1}/setup/{a3}", {"resource": "return_policy", "ids": [1]}),
        ("POST", f"/api/profiles/{pa1}/setup/{a3}/check", None),
        ("DELETE", f"/api/profiles/{pa1}/links/{a3}", None),
        ("PUT", f"/api/profiles/{pa1}/reference", {"reference_listing_id": 5}),
        ("POST", f"/api/profiles/{world['pb1']}/link-profile", {"other_profile_id": str(pa1)}),
        ("PATCH", f"/api/shop-groups/{extra['group']}", {"name": "Mine"}),
        ("DELETE", f"/api/shop-groups/{extra['group']}", None),
    ]
    for method, path, body in checks:
        res = await b.request(method, path, json=body)
        assert res.status_code == 404, f"{method} {path} -> {res.status_code}"
    # Bob's own profile cannot be put into Alice's shops either.
    res = await b.post(f"/api/profiles/{world['pb1']}/use-in", json={"scope": "shops", "connection_ids": [str(a3)]})
    assert res.status_code == 404
    groups = (await b.get("/api/shop-groups")).json()
    assert groups["groups"] == [] and [s["name"] for s in groups["ungrouped"]] == ["Bob Shop"]


async def test_a_shop_is_in_at_most_one_group(world) -> None:  # noqa: F811
    extra = await _setup_world(world)
    res = await world["a"].post("/api/shop-groups", json={"name": "Group B", "connection_ids": [str(world["a2"])]})
    groups = {g["name"]: [s["name"] for s in g["shops"]] for g in res.json()["groups"]}
    assert groups == {"Group A": ["Frost Caps"], "Group B": ["Frost Mugs"]}
    assert (await world["a"].post("/api/shop-groups", json={"name": "group b"})).status_code == 409
    res = await world["a"].delete(f"/api/shop-groups/{extra['group']}")
    assert "Frost Caps" in [s["name"] for s in res.json()["ungrouped"]]


def test_shared_settings_compare_without_shop_ids() -> None:
    class P:
        content_template, title_prefix, personalization, listing_style = "apparel", "CC", None, "classic"
        title_min_length = title_max_length = None
        fixed_image_ids = [1, 2]

    one, two = P(), P()
    one.cached_payload = {**PAYLOAD, "inventory_products": [{"product_id": 1, "property_values": [{"property_name": "Size", "values": ["S"]}],
                                                              "offerings": [{"offering_id": 9, "price": {"amount": 2500, "divisor": 100}}]}]}
    two.cached_payload = {**PAYLOAD, "shipping_profile_id": 777, "inventory_products": [{"product_id": 2, "property_values": [{"property_name": "Size", "values": ["S"]}],
                                                                                          "offerings": [{"offering_id": 8, "price": {"amount": 2500, "divisor": 100}}]}]}
    two.fixed_image_ids = [5, 6]  # each shop's own image ids
    assert L.shared_signature(one, None) == L.shared_signature(two, None)
    two.title_prefix = "Other"
    assert L.shared_signature(one, None) != L.shared_signature(two, None)
    two.cached_payload = None
    assert L.shared_signature(two, None) is None


async def test_the_setup_redis_plan_is_per_profile(world) -> None:  # noqa: F811
    redis = FakeAsyncRedis()
    await link_jobs._plan_save({"redis": redis}, world["pa1"], {"x": {}})
    assert await link_jobs.link_plan({"redis": redis}, world["pa1"]) == {"x": {}}
    assert await link_jobs.link_plan({"redis": redis}, world["pa2"]) == {}


async def test_state_of_reports_what_is_missing() -> None:
    profile = ListingProfile(connection_id=uuid.uuid4(), cached_payload=PAYLOAD)
    link = ProfileShopLink(connection_id=uuid.uuid4(), status="incomplete", shipping_profile_id=1,
                           notes={"return_policy": L.NOT_FOUND})
    state = profile_shops.state_of(profile, link)
    assert not state.ready and set(state.missing) == {"return_policy", "readiness_state", "production_partners"}
    assert state.reason.startswith("Return policy")
