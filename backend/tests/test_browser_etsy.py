"""The fake Etsy the draft browser test runs against (tests/browser_etsy.py), checked here in seconds.

The same walk as the browser's (frontend/e2e/manualDraft.spec.ts), through the API and the real jobs: the profile
form's options are read from the fake by the options job, a profile is made from scratch with them, a listing
takes it and its draft is made. The request the fake kept is the profile's own values, and valid against Etsy's
request schema.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import GeneratedContent, ListingProfile, ListingPublication
from app.workers import links as links_worker
from app.workers import publish as publish_worker
from tests.browser_etsy import LISTS, BrowserEtsy
from tests.test_end_to_end import _Httpx, _run_drafts, _send_drafts, _world
from tests.test_manual_profiles import _check

SCHEMA = json.loads((Path(__file__).parent / "fixtures" / "etsy_draft_schema.json").read_text(encoding="utf-8"))


async def test_a_profile_made_from_the_fakes_options_gives_a_finished_draft(tmp_path, test_settings: Settings,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    etsy = BrowserEtsy()
    w["etsy"] = etsy
    monkeypatch.setattr(publish_worker, "httpx", _Httpx(etsy))
    monkeypatch.setattr(links_worker, "httpx", _Httpx(etsy))

    async def token(session, connection) -> str:  # noqa: ANN001
        return "500.abc"

    monkeypatch.setattr(links_worker, "_token", token)
    a, shop = w["client"], w["shops"][0]

    # The form's options: asked for through the queue, read from Etsy by the job.
    first = (await a.get(f"/api/profile-form/options?shop={shop}")).json()
    assert first["reading"] is True and ("read_profile_options", (str(shop),)) in w["queue"].calls
    assert await links_worker.read_profile_options(w["ctx"], str(shop)) == "read"
    assert await links_worker.read_profile_options(w["ctx"], f"{shop}|9004") == "read"
    options = (await a.get(f"/api/profile-form/options?shop={shop}")).json()
    assert options["reading"] is False and options["currency"] == "USD"
    assert [o["id"] for o in options["shipping"]] == [r["shipping_profile_id"] for r in LISTS["shipping"]]
    assert options["partners"] and options["returns"] and options["readiness"]
    found = (await a.get(f"/api/profile-form/categories?shop={shop}&q=t-shirts")).json()
    assert any(c["id"] == 9004 for c in found["categories"])
    category = (await a.get(f"/api/profile-form/category?shop={shop}&taxonomy_id=9004")).json()
    assert category["reading"] is False and {p["name"] for p in category["properties"]} >= {"Primary color"}

    values = {
        "taxonomy_id": 9004, "who_made": "i_did", "when_made": "made_to_order", "is_supply": False, "price": 24.5, "quantity": 999,
        "variations": {"properties": ["Size"], "rows": [
            {"values": ["S"], "price": 24.5, "quantity": 999, "enabled": True},
            {"values": ["M"], "price": 24.5, "quantity": 999, "enabled": True},
            {"values": ["L"], "price": 27.25, "quantity": 999, "enabled": True}]},
        "shipping_profile_id": options["shipping"][0]["id"], "return_policy_id": options["returns"][0]["id"],
        "readiness_state_id": options["readiness"][0]["id"], "production_partner_ids": [options["partners"][0]["id"]],
        "materials": ["Cotton"], "attributes": {"Primary color": ["Black"]}, "description": "A soft cotton tee, printed to order.",
    }
    made = await a.post("/api/profile-form", json={"connection_id": str(shop), "name": "Pine Ridge tees", "values": values,
                                                   "listing_style": "search", "title_prefix": "", "default_tags": ["soft tee"],
                                                   "personalization": None})
    assert made.status_code == 201, made.text
    profile_id = uuid.UUID(made.json()["id"])

    # A listing takes the profile, and its draft is made.
    async with w["sm"]() as s:
        content = await s.get(GeneratedContent, w["contents"][0])
        content.listing_profile_id = profile_id
        assert (await s.get(ListingProfile, profile_id)).manual is True
        await s.commit()
    await _run_drafts(w, await _send_drafts(w))

    # What the fake was sent is the profile's own values.
    assert len(etsy.sent) == 1
    form = etsy.sent[0]["form"]
    assert form["taxonomy_id"] == "9004" and form["who_made"] == "i_did" and form["when_made"] == "made_to_order"
    assert float(form["price"]) == 24.5 and form["quantity"] == "999"
    assert form["shipping_profile_id"] == str(values["shipping_profile_id"]) and form["return_policy_id"] == str(values["return_policy_id"])
    assert form["readiness_state_id"] == str(values["readiness_state_id"])
    assert form["production_partner_ids"] == str(values["production_partner_ids"][0]) and form["materials"] == "Cotton"
    typed = {**form, "quantity": int(form["quantity"]), "price": float(form["price"]), "taxonomy_id": int(form["taxonomy_id"]),
             "shipping_profile_id": int(form["shipping_profile_id"]), "return_policy_id": int(form["return_policy_id"]),
             "readiness_state_id": int(form["readiness_state_id"]),
             "production_partner_ids": [int(i) for i in form["production_partner_ids"].split(",")],
             "materials": form["materials"].split(","), "tags": form["tags"].split(",")}
    for flag in ("is_supply", "should_auto_renew", "is_taxable", "is_customizable"):
        if flag in typed:
            typed[flag] = str(typed[flag]).lower() == "true"
    _check(typed, SCHEMA["createDraftListing"]["schema"], "createDraftListing")
    listing = etsy.listings[etsy.sent[0]["listing_id"]]
    inventory = json.loads(listing["inventory"])
    _check(inventory, SCHEMA["updateListingInventory"]["schema"], "updateListingInventory")
    assert [(p["property_values"][0]["values"][0], p["offerings"][0]["price"]) for p in inventory["products"]] == \
        [("S", 24.5), ("M", 24.5), ("L", 27.25)]
    assert all(o["readiness_state_id"] == values["readiness_state_id"] for p in inventory["products"] for o in p["offerings"])
    assert [r["values"] for r in etsy.properties[listing["listing_id"]].values()] == [["Black"]]
    async with w["sm"]() as s:
        publication = (await s.execute(select(ListingPublication))).scalars().one()
        assert publication.etsy_listing_id == listing["listing_id"] and publication.state == "draft"
