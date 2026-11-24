"""Profiles made and edited by hand (feature/manual-profiles; pipeline/profile_values.py, api/profile_form.py).

* a profile made from scratch gives a draft whose requests are valid against Etsy's own API schema
  (tests/fixtures/etsy_draft_schema.json: the request bodies of Etsy's OpenAPI file);
* a copy to an empty shop with no production partner: made, with the note, and its draft has no partner;
* a field the seller edited on a profile that follows a listing survives a Refresh; "Use the listing's value
  again" follows the listing once more;
* "Stop following the listing" keeps the values and never needs a refresh;
* a profile nobody edited is exactly as it was;
* only values Etsy would take are saved, each refusal one plain sentence about its field.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.models import EtsyConnection, GeneratedContent, Job, JobType, ListingProfile, ProfileShopLink
from app.etsy.publisher import PublishImage, publish_content
from app.pipeline import links as L
from app.pipeline import profile_values as V
from app.pipeline import targets
from app.pipeline.reference import build_inventory_from_reference
from app.workers import retention
from tests.test_multi_shop import _shop, world  # noqa: F401  (fixture)
from tests.test_profile_links import PAYLOAD, _main
from tests.test_publisher import CONFIG, FakeEtsy

SCHEMA = json.loads((Path(__file__).parent / "fixtures" / "etsy_draft_schema.json").read_text())
NODES = {"results": [{"id": 1, "name": "Clothing", "children": [
    {"id": 2, "name": "Gender-Neutral Adult Clothing", "children": [
        {"id": 3, "name": "Tops & Tees", "children": [{"id": 2078, "name": "T-shirts", "children": []}]}]}]},
    {"id": 50, "name": "Home & Living", "children": [{"id": 51, "name": "Mugs", "children": []}]}]}
PROPS = {"results": [
    {"property_id": 200, "name": "Primary color", "is_required": False, "supports_attributes": True, "is_multivalued": False,
     "possible_values": [{"value_id": 1, "name": "Black"}, {"value_id": 2, "name": "Blue"}], "scales": []},
    {"property_id": 300, "name": "Sleeve length", "is_required": True, "supports_attributes": True, "is_multivalued": False,
     "possible_values": [{"value_id": 31, "name": "Short sleeve"}, {"value_id": 32, "name": "Long sleeve"}], "scales": []},
]}
VALUES: dict[str, Any] = {
    "taxonomy_id": 2078, "who_made": "someone_else", "when_made": "made_to_order", "is_supply": False,
    "price": 24.5, "quantity": 40,
    "variations": {"properties": ["Size", "Colour"], "rows": [
        {"values": ["S", "Black"], "price": 24.5, "quantity": 10, "enabled": True},
        {"values": ["M", "Black"], "price": 24.5, "quantity": 12, "enabled": True},
        {"values": ["XL", "Black"], "price": 27.0, "quantity": 5, "enabled": True}]},
    "shipping_profile_id": 501, "return_policy_id": 601, "readiness_state_id": 701, "production_partner_ids": [801],
    "materials": ["Cotton", "Polyester"], "attributes": {"Sleeve length": ["Short sleeve"], "Primary color": ["Black"]},
    "description": "A soft tee, printed to order.\n\nCare: wash cold.",
}


async def _offer(world, shop_id: uuid.UUID, lists: L.ShopSettings) -> None:  # noqa: F811
    """What the form offers for a shop, as the queue's job leaves it (read_profile_options)."""
    data = {"shipping": lists.shipping, "returns": lists.returns, "readiness": lists.readiness, "partners": lists.partners,
            "currency": lists.currency or "USD"}
    await world["redis"].set(f"shop-settings:{shop_id}", json.dumps(data))
    await world["redis"].set("etsy:taxonomy:nodes", json.dumps(NODES))
    await world["redis"].set("etsy:taxonomy:props:2078", json.dumps(PROPS))


def _check(body: dict[str, Any], schema: dict[str, Any], where: str = "") -> None:
    """``body`` against a request schema of Etsy's OpenAPI file: required fields, types and enumerations."""
    kinds = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
    if body is None:
        assert schema.get("nullable"), f"{where}: null where Etsy takes none"
        return
    if schema.get("type") in kinds:
        assert isinstance(body, kinds[schema["type"]]) and not (schema["type"] != "boolean" and isinstance(body, bool)), \
            f"{where}: {body!r} is not {schema['type']}"
    if "enum" in schema:
        assert body in schema["enum"], f"{where}: {body!r} is not one of Etsy's values"
    if "minimum" in schema and isinstance(body, (int, float)):
        assert body >= schema["minimum"], f"{where}: {body} is under Etsy's minimum"
    if schema.get("type") == "object":
        for key in schema.get("required") or []:
            assert key in body, f"{where}: Etsy requires {key}"
        for key, value in body.items():
            assert key in (schema.get("properties") or {}), f"{where}: {key} is not a field of Etsy's request"
            _check(value, schema["properties"][key], f"{where}.{key}")
    if schema.get("type") == "array" and "items" in schema:
        for i, item in enumerate(body):
            _check(item, schema["items"], f"{where}[{i}]")


