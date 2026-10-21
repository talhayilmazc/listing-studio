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
* a reference listing uses its main shop's settings in every shop with no question
  (21 October 2026): Use in, drafting there and a newly connected shop all set it up,
  copying at once what the shop lacks; no dialog, no "Finish setup";
* a copy the app made that was deleted on Etsy is made again (found missing by the
  background check or refused by a draft), and the card says so in one line; a setting
  the seller made themselves is never recreated;
* two setups at once make one copy;
* an Etsy refusal is one plain line on the card, records nothing, and the other shops carry on;
* a reference listing's main shop is its own shop (the shop mix-up check);
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
    # The second card was checked before any copy existed ("no match in this shop" then; the card
    # now reads ready, since a draft there sets the shop up by itself).
    async with world["sm"]() as s:
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=second, connection_id=extra["a3"],
                              status="incomplete", production_partner_ids=[3801],
                              notes={"shipping_profile": L.NOT_FOUND, "return_policy": L.NOT_FOUND,
                                     "readiness_state": L.NOT_FOUND}))
        await s.commit()
    assert L.NOT_FOUND not in str(await _card(world, second, "Frost Caps"))

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
    assert fake_etsy.created == []  # not the app's copy: not made again
    card = await _card(world, world["pa1"], "Frost Mugs")
    assert card["ready"] is False
    assert card["reason"] == ('Shipping profile: "Standard US" was deleted in Frost Mugs\'s Shop Manager; choose another '
                              "of the shop's with change")
    # Set up again (Use in, a draft): still never remade.
    await _use_in(world, world["pa1"], [world["a2"]])
    await link_jobs._link_profile(ctx, str(world["pa1"]))
    assert fake_etsy.created == []
    # The seller's pick with change replaces the tombstone, for every card.
    fake_etsy.lists[102].shipping = [{"shipping_profile_id": 2599, "title": "New Mugs shipping"}]
    await world["a"].post(f"/api/profiles/{world['pa1']}/setup/{world['a2']}/check")
    await link_jobs._link_profile(ctx, str(world["pa1"]))
    res = await world["a"].put(f"/api/profiles/{world['pa1']}/setup/{world['a2']}",
                               json={"resource": "shipping_profile", "ids": [2599]})
    assert res.status_code == 200
    assert (await _card(world, world["pa1"], "Frost Mugs"))["ready"] is True


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


async def test_the_one_time_job_sets_up_what_the_old_rules_left_waiting(world, fake_etsy, monkeypatch) -> None:  # noqa: F811
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
    # Caps had no counterpart: copied at once, no "Finish setup", no question.
    assert (await _links(world, world["pa1"]))[extra["a3"]].status == "ready" and _made(fake_etsy, 103) == 1
    assert (await _card(world, world["pa1"], "Frost Caps"))["ready"] is True


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


# --- 21 October 2026: no dialog, no "Finish setup", no confirm -------------------------------------


async def test_opa_to_pcs_needs_no_dialog_and_no_seller_action(world, fake_etsy) -> None:  # noqa: F811
    """"Regular" (main OPA = Frost Tees) used in PCS (Frost Caps): its shipping profile,
    return policy and processing profile are copied at once; the card says the shop is set
    up; nothing waits for the seller."""
    extra = await _setup_world(world)
    second = await _second_profile(world)
    await _use_in(world, world["pa1"], [extra["a3"]])
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    for profile_id in (world["pa1"], second):  # the second reference listing too, without its own setup
        if profile_id == second:
            await _use_in(world, second, [extra["a3"]])
            await link_jobs._link_profile(_ctx(world), str(second))
        card = await _card(world, profile_id, "Frost Caps")
        assert card["ready"] is True and card["missing"] == [] and card["status"] == "ready"
    setup = (await world["a"].get(f"/api/profiles/{world['pa1']}/setup")).json()
    assert all(s["failure"] is None and s["default"] == "keep" for c in setup["shops"] for s in c["settings"])
    assert (_made(fake_etsy, 103), _made(fake_etsy, 103, "return_policy"), _made(fake_etsy, 103, "readiness")) == (1, 1, 1)


