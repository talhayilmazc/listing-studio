"""``python -m app.cli etsy-raw-listing``: one listing's raw JSON from Etsy, read only.

* found by shop and listing id, or by a profile's name (its main shop and reference listing);
* exactly three GET requests, never getListing's "User" association;
* every field name printed, and any that looks like a Creativity Standards answer listed apart
  from who_made / when_made, which the drafts already copy;
* an unknown or ambiguous profile name makes no request.
"""

from __future__ import annotations

from sqlalchemy import update

from app.db.models import EtsyConnection, ListingProfile
from app.ops.raw_listing import raw_listing
from tests.test_multi_shop import world  # noqa: F401  (fixture)


class FakeClient:
    def __init__(self, listing: dict, properties: dict) -> None:
        self.calls: list[tuple] = []
        self._listing, self._properties = listing, properties

    async def get_listing(self, listing_id, *, includes=None, **kw):  # noqa: ANN001, ANN201
        self.calls.append(("getListing", listing_id, tuple(includes or ())))
        return self._listing

    async def get_listing_properties(self, shop_id, listing_id, **kw):  # noqa: ANN001, ANN201
        self.calls.append(("getListingProperties", shop_id, listing_id))
        return self._properties

    async def get_listing_inventory(self, listing_id, **kw):  # noqa: ANN001, ANN201
        self.calls.append(("getListingInventory", listing_id))
        return {"products": [{"sku": "BR1", "offerings": []}]}


class Tokens:
    async def get_valid_access_token(self, session, connection):  # noqa: ANN001, ANN201
        return "token"


LISTING = {"listing_id": 1010, "title": "Retro Tee", "who_made": "i_did", "when_made": "made_to_order", "is_supply": False}


async def test_by_profile_name_three_reads_every_field_and_nothing_new(world) -> None:  # noqa: F811
    async with world["sm"]() as s:
        await s.execute(update(ListingProfile).where(ListingProfile.id == world["pa1"]).values(name="Regular - PCS"))
        await s.commit()
    client = FakeClient(LISTING, {"count": 1, "results": [{"property_id": 513, "property_name": "Sleeve length", "values": ["Short"]}]})
    out = await raw_listing(world["sm"], None, None, profile="regular - pcs",
                            client_factory=lambda shop, http: client, token_service=Tokens())
    assert [c[0] for c in client.calls] == ["getListing", "getListingProperties", "getListingInventory"]
    assert client.calls[0][1] == 1010 and "User" not in client.calls[0][2]  # the reference listing, no buyer/user data
    async with world["sm"]() as s:
        etsy_shop = (await s.get(EtsyConnection, world["a1"])).shop_id
    assert client.calls[1][1:] == (etsy_shop, 1010)  # the profile's main shop
    assert "Etsy requests made: 3" in out and "found by profile 'Regular - PCS'" in out
    for field in ("listing_id", "who_made", "when_made", "results[].property_name", "products[].sku"):
        assert f"  {field}" in out, field
    assert '"who_made": "i_did"' in out  # the raw JSON too
    assert out.rstrip().endswith("none")  # no field for "How does your shop produce" / "What tools"
    assert '"when_made": "made_to_order"' in out.split("already copied from the reference:")[1].splitlines()[0]


async def test_a_new_field_or_property_for_the_questions_is_listed(world) -> None:  # noqa: F811
    listing = {**LISTING, "production_method": "made_from_scratch"}
    props = {"results": [{"property_id": 9, "property_name": "Tools used", "values": ["Handheld tools"]}]}
    client = FakeClient(listing, props)
    async with world["sm"]() as s:
        etsy_shop = (await s.get(EtsyConnection, world["a1"])).shop_id
    out = await raw_listing(world["sm"], str(etsy_shop), "1010", client_factory=lambda shop, http: client, token_service=Tokens())
    tail = out.split("Other fields that might answer")[1]
    assert "production_method" in tail and "'Tools used'" in tail and "who_made" not in tail


async def test_an_unknown_or_ambiguous_profile_makes_no_request(world) -> None:  # noqa: F811
    client = FakeClient(LISTING, {})
    unknown = await raw_listing(world["sm"], None, None, profile="Nope", client_factory=lambda s, h: client, token_service=Tokens())
    both = await raw_listing(world["sm"], None, None, profile="Standard Tee", client_factory=lambda s, h: client, token_service=Tokens())
    assert "no profile named 'Nope'" in unknown and "no request made" in unknown
    close = await raw_listing(world["sm"], None, None, profile="Standard - PCS", client_factory=lambda s, h: client, token_service=Tokens())
    assert "close names:" in close and "'Standard Tee'  main shop Frost Tees" in close and "no request made" in close
    assert "profiles are named 'Standard Tee'" in both and "no request made" in both
    assert client.calls == []
