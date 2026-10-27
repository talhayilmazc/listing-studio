"""A batch published in one shop, then the same listings in another shop with that shop's
profile and its own SKUs (production report, 2026-10-27: a seller changed the profile and
SKUs while their second shop was shown and nothing went there).

"Publish in another shop" with each SKU mode, a duplicate SKU refused, the target shop's
own profile; the first shop untouched and the allowance unchanged; the profile control and
"Edit SKU" per shop; the per-shop status; another account's batch, shop and profile 404.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import Settings
from app.db.models import (
    Asset,
    GeneratedContent,
    ListingGroupSetting,
    ListingProfile,
    ListingPublication,
    ShopListingCache,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.pipeline import shop_listing
from tests.auth_support import authenticate, open_session
from tests.test_end_to_end import _run_drafts, _send_drafts, _world
from tests.test_publisher import REFERENCE


async def _published_in_first_shop(tmp_path, test_settings: Settings, monkeypatch, skus: list[str] | None = None) -> dict:
    """Two shops; three listings drafted and published in the first; a profile of the second shop."""
    w = await _world(tmp_path, test_settings, monkeypatch, shops=2, listings=3)
    async with w["sm"]() as s:
        if skus:
            for asset in (await s.execute(select(Asset).order_by(Asset.group_key))).scalars():
                asset.parsed_sku = skus[int(asset.group_key[1:])]
        pcs = ListingProfile(
            tenant_id=w["tenant"], connection_id=w["shops"][1], name="REGULAR - PCS", reference_listing_id=2,
            content_template="apparel", confirmed=True, listing_style="classic", title_prefix="PCS Prefix",
            cached_payload={**REFERENCE, "description": "PCS reference\n\nBody", "images": []},
            updated_at=datetime.now(timezone.utc),
        )
        s.add(pcs)
        await s.commit()
        w["pcs"] = pcs.id
    await _run_drafts(w, await _send_drafts(w))
    async with w["sm"]() as s:
        for pub in (await s.execute(select(ListingPublication))).scalars():
            pub.state = "active"
            pub.published_at = datetime.now(timezone.utc)
            w["etsy"].listings[pub.etsy_listing_id]["state"] = "active"
        await s.commit()
    w["first"] = {k: dict(v, inventory=v["inventory"]) for k, v in w["etsy"].listings.items()}
    return w


async def _first_shop_rows(w: dict) -> list[tuple]:
    async with w["sm"]() as s:
        return sorted((str(p.content_id), p.etsy_listing_id, p.state, p.sku, str(p.profile_id), p.title)
                      for p in (await s.execute(select(ListingPublication).where(
                          ListingPublication.connection_id == w["shops"][0]))).scalars())


def _skus_of(listing: dict) -> set[str]:
    """The listing's base SKUs on Etsy (the reference's per-size pattern: "SKU00-S", "SKU00-M")."""
    inventory = json.loads(listing["inventory"]) if listing["inventory"] else {"products": []}
    return {str(p.get("sku")).rsplit("-", 1)[0] for p in inventory.get("products") or []}


async def _allowance(w: dict) -> int:
    res = await w["client"].get("/api/account/allowance")
    assert res.status_code == 200, res.text
    return res.json()["used"]


async def test_keep_skus_and_each_listings_own_profile(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch)
    a, batch, (one, two) = w["client"], w["batch"], w["shops"]
    first, used = await _first_shop_rows(w), await _allowance(w)

    status = (await a.get(f"/api/batches/{batch}/shops")).json()
    by_shop = {s["shop_id"]: s for s in status["shops"]}
    assert by_shop[str(one)]["published"] == 3 and by_shop[str(two)]["not_sent"] == 3

    body = {"connection_id": str(two), "sku_mode": "keep"}
    preview = (await a.post(f"/api/batches/{batch}/another-shop/preview", json=body)).json()
    assert [r["state"] for r in preview["rows"]] == ["new"] * 3 and preview["refused"] == []
    assert [r["sku"] for r in preview["rows"]] == ["SKU00", "SKU01", "SKU02"]
    assert preview["button"] == "Create 3 drafts in Shop 02" and preview["disabled"] is None
    assert [p["name"] for p in preview["profiles"]] == ["REGULAR - PCS"] and preview["own_profiles"] == ["Standard Tee"]
    # The preview keeps nothing.
    async with w["sm"]() as s:
        assert all(c.shop_overrides is None for c in (await s.execute(select(GeneratedContent))).scalars())

    res = await a.post(f"/api/batches/{batch}/another-shop", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["message"] == "Creating 3 drafts in Shop 02. Nothing changed in your other shops."
    await _run_drafts(w, [j["job_id"] for j in res.json()["jobs"]])
    second = [d for d in w["etsy"].listings.values() if d["shop"] == 901]
    assert len(second) == 3 and {d["state"] for d in second} == {"draft"}
    assert sorted(sku for d in second for sku in _skus_of(d)) == ["SKU00", "SKU01", "SKU02"]
    # The first shop: the same rows, the same listings on Etsy; the allowance: the same.
    assert await _first_shop_rows(w) == first
    assert {k: v for k, v in w["etsy"].listings.items() if v["shop"] == 900} == w["first"]
    assert await _allowance(w) == used
    status = (await a.get(f"/api/batches/{batch}/shops")).json()
    by_shop = {s["shop_id"]: s for s in status["shops"]}
    assert by_shop[str(one)]["published"] == 3 and by_shop[str(two)]["drafted"] == 3
    # Sent again: already there, nothing new.
    again = (await a.post(f"/api/batches/{batch}/another-shop/preview", json=body)).json()
    assert again["total"] == 0 and again["disabled"].startswith("Nothing new to send")
    assert {r["state"] for r in again["rows"]} == {"there"}
    await w["engine"].dispose()


async def test_prefix_replaced_with_the_target_shops_own_profile(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch, skus=["PCS432", "pcs1206", "XYZ7"])
    a, batch, two = w["client"], w["batch"], w["shops"][1]
    first = await _first_shop_rows(w)
    body = {"connection_id": str(two), "profile_id": str(w["pcs"]), "sku_mode": "prefix", "prefix_from": "PCS", "prefix_to": "ILC"}
    preview = (await a.post(f"/api/batches/{batch}/another-shop/preview", json=body)).json()
    assert [(r["sku_from"], r["sku"]) for r in preview["rows"]] == [("PCS432", "ILC432"), ("pcs1206", "ILC1206"), ("XYZ7", "XYZ7")]
    assert preview["rows"][2]["note"] == "does not start with PCS: kept as it is"
    res = await a.post(f"/api/batches/{batch}/another-shop", json=body)
    assert res.status_code == 200, res.text
    await _run_drafts(w, [j["job_id"] for j in res.json()["jobs"]])
    async with w["sm"]() as s:
        pubs = list((await s.execute(select(ListingPublication).where(ListingPublication.connection_id == two))).scalars())
        contents = {c.id: c for c in (await s.execute(select(GeneratedContent))).scalars()}
    assert {p.profile_id for p in pubs} == {w["pcs"]}  # built with the shop's own profile
    assert sorted(p.sku for p in pubs) == ["ILC1206", "ILC432", "XYZ7"]
    for pub in pubs:
        listing = w["etsy"].listings[pub.etsy_listing_id]
        content = contents[pub.content_id]
        assert listing["title"] == content.title  # the text goes as it is: no prefix swapped
        assert _skus_of(listing) == {pub.sku}
        # The listing keeps its own profile and SKU; the shop has its own.
        assert content.listing_profile_id == w["profile"] and content.approved
    assert await _first_shop_rows(w) == first
    status = (await a.get(f"/api/batches/{batch}/shops")).json()
    row = next(r for r in status["listings"] if r["group_key"] == "D00")
    assert row["skus"] == {str(w["shops"][0]): "PCS432", str(two): "ILC432"}
    assert row["profiles"] == {str(two): str(w["pcs"])}
    await w["engine"].dispose()


async def test_skus_per_listing_and_duplicates_refused(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch)
    a, batch, two = w["client"], w["batch"], w["shops"][1]
    c = [str(x) for x in w["contents"]]
    async with w["sm"]() as s:
        s.add(ShopListingCache(tenant_id=w["tenant"], connection_id=two, listing_id=555001,
                               payload={"state": "active", "skus": ["DUP1"]}, fetched_at=datetime.now(timezone.utc)))
        await s.commit()
    body = {"connection_id": str(two), "sku_mode": "each", "skus": {c[0]: "dup1", c[1]: "SAME", c[2]: "same"}}
    preview = (await a.post(f"/api/batches/{batch}/another-shop/preview", json=body)).json()
    assert preview["disabled"].startswith("Change the SKUs listed above first")
    assert any("dup1 (D00) is already used in Shop 02 by listing 555001" in r for r in preview["refused"]), preview["refused"]
    assert any("is given to 2 listings here: D01, D02" in r for r in preview["refused"]), preview["refused"]
    refused = await a.post(f"/api/batches/{batch}/another-shop", json=body)
    assert refused.status_code == 409 and "555001" in refused.json()["detail"]
    async with w["sm"]() as s:
        assert all(x.shop_overrides is None for x in (await s.execute(select(GeneratedContent))).scalars())
        assert not list((await s.execute(select(ListingPublication).where(ListingPublication.connection_id == two))).scalars())
    # An invalid SKU is refused too; fixed, they go.
    bad = (await a.post(f"/api/batches/{batch}/another-shop/preview",
                        json={**body, "skus": {c[0]: "A^B", c[1]: "ILC1", c[2]: "ILC2"}})).json()
    assert any("Etsy does not accept ^" in r for r in bad["refused"])
    ok = await a.post(f"/api/batches/{batch}/another-shop", json={**body, "skus": {c[0]: "ILC0", c[1]: "ILC1"},
                                                                 "content_ids": c[:2]})
    assert ok.status_code == 200 and len(ok.json()["jobs"]) == 2, ok.text
    await w["engine"].dispose()


async def test_the_profile_control_per_shop(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """A listing already on Etsy in a shop: its profile there cannot change (the page offers
    "Publish in another shop"); in a shop it is not in yet, the profile is that shop's only."""
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch)
    a, batch, (one, two) = w["client"], w["batch"], w["shops"]
    here = await a.put(f"/api/batches/{batch}/groups", json={"group_keys": ["D00"], "profile_id": str(w["pcs"]), "for_shop": str(one)})
    assert here.status_code == 409 and "Publish in another shop" in here.json()["detail"]
    anywhere = await a.put(f"/api/batches/{batch}/groups", json={"group_keys": ["D00"], "profile_id": str(w["pcs"])})
    assert anywhere.status_code == 409 and "Publish in another shop" in anywhere.json()["detail"]
    there = await a.put(f"/api/batches/{batch}/groups", json={"group_keys": ["D00"], "profile_id": str(w["pcs"]), "for_shop": str(two)})
    assert there.status_code == 200, there.text
    async with w["sm"]() as s:
        content = await s.get(GeneratedContent, w["contents"][0])
        setting = (await s.execute(select(ListingGroupSetting).where(ListingGroupSetting.group_key == "D00"))).scalar_one_or_none()
    assert shop_listing.profile_in(content, two) == w["pcs"] and shop_listing.profile_in(content, one) is None
    assert content.listing_profile_id == w["profile"] and content.approved  # nothing in the first shop changes
    assert setting is None or setting.profile_id != w["pcs"]
    # The shop's profile is used when the listing is sent there.
    res = await a.post(f"/api/batches/{batch}/another-shop", json={"connection_id": str(two), "content_ids": [str(w["contents"][0])]})
    await _run_drafts(w, [j["job_id"] for j in res.json()["jobs"]])
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.connection_id == two))).scalar_one()
    assert pub.profile_id == w["pcs"]
    await w["engine"].dispose()