async def test_a_newly_connected_shop_gets_every_reference_listing(world, fake_etsy) -> None:  # noqa: F811
    from app.pipeline.profile_shops import use_all_in_shop

    extra = await _setup_world(world)
    second = await _second_profile(world)
    async with world["sm"]() as s:
        started = await use_all_in_shop(s, world["alice"], extra["a3"])
    assert sorted(started, key=str) == sorted([world["pa1"], second], key=str)
    for profile_id in started:
        await link_jobs._link_profile(_ctx(world), str(profile_id))
    assert (await _card(world, world["pa1"], "Frost Caps"))["ready"] is True
    assert (await _card(world, second, "Frost Caps"))["ready"] is True
    assert _made(fake_etsy, 103) == 1  # one copy for both
    async with world["sm"]() as s:  # connected again: nothing set up twice
        assert await use_all_in_shop(s, world["alice"], extra["a3"]) == []


async def test_drafting_in_a_shop_not_set_up_sets_it_up_first(world, fake_etsy) -> None:  # noqa: F811
    """Ticking Frost Caps for a listing whose profile was never used there: the draft is
    queued anyway, the profile is set up there (copies made), and the matrix shows the cell
    as available, not "set it up first"."""
    extra = await _setup_world(world)
    c0 = world["contents"][0]
    world["queue"].calls.clear()
    preview = (await world["a"].post(f"/api/batches/{world['batch']}/publish/preview",
                                     json={"pairs": [{"content_id": str(c0), "connection_id": str(extra["a3"])}]})).json()
    row = next(r for r in preview["rows"] if r["content_id"] == str(c0))
    cell = next(c for c in row["cells"] if c["connection_id"] == str(extra["a3"]))
    assert cell["state"] == "available" and "Frost Tees's shipping, return and processing settings" in cell["note"]
    column = next(c for c in preview["columns"] if c["connection_id"] == str(extra["a3"]))
    assert [n for n in column["needs"] if n["kind"] != "section"] == []
    res = await world["a"].post(f"/api/batches/{world['batch']}/publish",
                                json={"pairs": [{"content_id": str(c0), "connection_id": str(extra["a3"])}]})
    assert res.status_code == 200, res.text
    assert [j["connection_id"] for j in res.json()["jobs"]] == [str(extra["a3"])]
    assert ("link_profile", (str(world["pa1"]),)) in world["queue"].calls
    assert (await _links(world, world["pa1"]))[extra["a3"]].status == "checking"
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    assert (await _links(world, world["pa1"]))[extra["a3"]].status == "ready"


async def test_the_draft_job_waits_for_the_setup_and_fails_only_on_a_refusal(world, fake_etsy, monkeypatch) -> None:  # noqa: F811
    from app.db.models import Job, JobStatus, JobType
    from app.workers import publish as publish_worker
    from app.workers import recovery

    extra = await _setup_world(world)
    c0 = world["contents"][0]

    class Pool:
        def __init__(self) -> None:
            self.jobs: list[tuple] = []

        async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> None:
            self.jobs.append((function, args))

    pool = Pool()

    async def no_gate(ctx, session, job, function):  # noqa: ANN001
        return None

    monkeypatch.setattr(publish_worker, "start_job", no_gate)
    ctx = {"sessionmaker": world["sm"], "redis": pool}

    async def run() -> Job:
        async with world["sm"]() as s:
            job = Job(tenant_id=world["alice"], connection_id=extra["a3"], type=JobType.create_draft,
                      payload={"content_id": str(c0), "profile_id": str(world["pa1"])}, status=JobStatus.running)
            s.add(job)
            await s.commit()
            job_id = job.id
        assert await publish_worker._run_publish_job(ctx, str(job_id)) in ("deferred", "failed")
        async with world["sm"]() as s:
            return await s.get(Job, job_id)

    # Not set up in Frost Caps: set up now, and the draft waits without using a try.
    job = await run()
    assert (job.status, job.paused_reason, job.attempts or 0) == (JobStatus.queued, recovery.WAIT_SETUP, 0)
    assert ("link_profile", (str(world["pa1"]),)) in pool.jobs
    # Etsy refuses the copy: the draft for Frost Caps fails with that one line; nothing else stops.
    fake_etsy.fail[(103, "shipping")] = EtsyClientError(
        status_code=400, body='{"error": "Unable to create shipping profile"}', path="/x", method="POST")
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    job = await run()
    assert job.status is JobStatus.failed
    assert job.last_error.startswith('Frost Caps: Shipping profile: Etsy refused the copy of "Standard US"')


