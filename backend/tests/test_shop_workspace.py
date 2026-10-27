"""The batch page's shop workspace (seller feedback, 2026-10-27: pick the shop, change each
listing's profile, SKU and text for it on its row, select, send).

Per-row profile, SKU and text saved for that shop only; rows already in that shop locked; SKU
duplicates refused as typed and on save; "Send N" sends only approved, selected rows; that
shop's Where and when for the sent rows only; other shops untouched; allowance unchanged;
another account 404.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.config import Settings
from app.db.models import Asset, GeneratedContent, ListingPublication, PlannedSlot, Tenant, UploadBatch, UploadBatchStatus
from app.pipeline import shop_listing
from tests.auth_support import authenticate, open_session
from tests.test_another_shop import _allowance, _first_shop_rows, _published_in_first_shop, _skus_of
from tests.test_end_to_end import _run_drafts


async def _rows(w: dict, shop) -> dict[str, dict]:  # noqa: ANN001
    res = await w["client"].post(f"/api/batches/{w['batch']}/another-shop/preview", json={"connection_id": str(shop)})
    assert res.status_code == 200, res.text
    return {r["content_id"]: r for r in res.json()["rows"]}


async def test_each_row_saves_its_profile_sku_and_text_for_that_shop_only(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch, skus=["PCS10", "PCS11", "PCS12"])
    a, batch, (one, two) = w["client"], w["batch"], w["shops"]
    c = [str(x) for x in w["contents"]]
    base = f"/api/batches/{batch}/shops/{two}"
    rows = await _rows(w, two)
    assert all(r["state"] == "new" and r["profile_id"] is None and r["sku_own"] is False and r["text_own"] == [] for r in rows.values())
    assert rows[c[0]]["profile_name"] == "Standard Tee"

    res = await a.put(f"{base}/profile", json={"content_ids": [c[0]], "profile_id": str(w["pcs"])})
    assert res.status_code == 200, res.text
    out = {r["content_id"]: r for r in res.json()["rows"]}
    assert out[c[0]]["profile_id"] == str(w["pcs"]) and out[c[0]]["profile_name"] == "REGULAR - PCS"
    assert out[c[1]]["profile_id"] is None

    assert (await a.put(f"{base}/skus", json={"skus": {c[0]: "ILC10"}})).json() == {"saved": 1, "refused": []}
    texts = (await a.put(f"{base}/texts", json={"texts": {c[0]: {"title": "Juniper Retro Tee For Hikers"}}})).json()
    assert texts[0]["text_own"] == ["title"] and texts[0]["errors"] == []
    rows = await _rows(w, two)
    assert rows[c[0]]["sku"] == "ILC10" and rows[c[0]]["sku_own"] and rows[c[0]]["title"] == "Juniper Retro Tee For Hikers"
    assert rows[c[1]]["sku"] == "PCS11" and not rows[c[1]]["sku_own"]  # left alone: follows the listing
    async with w["sm"]() as s:
        content = await s.get(GeneratedContent, w["contents"][0])
        asset = await s.get(Asset, content.asset_id)
    assert asset.parsed_sku == "PCS10" and content.listing_profile_id == w["profile"] and content.title != "Juniper Retro Tee For Hikers"
    assert shop_listing.entry(content, one) == {}  # nothing for the first shop
    # A field set back to the listing's follows it again.
    assert (await a.put(f"{base}/skus", json={"skus": {c[0]: "PCS10"}})).json()["saved"] == 1
    assert (await _rows(w, two))[c[0]]["sku_own"] is False
    await w["engine"].dispose()


async def test_rows_already_in_the_shop_are_locked(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch)
    a, batch, one = w["client"], w["batch"], w["shops"][0]
    c = [str(x) for x in w["contents"]]
    rows = await _rows(w, one)
    assert all(r["state"] == "there" and r["shop_state"] == "published" and r["listing_link"] for r in rows.values())
    base = f"/api/batches/{batch}/shops/{one}"
    for path, body in (("profile", {"content_ids": [c[0]], "profile_id": None}), ("skus", {"skus": {c[0]: "X1"}}),
                       ("texts", {"texts": {c[0]: {"title": "No"}}})):
        res = await a.put(f"{base}/{path}", json=body)
        assert res.status_code == 409 and "edited on Etsy" in res.json()["detail"], (path, res.text)
    sent = await a.post(f"/api/batches/{batch}/another-shop", json={"connection_id": str(one), "content_ids": c})
    assert sent.status_code == 409  # nothing new to send there
    await w["engine"].dispose()


async def test_bulk_skus_are_checked_as_typed_and_refused_together(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch, skus=["PCS10", "PCS11", "PCS12"])
    a, batch, two = w["client"], w["batch"], w["shops"][1]
    c = [str(x) for x in w["contents"]]
    base = f"/api/batches/{batch}/shops/{two}"
    assert (await a.put(f"{base}/skus", json={"skus": {c[2]: "ILC12"}})).json()["saved"] == 1
    clash = {"skus": {c[0]: "ILC10", c[1]: "ilc12"}}  # c[2] keeps ILC12 there
    check = (await a.post(f"{base}/skus/check", json=clash)).json()
    assert check["saved"] == 0 and any("ilc12 (D01) is already used in Shop 02" in r for r in check["refused"]), check
    refused = await a.put(f"{base}/skus", json=clash)
    assert refused.status_code == 409
    rows = await _rows(w, two)
    assert rows[c[0]]["sku"] == "PCS10" and rows[c[1]]["sku"] == "PCS11"  # none saved
    twice = (await a.post(f"{base}/skus/check", json={"skus": {c[0]: "SAME", c[1]: "same"}})).json()
    assert any("given to 2 listings" in r for r in twice["refused"])
    assert (await a.put(f"{base}/skus", json={"skus": {c[0]: "ILC10", c[1]: "ILC11"}})).json()["saved"] == 2
    await w["engine"].dispose()


async def test_send_n_sends_only_approved_selected_rows_then_that_shops_when(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch, skus=["PCS10", "PCS11", "PCS12"])
    a, batch, (one, two) = w["client"], w["batch"], w["shops"]
    c = [str(x) for x in w["contents"]]
    first, used = await _first_shop_rows(w), await _allowance(w)
    async with w["sm"]() as s:
        (await s.get(GeneratedContent, w["contents"][2])).approved = False
        await s.commit()
    base = f"/api/batches/{batch}/shops/{two}"
    await a.put(f"{base}/profile", json={"content_ids": [c[0]], "profile_id": str(w["pcs"])})
    await a.put(f"{base}/skus", json={"skus": {c[0]: "ILC10", c[1]: "ILC11"}})
    # The summary: what goes, with what, and what will not (and why).
    summary = (await a.post(f"/api/batches/{batch}/another-shop/preview", json={"connection_id": str(two), "content_ids": c})).json()
    states = {r["content_id"]: (r["state"], r["reason"]) for r in summary["rows"]}
    assert states[c[2]] == ("cannot", "not approved yet") and summary["total"] == 2
    assert summary["button"] == "Create 2 drafts in Shop 02"
    res = await a.post(f"/api/batches/{batch}/another-shop", json={"connection_id": str(two), "content_ids": c[:2]})
    assert res.status_code == 200 and len(res.json()["jobs"]) == 2, res.text
    await _run_drafts(w, [j["job_id"] for j in res.json()["jobs"]])
    async with w["sm"]() as s:
        pubs = {str(p.content_id): p for p in (await s.execute(select(ListingPublication).where(
            ListingPublication.connection_id == two))).scalars()}
    assert set(pubs) == set(c[:2]) and pubs[c[0]].profile_id == w["pcs"] and pubs[c[0]].sku == "ILC10"
    assert _skus_of(w["etsy"].listings[pubs[c[1]].etsy_listing_id]) == {"ILC11"}
    # That shop's Where and when, for the sent rows only: publish now goes there and nowhere else.
    when = {"when": "publish", "connection_id": str(two), "content_ids": c[:2]}
    preview = (await a.post(f"/api/batches/{batch}/send/preview", json=when)).json()
    assert preview["total"] == 2 and {cell["shop_id"] for r in preview["rows"] for cell in r["cells"]} == {str(two)}
    done = await a.post(f"/api/batches/{batch}/send", json=when)
    assert done.status_code == 200 and done.json()["scheduled"] == 2, done.text
    async with w["sm"]() as s:
        slots = list((await s.execute(select(PlannedSlot))).scalars())
    assert {sl.connection_id for sl in slots} == {two} and len(slots) == 2
    assert await _first_shop_rows(w) == first
    assert {k: v for k, v in w["etsy"].listings.items() if v["shop"] == 900} == w["first"]
    assert await _allowance(w) == used
    await w["engine"].dispose()


async def test_another_account_is_404(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch)
    a, batch, two = w["client"], w["batch"], w["shops"][1]
    c = str(w["contents"][0])
    async with w["sm"]() as s:
        other = Tenant(email="other@example.com", password_hash="!")
        s.add(other)
        await s.flush()
        own = UploadBatch(tenant_id=other.id, status=UploadBatchStatus.ready, file_count=0)
        s.add(own)
        await s.commit()
        other_id, own_batch = other.id, own.id
    authenticate(a, await open_session(w["ctx"]["redis"], other_id))
    for b in (batch, own_batch):
        base = f"/api/batches/{b}/shops/{two}"
        assert (await a.put(f"{base}/profile", json={"content_ids": [c]})).status_code == 404
        assert (await a.put(f"{base}/skus", json={"skus": {c: "X"}})).status_code == 404
        assert (await a.post(f"{base}/skus/check", json={"skus": {c: "X"}})).status_code == 404
        assert (await a.put(f"{base}/texts", json={"texts": {c: {"title": "X"}}})).status_code == 404
    assert (await a.post(f"/api/batches/{own_batch}/send/preview",
                         json={"when": "publish", "connection_id": str(two)})).status_code == 404
    authenticate(a, await open_session(w["ctx"]["redis"], w["tenant"]))
    async with w["sm"]() as s:
        assert all(x.shop_overrides is None for x in (await s.execute(select(GeneratedContent))).scalars())
    await w["engine"].dispose()
