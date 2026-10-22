"""Working across several shops (multi-shop sellers after Commercial Access).

* bug 1: "Use in -> Chosen shops" works for single shops and for a group, and a shop
  whose setup was unfinished or failed can be chosen again (it was hidden before);
* bug 2: Overview totals every shop with a row per shop; one shop narrows it; Analytics
  has the same "All shops" view; nothing of another account's shows;
* bug 3: the setup the Batches/review page opens covers one shop or several in one go,
  and ticking a group ticks every shop in it;
* point 4: each shop's shipping, return and processing setting defaults to the shop's
  matching one (same name / same terms), else a copy of the main shop's is preselected,
  created only after the seller confirms; any choice can be changed;
* point 5: groups with overlapping shops, renamed, edited, deleted, used in every
  picker; "Split between groups" and the cross-group warning still work;
* another account's shops and groups are 404 everywhere.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select

from app.db.models import EtsyConnection, ListingGroupSetting, ProfileShopLink, ShopGroupMember, ShopListingCache
from app.pipeline import group_plan as G
from app.workers import links as link_jobs
from tests.test_multi_shop import _shop, world  # noqa: F401  (fixture)
from tests.test_profile_links import _ctx, _links, _setup_world, fake_etsy  # noqa: F401  (fixtures)

SCHEDULE = {"start_date": (date.today() + timedelta(days=1)).isoformat(), "per_shop_per_day": 5,
            "window_start": "09:00", "window_end": "17:00", "spacing_minutes": 30, "stagger_minutes": 5}


async def _groups(world, **members: list[uuid.UUID]) -> dict[str, str]:  # noqa: F811
    out = {}
    for name, ids in members.items():
        res = await world["a"].post("/api/shop-groups", json={"name": name, "connection_ids": [str(i) for i in ids]})
        assert res.status_code == 201, res.text
        out[name] = next(g["id"] for g in res.json()["groups"] if g["name"] == name)
    return out


# --- point 5: groups ----------------------------------------------------------------------------


async def test_groups_overlap_rename_edit_delete_and_show_on_each_shop(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        a3 = (await _shop(s, world["alice"], 103, "Frost Caps", 2)).id
        await s.commit()
    g = await _groups(world, A=[world["a1"], world["a2"]], B=[world["a2"], a3])
    shops = {s["name"]: s for s in (await world["a"].get("/api/shops")).json()["shops"]}
    assert set(shops["Frost Mugs"]["group_ids"]) == {g["A"], g["B"]}  # in both groups
    assert shops["Frost Tees"]["group_ids"] == [g["A"]]

    res = await world["a"].patch(f"/api/shop-groups/{g['B']}", json={"name": "Group B", "connection_ids": [str(a3)]})
    assert res.status_code == 200
    groups = {x["name"]: [s["name"] for s in x["shops"]] for x in res.json()["groups"]}
    # Taking Mugs out of B leaves it in A.
    assert groups == {"A": ["Frost Tees", "Frost Mugs"], "Group B": ["Frost Caps"]}

    res = await world["a"].delete(f"/api/shop-groups/{g['A']}")
    assert {x["name"] for x in res.json()["groups"]} == {"Group B"}
    assert [s["name"] for s in res.json()["ungrouped"]] == ["Frost Tees", "Frost Mugs"]
    async with world["sm"]() as s:  # the rows went with the group; the shops stay
        assert len((await s.execute(select(ShopGroupMember))).all()) == 1
        assert (await s.get(EtsyConnection, world["a1"])) is not None


async def test_another_accounts_groups_and_shops_are_404(world) -> None:  # noqa: F811
    g = await _groups(world, A=[world["a1"]])
    b = world["b"]
    assert (await b.patch(f"/api/shop-groups/{g['A']}", json={"name": "Mine"})).status_code == 404
    assert (await b.delete(f"/api/shop-groups/{g['A']}")).status_code == 404
    # Bob cannot put Alice's shop in a group of his.
    assert (await b.post("/api/shop-groups", json={"name": "X", "connection_ids": [str(world["a1"])]})).status_code == 404
    # Nor use her group or shops anywhere a target is picked.
    assert (await b.post(f"/api/profiles/{world['pb1']}/use-in", json={"scope": "group", "group_id": g["A"]})).status_code == 404
    assert (await b.post(f"/api/profiles/{world['pb1']}/use-in",
                         json={"scope": "shops", "connection_ids": [str(world["a2"])]})).status_code == 404
    assert (await world["a"].put(f"/api/batches/{world['batch']}/targets",
                                 json={"group_id": str(uuid.uuid4()), "on": True})).status_code == 404
    assert (await b.get("/api/shop-groups")).json()["groups"] == []
    overview = (await b.get("/api/shop/overview")).json()
    assert [r["name"] for r in overview["shops"]] == ["Bob Shop"]


# --- bug 1: Use in -> Chosen shops ---------------------------------------------------------------


async def test_chosen_shops_single_shops_group_and_an_unfinished_shop_again(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    await _no_shops_w(world, extra["a3"])
    a = world["a"]
    # Single shops.
    res = await a.post(f"/api/profiles/{world['pa1']}/use-in", json={"scope": "shops", "connection_ids": [str(extra["a3"])]})
    assert res.status_code == 200 and res.json()["started"] == [str(extra["a3"])]
    assert await link_jobs._link_profile(_ctx(world), str(world["pa1"])) == "linked"
    caps = (await _links(world, world["pa1"]))[extra["a3"]]
    assert caps.status == "incomplete"  # no match in Caps, and Caps cannot be written to
    # Bug 1: the unfinished shop can be chosen again from "Chosen shops" (it was hidden):
    # choosing it reads it again; nothing is created.
    res = await a.post(f"/api/profiles/{world['pa1']}/use-in", json={"scope": "shops", "connection_ids": [str(extra["a3"])]})
    assert res.json()["started"] == [str(extra["a3"])]
    # A group, in the same picker: every shop of it.
    res = await a.post(f"/api/profiles/{world['pa1']}/use-in", json={"scope": "group", "group_id": extra["group"]})
    assert set(res.json()["started"]) == {str(world["a2"])}  # Caps is already being checked
    assert fake_etsy.created == []


async def _no_shops_w(world, shop_id) -> None:  # noqa: ANN001
    """A shop that has not given the app permission to change its settings: nothing can be
    copied there, so the seller is asked."""
    async with world["sm"]() as s:
        (await s.get(EtsyConnection, shop_id)).scopes = ["listings_r", "listings_w", "shops_r"]
        await s.commit()


# --- point 4: the default per shop; a missing setting is copied automatically ---------------------


async def test_matching_settings_by_default_and_a_missing_one_copied_automatically(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    a = world["a"]
    fake_etsy.lists[102].shipping.append({"shipping_profile_id": 2502, "title": "Free shipping"})
    await a.post(f"/api/profiles/{world['pa1']}/use-in", json={"scope": "group", "group_id": extra["group"]})
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))

    setup = (await a.get(f"/api/profiles/{world['pa1']}/setup")).json()
    by_shop = {s["connection_id"]: {x["resource"]: x for x in s["settings"]} for s in setup["shops"]}
    mugs, caps = by_shop[str(world["a2"])], by_shop[str(extra["a3"])]
    # Mugs: every setting is the shop's own matching one, chosen automatically.
    assert {r: (x["default"], x["how"], x["linked_ids"]) for r, x in mugs.items()} == {
        "shipping_profile": ("keep", "same name", [2501]),
        "return_policy": ("keep", "same terms", [2601]),
        "readiness_state": ("keep", "same terms", [2701]),
        "production_partners": ("keep", "same name", [2801]),
    }
    assert mugs["shipping_profile"]["linked_label"] == "Standard US"
    assert [o["label"] for o in mugs["shipping_profile"]["options"]] == ["Standard US", "Free shipping"]  # changeable
    # Caps: no match, so the main shop's was copied there automatically, once each; no question.
    for r in ("shipping_profile", "return_policy", "readiness_state"):
        assert caps[r]["default"] == "keep" and caps[r]["how"] == "created" and caps[r]["linked_ids"]
    assert caps["production_partners"]["how"] == "same name"
    assert sorted(what for _, what, _ in fake_etsy.created) == [
        "destination", "readiness", "return_policy", "shipping", "upgrade"]
    made = len(fake_etsy.created)

    # A wrong choice changes nothing: a pick Etsy did not list, a copy over a match.
    bad = [{"connection_id": str(world["a2"]), "resource": "shipping_profile", "use": "pick", "ids": [999]}]
    assert (await a.post(f"/api/profiles/{world['pa1']}/setup/apply", json={"choices": bad})).status_code == 409
    over = [{"connection_id": str(world["a2"]), "resource": "return_policy", "use": "copy"}]
    assert (await a.post(f"/api/profiles/{world['pa1']}/setup/apply", json={"choices": over})).status_code == 409
    assert (await _links(world, world["pa1"]))[world["a2"]].shipping_profile_id == 2501

    # The seller changes one: Mugs uses another of its own. Nothing is created or queued.
    choices = [{"connection_id": str(world["a2"]), "resource": "shipping_profile", "use": "pick", "ids": [2502]}]
    world["queue"].calls.clear()
    res = await a.post(f"/api/profiles/{world['pa1']}/setup/apply", json={"choices": choices})
    assert res.status_code == 200, res.text
    assert "requests" not in res.json()  # request counts are for admins only (A3)
    assert ("create_link_resources", (str(world["pa1"]),)) not in world["queue"].calls
    assert len(fake_etsy.created) == made
    links = await _links(world, world["pa1"])
    assert links[world["a2"]].shipping_profile_id == 2502
    assert links[extra["a3"]].status == "ready"
    setup = (await a.get(f"/api/profiles/{world['pa1']}/setup")).json()
    by_shop = {s["connection_id"]: {x["resource"]: x for x in s["settings"]} for s in setup["shops"]}
    assert by_shop[str(world["a2"])]["shipping_profile"]["how"] == "your choice"
    assert by_shop[str(world["a2"])]["shipping_profile"]["linked_label"] == "Free shipping"
    assert by_shop[str(extra["a3"])]["shipping_profile"]["how"] == "created"


# --- bug 3: setup for one shop or several from a batch; ticking a group ---------------------------


async def test_review_ticks_a_group_and_sets_the_profile_up_in_each_of_its_shops(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    await _no_shops_w(world, extra["a3"])  # Caps keeps a need to show
    async with world["sm"]() as s:
        for link in (await s.execute(select(ProfileShopLink).where(ProfileShopLink.profile_id == world["pa1"],
                                                                   ProfileShopLink.connection_id != world["a1"]))).scalars():
            await s.delete(link)
        await s.commit()
    world["queue"].calls.clear()
    res = await world["a"].put(f"/api/batches/{world['batch']}/targets", json={"group_id": extra["group"], "on": True})
    assert res.status_code == 200, res.text
    assert res.json()["changed"] == 4  # 2 listings x 2 shops
    assert [p["id"] for p in res.json()["setting_up"]] == [str(world["pa1"])]
    links = await _links(world, world["pa1"])
    assert {links[world["a2"]].status, links[extra["a3"]].status} == {"checking"}  # both shops, one job
    assert world["queue"].calls.count(("link_profile", (str(world["pa1"]),))) == 1
    # The matrix lists what each shop needs; the dialog sets up several shops in one call.
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    preview = (await world["a"].post(f"/api/batches/{world['batch']}/publish/preview", json={})).json()
    caps = next(c for c in preview["columns"] if c["connection_id"] == str(extra["a3"]))
    assert {n["profile_id"] for n in caps["needs"] if n["kind"] != "section"} == {str(world["pa1"])}
    res = await world["a"].post(f"/api/profiles/{world['pa1']}/use-in",
                                json={"scope": "shops", "connection_ids": [str(world["a2"]), str(extra["a3"])]})
    assert res.status_code == 200
    # Unticking the group unticks every shop in it again.
    res = await world["a"].put(f"/api/batches/{world['batch']}/targets", json={"group_id": extra["group"], "on": False})
    assert res.json()["changed"] == 4


# --- bug 2: Overview and Analytics across every shop --------------------------------------------


async def test_overview_totals_every_shop_with_a_row_each_and_one_shop_narrows(world) -> None:  # noqa: F811
    now = datetime.now(timezone.utc)
    async with world["sm"]() as s:
        # No fresh listing copy: the per-state counts Etsy reported are used.
        for row in (await s.execute(select(ShopListingCache))).scalars():
            await s.delete(row)
        for cid, active, draft in ((world["a1"], 10, 1), (world["a2"], 5, 2)):
            shop = await s.get(EtsyConnection, cid)
            shop.listing_counts, shop.listing_counts_at = {"active": active, "draft": draft}, now
        await s.commit()
    g = await _groups(world, A=[world["a1"], world["a2"]])
    overview = (await world["a"].get("/api/shop/overview")).json()
    rows = {r["name"]: r for r in overview["shops"]}
    assert set(rows) == {"Frost Tees", "Frost Mugs"}
    assert (rows["Frost Tees"]["summary"]["active"], rows["Frost Mugs"]["summary"]["active"]) == (10, 5)
    assert rows["Frost Tees"]["groups"] == ["A"] and rows["Frost Tees"]["profiles"] == 1
    assert (overview["total"]["active"], overview["total"]["draft"], overview["total"]["shop_counts_known"]) == (15, 3, True)
    assert g  # the group shows on each row
    # The strip's "All shops" is the same total; a shop narrows it.
    every = (await world["a"].get("/api/shop/summary?all=1")).json()
    assert (every["active"], every["draft"]) == (15, 3)
    one = (await world["a"].get(f"/api/shop/summary?shop={world['a2']}")).json()
    assert (one["active"], one["draft"]) == (5, 2)
    # A shop whose counts are unknown makes the total "not known", never a smaller number.
    async with world["sm"]() as s:
        (await s.get(EtsyConnection, world["a2"])).listing_counts = None
        await s.commit()
    total = (await world["a"].get("/api/shop/overview")).json()["total"]
    assert total["shop_counts_known"] is False and total["active"] == 10


async def test_analytics_all_shops_has_a_row_per_shop_and_blank_totals_without_figures(world) -> None:  # noqa: F811
    res = await world["a"].get("/api/analytics/shops")
    assert res.status_code == 200, res.text
    data = res.json()
    assert [s["name"] for s in data["shops"]] == ["Frost Tees", "Frost Mugs"]
    total = data["totals"][0]
    assert total["shops"] == 2
    # No sales read and no statement: blank, with the reason, never 0.
    assert total["profit"]["minor"] is None and "No figure for" in total["profit"]["note"]
    assert [s["name"] for s in (await world["b"].get("/api/analytics/shops")).json()["shops"]] == ["Bob Shop"]


# --- sending: a single shop next to a group, another account's shop -------------------------------


async def test_a_single_shop_next_to_a_group_and_another_accounts_shop_is_404(world) -> None:  # noqa: F811
    g = await _groups(world, A=[world["a1"], world["a2"]], B=[world["a2"]])
    a, batch = world["a"], world["batch"]
    res = await a.put(f"/api/batches/{batch}/send-to", json={"send_to": {"group_ids": [g["B"]], "connection_ids": [str(world["a1"])]}})
    assert res.status_code == 200 and res.json()["label"] == "B + Frost Tees"
    preview = (await a.post(f"/api/batches/{batch}/send/preview", json={"when": "drafts"})).json()
    assert all(sorted(c["shop_name"] for c in r["cells"]) == ["Frost Mugs", "Frost Tees"] for r in preview["rows"])
    res = await a.put(f"/api/batches/{batch}/send-to", json={"send_to": {"connection_ids": [str(world["b1"])]}})
    assert res.status_code == 404


def test_a_shop_in_two_chosen_groups_gets_both_groups_listings_on_one_calendar() -> None:
    s1, s2, s3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    shops = [G.Shop(s1, "One", "g:A"), G.Shop(s2, "Two", "g:A"), G.Shop(s2, "Two", "g:B"), G.Shop(s3, "Three", "g:B")]
    l1, l2 = uuid.uuid4(), uuid.uuid4()
    listings = [G.Listing(l1, "first", "g:A"), G.Listing(l2, "second", "g:B")]
    settings = G.Settings(start=date.today() + timedelta(days=1), per_shop_per_day=5, window_start=time(9), window_end=time(17),
                          spacing_minutes=30, stagger_minutes=5, zone="UTC")
    plan = G.plan(settings, listings, shops, capacity=lambda d: 10_000, now=datetime.now(timezone.utc))
    by_shop: dict[uuid.UUID, list[uuid.UUID]] = {}
    for slot in plan.slots:
        by_shop.setdefault(slot.shop_id, []).append(slot.content_id)
    assert by_shop == {s1: [l1], s2: [l1, l2], s3: [l2]}  # Two takes both, once each
    # No design goes live in two shops of its group in the same minute.
    times = {(x.content_id, x.publish_at) for x in plan.slots}
    assert len(times) == len(plan.slots)
    starts = {x.shop_id: x.local_time for x in plan.slots if x.content_id == l2}
    assert starts[s2] != starts[s3]


async def test_the_group_card_profile_follows_the_group(world) -> None:  # noqa: F811
    # The batch page's "Set it up there" names the profile and shop the dialog opens with.
    key = (await world["a"].get(f"/api/batches/{world['batch']}/groups")).json()[0]["group_key"]
    async with world["sm"]() as s:
        a3 = (await _shop(s, world["alice"], 103, "Frost Caps", 2)).id
        s.add(ListingGroupSetting(tenant_id=world["alice"], batch_id=world["batch"], group_key=key, connection_id=a3,
                                  profile_id=world["pa1"]))
        await s.commit()
    groups = (await world["a"].get(f"/api/batches/{world['batch']}/groups")).json()
    group = next(g for g in groups if g["group_key"] == key)
    # B3: the group goes to its profile's main shop, where the profile is set up: nothing to set up.
    assert group["profile_setup"] is None and group["connection_id"] == str(world["a1"])
