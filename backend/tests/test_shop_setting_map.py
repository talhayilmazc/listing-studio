"""One shop setting map per account (pipeline/setting_map.py, workers/links.py).

Production (2026-10-20): setting the reference listing "Regular" up in PCS created
"MemoPaid" there and the dialog said "1 of 1 shops are set up"; a second reference
listing using the same shipping profile still said "Shipping profile: no match in this
shop" with "Finish setup in 1 shop". Each profile's link kept its own result, computed
when that link was checked; nothing told the second link about the copy.

Now (source shop, source setting) -> (target shop, target id) is the account's, shared by
every reference listing, current and future:

* the screenshot case: the second card is set up the moment the copy exists;
* one source setting, 10 reference listings, 100 shops: one copy per shop, no question,
  each shop's lists read once (plus one fresh read before each copy);
* a seller's choice in one card is every card's;
* copies are made only on the seller's own action (Use in, Finish setup, "Use all
  reference listings here"): that click is the confirmation; the background check, a
  reconnected shop and the one-time job only match;
* a copy the app made that was deleted on Etsy is made again (found missing by the
  background check or refused by a draft), and the card says so in one line; a setting
  the seller made themselves is never recreated;
* two setups at once make one copy;
* an Etsy refusal still asks the seller, with the reason, and records nothing;
* existing accounts: the map built from the links (migration 0082, tests/test_migration_0082.py)
  fills the second card without any Etsy request (the one-time job).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy import func, select

from app.db.models import EtsyConnection, ListingProfile, ProfileShopLink, ShopSettingMap
from app.etsy.errors import EtsyClientError
from app.pipeline import links as L
from app.pipeline import setting_map
from app.workers import links as link_jobs
from tests.test_multi_shop import _shop, world  # noqa: F401  (fixture)
from tests.test_profile_links import PAYLOAD, _ctx, _links, _setup_world, fake_etsy  # noqa: F401  (fixture)


async def _second_profile(world, name: str = "Second listing") -> uuid.UUID:  # noqa: F811
    """Another reference listing in the main shop (Frost Tees) using the same settings."""
    async with world["sm"]() as s:
        profile = ListingProfile(tenant_id=world["alice"], connection_id=world["a1"], name=name,
                                 reference_listing_id=uuid.uuid4().int % 10**9, content_template="apparel",
                                 listing_style="classic", confirmed=True, cached_payload=dict(PAYLOAD))
        s.add(profile)
        await s.commit()
        return profile.id


async def _use_in(world, profile_id, shops: list) -> None:  # noqa: F811
    res = await world["a"].post(f"/api/profiles/{profile_id}/use-in",
                                json={"scope": "shops", "connection_ids": [str(s) for s in shops]})
    assert res.status_code == 200, res.text


async def _card(world, profile_id, shop_name: str) -> dict:  # noqa: F811
    profiles = (await world["a"].get("/api/profiles")).json()
    profile = next(p for p in profiles if p["id"] == str(profile_id))
    return next(link for link in profile["links"] if link["shop_name"] == shop_name)


def _made(fake, sid: int, what: str = "shipping") -> int:  # noqa: ANN001
    return sum(1 for s, w, _ in fake.created if s == sid and w == what)


async def _map_rows(world) -> list[ShopSettingMap]:  # noqa: F811
    async with world["sm"]() as s:
        return list((await s.execute(select(ShopSettingMap))).scalars())


# --- the screenshot case -------------------------------------------------------------------------


async def test_a_second_reference_listing_is_set_up_the_moment_the_copy_exists(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    second = await _second_profile(world)
    # The second card was checked before any copy existed: "no match in this shop".
    async with world["sm"]() as s:
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=second, connection_id=extra["a3"],
                              status="incomplete", production_partner_ids=[3801],
                              notes={"shipping_profile": L.NOT_FOUND, "return_policy": L.NOT_FOUND,
                                     "readiness_state": L.NOT_FOUND}))
        await s.commit()
    assert (await _card(world, second, "Frost Caps"))["ready"] is False

    # "Regular" is set up in Caps: the copies are made, and the second card is set up too,
    # without a job of its own and without a question.
    await _use_in(world, world["pa1"], [extra["a3"]])
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    first, card = await _card(world, world["pa1"], "Frost Caps"), await _card(world, second, "Frost Caps")
    assert first["ready"] is True and card["ready"] is True and card["missing"] == []
    links = {(l.profile_id, l.connection_id): l for l in (await _links(world, world["pa1"])).values()}
    async with world["sm"]() as s:
        theirs = (await s.execute(select(ProfileShopLink).where(ProfileShopLink.profile_id == second))).scalar_one()
    ours = links[(world["pa1"], extra["a3"])]
    assert (theirs.shipping_profile_id, theirs.return_policy_id, theirs.readiness_state_id) == (
        ours.shipping_profile_id, ours.return_policy_id, ours.readiness_state_id)
    # Its own setup later makes nothing more: the map answers.
    await _use_in(world, second, [extra["a3"]])
    await link_jobs._link_profile(_ctx(world), str(second))
    assert (_made(fake_etsy, 103), _made(fake_etsy, 103, "return_policy"), _made(fake_etsy, 103, "readiness")) == (1, 1, 1)


def test_names_match_whatever_their_spacing_and_case() -> None:
    assert L.same_name("MemoPaid", " memopaid ") and L.same_name("Memo  Paid", "memo paid")
    assert not L.same_name("MemoPaid", "MemoPaid (created)") and not L.same_name("", "")
    main = L.ShopSettings(shipping=[{"shipping_profile_id": 1, "title": "MemoPaid"}])
    outcome = L.plan_resource(L.SHIPPING, 1, main, L.ShopSettings(shipping=[{"shipping_profile_id": 9, "title": "memopaid "}]))
    assert (outcome.linked, outcome.how) == (9, "same name")


# --- scale: 10 reference listings, 100 shops ------------------------------------------------------


async def test_one_setting_used_by_ten_listings_in_a_hundred_shops_gives_one_copy_per_shop(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    shops = [world["a2"], extra["a3"]]
    async with world["sm"]() as s:
        for n in range(98):
            sid = 2000 + n
            shop = await _shop(s, world["alice"], sid, f"Shop {n:02d}", 10 + n)
            shops.append(shop.id)
            fake_etsy.lists[sid] = L.ShopSettings(partners=[{"production_partner_id": sid * 10, "partner_name": "Print Co"}])
        await s.commit()
    profiles = [world["pa1"]] + [await _second_profile(world, f"Listing {n}") for n in range(9)]
    assert len(shops) == 100 and len(profiles) == 10
    for profile_id in profiles:
        await _use_in(world, profile_id, shops)
        assert await link_jobs._link_profile(_ctx(world), str(profile_id)) == "linked"

    # One copy per shop that had none (Frost Mugs has its own by name and terms), never two.
    for sid in [103] + [2000 + n for n in range(98)]:
        assert (_made(fake_etsy, sid), _made(fake_etsy, sid, "return_policy"), _made(fake_etsy, sid, "readiness")) == (1, 1, 1)
    assert (_made(fake_etsy, 102), _made(fake_etsy, 102, "return_policy")) == (0, 0)
    # Every card in every shop is set up; no shop asks anything.
    async with world["sm"]() as s:
        statuses = (await s.execute(select(ProfileShopLink.status, func.count()).where(
            ProfileShopLink.tenant_id == world["alice"], ProfileShopLink.connection_id != world["a1"],
        ).group_by(ProfileShopLink.status))).all()
    assert dict(statuses) == {"ready": 1000}
    setup = (await world["a"].get(f"/api/profiles/{profiles[-1]}/setup")).json()
    assert all(s["default"] == "keep" and s["failure"] is None for card in setup["shops"] for s in card["settings"])
    # Each shop's lists are read once for all ten (kept an hour), plus one fresh read before
    # each copy; the main shop's once. Not ten times each.
    assert fake_etsy.reads[(101, "shipping")] == 1
    assert fake_etsy.reads[(102, "shipping")] == 1
    assert all(fake_etsy.reads[(2000 + n, "shipping")] == 2 for n in range(98))
    assert all(fake_etsy.reads[(2000 + n, "partners")] == 1 for n in range(98))
    assert len(await _map_rows(world)) == 3 * 100


# --- a choice in one card is every card's ---------------------------------------------------------


async def test_a_sellers_choice_in_one_card_applies_to_every_card(world, fake_etsy) -> None:  # noqa: F811
    await _setup_world(world)
    second = await _second_profile(world)
    fake_etsy.lists[102].shipping.append({"shipping_profile_id": 2502, "title": "Free shipping"})
    for profile_id in (world["pa1"], second):
        await _use_in(world, profile_id, [world["a2"]])
        await link_jobs._link_profile(_ctx(world), str(profile_id))
    assert (await _links(world, second))[world["a2"]].shipping_profile_id == 2501  # same name, both

    res = await world["a"].put(f"/api/profiles/{world['pa1']}/setup/{world['a2']}",
                               json={"resource": "shipping_profile", "ids": [2502]})
    assert res.status_code == 200
    assert (await _links(world, world["pa1"]))[world["a2"]].shipping_profile_id == 2502
    assert (await _links(world, second))[world["a2"]].shipping_profile_id == 2502  # the other card too
    [row] = [r for r in await _map_rows(world) if r.resource == L.SHIPPING]
    assert (row.target_id, row.how) == (2502, "your choice")
    # A later setup keeps the seller's choice.
    await _use_in(world, second, [world["a2"]])
    async with world["sm"]() as s:
        (await s.get(ProfileShopLink, (await _links(world, second))[world["a2"]].id)).status = "checking"
        await s.commit()
    await link_jobs._link_profile(_ctx(world), str(second))
    assert (await _links(world, second))[world["a2"]].shipping_profile_id == 2502


# --- a target deleted on Etsy ---------------------------------------------------------------------


async def test_a_deleted_target_is_copied_again_and_the_card_says_so(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    second = await _second_profile(world)
    for profile_id in (world["pa1"], second):
        await _use_in(world, profile_id, [extra["a3"]])
        await link_jobs._link_profile(_ctx(world), str(profile_id))
    old = (await _links(world, world["pa1"]))[extra["a3"]].shipping_profile_id
    # Deleted in Frost Caps's Shop Manager.
    fake_etsy.lists[103].shipping = [r for r in fake_etsy.lists[103].shipping if r["shipping_profile_id"] != old]
    queued: list[tuple] = []

    async def enqueue(function: str, *args: Any, **_: Any) -> None:
        queued.append((function, args))

    ctx = {**_ctx(world), "enqueue": enqueue}
    assert await link_jobs._refresh_shop_links(ctx, str(extra["a3"])) == "checked:2 re-resolved"
    assert sorted(queued) == sorted([("link_profile", (str(world["pa1"]),)), ("link_profile", (str(second),))])
    await link_jobs._link_profile(ctx, str(world["pa1"]))
    new = (await _links(world, world["pa1"]))[extra["a3"]].shipping_profile_id
    assert new not in (None, old) and _made(fake_etsy, 103) == 2
    assert (await _links(world, second))[extra["a3"]].shipping_profile_id == new  # every card, at once
    card = await _card(world, world["pa1"], "Frost Caps")
    assert card["ready"] is True
    assert card["notice"] == 'Shipping profile "Standard US" is no longer in Frost Caps: a new copy was made.'
    await link_jobs._link_profile(ctx, str(second))
    assert _made(fake_etsy, 103) == 2  # the second card's own run makes nothing


async def test_a_deleted_setting_the_seller_made_is_never_recreated(world, fake_etsy) -> None:  # noqa: F811
    await _setup_world(world)
    await _use_in(world, world["pa1"], [world["a2"]])
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))  # "Standard US" in Mugs: the seller's own, same name
    fake_etsy.lists[102].shipping = []  # deleted in Mugs's Shop Manager
    queued: list[tuple] = []

    async def enqueue(function: str, *args: Any, **_: Any) -> None:
        queued.append((function, args))

    ctx = {**_ctx(world), "enqueue": enqueue}
    await link_jobs._refresh_shop_links(ctx, str(world["a2"]))
    await link_jobs._link_profile(ctx, str(world["pa1"]))
    assert fake_etsy.created == []  # not the app's copy: not made again without the seller
    card = await _card(world, world["pa1"], "Frost Mugs")
    assert card["ready"] is False
    assert card["notice"] == 'Shipping profile "Standard US" is no longer in Frost Mugs; set it up again.'


async def test_use_all_reference_listings_here_sets_every_profile_up_in_one_click(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    second = await _second_profile(world)
    caps = str(extra["a3"])
    status = (await world["a"].get(f"/api/profiles/shop/{caps}")).json()
    assert (status["total"], status["not_set_up"], status["set_up"]) == (2, 2, 0)
    world["queue"].calls.clear()
    res = await world["a"].post(f"/api/profiles/shop/{caps}/use-all")
    assert res.status_code == 200 and res.json()["started"] == 2 and res.json()["checking"] == 2
    assert sorted(world["queue"].calls) == sorted([("link_profile", (str(world["pa1"]),)), ("link_profile", (str(second),))])
    for profile_id in (world["pa1"], second):
        await link_jobs._link_profile(_ctx(world), str(profile_id))
    status = (await world["a"].get(f"/api/profiles/shop/{caps}")).json()
    assert (status["set_up"], status["waiting"], status["checking"]) == (2, [], 0)
    assert _made(fake_etsy, 103) == 1  # one copy for both reference listings
    # A second click starts nothing; another account's shop is 404.
    assert (await world["a"].post(f"/api/profiles/shop/{caps}/use-all")).json()["started"] == 0
    assert (await world["b"].post(f"/api/profiles/shop/{caps}/use-all")).status_code == 404
    assert (await world["b"].get(f"/api/profiles/shop/{caps}")).status_code == 404


async def test_use_all_reports_what_etsy_refused_for_the_dialog(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    fake_etsy.fail[(103, "shipping")] = EtsyClientError(
        status_code=400, body='{"error": "Unable to create shipping profile"}', path="/x", method="POST")
    await world["a"].post(f"/api/profiles/shop/{extra['a3']}/use-all")
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    status = (await world["a"].get(f"/api/profiles/shop/{extra['a3']}")).json()
    assert status["set_up"] == 0 and [w["id"] for w in status["waiting"]] == [str(world["pa1"])]
    assert "Create it in Frost Caps's Shop Manager" in status["waiting"][0]["reason"]


async def test_a_reconnected_shop_is_matched_again_but_nothing_is_copied(world, fake_etsy) -> None:  # noqa: F811
    from app.pipeline.profile_shops import recheck_shop

    extra = await _setup_world(world)
    async with world["sm"]() as s:
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=world["pa1"], connection_id=extra["a3"],
                              status="incomplete", production_partner_ids=[3801], notes={"shipping_profile": L.NOT_FOUND}))
        await s.commit()
    async with world["sm"]() as s:
        assert await recheck_shop(s, extra["a3"]) == [world["pa1"]]
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    assert fake_etsy.created == []
    assert (await _links(world, world["pa1"]))[extra["a3"]].status == "incomplete"


async def test_a_draft_refused_for_a_deleted_setting_resolves_it_again(world, fake_etsy) -> None:  # noqa: F811
    from app.db.models import Job, JobType

    extra = await _setup_world(world)
    await _use_in(world, world["pa1"], [extra["a3"]])
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    held = (await _links(world, world["pa1"]))[extra["a3"]].shipping_profile_id
    job = Job(tenant_id=world["alice"], connection_id=extra["a3"], type=JobType.create_draft,
              payload={"content_id": str(world["contents"][0]), "profile_id": str(world["pa1"])})

    def refusal(listing_id: int) -> EtsyClientError:
        body = f'{{"error": "Could not find shipping_profile_id=\'{listing_id}\' associated with shop \'103\'"}}'
        return EtsyClientError(status_code=404, body=body, path="/x", method="POST")

    async with world["sm"]() as s:
        # Another shop's id was sent (not this link's): nothing was deleted, nothing changes.
        assert await link_jobs.setting_gone(s, world["redis"], job, refusal(123456)) is None
        assert await link_jobs.setting_gone(s, world["redis"], job, refusal(held)) == world["pa1"]
        await s.commit()
    assert not [r for r in await _map_rows(world) if r.resource == L.SHIPPING]
    assert (await _links(world, world["pa1"]))[extra["a3"]].status == "checking"


# --- two setups at once ---------------------------------------------------------------------------


async def test_two_setups_at_once_make_one_copy(world, fake_etsy, monkeypatch) -> None:  # noqa: F811
    extra = await _setup_world(world)
    second = await _second_profile(world)
    monkeypatch.setattr(link_jobs, "COPY_POLL_SECONDS", 0.01)
    fake_etsy.delay = 0.05  # each copy takes a moment: the two overlap
    await _use_in(world, world["pa1"], [extra["a3"]])
    await _use_in(world, second, [extra["a3"]])
    results = await asyncio.gather(link_jobs._link_profile(_ctx(world), str(world["pa1"])),
                                   link_jobs._link_profile(_ctx(world), str(second)))
    assert results == ["linked", "linked"]
    assert (_made(fake_etsy, 103), _made(fake_etsy, 103, "return_policy"), _made(fake_etsy, 103, "readiness")) == (1, 1, 1)
    assert len(await _map_rows(world)) == 3
    ids = {(await _links(world, p))[extra["a3"]].shipping_profile_id for p in (world["pa1"], second)}
    assert len(ids) == 1 and None not in ids


async def test_the_map_keeps_one_row_per_setting_and_shop(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        for target_id in (11, 12):
            await setting_map.remember(s, world["alice"], L.SHIPPING, world["a1"], 501, world["a2"], target_id,
                                       how="same name", label="Standard US")
        await s.commit()
    [row] = await _map_rows(world)
    assert row.target_id == 12


# --- an Etsy refusal still asks the seller ---------------------------------------------------------


async def test_a_refusal_asks_the_seller_with_the_reason_and_records_nothing(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    fake_etsy.fail[(103, "shipping")] = EtsyClientError(
        status_code=400, body='{"error": "Unable to create shipping profile"}', path="/x", method="POST")
    await _use_in(world, world["pa1"], [extra["a3"]])
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    setup = (await world["a"].get(f"/api/profiles/{world['pa1']}/setup")).json()
    shipping = next(s for s in setup["shops"][0]["settings"] if s["resource"] == "shipping_profile")
    assert shipping["failure"]["action"] == "manual" and shipping["default"] == "choose"
    assert not [r for r in await _map_rows(world) if r.resource == L.SHIPPING]
    assert (await _card(world, world["pa1"], "Frost Caps"))["ready"] is False


# --- existing accounts: the one-time job ----------------------------------------------------------


async def test_the_one_time_job_fills_existing_cards_from_the_map_without_etsy(world, fake_etsy, monkeypatch) -> None:  # noqa: F811
    """After migration 0082 the map holds what the first card's link held; the job fills the
    second card from it: no Etsy request, no question. A second run changes nothing."""
    extra = await _setup_world(world)
    second = await _second_profile(world)
    async with world["sm"]() as s:
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=world["pa1"], connection_id=extra["a3"], status="ready",
                              shipping_profile_id=9001, return_policy_id=9002, readiness_state_id=9003,
                              production_partner_ids=[3801]))
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=second, connection_id=extra["a3"], status="incomplete",
                              production_partner_ids=[3801],
                              notes={"shipping_profile": L.NOT_FOUND, "return_policy": L.NOT_FOUND,
                                     "readiness_state": L.NOT_FOUND}))
        for resource, source_id, target_id in ((L.SHIPPING, 501, 9001), (L.RETURNS, 601, 9002), (L.READINESS, 701, 9003)):
            s.add(ShopSettingMap(tenant_id=world["alice"], resource=resource, source_connection_id=world["a1"],
                                 source_id=source_id, target_connection_id=extra["a3"], target_id=target_id,
                                 how="earlier choice"))
        await s.commit()
    queued: list[tuple] = []

    async def enqueue(function: str, *args: Any, **_: Any) -> None:
        queued.append((function, args))

    ctx = {**_ctx(world), "enqueue": enqueue}
    # A development machine (ETSY_CALLS_BLOCKED) never runs it by itself.
    monkeypatch.setattr(link_jobs, "get_settings", lambda: type("S", (), {"etsy_calls_blocked": True})())
    assert await link_jobs.resolve_shop_settings_once(ctx) == "skipped"
    monkeypatch.setattr(link_jobs, "get_settings", lambda: type("S", (), {"etsy_calls_blocked": False})())
    assert await link_jobs.resolve_shop_settings_once(ctx) == "filled:1 resolve:0"
    card = await _card(world, second, "Frost Caps")
    assert card["ready"] is True
    theirs = (await _links(world, second))[extra["a3"]]
    assert (theirs.shipping_profile_id, theirs.return_policy_id, theirs.readiness_state_id) == (9001, 9002, 9003)
    assert queued == [] and sum(fake_etsy.reads.values()) == 0 and fake_etsy.created == []
    assert await link_jobs.resolve_shop_settings_once(ctx) == "done-before"


async def test_the_one_time_job_matches_but_never_makes_a_copy_nobody_asked_for(world, fake_etsy, monkeypatch) -> None:  # noqa: F811
    extra = await _setup_world(world)
    async with world["sm"]() as s:
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=world["pa1"], connection_id=extra["a3"],
                              status="incomplete", production_partner_ids=[3801],
                              notes={"shipping_profile": L.NOT_FOUND}))
        await s.commit()
    async with world["sm"]() as s:
        dry = await link_jobs.resolve_all(s, apply=False)
    assert (dry["links_filled"], dry["links_to_resolve"], dry["profiles"]) == (0, 1, [world["pa1"]])
    assert (await _links(world, world["pa1"]))[extra["a3"]].status == "incomplete"  # a dry run writes nothing
    async with world["sm"]() as s:
        done = await link_jobs.resolve_all(s, apply=True)
    assert done["links_to_resolve"] == 1
    assert (await _links(world, world["pa1"]))[extra["a3"]].status == "checking"
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    # Read and matched only: Caps has no counterpart, and nobody asked for a copy.
    assert fake_etsy.created == []
    assert (await _links(world, world["pa1"]))[extra["a3"]].status == "incomplete"
    card = await _card(world, world["pa1"], "Frost Caps")
    assert card["ready"] is False
    # "Finish setup" is the seller's click: now the copies are made.
    await _use_in(world, world["pa1"], [extra["a3"]])
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    assert (await _links(world, world["pa1"]))[extra["a3"]].status == "ready" and _made(fake_etsy, 103) == 1


async def test_disconnecting_a_shop_forgets_its_settings_both_ways(world, fake_etsy) -> None:  # noqa: F811
    from app.pipeline.profile_shops import detach_shop

    extra = await _setup_world(world)
    await _use_in(world, world["pa1"], [extra["a3"]])
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    assert len(await _map_rows(world)) == 3
    async with world["sm"]() as s:
        await detach_shop(s, extra["a3"])
        await s.commit()
    assert await _map_rows(world) == []
    async with world["sm"]() as s:
        assert (await s.get(EtsyConnection, extra["a3"])) is not None  # only the map and links go