async def _create(world, shop: uuid.UUID, values: dict[str, Any] | None = None, **more: Any):  # noqa: F811
    return await world["a"].post("/api/profile-form", json={"connection_id": str(shop), "name": "Hand tees",
                                                           "values": values or VALUES, "listing_style": "search",
                                                           "default_tags": ["soft tee", "gift for her"], **more})


# --- from scratch ---------------------------------------------------------------------------------


async def test_the_form_offers_the_shops_own_settings_and_etsys_categories(world) -> None:  # noqa: F811
    # Nothing read yet: the answer says so and the read is queued, once per question.
    first = (await world["a"].get(f"/api/profile-form/options?shop={world['a1']}")).json()
    assert first["reading"] is True and ("read_profile_options", (str(world["a1"]),)) in world["queue"].calls
    await _offer(world, world["a1"], _main())
    options = (await world["a"].get(f"/api/profile-form/options?shop={world['a1']}")).json()
    assert options["reading"] is False and options["currency"] == "USD"
    assert [o["label"] for o in options["shipping"]] == ["Standard US"] and options["shipping"][0]["type"] == "manual"
    assert [o["label"] for o in options["partners"]] == ["Print Co"] and options["partner_note"] is None
    assert {w["value"] for w in options["who_made"]} == set(V.WHO_MADE)
    found = (await world["a"].get(f"/api/profile-form/categories?shop={world['a1']}&q=shirt")).json()["categories"]
    assert [c["id"] for c in found] == [2078] and found[0]["path"][-1] == "T-shirts"
    category = (await world["a"].get(f"/api/profile-form/category?shop={world['a1']}&taxonomy_id=2078")).json()
    assert [p["name"] for p in category["properties"]] == ["Primary color", "Sleeve length"]
    # A category whose attributes are not read yet is read through the queue.
    other = (await world["a"].get(f"/api/profile-form/category?shop={world['a1']}&taxonomy_id=51")).json()
    assert other["reading"] is True and ("read_profile_options", (f"{world['a1']}|51",)) in world["queue"].calls
    # Another account's shop: 404.
    assert (await world["b"].get(f"/api/profile-form/options?shop={world['a1']}")).status_code == 404


async def test_a_profile_from_scratch_gives_a_draft_valid_against_etsys_schema(world) -> None:  # noqa: F811
    await _offer(world, world["a1"], _main())
    res = await _create(world, world["a1"])
    assert res.status_code == 201, res.text
    made = res.json()
    assert made["manual"] is True and made["reference_listing_id"] is None and made["confirmed"] is True
    assert made["is_fresh"] is True and made["refresh_error"] is None and made["default_tags"] == ["soft tee", "gift for her"]
    assert set(made["own_fields"]) == set(V.PAYLOAD_FIELDS)
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, uuid.UUID(made["id"]))
        assert profile.manual and profile.own_values["price"] == 24.5
        payload = dict(profile.cached_payload)
        assert targets.is_fresh(profile)
    assert payload["category_names"][-1] == "T-shirts" and payload["currency"] == "USD"
    assert len(payload["inventory_products"]) == 3 and payload["price_on_property"] == [513, 514]

    # The draft, through the same path as every profile's.
    fake = FakeEtsy(properties=PROPS)
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, world["contents"][0])
        content.listing_profile_id = uuid.UUID(made["id"])
        job = Job(tenant_id=world["alice"], connection_id=world["a1"], type=JobType.create_draft, payload={})
        s.add(job)
        await s.flush()
        await publish_content(s, job_id=job.id, content=content, connection=await s.get(EtsyConnection, world["a1"]), sku="HT-1",
                              thumbnail=PublishImage(b"t", "t.jpg"), fixed_image_ids=[], client=fake, access_token="tok",
                              config=CONFIG, reference=L.shop_reference(payload, None), tenant_limit=2000,
                              profile_id=uuid.UUID(made["id"]))
    listing = fake.last_listing
    _check(listing, SCHEMA["createDraftListing"]["schema"], "createDraftListing")
    assert (listing["taxonomy_id"], listing["price"], listing["quantity"], listing["who_made"], listing["when_made"]) == (
        2078, 24.5, 40, "someone_else", "made_to_order")
    assert (listing["shipping_profile_id"], listing["return_policy_id"], listing["readiness_state_id"]) == (501, 601, 701)
    assert listing["production_partner_ids"] == [801] and listing["materials"] == ["Cotton", "Polyester"]
    _check(fake.inventory, SCHEMA["updateListingInventory"]["schema"], "updateListingInventory")
    rows = [(p["property_values"][0]["values"], p["offerings"][0]["price"], p["offerings"][0]["quantity"], p["sku"])
            for p in fake.inventory["products"]]
    assert rows == [(["S"], 24.5, 10, "HT-1"), (["M"], 24.5, 12, "HT-1"), (["XL"], 27.0, 5, "HT-1")]
    assert all(o["readiness_state_id"] == 701 for p in fake.inventory["products"] for o in p["offerings"])
    # Its attributes, by the ids of the category the draft goes to (the required one too).
    written = {p["property_id"]: p for p in fake.properties_set}
    assert written[300]["value_ids"] == [31] and written[200]["value_ids"] == [1]


