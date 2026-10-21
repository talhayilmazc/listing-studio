"""The "change" panel names each shop's settings (A2).

Production (2026-10-22): the panel under "Shipping, returns and processing: same as … in
every shop" showed Shipping profile "#289500447511", Processing profile "#1405609947940" and
an empty Return policy. A shop filled from the account map was never resolved for that
profile, so this profile's plan held no list for it and only ids were left.

Now the shop's own lists (kept an hour) name every setting; a return policy reads by its
terms; a shop whose lists are not read yet says so (the panel reads them and shows
"reading…"); a bare id is never shown, and picking from the shop's list works.
"""

from __future__ import annotations

import json

from app.db.models import ProfileShopLink
from app.pipeline import links as L
from app.workers.links import LISTS_KEY
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_profile_links import _setup_world, fake_etsy  # noqa: F401  (fixture)

LISTS = {
    "shipping": [{"shipping_profile_id": 9001, "title": "MemoPaid"}, {"shipping_profile_id": 9002, "title": "free66"}],
    "returns": [{"return_policy_id": 7, "accepts_returns": True, "accepts_exchanges": False, "return_deadline": 30},
                {"return_policy_id": 8, "accepts_returns": False, "accepts_exchanges": False, "return_deadline": None}],
    "readiness": [{"readiness_state_id": 5, "readiness_state": "made_to_order", "min_processing_time": 2,
                   "max_processing_time": 4, "processing_time_unit": "days"}],
    "partners": [],
    "currency": "USD",
}


def test_a_return_policy_reads_by_its_terms() -> None:
    assert L.label_of(L.RETURNS, LISTS["returns"][0]) == "Returns within 30 days, no exchanges"
    assert L.label_of(L.RETURNS, LISTS["returns"][1]) == "No returns, no exchanges"
    assert L.label_of(L.RETURNS, {"accepts_returns": True, "accepts_exchanges": True, "return_deadline": 14}) == \
        "Returns and exchanges within 14 days"


async def test_the_panel_names_settings_filled_from_the_map(world, fake_etsy) -> None:  # noqa: F811
    extra = await _setup_world(world)
    async with world["sm"]() as s:
        # Filled from the account map: ids only, never resolved for this profile (no plan).
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=world["pa1"], connection_id=extra["a3"], status="ready",
                              shipping_profile_id=9001, return_policy_id=7, readiness_state_id=5, production_partner_ids=[3801]))
        await s.commit()

    # The shop's lists are not read yet: said, never shown as ids.
    before = (await world["a"].get(f"/api/profiles/{world['pa1']}/setup")).json()
    [caps] = [shop for shop in before["shops"] if shop["connection_id"] == str(extra["a3"])]
    assert caps["lists_missing"] is True
    assert "#9001" not in json.dumps(before) and "#5" not in json.dumps(before)

    await world["redis"].set(LISTS_KEY.format(connection_id=extra["a3"]), json.dumps(LISTS))
    after = (await world["a"].get(f"/api/profiles/{world['pa1']}/setup")).json()
    [caps] = [shop for shop in after["shops"] if shop["connection_id"] == str(extra["a3"])]
    named = {s["resource"]: s["linked_label"] for s in caps["settings"]}
    assert caps["lists_missing"] is False
    assert named[L.SHIPPING] == "MemoPaid"
    assert named[L.RETURNS] == "Returns within 30 days, no exchanges"
    assert named[L.READINESS] and not named[L.READINESS].startswith("#")
    options = {s["resource"]: [o["label"] for o in s["options"]] for s in caps["settings"]}
    assert options[L.SHIPPING] == ["MemoPaid", "free66"]

    # Picking another of the shop's own settings works from that list.
    picked = await world["a"].put(f"/api/profiles/{world['pa1']}/setup/{extra['a3']}",
                                  json={"resource": L.RETURNS, "ids": [8]})
    assert picked.status_code == 200, picked.text
    again = (await world["a"].get(f"/api/profiles/{world['pa1']}/setup")).json()
    [caps] = [shop for shop in again["shops"] if shop["connection_id"] == str(extra["a3"])]
    assert {s["resource"]: s["linked_label"] for s in caps["settings"]}[L.RETURNS] == "No returns, no exchanges"
