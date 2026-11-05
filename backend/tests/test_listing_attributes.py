"""Every attribute Etsy offers for a listing's category, set by the seller (pipeline/listing_attributes.py,
api/listing_attributes.py).

Etsy's values only, several where Etsy takes several, units; the seller's value beats the writer's and survives a
rewrite; a shop's own values and Keep as it is; the bulk preview and its skips; a category change; what the draft sends
(hand-written and AI listings alike); a listing already live keeps its attributes; another account gets 404.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import GeneratedContent, ListingProfile, ListingPublication
from app.pipeline import listing_attributes as LA
from tests.test_end_to_end import _run_drafts, _send_drafts
from tests.test_shop_photos import TAXONOMY_PROPS, both_shops, drafts_by_shop, other_client, world

HOLIDAY, OCCASION, WIDTH, SLEEVE = 46803063641, 46803063659, 300, 301


def _url(w: dict, content=None) -> str:  # noqa: ANN001
    return f"/api/content/{content or w['contents'][0]}/attributes"


async def _put(w: dict, values: dict, shop=None, content=None):  # noqa: ANN001, ANN202
    body = {"values": values, **({"connection_id": str(shop)} if shop else {})}
    return await w["client"].put(_url(w, content), json=body)


def _by_name(out: dict) -> dict:
    return {p["name"]: p for p in out["properties"]}


def test_compact_keeps_etsys_lists_units_and_limits_and_leaves_out_free_text() -> None:
    props = LA.properties_of({"category_properties": LA.compact(TAXONOMY_PROPS)})
    names = [p.name for p in props]
    assert names == ["Holiday", "Occasion", "Width", "Sleeve length"]  # "Care" is free text: Shop Manager
    holiday, occasion, width = props[0], props[1], props[2]
    assert (holiday.kind, holiday.max_values, holiday.values) == (LA.CHOICE, 2, ["Christmas", "Easter", "Halloween"])
    assert occasion.max_values == 1
    assert (width.kind, width.scales) == (LA.NUMBER, ["Inches", "Centimeters"])


def test_an_older_payload_still_offers_its_lists() -> None:
    props = LA.properties_of({"category_attributes": {"Holiday": ["Christmas"]}, "category_attribute_limits": {"Holiday": 2}})
    assert [(p.name, p.max_values, p.values) for p in props] == [("Holiday", 2, ["Christmas"])]


@pytest.mark.parametrize("name,values,unit,ok", [
    ("Holiday", ["christmas", "Easter"], None, ["Christmas", "Easter"]),
    ("Holiday", ["Christmas", "Easter", "Halloween"], None, "up to 2 values"),
    ("Holiday", ["Diwali"], None, "not one of Etsy's Holiday values"),
    ("Occasion", ["Birthday", "Wedding"], None, "takes one value"),
    ("Width", ["12.5"], "inches", ["12.5"]),
    ("Width", ["12"], None, "Choose a unit"),
    ("Width", ["wide"], "Inches", "takes a number"),
    ("Width", ["12"], "Miles", "not one of Etsy's units"),
    ("Holiday", [], None, []),
])
def test_only_etsys_values_and_units(name: str, values: list, unit: str | None, ok) -> None:  # noqa: ANN001
    prop = LA.find(LA.properties_of({"category_properties": LA.compact(TAXONOMY_PROPS)}), name)
    if isinstance(ok, list):
        assert LA.check(prop, values, unit, name)[0] == ok
    else:
        with pytest.raises(ValueError, match=ok):
            LA.check(prop, values, unit, name)


async def test_the_card_shows_every_attribute_and_saves_only_etsys_values(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch)
    out = (await w["client"].get(_url(w))).json()
    props = _by_name(out)
    assert set(props) == {"Holiday", "Occasion", "Width", "Sleeve length"} and props["Width"]["units"] == ["Inches", "Centimeters"]
    assert props["Holiday"]["source"] == "none" and props["Holiday"]["max_values"] == 2
    res = await _put(w, {"Holiday": {"values": ["Christmas", "Easter"]}, "Width": {"values": ["12"], "unit": "Inches"}})
    assert res.status_code == 200, res.text
    props = _by_name(res.json())
    assert props["Holiday"]["current"] == ["Christmas", "Easter"] and props["Holiday"]["source"] == "seller"
    assert (props["Width"]["current"], props["Width"]["unit"]) == (["12"], "Inches")
    for bad in ({"Holiday": {"values": ["Diwali"]}}, {"Occasion": {"values": ["Birthday", "Wedding"]}}, {"Care": {"values": ["Hand wash"]}}):
        assert (await _put(w, bad)).status_code == 422, bad
    cleared = _by_name((await _put(w, {"Holiday": {"values": []}})).json())["Holiday"]
    assert cleared["current"] == [] and cleared["source"] == "seller"  # "not set", on purpose
    async with w["sm"]() as s:
        row = await s.get(GeneratedContent, w["contents"][0])
        assert LA.draft_set(row)[0].get("Holiday") is None and "Holiday" in LA.draft_set(row)[1]
    await w["engine"].dispose()


async def test_the_sellers_value_beats_the_writers_and_survives_a_rewrite(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch)
    async with w["sm"]() as s:
        row = await s.get(GeneratedContent, w["contents"][0])
        row.attributes = {**(row.attributes or {}), "listing": {"Occasion": "Wedding", "Holiday": ["Easter"]}}
        await s.commit()
    props = _by_name((await w["client"].get(_url(w))).json())
    assert (props["Occasion"]["current"], props["Occasion"]["source"], props["Occasion"]["source_words"]) == (["Wedding"], "writer", "suggested")
    await _put(w, {"Occasion": {"values": ["Birthday"]}})
    async with w["sm"]() as s:  # a rewrite writes only the suggestion
        row = await s.get(GeneratedContent, w["contents"][0])
        row.attributes = {**row.attributes, "listing": {"Occasion": "Wedding", "Holiday": ["Halloween"]}}
        await s.commit()
    props = _by_name((await w["client"].get(_url(w))).json())
    assert (props["Occasion"]["current"], props["Occasion"]["suggested"]) == (["Birthday"], ["Wedding"])
    assert props["Holiday"]["current"] == ["Halloween"] and props["Holiday"]["source"] == "writer"
    back = _by_name((await _put(w, {"Occasion": None})).json())["Occasion"]  # back to the suggestion
    assert back["current"] == ["Wedding"] and back["source"] == "writer"
    await w["engine"].dispose()


async def test_a_shops_own_values_and_keep_as_it_is(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch)
    one, two = w["shops"]
    await _put(w, {"Holiday": {"values": ["Christmas"]}})
    res = await _put(w, {"Holiday": {"values": ["Halloween"]}}, shop=two)
    assert res.status_code == 200 and _by_name(res.json())["Holiday"]["source"] == "shop"
    assert _by_name((await w["client"].get(_url(w), params={"shop": str(one)})).json())["Holiday"]["current"] == ["Christmas"]
    rows = (await w["client"].get(f"/api/batches/{w['batch']}/shops/{two}/rows")).json()["rows"]
    assert rows[0]["attributes_own"] is True
    async with w["sm"]() as s:
        row = await s.get(GeneratedContent, w["contents"][0])
        assert LA.draft_set(row, two)[0]["Holiday"] == ["Halloween"] and LA.draft_set(row, one)[0]["Holiday"] == ["Christmas"]
    kept = await w["client"].put(_url(w), json={"connection_id": str(two), "keep": True})
    assert _by_name(kept.json())["Holiday"]["current"] == ["Christmas"]
    assert (await w["client"].put(_url(w), json={"keep": True})).status_code == 422  # keep is for one shop
    await w["engine"].dispose()


async def test_bulk_preview_says_who_changes_and_who_is_skipped(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch, listings=2)
    first, second = w["contents"]
    async with w["sm"]() as s:  # the second listing's profile is a category without Holiday
        own = await s.get(ListingProfile, w["profile"])
        other = ListingProfile(tenant_id=own.tenant_id, connection_id=own.connection_id, name="Mugs", reference_listing_id=2,
                               content_template="apparel", confirmed=True,
                               cached_payload={**own.cached_payload, "category_properties": LA.compact(TAXONOMY_PROPS[1:])})
        s.add(other)
        await s.flush()
        (await s.get(GeneratedContent, second)).listing_profile_id = other.id
        await s.commit()
    body = {"content_ids": [str(first), str(second)], "name": "Holiday", "values": ["Christmas"]}
    preview = (await w["client"].post(f"/api/batches/{w['batch']}/attributes/bulk", json=body)).json()
    rows = {r["content_id"]: r for r in preview["rows"]}
    assert rows[str(first)]["changed"] is True and rows[str(first)]["after"] == ["Christmas"]
    assert rows[str(second)]["changed"] is False and rows[str(second)]["reason"] == "its category has no Holiday"
    assert preview["applied"] is False and "1 skipped" in preview["sentence"]
    async with w["sm"]() as s:
        assert LA.own(await s.get(GeneratedContent, first)) == {}  # a preview saves nothing
    bad = (await w["client"].post(f"/api/batches/{w['batch']}/attributes/bulk", json={**body, "values": ["Diwali"]})).json()
    assert bad["changed"] == 0 and "not one of Etsy's" in bad["rows"][0]["reason"]
    done = (await w["client"].post(f"/api/batches/{w['batch']}/attributes/bulk", json={**body, "apply": True})).json()
    assert done["applied"] is True and done["changed"] == 1
    shop = (await w["client"].post(f"/api/batches/{w['batch']}/attributes/bulk",
                                   json={**body, "values": ["Easter"], "connection_id": str(w["shops"][1]), "apply": True})).json()
    assert shop["changed"] == 1 and "in Shop 02" in shop["sentence"]
    async with w["sm"]() as s:
        row = await s.get(GeneratedContent, first)
        assert LA.own(row)["Holiday"] == ["Christmas"] and LA.shop_own(row, w["shops"][1])["Holiday"] == ["Easter"]
    await w["engine"].dispose()


async def test_a_category_change_drops_what_no_longer_applies_and_says_so(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch)
    await _put(w, {"Holiday": {"values": ["Christmas"]}, "Occasion": {"values": ["Birthday"]}})
    async with w["sm"]() as s:  # the profile's category now has no Holiday
        profile = await s.get(ListingProfile, w["profile"])
        profile.cached_payload = {**profile.cached_payload, "category_properties": LA.compact(TAXONOMY_PROPS[1:])}
        await s.commit()
    out = (await w["client"].get(_url(w))).json()
    assert out["dropped"] == [{"name": "Holiday", "values": ["Christmas"]}] and "Holiday" not in _by_name(out)
    await _run_drafts(w, await _send_drafts(w))
    sent = w["etsy"].properties[next(iter((await drafts_by_shop(w)).values()))]
    assert OCCASION in sent and HOLIDAY not in sent  # dropped with the card's note, never sent
    await w["engine"].dispose()


@pytest.mark.parametrize("source", ["manual", "ai"])
async def test_the_draft_sends_the_values_with_their_ids_and_units(tmp_path, test_settings: Settings, monkeypatch, source: str) -> None:
    w = await world(tmp_path, test_settings, monkeypatch)
    one, two = w["shops"]
    async with w["sm"]() as s:
        row = await s.get(GeneratedContent, w["contents"][0])
        row.source = source
        row.attributes = {**(row.attributes or {}), "listing": {"Occasion": "Wedding", "Sleeve length": "Long sleeve"}}
        await s.commit()
    await _put(w, {"Holiday": {"values": ["Christmas", "Easter"]}, "Width": {"values": ["12"], "unit": "Inches"},
                   "Occasion": {"values": ["Birthday"]}})
    await _put(w, {"Holiday": {"values": ["Halloween"]}, "Sleeve length": {"values": []}}, shop=two)
    await both_shops(w)
    await _run_drafts(w, await _send_drafts(w))
    drafts = await drafts_by_shop(w)
    a, b = w["etsy"].properties[drafts[one]], w["etsy"].properties[drafts[two]]
    assert a[HOLIDAY]["value_ids"] == [1, 2] and a[OCCASION]["value_ids"] == [11]  # the seller's, not the writer's Wedding
    assert (a[WIDTH]["values"], a[WIDTH]["scale_id"]) == (["12"], 5)
    assert a[SLEEVE]["value_ids"] == [22]  # the writer's suggestion still goes where the seller set nothing
    assert b[HOLIDAY]["value_ids"] == [3] and SLEEVE not in b  # the shop's own; "not set" there is not sent
    await w["engine"].dispose()


async def test_a_listing_live_in_a_shop_keeps_its_attributes_there(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch)
    one = w["shops"][0]
    await _run_drafts(w, await _send_drafts(w))
    async with w["sm"]() as s:
        for pub in (await s.execute(select(ListingPublication))).scalars():
            pub.state, pub.published_at = "active", datetime.now(timezone.utc)
        await s.commit()
    sent = len(w["etsy"].requests)
    res = await _put(w, {"Holiday": {"values": ["Christmas"]}}, shop=one)
    assert res.json()["note"].startswith("Already on Etsy in Shop 01: these values go to new drafts only")
    whole = await _put(w, {"Occasion": {"values": ["Birthday"]}})
    assert "Already on Etsy in Shop 01" in whole.json()["note"]
    assert len(w["etsy"].requests) == sent
    await w["engine"].dispose()


async def test_another_account_gets_404(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await world(tmp_path, test_settings, monkeypatch)
    other = await other_client(w)
    assert (await other.get(_url(w))).status_code == 404
    assert (await other.put(_url(w), json={"values": {"Holiday": {"values": ["Christmas"]}}})).status_code == 404
    bulk = {"content_ids": [str(w["contents"][0])], "name": "Holiday", "values": ["Christmas"], "apply": True}
    assert (await other.post(f"/api/batches/{w['batch']}/attributes/bulk", json=bulk)).status_code == 404
    mine = await w["client"].get(_url(w), params={"shop": "00000000-0000-0000-0000-000000000001"})
    assert mine.status_code == 404
    async with w["sm"]() as s:
        assert LA.own(await s.get(GeneratedContent, w["contents"][0])) == {}
    await w["engine"].dispose()