async def test_only_values_etsy_would_take_are_saved_each_refusal_in_plain_words(world) -> None:  # noqa: F811
    await _offer(world, world["a1"], _main())
    bad = {**VALUES, "price": 0.05, "quantity": 5000, "who_made": "me", "shipping_profile_id": 999999, "description": " ",
           "materials": ["Cotton!"], "variations": {"properties": ["Size", "Size"], "rows": []},
           "attributes": {"Primary color": ["Chartreuse"]}}
    res = await _create(world, world["a1"], bad, default_tags=["a tag that is far too long for etsy"], name="")
    assert res.status_code == 422
    fields = res.json()["detail"]["fields"]
    assert set(fields) >= {"name", "price", "quantity", "who_made", "shipping_profile_id", "description", "materials", "variations",
                           "attributes", "default_tags"}
    assert fields["price"] == "The price must be between 0.20 and 50,000: Etsy takes nothing outside that."
    assert fields["shipping_profile_id"] == "That shipping profile is not one of this shop's on Etsy; choose one from the list."
    assert fields["variations"] == "The two variations have the same name; name them differently."
    assert "20 at most on Etsy" in fields["default_tags"]
    async with world["sm"]() as s:  # nothing was saved
        assert not list((await s.execute(select(ListingProfile).where(ListingProfile.manual.is_(True)))).scalars())
    # Unit rules, without the API.
    assert V.problems({**VALUES, "variations": None}) == {}
    many = {"properties": ["Size"], "rows": [{"values": [f"S{i}"], "price": 9.0, "quantity": 1} for i in range(71)]}
    assert "at most 70" in V.problems({**VALUES, "variations": many})["variations"]
    calculated = {"shipping": [{"id": 501, "label": "Calc", "type": "calculated"}], "returns": [{"id": 601, "label": "r"}],
                  "readiness": [{"id": 701, "label": "p"}], "partners": [{"id": 801, "label": "Print Co"}]}
    assert "weight and" in V.problems(VALUES, options=calculated)["item_weight"]
    assert V.problems({**VALUES, "item_weight": 5, "item_weight_unit": "oz", "item_length": 10, "item_width": 8, "item_height": 1,
                       "item_dimensions_unit": "in"}, options=calculated) == {}


# --- copy to another shop -------------------------------------------------------------------------