async def test_an_etsy_refusal_in_one_shop_leaves_the_others_publishing(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    fake_etsy.fail[(103, "shipping")] = EtsyClientError(
        status_code=400, body='{"error": "Unable to create shipping profile"}', path="/x", method="POST")
    await _use_in(world, world["pa1"], [world["a2"], extra["a3"]])
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    caps, mugs = await _card(world, world["pa1"], "Frost Caps"), await _card(world, world["pa1"], "Frost Mugs")
    assert mugs["ready"] is True
    assert caps["ready"] is False and caps["reason"].startswith('Shipping profile: Etsy refused the copy of "Standard US"')
    assert "Create it in Frost Caps's Shop Manager" in caps["reason"]
    c0 = world["contents"][0]
    res = await world["a"].post(f"/api/batches/{world['batch']}/publish",
                                json={"pairs": [{"content_id": str(c0), "connection_id": str(world["a2"])},
                                                {"content_id": str(c0), "connection_id": str(extra["a3"])}]})
    assert res.status_code == 200
    assert str(world["a2"]) in [j["connection_id"] for j in res.json()["jobs"]]  # Mugs drafts as usual


# --- the shop mix-up check: a reference listing's main shop is its own shop ------------------------


async def test_a_profile_whose_reference_listing_is_in_another_shop_gets_that_shop_as_main(world, fake_etsy) -> None:  # noqa: F811
    """A profile attached to Frost Tees whose reference listing is (by the account's own listing
    cache) in Frost Caps: Frost Caps becomes its main shop, its ids are that listing's own, and
    Frost Tees is set up like any other shop. No Etsy request."""
    from datetime import datetime, timezone

    from app.db.models import ShopListingCache

    extra = await _setup_world(world)
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        s.add(ShopListingCache(tenant_id=world["alice"], connection_id=extra["a3"], listing_id=profile.reference_listing_id,
                               payload={"listing_id": profile.reference_listing_id, "title": "Ref"},
                               fetched_at=datetime.now(timezone.utc)))
        await s.commit()
    async with world["sm"]() as s:
        result = await link_jobs.resolve_all(s, apply=True)
    assert result["main_shops_corrected"] == 1 and result["refresh"] == [world["pa1"]]
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        assert profile.connection_id == extra["a3"] and profile.updated_at is None  # read again from its own shop
    links = await _links(world, world["pa1"])
    assert links[world["a1"]].status == "checking"  # Frost Tees: set up like any other shop
    assert sum(fake_etsy.reads.values()) == 0
    profiles = (await world["a"].get("/api/profiles")).json()
    mine = next(p for p in profiles if p["id"] == str(world["pa1"]))
    assert next(link for link in mine["links"] if link["main"])["shop_name"] == "Frost Caps"


async def test_a_reference_listing_of_another_shop_is_refused_or_used_as_its_own_shop(world) -> None:  # noqa: F811
    from datetime import datetime, timezone

    from app.db.models import ShopListingCache

    async with world["sm"]() as s:
        s.add(ShopListingCache(tenant_id=world["alice"], connection_id=world["a2"], listing_id=777001,
                               payload={"listing_id": 777001, "title": "A Mugs listing"},
                               fetched_at=datetime.now(timezone.utc)))
        await s.commit()
    # Changing a profile's reference to another shop's listing: refused, naming both shops.
    res = await world["a"].put(f"/api/profiles/{world['pa1']}/reference", json={"reference_listing_id": 777001})
    assert res.status_code == 409 and "Frost Mugs" in res.json()["detail"] and "Frost Tees" in res.json()["detail"]
    # A new profile on that listing with the wrong shop: its main shop is the listing's own.
    res = await world["a"].post("/api/profiles", json={"connection_id": str(world["a1"]), "name": "From Mugs",
                                                       "reference_listing_id": 777001, "content_template": "apparel"})
    assert res.status_code == 201 and res.json()["connection_id"] == str(world["a2"])


async def test_the_refresh_moves_a_profile_to_its_reference_listings_shop(world, monkeypatch) -> None:  # noqa: F811
    """The listing Etsy returns says which shop it is in: when that is another of the account's
    shops, the profile moves there (audited) and is read again from it."""
    from app.workers import profiles as profiles_worker

    queued: list[tuple] = []

    async def enqueue(function: str, *args: Any, **_: Any) -> None:
        queued.append((function, args))

    ctx = {"sessionmaker": world["sm"], "enqueue": enqueue}
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        mugs = await s.get(EtsyConnection, world["a2"])
        assert await profiles_worker._wrong_main(ctx, s, profile, int(mugs.shop_id)) == "main-moved"
    async with world["sm"]() as s:
        assert (await s.get(ListingProfile, world["pa1"])).connection_id == world["a2"]
    assert ("refresh_profile", (str(world["pa1"]),)) in queued and ("link_profile", (str(world["pa1"]),)) in queued
    # A listing in no shop of this account: not moved; the card says to choose one of the main shop's.
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        assert await profiles_worker._wrong_main(ctx, s, profile, 99999999) == "foreign-reference"
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        assert profile.connection_id == world["a2"] and "choose a listing of its main shop" in profile.refresh_error


async def test_a_link_left_at_no_match_by_the_old_rules_reads_ready_on_the_card(world, fake_etsy) -> None:  # noqa: F811
    """A link written before every setup copied ("no match in this shop", nothing tried, nothing
    refused) reads as ready: a draft there sets the shop up by itself. A refused link still says why."""
    extra = await _setup_world(world)
    async with world["sm"]() as s:
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=world["pa1"], connection_id=extra["a3"], status="incomplete", notes={}))
        await s.commit()
    card = await _card(world, world["pa1"], "Frost Caps")
    assert (card["ready"], card["status"], card["reason"], card["missing"]) == (True, "ready", None, [])
    profiles = (await world["a"].get("/api/profiles")).text
    assert L.NOT_FOUND not in profiles
    async with world["sm"]() as s:
        link = (await s.execute(select(ProfileShopLink).where(ProfileShopLink.connection_id == extra["a3"]))).scalar_one()
        link.notes = {"shipping_profile": 'Etsy refused the copy of "MemoPaid": "Nope".', "error": "refused"}
        await s.commit()
    card = await _card(world, world["pa1"], "Frost Caps")
    assert card["ready"] is False and "Nope" in (card["reason"] or "")


async def test_a_tie_to_pick_is_what_the_card_says_never_no_match(world, fake_etsy) -> None:  # noqa: F811
    """A link that lacks a copy and has two equal candidates for another setting: the card names
    the tie (the seller picks with change), not "no match"."""
    extra = await _setup_world(world)
    async with world["sm"]() as s:
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=world["pa1"], connection_id=extra["a3"], status="incomplete",
                              notes={"shipping_profile": L.NOT_FOUND, "readiness_state": L.AMBIGUOUS}))
        await s.commit()
    card = await _card(world, world["pa1"], "Frost Caps")
    assert card["ready"] is False and card["reason"] == f"Processing profile: {L.AMBIGUOUS}"
