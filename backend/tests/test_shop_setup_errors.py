"""Setting a profile up in another shop when Etsy refuses the copy (a seller's second
shop: the shipping profile had no match, the preselected copy failed with only
"creating it in this shop failed; try again", and every retry failed the same way).

* the copy is built the way Etsy's schema takes it: a delivery time as a carrier with
  its mail class *or* the fewest and most days (never both, never a carrier alone), a
  destination as a country *or* a region, a country's destination in the create call;
* a refusal says Etsy's own reason in plain words with the one action that fixes it:
  reconnect (permission), the field to change, finish in Shop Manager (made, a part
  refused), Refresh (no answer), or make it by hand, then Refresh and pick it;
* a refused copy is not preselected again; one shop's refusal does not stop another;
* Refresh reads the shop's lists again: a profile the seller made by hand is matched by
  its name, or picked from the list.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.etsy.errors import EtsyClientError, EtsyRateLimited, EtsyServerError
from app.pipeline import links as L
from app.workers import links as link_jobs
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_profile_links import SHIP_A, _ctx, _links, _main, _setup_world, fake_etsy  # noqa: F401  (fixture)


def refused(status: int, message: str, path: str = "/application/shops/103/shipping-profiles") -> EtsyClientError:
    return EtsyClientError(status_code=status, body=f'{{"error": "{message}"}}', path=path, method="POST")


# --- the copy's shape (pure) -------------------------------------------------------------------


def _dest(**over: Any) -> dict[str, Any]:
    return {"destination_country_iso": "US", "primary_cost": {"amount": 500, "divisor": 100},
            "secondary_cost": {"amount": 100, "divisor": 100}, **over}


def test_a_delivery_time_is_sent_in_exactly_one_of_etsys_two_forms() -> None:
    # Etsy answers a carrier-based destination with the days it worked out: only the carrier goes back.
    both = L.delivery({"shipping_carrier_id": 1, "mail_class": "usps_ground", "min_delivery_days": 2, "max_delivery_days": 5})
    assert both == {"shipping_carrier_id": 1, "mail_class": "usps_ground"}
    # A carrier without its mail class ("Required with mail_class"): the days go instead.
    assert L.delivery({"shipping_carrier_id": 1, "mail_class": None, "min_delivery_days": 2, "max_delivery_days": 5}) == {
        "min_delivery_days": 2, "max_delivery_days": 5}
    # "Other" carrier (0) with days: the days.
    assert L.delivery({"shipping_carrier_id": 0, "min_delivery_days": 3, "max_delivery_days": 7}) == {
        "min_delivery_days": 3, "max_delivery_days": 7}
    # Neither form complete: nothing to send.
    assert L.delivery({"shipping_carrier_id": 1, "min_delivery_days": 3}) is None


def test_the_copy_never_sends_both_forms_and_never_both_destination_targets() -> None:
    source = {**SHIP_A, "shipping_profile_destinations": [
        # Etsy's order: "everywhere else" first. It must not go in the create call on its own.
        {"destination_country_iso": "", "destination_region": "none", "primary_cost": {"amount": 2000, "divisor": 100},
         "secondary_cost": {"amount": 500, "divisor": 100}, "min_delivery_days": 10, "max_delivery_days": 30},
        _dest(destination_region="none", shipping_carrier_id=1, mail_class="usps_ground",
              min_delivery_days=2, max_delivery_days=5),
        _dest(destination_country_iso="", destination_region="eu", min_delivery_days=5, max_delivery_days=15),
    ], "shipping_profile_upgrades": [
        {"upgrade_name": "Express", "type": "1", "price": {"amount": 900, "divisor": 100},
         "secondary_price": {"amount": 0, "divisor": 100}, "shipping_carrier_id": 1, "mail_class": "usps_priority",
         "min_delivery_days": 1, "max_delivery_days": 3}]}
    profile, destinations, upgrades = L.shipping_copy(source)
    assert profile["destination_country_iso"] == "US" and "destination_region" not in profile
    assert profile["shipping_carrier_id"] == 1 and profile["mail_class"] == "usps_ground"
    assert "min_delivery_days" not in profile and "max_delivery_days" not in profile
    sent = [L.send(d) for d in destinations]
    # The EU names its region; "everywhere else" names neither (never region "none"), and goes last.
    assert [{k: v for k, v in d.items() if k.startswith("destination_")} for d in sent] == [
        {"destination_region": "eu"}, {}]
    assert all(not ("mail_class" in d and "min_delivery_days" in d) for d in sent)
    assert [d["label"] for d in destinations] == ["the EU", "everywhere else"]
    assert L.send(upgrades[0]) == {"type": 1, "upgrade_name": "Express", "price": 9.0, "secondary_price": 0.0,
                                   "shipping_carrier_id": 1, "mail_class": "usps_priority"}


def test_no_delivery_time_is_invented_and_a_refusal_for_it_says_so() -> None:
    main = _main()
    main.shipping = [{**SHIP_A, "shipping_profile_destinations": [_dest(), _dest(destination_country_iso="CA")],
                      "shipping_profile_upgrades": []}]
    outcome = L.plan_resource(L.SHIPPING, 501, main, L.ShopSettings())
    assert outcome.creatable
    sent = [outcome.create["profile"], *map(L.send, outcome.create["destinations"])]
    assert all(k not in row for row in sent for k in ("shipping_carrier_id", "mail_class", "min_delivery_days",
                                                      "max_delivery_days"))
    failure = L.create_failure(refused(400, "min_delivery_days is required when mail_class is null"), L.SHIPPING,
                               shop="PCS", main="OPA", source_label="Regular")
    assert failure["field"] == "min_delivery_days" and "because of the delivery time" in failure["reason"]


def test_a_missing_permission_names_reconnect() -> None:
    assert "reconnect the shop on Shops & groups" in L.NO_SCOPE


# --- what the seller reads (pure) --------------------------------------------------------------

KW = {"shop": "PCS", "main": "OPA", "source_label": "Regular"}


def test_each_refusal_says_etsys_reason_and_the_action_that_fixes_it() -> None:
    scope = L.create_failure(refused(403, "The provided access token lacks the shops_w scope"), L.SHIPPING, **KW)
    assert scope["action"] == "reconnect"
    assert '"The provided access token lacks the shops_w scope"' in scope["reason"]
    assert "Reconnect PCS on Shops & groups" in scope["reason"]

    postal = L.create_failure(refused(400, "origin_postal_code is not a valid postal code for US"), L.SHIPPING, **KW)
    assert (postal["action"], postal["field"]) == ("fix", "origin_postal_code")
    assert "the ship-from postal code" in postal["reason"] and "OPA's Shop Manager" in postal["reason"]
    assert "or create it in PCS's Shop Manager (Settings → Shipping settings), then press Refresh and pick it" in postal["reason"]

    mail = L.create_failure(refused(400, "mail_class is required with shipping_carrier_id"), L.SHIPPING, **KW)
    assert mail["field"] == "mail_class" and "the mail class" in mail["reason"]

    other = L.create_failure(refused(400, "Something Etsy does not explain"), L.SHIPPING, **KW)
    assert other["action"] == "manual"
    assert other["reason"] == ('Etsy refused the copy of "Regular": "Something Etsy does not explain". Create it in '
                               "PCS's Shop Manager (Settings → Shipping settings), then press Refresh and pick it.")

    silent = L.create_failure(EtsyClientError(status_code=400, body="", path="/x", method="POST"), L.SHIPPING, **KW)
    assert silent["action"] == "manual" and "(it answered 400) without saying why" in silent["reason"]

    down = L.create_failure(EtsyServerError(503, body="", path="/x", method="POST"), L.SHIPPING, **KW)
    assert down["action"] == "refresh" and "may have made the copy anyway" in down["reason"]
    lost = L.create_failure(httpx.ReadTimeout("no answer"), L.SHIPPING, **KW)
    assert lost["action"] == "refresh"
    fast = L.create_failure(EtsyRateLimited(30), L.SHIPPING, **KW)
    assert fast["action"] == "refresh" and "too fast" in fast["reason"]

    part = L.create_failure(refused(400, "Invalid destination_region"), L.SHIPPING, made="the destination everywhere else",
                            **KW)
    assert part["action"] == "finish"
    assert part["reason"].startswith('"Regular" was created in PCS, but Etsy refused the destination everywhere else: '
                                     '"Invalid destination_region". Finish it in PCS\'s Shop Manager')


def test_no_token_or_address_reaches_the_seller_through_etsys_reason() -> None:
    leak = L.create_failure(refused(400, 'bad request for seller@example.com access_token: \\"abc.def\\"'), L.SHIPPING, **KW)
    assert "seller@example.com" not in leak["reason"] and "abc.def" not in leak["reason"]


# --- the job and the dialog, with Etsy faked -----------------------------------------------------
# A setting the shop has no counterpart for is copied automatically by the setup job itself
# (``link_profile``); the dialog appears only when Etsy refuses, with the reason.


async def _set_up(world, fake_etsy) -> tuple[str, Any]:  # noqa: F811
    """Frost Caps (103) has no shipping profile: setting the profile up there copies one."""
    extra = await _setup_world(world)
    shop = str(extra["a3"])
    await world["a"].post(f"/api/profiles/{world['pa1']}/use-in", json={"scope": "shops", "connection_ids": [shop]})
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    return shop, extra


async def _setting(world, shop: str, resource: str) -> dict:  # noqa: F811
    setup = (await world["a"].get(f"/api/profiles/{world['pa1']}/setup")).json()
    card = next(s for s in setup["shops"] if s["connection_id"] == shop)
    return next(s for s in card["settings"] if s["resource"] == resource)


def _shipping_creates(fake_etsy, sid: int = 103) -> list[str]:  # noqa: F811
    return [what for s, what, _ in fake_etsy.created if s == sid and what in ("shipping", "destination", "upgrade")]


async def test_a_permission_refusal_says_reconnect_and_is_not_preselected_again(world, fake_etsy) -> None:  # noqa: F811
    fake_etsy.fail[(103, "shipping")] = refused(403, "insufficient_scope: shops_w")
    shop, extra = await _set_up(world, fake_etsy)
    link = (await _links(world, world["pa1"]))[extra["a3"]]
    # Waiting for the seller (a choice, a reconnect, a Refresh), not "error": the dialog shows every setting.
    assert link.status == "incomplete" and link.shipping_profile_id is None and link.pending_create is None
    setting = await _setting(world, shop, "shipping_profile")
    assert setting["failure"]["action"] == "reconnect"
    assert '"insufficient_scope: shops_w"' in setting["failure"]["reason"] and "Reconnect Frost Caps" in setting["failure"]["reason"]
    assert setting["default"] == "choose"  # the same request would be refused again
    assert setting["creatable"] is True  # still possible once the shop is reconnected
    profile = (await world["a"].get("/api/profiles")).json()[0]
    caps = next(link for link in profile["links"] if link["shop_name"] == "Frost Caps")
    assert "Reconnect Frost Caps" in caps["reason"]
    assert _shipping_creates(fake_etsy) == []


async def test_a_field_refusal_names_the_field_and_the_main_shop(world, fake_etsy) -> None:  # noqa: F811
    fake_etsy.fail[(103, "shipping")] = refused(400, "origin_postal_code is required for origin_country_iso US")
    shop, _ = await _set_up(world, fake_etsy)
    failure = (await _setting(world, shop, "shipping_profile"))["failure"]
    assert (failure["action"], failure["field"]) == ("fix", "origin_postal_code")
    assert "the ship-from postal code" in failure["reason"] and "Frost Tees's Shop Manager" in failure["reason"]


async def test_an_unexplained_refusal_says_make_it_by_hand_then_refresh(world, fake_etsy) -> None:  # noqa: F811
    fake_etsy.fail[(103, "shipping")] = refused(400, "Unable to create shipping profile")
    shop, _ = await _set_up(world, fake_etsy)
    failure = (await _setting(world, shop, "shipping_profile"))["failure"]
    assert failure["action"] == "manual"
    assert "Create it in Frost Caps's Shop Manager (Settings → Shipping settings), then press Refresh and pick it" in failure["reason"]


async def test_no_answer_says_refresh(world, fake_etsy) -> None:  # noqa: F811
    fake_etsy.fail[(103, "shipping")] = EtsyServerError(502, body="", path="/x", method="POST")
    shop, _ = await _set_up(world, fake_etsy)
    assert (await _setting(world, shop, "shipping_profile"))["failure"]["action"] == "refresh"


async def test_a_refused_destination_leaves_the_made_profile_and_refresh_links_it(world, fake_etsy) -> None:  # noqa: F811
    fake_etsy.fail[(103, "destination")] = refused(400, "Invalid destination_region")
    shop, extra = await _set_up(world, fake_etsy)
    failure = (await _setting(world, shop, "shipping_profile"))["failure"]
    assert failure["action"] == "finish"
    assert failure["reason"].startswith('"Standard US" was created in Frost Caps, but Etsy refused the destination the EU')
    # Nothing is deleted in the shop; nothing else of that profile is sent after the refused part.
    assert _shipping_creates(fake_etsy) == ["shipping"]
    assert (await _links(world, world["pa1"]))[extra["a3"]].shipping_profile_id is None
    # Set up again before the seller finishes it: the half-made copy is neither used nor copied again.
    await world["a"].post(f"/api/profiles/{world['pa1']}/use-in", json={"scope": "shops", "connection_ids": [shop]})
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    assert _shipping_creates(fake_etsy) == ["shipping"]
    assert (await _setting(world, shop, "shipping_profile"))["failure"]["action"] == "finish"
    # The seller finishes it in Shop Manager and presses Refresh: matched by its name.
    del fake_etsy.fail[(103, "destination")]
    res = await world["a"].post(f"/api/profiles/{world['pa1']}/setup/{shop}/check")
    assert res.status_code == 200
    assert await link_jobs._link_profile(_ctx(world), str(world["pa1"])) == "linked"
    setting = await _setting(world, shop, "shipping_profile")
    assert setting["failure"] is None and setting["how"] == "same name" and setting["linked_ids"]
    assert _shipping_creates(fake_etsy) == ["shipping"]  # never a second copy


async def test_refresh_offers_a_profile_the_seller_made_by_hand(world, fake_etsy) -> None:  # noqa: F811
    fake_etsy.fail[(103, "shipping")] = refused(400, "Unable to create shipping profile")
    shop, extra = await _set_up(world, fake_etsy)
    # Made by hand in Frost Caps's Shop Manager, under another name.
    fake_etsy.lists[103].shipping.append({"shipping_profile_id": 3999, "title": "Caps shipping"})
    assert (await world["a"].post(f"/api/profiles/{world['pa1']}/setup/{shop}/check")).status_code == 200
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    setting = await _setting(world, shop, "shipping_profile")
    assert {"id": 3999, "label": "Caps shipping"} in setting["options"]  # read fresh: offered to pick
    res = await world["a"].post(f"/api/profiles/{world['pa1']}/setup/apply",
                                json={"choices": [{"connection_id": shop, "resource": "shipping_profile", "use": "pick",
                                                   "ids": [3999]}]})
    assert res.status_code == 200
    assert (await _links(world, world["pa1"]))[extra["a3"]].shipping_profile_id == 3999
    # The choice is the account's: set up again, no copy is tried any more.
    attempts = len(fake_etsy.created)
    await world["a"].post(f"/api/profiles/{world['pa1']}/setup/{shop}/check")
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    assert (await _setting(world, shop, "shipping_profile"))["linked_ids"] == [3999]
    assert len(fake_etsy.created) == attempts


async def test_confirming_the_copy_again_after_the_fix_creates_it(world, fake_etsy) -> None:  # noqa: F811
    fake_etsy.fail[(103, "shipping")] = refused(403, "insufficient_scope")
    shop, extra = await _set_up(world, fake_etsy)
    del fake_etsy.fail[(103, "shipping")]  # reconnected
    res = await world["a"].post(f"/api/profiles/{world['pa1']}/setup/apply",
                                json={"choices": [{"connection_id": shop, "resource": "shipping_profile", "use": "copy"}]})
    assert res.status_code == 200
    await link_jobs._create_link_resources(_ctx(world), str(world["pa1"]))
    assert (await _links(world, world["pa1"]))[extra["a3"]].shipping_profile_id is not None
    assert (await _setting(world, shop, "shipping_profile"))["failure"] is None
    assert _shipping_creates(fake_etsy) == ["shipping", "destination", "upgrade"]


async def test_a_shop_whose_lists_cannot_be_read_says_why_and_the_others_carry_on(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)

    real = fake_etsy.for_shop

    def for_shop(shop_id: int) -> Any:
        client = real(shop_id)
        if shop_id == 103:
            async def no(*_: Any, **__: Any) -> dict:
                raise EtsyClientError(status_code=401, body='{"error": "invalid_token"}', path="/x", method="GET")
            client.get_shop_shipping_profiles = no
        return client

    fake_etsy.for_shop = for_shop
    await world["a"].post(f"/api/profiles/{world['pa1']}/use-in", json={"scope": "group", "group_id": extra["group"]})
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    links = await _links(world, world["pa1"])
    assert links[world["a2"]].status == "ready"  # Frost Mugs linked all the same
    caps = links[extra["a3"]]
    assert caps.status == "error" and "reconnect Frost Caps on Shops & groups" in caps.notes["error"]