async def test_a_copy_to_an_empty_shop_with_no_partner_is_made_with_the_note_and_drafts_without_one(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        new = await _shop(s, world["alice"], 104, "GLHCUSTOMWEAR", 3)
        (await s.get(ListingProfile, world["pa1"])).cached_payload = {
            **PAYLOAD, "taxonomy_id": 2078, "price": 25.0, "who_made": "someone_else", "when_made": "made_to_order",
            "is_supply": False, "inventory_products": [], "category_names": ["Clothing", "T-shirts"]}
        await s.commit()
        new_id = new.id
    await _offer(world, world["a1"], _main())
    # The new shop: a shipping profile and a processing profile of its own, no return policy like the main shop's,
    # and no production partner at all.
    empty = L.ShopSettings(shipping=[{"shipping_profile_id": 4501, "title": "Standard US", "profile_type": "manual"}],
                           returns=[{"return_policy_id": 4601, "accepts_returns": False, "accepts_exchanges": False}],
                           readiness=[{"readiness_state_id": 4701, "readiness_state": "made_to_order", "min_processing_days": 1,
                                       "max_processing_days": 3}], partners=[], currency="USD")
    await _offer(world, new_id, empty)
    form = (await world["a"].get(f"/api/profile-form/copy?profile={world['pa1']}&shop={new_id}")).json()
    assert form["reading"] is False and form["manual"] is True and form["connection_id"] == str(new_id)
    values = form["values"]
    # Matched by the rules of every setup: the same name, the same terms.
    assert (values["shipping_profile_id"], values["readiness_state_id"]) == (4501, 4701)
    # No counterpart: asked in the form, never guessed.
    assert values["return_policy_id"] is None and [a["field"] for a in form["asked"]] == ["return_policy_id"]
    # Etsy's API can make a return policy: a copy of the main shop's is offered beside the question, as "Use in…" does.
    assert form["asked"][0]["creatable"] is True and form["asked"][0]["reason"].endswith("or copy the main shop's there.")
    # No partner in the shop: not asked (there is nothing to choose), said, and the profile goes without one.
    assert values["production_partner_ids"] == [] and form["partner_note"] == (
        "GLHCUSTOMWEAR has no production partner on Etsy. Drafts are made without one; add it on Etsy "
        "(Shop Manager → Settings → Production partners) before publishing.")
    # While the main shop's settings are being matched or copied there ("Copy the main shop's there"), the form waits;
    # afterwards it uses what the profile's own link to that shop holds.
    async with world["sm"]() as s:
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=world["pa1"], connection_id=new_id, status="checking"))
        await s.commit()
    assert (await world["a"].get(f"/api/profile-form/copy?profile={world['pa1']}&shop={new_id}")).json()["reading"] is True
    async with world["sm"]() as s:
        link = (await s.execute(select(ProfileShopLink).where(ProfileShopLink.profile_id == world["pa1"],
                                                             ProfileShopLink.connection_id == new_id))).scalar_one()
        link.status, link.shipping_profile_id, link.return_policy_id, link.readiness_state_id = "ready", 4501, 4601, 4701
        await s.commit()
    linked = (await world["a"].get(f"/api/profile-form/copy?profile={world['pa1']}&shop={new_id}")).json()
    assert linked["values"]["return_policy_id"] == 4601 and linked["asked"] == []
    async with world["sm"]() as s:
        await s.delete((await s.execute(select(ProfileShopLink).where(ProfileShopLink.profile_id == world["pa1"],
                                                                     ProfileShopLink.connection_id == new_id))).scalar_one())
        await s.commit()
    # Saving before the question is answered is refused in plain words; answered, it is made.
    refused = await world["a"].post("/api/profile-form", json={"connection_id": str(new_id), "name": form["name"], "values": values})
    assert refused.status_code == 422 and refused.json()["detail"]["fields"] == {
        "return_policy_id": "Choose a return policy: Etsy needs one on a physical listing."}
    res = await world["a"].post("/api/profile-form", json={"connection_id": str(new_id), "name": form["name"],
                                                          "values": {**values, "return_policy_id": 4601},
                                                          "copied_from": str(world["pa1"])})
    assert res.status_code == 201, res.text
    copy = res.json()
    assert copy["manual"] is True and copy["connection_id"] == str(new_id) and copy["reference_listing_id"] is None
    assert copy["name"] == f"{(await world['a'].get(f'/api/profiles/{world['pa1']}')).json()['name']} (GLHCUSTOMWEAR)"
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, uuid.UUID(copy["id"]))
        payload = dict(profile.cached_payload)
        source = await s.get(ListingProfile, world["pa1"])
        assert source.connection_id == world["a1"] and not source.manual  # the profile copied from is untouched
    assert (payload["shipping_profile_id"], payload["return_policy_id"], payload["readiness_state_id"],
            payload["production_partner_ids"]) == (4501, 4601, 4701, [])
    fake = FakeEtsy()
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, world["contents"][0])
        job = Job(tenant_id=world["alice"], connection_id=new_id, type=JobType.create_draft, payload={})
        s.add(job)
        await s.flush()
        await publish_content(s, job_id=job.id, content=content, connection=await s.get(EtsyConnection, new_id), sku="GL-1",
                              thumbnail=PublishImage(b"t", "t.jpg"), fixed_image_ids=[], client=fake, access_token="tok",
                              config=CONFIG, reference=L.shop_reference(payload, None), tenant_limit=2000)
    assert "production_partner_ids" not in fake.last_listing and fake.last_listing["shipping_profile_id"] == 4501
    _check(fake.last_listing, SCHEMA["createDraftListing"]["schema"], "createDraftListing")


# --- editing a profile that follows a listing -----------------------------------------------------


