"""Copying a shipping profile to another shop: every create call names a destination
country or region, whatever the main shop's profile looks like.

Production (OPA 65867984 -> PCS 66980147), on every retry:
    POST /application/shops/66980147/shipping-profiles -> 400:
    {"error":"You must provide either a destination_country_iso code or a destination_region."}

Etsy lists "everywhere else" as no country and ``destination_region`` "none", and in Etsy's
spec (createShopShippingProfile / createShopShippingProfileDestination) ``destination_region``
takes ``eu``, ``non_eu`` or ``none``, where ``none`` means "use destination_country_iso", not a
region. The copy put that destination first, so the create call carried
``destination_region=none`` and no country. Now the create call carries the ship-from country,
another country, or the EU / Europe outside the EU; "everywhere else" is added afterwards
naming neither field (Etsy's spec: "If the request sets neither destination_country_iso nor
destination_region, the default destination is 'everywhere'"); a profile whose only
destination is "everywhere else" cannot be created through Etsy's API and is not offered.

Each shape is checked twice: in the request body that goes over HTTP (the real client,
Etsy mocked), and through the setup job against a fake Etsy that applies Etsy's rule.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from app.etsy.api import EtsyApiClient
from app.etsy.errors import EtsyClientError
from app.pipeline import links as L
from app.workers import links as link_jobs
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_profile_links import SHIP_A, _ctx, _links, _main, _setup_world, fake_etsy  # noqa: F401  (fixture)

PCS = 66980147
ETSY_400 = '{"error":"You must provide either a destination_country_iso code or a destination_region."}'
MONEY = {"primary_cost": {"amount": 500, "divisor": 100}, "secondary_cost": {"amount": 100, "divisor": 100},
         "min_delivery_days": 3, "max_delivery_days": 7}

# Destinations as Etsy's getShopShippingProfiles returns them.
US = {"destination_country_iso": "US", "destination_region": "none", **MONEY}
CA = {"destination_country_iso": "CA", "destination_region": "none", **MONEY}
EU = {"destination_country_iso": "", "destination_region": "eu", **MONEY}
NON_EU = {"destination_country_iso": None, "destination_region": "non_eu", **MONEY}
EVERYWHERE = {"destination_country_iso": "", "destination_region": "none", **MONEY}


def profile(*destinations: dict[str, Any]) -> dict[str, Any]:
    return {**SHIP_A, "title": "Regular", "shipping_profile_destinations": list(destinations),
            "shipping_profile_upgrades": []}


def etsy_400(path: str = f"/application/shops/{PCS}/shipping-profiles") -> EtsyClientError:
    return EtsyClientError(status_code=400, body=ETSY_400, path=path, method="POST")


def etsy_rule(what: str, data: dict[str, Any]) -> None:
    """Etsy's destination rule, from its spec and the production refusal: the create call
    needs a country or a region (``none`` is neither); a destination may name neither
    ("everywhere"), never both; ``none`` is never sent."""
    if what not in ("shipping", "destination"):
        return
    country = data.get("destination_country_iso")
    region = data.get("destination_region")
    if country and region:
        raise EtsyClientError(status_code=400, body='{"error":"destination_country_iso and destination_region are '
                              'mutually exclusive"}', path="/x", method="POST")
    if what == "shipping" and not country and region not in ("eu", "non_eu"):
        raise etsy_400()
    assert region in (None, "eu", "non_eu"), f"region {region!r} sent"


def targets(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k.startswith("destination_")}


# --- the shapes (pure) ---------------------------------------------------------------------------

SHAPES = {
    # name: (destinations as Etsy lists them, create call's target, the added destinations' targets)
    "countries": ((CA, US), {"destination_country_iso": "US"}, [{"destination_country_iso": "CA"}]),
    "only the EU": ((EU,), {"destination_region": "eu"}, []),
    "only regions": ((NON_EU, EU), {"destination_region": "non_eu"}, [{"destination_region": "eu"}]),
    "EU and everywhere else": ((EVERYWHERE, EU), {"destination_region": "eu"}, [{}]),
    "country and everywhere else": ((EVERYWHERE, US), {"destination_country_iso": "US"}, [{}]),
    "another country and everywhere else": ((EVERYWHERE, CA), {"destination_country_iso": "CA"}, [{}]),
}


@pytest.mark.parametrize("name", SHAPES)
def test_the_create_call_always_names_a_country_or_a_region(name: str) -> None:
    destinations, create, added = SHAPES[name]
    first, rest, _ = L.shipping_copy(profile(*destinations))
    assert targets(first) == create
    assert [targets(L.send(d)) for d in rest] == added
    etsy_rule("shipping", first)
    for d in rest:
        etsy_rule("destination", L.send(d))


@pytest.mark.parametrize("name", SHAPES)
async def test_the_request_body_sent_to_etsy(name: str) -> None:
    """What goes over HTTP: the form body of createShopShippingProfile and of each
    createShopShippingProfileDestination, through the real client (Etsy mocked)."""
    destinations, create, added = SHAPES[name]
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"shipping_profile_id": 7, "shipping_profile_destination_id": 8})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = EtsyApiClient(client_id="k", shared_secret="s", http_client=http)
        first, rest, _ = L.shipping_copy(profile(*destinations))
        await client.create_shop_shipping_profile(PCS, data=L.send(first), access_token="1.a")
        for d in rest:
            await client.create_shop_shipping_profile_destination(PCS, 7, data=L.send(d), access_token="1.a")

    bodies = [{k: v[0] for k, v in parse_qs(r.content.decode()).items()} for r in seen]
    assert seen[0].url.path == f"/v3/application/shops/{PCS}/shipping-profiles"
    assert targets(bodies[0]) == create
    assert "destination_region" not in bodies[0] or bodies[0]["destination_region"] in ("eu", "non_eu")
    assert [targets(b) for b in bodies[1:]] == added
    assert all(b.get("destination_region") != "none" for b in bodies)


def test_an_everywhere_else_only_profile_is_copied_with_a_temporary_ship_from_destination() -> None:
    """Etsy's create call needs a country or a region and "everywhere else" is neither: the copy
    is created for the ship-from country (the same price and time), "everywhere else" is added
    naming neither field, then the temporary destination is deleted (deleteShopShippingProfileDestination)."""
    main = _main()
    main.shipping = [{**profile(EVERYWHERE), "shipping_profile_id": 501}]
    outcome = L.plan_resource(L.SHIPPING, 501, main, L.ShopSettings(shipping=[{"shipping_profile_id": 9, "title": "Own"}]))
    assert outcome.creatable and outcome.requests == 3  # the profile, "everywhere else", the delete
    first, rest = outcome.create["profile"], outcome.create["destinations"]
    assert targets(L.send(first)) == {"destination_country_iso": "US"} and first[L.TEMPORARY] == "US"
    assert L.TEMPORARY not in L.send(first)
    assert (L.send(first)["primary_cost"], L.send(first)["min_delivery_days"]) == (5.0, 3)  # "everywhere else"'s terms
    assert [targets(L.send(d)) for d in rest] == [{}]
    etsy_rule("shipping", L.send(first))
    # No ship-from country: nothing to create it with; the seller picks one with change.
    main.shipping = [{**profile(EVERYWHERE), "origin_country_iso": None, "shipping_profile_id": 501}]
    outcome = L.plan_resource(L.SHIPPING, 501, main, L.ShopSettings())
    assert not outcome.creatable and outcome.reason == L.EVERYWHERE_ONLY
    with pytest.raises(ValueError):
        L.shipping_copy({**profile(EVERYWHERE), "origin_country_iso": None})
    main.shipping = [{**profile(), "shipping_profile_id": 501}]
    assert L.plan_resource(L.SHIPPING, 501, main, L.ShopSettings()).reason == L.NO_DESTINATION


def test_the_old_copy_is_what_etsy_refused() -> None:
    """The request the production logs show: "everywhere else" first, region "none", no country."""
    with pytest.raises(EtsyClientError) as refused:
        etsy_rule("shipping", {"title": "Regular", "origin_country_iso": "US", "primary_cost": 5.0,
                               "secondary_cost": 1.0, "destination_region": "none"})
    assert refused.value.body == ETSY_400


# --- the setup job against Etsy's rule -----------------------------------------------------------


async def _copy(world, fake_etsy, *destinations: dict[str, Any], refusal: Exception | None = None) -> tuple[str, Any]:  # noqa: F811
    """Set the profile up in Frost Caps (103): the copy is made there by the setup job itself."""
    extra = await _setup_world(world)
    fake_etsy.lists[101].shipping = [{**profile(*destinations), "shipping_profile_id": 501}]
    fake_etsy.check = etsy_rule
    if refusal is not None:
        fake_etsy.fail[(103, "shipping")] = refusal
    shop = str(extra["a3"])
    await world["a"].post(f"/api/profiles/{world['pa1']}/use-in", json={"scope": "shops", "connection_ids": [shop]})
    await link_jobs._link_profile(_ctx(world), str(world["pa1"]))
    return shop, extra


async def _setting(world, shop: str) -> dict:  # noqa: F811
    setup = (await world["a"].get(f"/api/profiles/{world['pa1']}/setup")).json()
    card = next(s for s in setup["shops"] if s["connection_id"] == shop)
    return next(s for s in card["settings"] if s["resource"] == "shipping_profile")


@pytest.mark.parametrize("name", ["countries", "only regions", "EU and everywhere else", "country and everywhere else"])
async def test_each_shape_is_created_and_linked(world, fake_etsy, name: str) -> None:  # noqa: F811
    destinations, create, added = SHAPES[name]
    shop, extra = await _copy(world, fake_etsy, *destinations)
    sent = [(what, data) for sid, what, data in fake_etsy.created if sid == 103 and what in ("shipping", "destination")]
    assert [what for what, _ in sent] == ["shipping", *["destination"] * len(added)]
    assert targets(sent[0][1]) == create and [targets(d) for _, d in sent[1:]] == added
    setting = await _setting(world, shop)
    assert setting["failure"] is None and setting["how"] == "created"
    assert (await _links(world, world["pa1"]))[extra["a3"]].shipping_profile_id is not None


async def test_only_everywhere_else_is_copied_with_no_seller_action(world, fake_etsy) -> None:  # noqa: F811
    shop, extra = await _copy(world, fake_etsy, EVERYWHERE)
    sent = [(what, data) for sid, what, data in fake_etsy.created if sid == 103 and what in ("shipping", "destination")]
    assert [what for what, _ in sent] == ["shipping", "destination"]
    assert targets(sent[0][1]) == {"destination_country_iso": "US"} and targets(sent[1][1]) == {}
    # The temporary ship-from destination is gone; "everywhere else" stays.
    link = (await _links(world, world["pa1"]))[extra["a3"]]
    [(sid, pid, _)] = fake_etsy.deleted
    assert (sid, pid) == (103, link.shipping_profile_id)
    assert [targets(d) for d in fake_etsy.destinations[pid]] == [{}]
    assert link.status == "ready" and (await _setting(world, shop))["failure"] is None


async def test_the_copy_still_works_if_the_temporary_destination_cannot_be_deleted(world, fake_etsy) -> None:  # noqa: F811
    real = fake_etsy.for_shop

    def for_shop(shop_id: int) -> Any:
        client = real(shop_id)

        async def refuse(*_: Any, **__: Any) -> dict:
            raise EtsyClientError(status_code=400, body='{"error": "nope"}', path="/x", method="DELETE")
        client.delete_shop_shipping_profile_destination = refuse
        return client

    fake_etsy.for_shop = for_shop
    _, extra = await _copy(world, fake_etsy, EVERYWHERE)
    # Kept: the ship-from country is charged exactly what "everywhere else" is. Set up all the same.
    assert (await _links(world, world["pa1"]))[extra["a3"]].status == "ready"


async def test_etsys_exact_refusal_reaches_the_seller_in_plain_words(world, fake_etsy) -> None:  # noqa: F811
    """The production 400, replayed: the seller reads what happened and what to do, not
    "creating it in this shop failed; try again", and is not told to change a field."""
    shop, extra = await _copy(world, fake_etsy, US, EVERYWHERE, refusal=etsy_400())
    failure = (await _setting(world, shop))["failure"]
    assert failure == {
        "action": "manual", "field": None,
        "reason": ('Etsy refused the copy of "Regular" because the copy did not name a destination country or region: '
                   '"You must provide either a destination_country_iso code or a destination_region.". Create it in '
                   "Frost Caps's Shop Manager (Settings → Shipping settings), then press Refresh and pick it."),
    }
    link = (await _links(world, world["pa1"]))[extra["a3"]]
    assert link.status == "incomplete" and link.shipping_profile_id is None
    assert "failed; try again" not in str(link.notes)