async def test_edit_sku_is_per_shop_once_on_etsy(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch)
    a, batch, (one, two) = w["client"], w["batch"], w["shops"]
    cid = str(w["contents"][0])
    # While the second shop is shown: its SKU there only; the first shop's draft is not offered.
    res = (await a.put(f"/api/batches/{batch}/groups/sku", json={"group_key": "D00", "sku": "ilc10", "connection_id": str(two)})).json()
    assert res["connection_id"] == str(two) and res["sku"] == "ilc10" and res["etsy"] == []
    async with w["sm"]() as s:
        asset = (await s.execute(select(Asset).where(Asset.group_key == "D00"))).scalar_one()
    assert asset.parsed_sku == "SKU00"  # the listing's own SKU (and the first shop's) stays
    row = next(r for r in (await a.get(f"/api/batches/{batch}/shops")).json()["listings"] if r["content_id"] == cid)
    assert row["skus"] == {str(one): "SKU00", str(two): "ilc10"}
    sent = await a.post(f"/api/batches/{batch}/another-shop", json={"connection_id": str(two), "content_ids": [cid]})
    await _run_drafts(w, [j["job_id"] for j in sent.json()["jobs"]])
    # On Etsy in the second shop now: a new SKU there offers that shop's draft only, by name.
    res = (await a.put(f"/api/content/{cid}/sku", json={"sku": "ILC10B", "connection_id": str(two)})).json()
    assert [(e["shop_name"], e["current_sku"]) for e in res["etsy"]] == [("Shop 02", "ilc10")]
    queued = (await a.post(f"/api/content/{cid}/sku/etsy", json={"confirm": True, "connection_ids": [str(two)]})).json()
    assert queued["queued"] is True and queued["sku"] == "ILC10B" and [s["shop_name"] for s in queued["shops"]] == ["Shop 02"]
    spec = [args for function, args in w["queue"].calls if function == "update_sku_on_etsy"]
    assert len(spec) == 1 and json.loads(spec[0][0])["sku"] == "ILC10B"
    # With "All shops" shown, the listing's own SKU, as before: the first shop's draft is offered.
    res = (await a.put(f"/api/batches/{batch}/groups/sku", json={"group_key": "D01", "sku": "NEW01"})).json()
    assert res["connection_id"] is None and [e["shop_name"] for e in res["etsy"]] == ["Shop 01"]
    await w["engine"].dispose()