def _reference() -> dict[str, Any]:
    products = [{"product_id": 1, "property_values": [{"property_id": 100, "property_name": "Size", "scale_id": 5, "value_ids": [v],
                                                      "values": [name]}],
                 "offerings": [{"offering_id": 9, "price": {"amount": amount, "divisor": 100}, "quantity": 999, "is_enabled": True,
                                "readiness_state_id": 701}]}
                for v, name, amount in ((11, "S", 2000), (12, "M", 2000), (13, "XL", 2200))]
    return {**PAYLOAD, "taxonomy_id": 2078, "price": 20.0, "who_made": "i_did", "when_made": "made_to_order", "is_supply": False,
            "materials": ["Cotton"], "inventory_products": products, "price_on_property": [100], "quantity_on_property": [],
            "sku_on_property": [], "attributes": [], "category_names": ["Clothing", "T-shirts"]}


async def test_an_edited_field_is_your_value_and_survives_a_refresh(world) -> None:  # noqa: F811
    await _offer(world, world["a1"], _main())
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        profile.cached_payload, profile.updated_at = _reference(), datetime.now(timezone.utc)
        await s.commit()
    form = (await world["a"].get(f"/api/profiles/{world['pa1']}/values")).json()
    assert form["manual"] is False and form["own_fields"] == [] and form["values"]["price"] == 20.0
    table = form["values"]["variations"]
    assert table["properties"] == ["Size"] and [r["price"] for r in table["rows"]] == [20.0, 20.0, 22.0]
    # The seller raises the XL price and changes the materials; everything else is sent back as it was.
    table["rows"][2]["price"] = 26.0
    values = {**form["values"], "variations": table, "materials": ["Cotton", "Linen"]}
    res = await world["a"].put(f"/api/profiles/{world['pa1']}/values", json={"name": form["name"], "values": values})
    assert res.status_code == 200, res.text
    assert res.json()["own_fields"] == ["materials", "variations"] and res.json()["manual"] is False
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        payload = profile.cached_payload
        assert set(profile.own_values) == {"materials", "variations"}
    # The listing's own variation ids stay (the options did not change): only the price is the seller's.
    assert [p["property_values"][0]["property_id"] for p in payload["inventory_products"]] == [100, 100, 100]
    built = build_inventory_from_reference(payload["inventory_products"], sku="X", quantity=999, price_on_property=payload["price_on_property"],
                                           own_quantities=bool(payload.get("own_quantities")))
    assert [p["offerings"][0]["price"] for p in built["products"]] == [20.0, 20.0, 26.0]
    assert payload["materials"] == ["Cotton", "Linen"] and payload["who_made"] == "i_did"

    # A Refresh reads the listing again (its price went up to 21 on Etsy): the seller's values stay over it,
    # every other field follows the listing.
    fresh = _reference()
    fresh["price"] = 21.0
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        profile.cached_payload = V.apply(fresh, profile.own_values)  # what the refresh job does (workers/profiles.py)
        await s.commit()
    form = (await world["a"].get(f"/api/profiles/{world['pa1']}/values")).json()
    assert form["own_fields"] == ["materials", "variations"]
    assert [r["price"] for r in form["values"]["variations"]["rows"]] == [20.0, 20.0, 26.0]
    assert form["values"]["materials"] == ["Cotton", "Linen"] and form["listing_values"]["materials"] == ["Cotton"]
    assert [r["price"] for r in form["listing_values"]["variations"]["rows"]] == [20.0, 20.0, 22.0]

    # "Use the listing's value again": the field follows the listing from now on; the other stays the seller's.
    back = await world["a"].delete(f"/api/profiles/{world['pa1']}/values?field=variations")
    assert back.status_code == 200 and back.json()["own_fields"] == ["materials"]
    form = (await world["a"].get(f"/api/profiles/{world['pa1']}/values")).json()
    assert [r["price"] for r in form["values"]["variations"]["rows"]] == [20.0, 20.0, 22.0]
    assert form["values"]["materials"] == ["Cotton", "Linen"]
    # What Etsy would refuse is not saved, and only the changed field is answered for.
    bad = await world["a"].put(f"/api/profiles/{world['pa1']}/values", json={"name": form["name"], "values": {**form["values"], "price": 0}})
    assert bad.status_code == 422 and set(bad.json()["detail"]["fields"]) == {"price"}
    # Another account: 404.
    assert (await world["b"].get(f"/api/profiles/{world['pa1']}/values")).status_code == 404
    assert (await world["b"].put(f"/api/profiles/{world['pa1']}/values", json={"name": "x", "values": {}})).status_code == 404


