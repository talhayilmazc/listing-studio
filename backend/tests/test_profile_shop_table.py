"""The Shops table on a profile (B7).

* one row per shop the profile is used in: its main shop first;
* a shop's own title prefix changes only the prefix of the profile's titles there (never cut),
  the main shop keeps the profile's own; "follow" puts it back;
* a shop's section title is the section its listings default to there, after the seller's own
  choice, and only when the shop has a section by that name;
* the shop's daily limit caps a spread-out schedule there;
* a shop connected later joins the "All shops" group;
* another account gets 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.db.models import EtsyConnection, GeneratedContent, ListingProfile, ProfileShopLink, ShopGroupMember
from app.pipeline import item_options
from app.pipeline import group_plan as G
from app.pipeline.profile_shop_settings import shop_section
from app.pipeline.targets import resolve_target
from tests.test_multi_shop import PREFIX_A, _shop, world  # noqa: F401  (fixture)


async def _linked(world) -> None:  # noqa: F811
    """Profile pa1 (main shop Frost Tees) also used in Frost Mugs; Frost Mugs' sections read."""
    async with world["sm"]() as s:
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=world["pa1"], connection_id=world["a2"], status="ready"))
        mugs = await s.get(EtsyConnection, world["a2"])
        mugs.sections = [{"id": 7, "title": "Nurse Gifts"}, {"id": 8, "title": "Mugs"}]
        mugs.sections_at = datetime.now(timezone.utc)
        await s.commit()


async def test_one_row_per_shop_main_first(world) -> None:  # noqa: F811
    await _linked(world)
    res = await world["a"].get(f"/api/profiles/{world['pa1']}/shops")
    assert res.status_code == 200, res.text
    rows = res.json()["rows"]
    assert [(r["shop_name"], r["main"]) for r in rows] == [("Frost Tees", True), ("Frost Mugs", False)]
    assert rows[0]["title_prefix"] == PREFIX_A and rows[0]["prefix_own"] is True
    assert rows[1]["title_prefix"] == PREFIX_A and rows[1]["prefix_own"] is False  # follows the profile
    assert rows[1]["sections"] == ["Nurse Gifts", "Mugs"] and rows[1]["section_title"] is None


async def test_a_shops_own_prefix_changes_only_the_prefix_there(world) -> None:  # noqa: F811
    await _linked(world)
    put = lambda body: world["a"].put(f"/api/profiles/{world['pa1']}/shops/{world['a2']}", json=body)  # noqa: E731
    res = await put({"field": "title_prefix", "value": "Gift Den"})
    assert res.status_code == 200
    assert res.json()["rows"][1]["title_prefix"] == "Gift Den" and res.json()["rows"][1]["prefix_own"] is True
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, world["contents"][0])
        here = await resolve_target(s, content, await s.get(EtsyConnection, world["a2"]))
        home = await resolve_target(s, content, await s.get(EtsyConnection, world["a1"]))
    assert here.ok and here.title.startswith("Gift Den") and PREFIX_A not in here.title
    assert home.title == content.title  # the main shop keeps the profile's own prefix
    # Back to following the profile.
    res = await put({"field": "title_prefix", "follow": True})
    assert res.json()["rows"][1]["prefix_own"] is False
    async with world["sm"]() as s:
        assert (await s.get(ListingProfile, world["pa1"])).shop_settings is None


async def test_a_shops_section_is_the_default_there_after_the_sellers_choice(world) -> None:  # noqa: F811
    await _linked(world)
    res = await world["a"].put(f"/api/profiles/{world['pa1']}/shops/{world['a2']}",
                               json={"field": "section_title", "value": "Nurse Gifts"})
    assert res.json()["rows"][1]["section_title"] == "Nurse Gifts"
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        assert shop_section(profile, world["a2"]) == "Nurse Gifts" and shop_section(profile, world["a1"]) is None
    # What the draft gets: the profile's title unless the seller chose for this listing.
    assert item_options.section_choice(None, world["a2"], "Nurse Gifts") == {"id": None, "title": "Nurse Gifts", "explicit": False}
    mine = {item_options.SECTIONS: {str(world["a2"]): {"id": 8, "title": "Mugs"}}}
    assert item_options.section_choice(mine, world["a2"], "Nurse Gifts")["title"] == "Mugs"
    # The review card offers it, saying why (the batch goes to both shops).
    await world["a"].put(f"/api/batches/{world['batch']}/send-to",
                         json={"send_to": {"connection_ids": [str(world["a1"]), str(world["a2"])]}})
    card = (await world["a"].get(f"/api/content/{world['contents'][0]}/options")).json()
    mugs = next(x for x in card["shops"] if x["connection_id"] == str(world["a2"]))
    assert mugs["selected_title"] == "Nurse Gifts" and mugs["source"] == "profile"
    too_long = await world["a"].put(f"/api/profiles/{world['pa1']}/shops/{world['a2']}",
                                    json={"field": "section_title", "value": "x" * 25})
    assert too_long.status_code == 422


async def test_a_shops_daily_limit_caps_a_spread_out_schedule(world) -> None:  # noqa: F811
    res = await world["a"].put(f"/api/shops/{world['a1']}/daily-limit", json={"daily_limit": 1})
    assert res.status_code == 200 and res.json()["daily_limit"] == 1
    assert (await world["a"].put(f"/api/shops/{world['a1']}/daily-limit", json={"daily_limit": 0})).status_code == 422
    from datetime import time

    shop = G.Shop(id=world["a1"], name="Frost Tees", group_id="k", daily=1)
    listings = [G.Listing(content_id=uuid.uuid4(), title=f"L{i}", group_id="k") for i in range(3)]
    settings = G.Settings(start=(datetime.now(timezone.utc) + timedelta(days=1)).date(), per_shop_per_day=5,
                          window_start=time(9), window_end=time(17), spacing_minutes=30, stagger_minutes=5, zone="UTC")
    plan = G.plan(settings, listings, [shop], capacity=lambda d: 10_000, now=datetime.now(timezone.utc))
    assert len({x.local_day for x in plan.slots}) == 3  # one a day, not five


async def test_a_shop_connected_later_joins_all_shops(world) -> None:  # noqa: F811
    from app.api.send import join_all_shops

    state = (await world["a"].post(f"/api/batches/{world['batch']}/send-to/all-shops")).json()
    group = next(g for g in state["groups"] if g["name"] == "All shops")
    async with world["sm"]() as s:
        caps = await _shop(s, world["alice"], 103, "Frost Caps", 2)
        await s.commit()
        await join_all_shops(s, caps)
        members = {m.connection_id for m in (await s.execute(
            ShopGroupMember.__table__.select().where(ShopGroupMember.group_id == uuid.UUID(group["id"])))).all()}
    assert members == {world["a1"], world["a2"], caps.id}


async def test_another_account_gets_404(world) -> None:  # noqa: F811
    await _linked(world)
    b = world["b"]
    assert (await b.get(f"/api/profiles/{world['pa1']}/shops")).status_code == 404
    assert (await b.put(f"/api/profiles/{world['pa1']}/shops/{world['a2']}", json={"field": "title_prefix", "value": "x"})).status_code == 404
    assert (await b.put(f"/api/shops/{world['a1']}/daily-limit", json={"daily_limit": 3})).status_code == 404
    # Alice's profile in a shop it is not used in: 404.
    async with world["sm"]() as s:
        caps = await _shop(s, world["alice"], 104, "Frost Caps", 3)
        await s.commit()
    assert (await world["a"].put(f"/api/profiles/{world['pa1']}/shops/{caps.id}", json={"field": "title_prefix", "value": "x"})).status_code == 404