async def test_status_counts_scheduled_and_drafted(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=2, listings=3)
    await _run_drafts(w, await _send_drafts(w))
    async with w["sm"]() as s:
        pubs = list((await s.execute(select(ListingPublication).order_by(ListingPublication.etsy_listing_id))).scalars())
        pubs[0].state = "active"
        pubs[1].scheduled_for = datetime.now(timezone.utc) + timedelta(days=1)
        await s.commit()
    status = (await w["client"].get(f"/api/batches/{w['batch']}/shops")).json()
    first = next(s for s in status["shops"] if s["shop_id"] == str(w["shops"][0]))
    assert (first["published"], first["scheduled"], first["drafted"], first["not_sent"]) == (1, 1, 1, 0)
    await w["engine"].dispose()


async def test_another_account_is_404(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch)
    a, batch, two = w["client"], w["batch"], w["shops"][1]
    async with w["sm"]() as s:
        other = Tenant(email="other@example.com", password_hash="!")
        s.add(other)
        await s.flush()
        own = UploadBatch(tenant_id=other.id, status=UploadBatchStatus.ready, file_count=0)
        s.add(own)
        await s.commit()
        other_id, own_batch = other.id, own.id
    redis = w["ctx"]["redis"]
    authenticate(a, await open_session(redis, other_id))
    body = {"connection_id": str(two), "sku_mode": "keep"}
    assert (await a.get(f"/api/batches/{batch}/shops")).status_code == 404
    assert (await a.post(f"/api/batches/{batch}/another-shop/preview", json=body)).status_code == 404
    assert (await a.post(f"/api/batches/{batch}/another-shop", json=body)).status_code == 404
    # Its own batch, the seller's shop or profile: 404.
    assert (await a.post(f"/api/batches/{own_batch}/another-shop/preview", json=body)).status_code == 404
    assert (await a.put(f"/api/batches/{own_batch}/groups", json={"group_keys": [], "profile_id": str(w["pcs"]),
                                                                  "for_shop": str(two)})).status_code == 404
    authenticate(a, await open_session(redis, w["tenant"]))
    # The seller's own batch with a profile of another shop: 404.
    wrong = await a.post(f"/api/batches/{batch}/another-shop/preview",
                         json={**body, "profile_id": str(w["profile"])})
    assert wrong.status_code == 404
    async with w["sm"]() as s:
        assert all(c.shop_overrides is None for c in (await s.execute(select(GeneratedContent))).scalars())
    await w["engine"].dispose()


def test_replace_prefix_matches_the_pages_copy() -> None:
    """The same cases as lib/anotherShop.test.ts: the page's live preview and the server agree."""
    assert shop_listing.replace_prefix("PCS432", "PCS", "ILC") == ("ILC432", True)
    assert shop_listing.replace_prefix("pcs1206", "PCS", "ILC") == ("ILC1206", True)
    assert shop_listing.replace_prefix(" PCS9 ", " PCS ", "ILC-") == ("ILC-9", True)
    assert shop_listing.replace_prefix("XYZ7", "PCS", "ILC") == ("XYZ7", False)
    assert shop_listing.replace_prefix("AB1", "", "ILC") == ("ILCAB1", True)
    assert shop_listing.replace_prefix(None, "PCS", "ILC") == ("", False)