async def test_stop_following_the_listing_keeps_the_values_and_never_needs_a_refresh(world) -> None:  # noqa: F811
    await _offer(world, world["a1"], _main())
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        profile.cached_payload, profile.updated_at = _reference(), datetime.now(timezone.utc)
        profile.refresh_error, profile.fixed_image_ids = "Couldn't refresh: the listing is gone.", [5]
        await s.commit()
    res = await world["a"].post(f"/api/profiles/{world['pa1']}/stop-following")
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["manual"] is True and out["reference_listing_id"] is None and out["refresh_error"] is None and out["is_fresh"] is True
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        assert profile.own_values["price"] == 20.0 and profile.own_values["materials"] == ["Cotton"]
        assert [r["price"] for r in profile.own_values["variations"]["rows"]] == [20.0, 20.0, 22.0]
        # Two days later: retention leaves the seller's own values (only Etsy's category lists go), and the
        # profile is still usable with no refresh.
        profile.cached_payload = {**profile.cached_payload, "category_properties": [{"name": "x"}],
                                  "category_read_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()}
        profile.updated_at = datetime.now(timezone.utc) - timedelta(days=2)
        await s.commit()
        await retention.purge_expired_rows(s)
        await s.commit()
        profile = await s.get(ListingProfile, world["pa1"])
        assert profile.cached_payload and profile.cached_payload["price"] == 20.0
        assert "category_properties" not in profile.cached_payload and targets.is_fresh(profile)
        from app.etsy.refresh import refresh_due

        assert refresh_due(profile) is None
    shown = (await world["a"].get(f"/api/profiles/{world['pa1']}")).json()
    assert shown["manual"] is True and shown["is_fresh"] is True and shown["refresh_error"] is None
    # Every field is editable and saved as its own.
    form = (await world["a"].get(f"/api/profiles/{world['pa1']}/values")).json()
    saved = await world["a"].put(f"/api/profiles/{world['pa1']}/values", json={"name": "Hand kept", "values": {**form["values"], "price": 23.0, "quantity": 50}})
    assert saved.status_code == 200, saved.text
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        assert (profile.name, profile.cached_payload["price"], profile.cached_payload["quantity"]) == ("Hand kept", 20.0, 50)


async def test_a_profile_nobody_edited_is_exactly_as_it_was(world) -> None:  # noqa: F811
    reference = _reference()
    assert V.apply(reference, None) == reference and V.apply(reference, {}) == reference
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, world["pa1"])
        assert profile.manual is False and profile.own_values is None and profile.default_tags is None
        profile.cached_payload, profile.updated_at = reference, datetime.now(timezone.utc) - timedelta(days=2)
        await s.commit()
        # It still goes stale after 24 hours and its copy of the listing is still cleared: nothing changed for it.
        assert not targets.is_fresh(profile)
        await retention.purge_expired_rows(s)
        await s.commit()
        assert (await s.get(ListingProfile, world["pa1"])).cached_payload is None
    out = (await world["a"].get(f"/api/profiles/{world['pa1']}")).json()
    assert out["manual"] is False and out["own_fields"] == [] and out["default_tags"] == []
    # The draft's quantity and inventory are the app's own, as before, for a payload without the new keys.
    built = build_inventory_from_reference(reference["inventory_products"], sku="X", quantity=999, price_on_property=[100])
    assert [o["quantity"] for p in built["products"] for o in p["offerings"]] == [999, 999, 999]


async def test_use_in_works_for_a_profile_made_by_hand(world) -> None:  # noqa: F811
    await _offer(world, world["a1"], _main())
    made = (await _create(world, world["a1"])).json()
    res = await world["a"].post(f"/api/profiles/{made['id']}/use-in", json={"scope": "shops", "connection_ids": [str(world["a2"])]})
    assert res.status_code == 200 and res.json()["started"] == [str(world["a2"])]
    async with world["sm"]() as s:
        link = (await s.execute(select(ProfileShopLink).where(ProfileShopLink.profile_id == uuid.UUID(made["id"]),
                                                             ProfileShopLink.connection_id == world["a2"]))).scalar_one()
        assert link.status == "checking"
        profile = await s.get(ListingProfile, uuid.UUID(made["id"]))
        # What a setup needs of it is there: the main shop's ids, by the same keys as a reference listing's.
        assert [L.wanted(profile.cached_payload, r) for r in L.RESOURCES] == [501, 601, 701, [801]]


async def test_disconnecting_its_main_shop_moves_a_profile_made_by_hand_with_its_values(world) -> None:  # noqa: F811
    """A profile that follows a listing then waits for a listing of the new main shop; one made by hand has
    none to wait for: its values stay and its shop's own settings become the new main shop's."""
    from app.pipeline import profile_shops

    await _offer(world, world["a1"], _main())
    made = (await _create(world, world["a1"])).json()
    pid = uuid.UUID(made["id"])
    async with world["sm"]() as s:
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=pid, connection_id=world["a2"], shipping_profile_id=2501,
                              return_policy_id=2601, readiness_state_id=2701, production_partner_ids=[2801], status="ready"))
        await s.commit()
        await profile_shops.detach_shop(s, world["a1"])
        await s.commit()
        profile = await s.get(ListingProfile, pid)
        assert profile is not None and profile.manual and profile.connection_id == world["a2"]
        assert profile.refresh_error is None and targets.is_fresh(profile)
        payload = profile.cached_payload
        assert (payload["shipping_profile_id"], payload["return_policy_id"], payload["readiness_state_id"],
                payload["production_partner_ids"]) == (2501, 2601, 2701, [2801])
        assert payload["price"] == 24.5 and len(payload["inventory_products"]) == 3  # its own values are as they were


async def test_default_tags_start_every_listing_written_with_the_profile(world) -> None:  # noqa: F811
    from app.db.models import Asset
    from app.pipeline import manual

    written = [f"tag {n}" for n in range(13)]
    assert V.with_default_tags(None, written) == written and V.with_default_tags([], written) == written  # none: as written
    mixed = V.with_default_tags(["Soft Tee", "gift for her"], ["soft tee", *written])
    assert mixed[:2] == ["Soft Tee", "gift for her"] and len(mixed) == 13 and "soft tee" not in mixed[2:]
    await _offer(world, world["a1"], _main())
    made = (await _create(world, world["a1"])).json()
    async with world["sm"]() as s:
        profile = await s.get(ListingProfile, uuid.UUID(made["id"]))
        content = await s.get(GeneratedContent, world["contents"][0])
        asset = await s.get(Asset, content.asset_id)
        mine = await manual.write(s, tenant_id=world["alice"], batch_id=content.batch_id, asset=asset, profile=profile)
        assert mine.tags == ["soft tee", "gift for her"] and mine.title == ""
        # A profile without default tags: the listing starts empty, as before.
        plain = await s.get(ListingProfile, world["pa1"])
        plain.cached_payload = dict(PAYLOAD)
        assert (await manual.write(s, tenant_id=world["alice"], batch_id=content.batch_id, asset=asset, profile=plain)).tags == []
        await s.rollback()


async def test_a_profile_made_by_hand_is_never_given_a_reference_listing(world) -> None:  # noqa: F811
    await _offer(world, world["a1"], _main())
    made = (await _create(world, world["a1"])).json()
    res = await world["a"].put(f"/api/profiles/{made['id']}/reference", json={"reference_listing_id": 1})
    assert res.status_code == 409 and "made by hand" in res.json()["detail"]
    # "Use the listing's value again" has no listing to go back to either.
    assert (await world["a"].delete(f"/api/profiles/{made['id']}/values?field=price")).status_code == 409


async def test_without_default_tags_the_tags_are_exactly_what_was_written_before(async_sm, make_image, monkeypatch) -> None:
    """Default tags are opt-in. A profile with none (NULL, as every existing profile, or an empty list): the
    writer's tags are stored as they were before the setting existed, and the default-tags code is not even run.
    On the generation test's own fixtures (tests/test_generation.py) and the tag sets of the shared fixture file."""
    from app.pipeline.generation import generate_listing_content
    from tests import test_generation as G
    from tests.support import FakeMessages, fake_response

    # Every tag set of the existing fixtures, odd ones included (repeats, 9 tags, 5 tags): returned as given.
    sets = json.loads((Path(__file__).parent / "fixtures" / "free_tool_cases.json").read_text(encoding="utf-8"))["tag_sets"]
    assert len(sets) >= 3
    for case in sets:
        assert V.with_default_tags(None, case["tags"]) == case["tags"]
        assert V.with_default_tags([], case["tags"]) == case["tags"]

    def never(*_a: Any, **_k: Any) -> list[str]:
        raise AssertionError("default tags were applied for a profile that has none")

    written = [f"tag{i}" for i in range(13)]  # the tags of tests/test_generation.py::test_success_persists_generated_content
    for none in (None, []):
        tenant_id, batch_id, asset_id, profile_id = await G._seed(async_sm)
        messages = FakeMessages([fake_response(G.VISION_DATA, input_tokens=1200, output_tokens=90),
                                 fake_response(G._content(written), input_tokens=300, output_tokens=120)])
        analyzer, generator = G._pipeline(messages)
        async with async_sm() as session:
            profile = await session.get(ListingProfile, profile_id)
            profile.default_tags = none
            with monkeypatch.context() as patched:
                patched.setattr(V, "with_default_tags", never)
                outcome = await generate_listing_content(
                    session, tenant_id=tenant_id, batch_id=batch_id, asset_id=asset_id, image_data=make_image(400, 300),
                    media_type="image/png", sku="SKU1", analyzer=analyzer, generator=generator, profile=profile)
            assert outcome.status == "generated"
            row = await session.get(GeneratedContent, outcome.generated_content_id)
            assert row.tags == written  # the writer's own, in its order

    # With default tags (the seller opted in), they come first and the writer's fill the rest, 13 in all.
    tenant_id, batch_id, asset_id, profile_id = await G._seed(async_sm)
    messages = FakeMessages([fake_response(G.VISION_DATA), fake_response(G._content(written))])
    analyzer, generator = G._pipeline(messages)
    async with async_sm() as session:
        profile = await session.get(ListingProfile, profile_id)
        profile.default_tags = ["soft tee", "tag3"]
        outcome = await generate_listing_content(
            session, tenant_id=tenant_id, batch_id=batch_id, asset_id=asset_id, image_data=make_image(400, 300),
            media_type="image/png", sku="SKU1", analyzer=analyzer, generator=generator, profile=profile)
        row = await session.get(GeneratedContent, outcome.generated_content_id)
        assert row.tags == ["soft tee", "tag3", *[t for t in written if t != "tag3"]][:13]


async def test_a_hand_written_listing_starts_empty_unless_the_profile_has_default_tags(world) -> None:  # noqa: F811
    from app.db.models import Asset
    from app.pipeline import manual

    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, world["contents"][0])
        asset = await s.get(Asset, content.asset_id)
        plain = await s.get(ListingProfile, world["pa1"])
        plain.cached_payload = dict(PAYLOAD)
        for none in (None, []):
            plain.default_tags = none
            mine = await manual.write(s, tenant_id=world["alice"], batch_id=content.batch_id, asset=asset, profile=plain)
            assert mine.tags == [] and mine.title == ""
        await s.rollback()


def test_a_description_typed_by_hand_is_kept_whole_in_every_listing() -> None:
    """The draft path replaces a description's first block (a reference listing's own title block). A description
    typed into the form has no such block: its first paragraph was once dropped from every listing (found by the
    browser test against the fake Etsy)."""
    from app.pipeline import manual
    from app.pipeline.reference import replace_title_block, with_opening

    typed = "A soft tee, printed to order.\n\nCare: wash cold, hang dry."
    payload = V.to_payload({**VALUES, "description": typed}, currency="USD")
    assert V.from_payload(payload)["description"] == typed  # the form shows what was typed
    assert with_opening(payload["description"], "A pine ridge shirt for hikers.") == "A pine ridge shirt for hikers.\n\n" + typed
    assert replace_title_block(payload["description"], "Pine Ridge Shirt") == "Pine Ridge Shirt\n\n" + typed
    short = ListingProfile(name="p", listing_style="search", content_template="apparel", cached_payload=payload)
    long = ListingProfile(name="p", listing_style="classic", content_template="apparel", cached_payload=payload)
    assert manual.description_for(short, "") == typed and manual.description_for(short, "Pine Ridge Shirt") == typed
    assert manual.description_for(long, "Pine Ridge Shirt") == "Pine Ridge Shirt\n\n" + typed

    # A reference listing's description: the form shows what every listing keeps (its body), nothing changes
    # until the seller edits it, and "Stop following the listing" gives the same listings as before.
    reference = {**PAYLOAD, "description": "Old Reference Title\nSecond line\n\nSizes: S-3XL\n\nShips in 3 days."}
    assert V.from_payload(reference)["description"] == "Sizes: S-3XL\n\nShips in 3 days."
    assert V.apply(reference, None)["description"] == reference["description"]
    stopped = V.to_payload(V.from_payload(reference), currency="USD")
    assert with_opening(stopped["description"], "Open.") == with_opening(reference["description"], "Open.")
    assert replace_title_block(stopped["description"], "New Title") == replace_title_block(reference["description"], "New Title")
    mine = V.apply(reference, {"description": "Mine.\n\nMore of mine."})
    assert with_opening(mine["description"], "Open.") == "Open.\n\nMine.\n\nMore of mine."
    assert V.from_payload(mine)["description"] == "Mine.\n\nMore of mine."
    # A reference description with no blank line is all body, as the writer treats it.
    assert V.from_payload({**PAYLOAD, "description": "One line only"})["description"] == "One line only"
