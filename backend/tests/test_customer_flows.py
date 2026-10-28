"""The sellers' real flows, end to end against the fake Etsy (production bug, 2026-10-27: a
hand-written batch published in one shop showed "no title yet" and "Generate the empty ones"
when the seller worked on a second shop).

a) hand-written: approve, draft and publish in shop A; work on shop B; every listing approved
   with its title and tags; SKU prefix PCS -> ILC; send to B; B's drafts carry the listing's
   text and the new SKUs; A untouched. b) the same written by the app. c) one listing with its
   own text for B. d) a batch never sent anywhere: the workspace for its first shop.

Each: nothing reads as "no title yet", nothing is empty for Generate, the counts are right.
Then: approval per shop, the production partner rule, Generate safety, workspace-check.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import Asset, AssetStatus, GeneratedContent, ListingProfile, ListingPublication
from app.ops import workspace_check
from app.pipeline import manual, shop_listing
from tests.test_another_shop import _allowance, _first_shop_rows, _skus_of
from tests.test_end_to_end import _run_drafts, _send_drafts, _world

SKUS = ["PCS417", "PCS418", "PCS419"]


async def _batch_in_shop_a(tmp_path, test_settings: Settings, monkeypatch, *, source: str, sent: bool = True) -> dict:
    """Three approved listings written ``source`` ("manual" or "ai"); published in shop A unless not ``sent``."""
    w = await _world(tmp_path, test_settings, monkeypatch, shops=2, listings=3)
    async with w["sm"]() as s:
        for asset in (await s.execute(select(Asset))).scalars():
            asset.parsed_sku = SKUS[int(asset.group_key[1:])]
        for content in (await s.execute(select(GeneratedContent))).scalars():
            content.source = source
        await s.commit()
    if sent:
        await _run_drafts(w, await _send_drafts(w))
        async with w["sm"]() as s:
            for pub in (await s.execute(select(ListingPublication))).scalars():
                pub.state, pub.published_at = "active", datetime.now(timezone.utc)
                w["etsy"].listings[pub.etsy_listing_id]["state"] = "active"
            await s.commit()
    w["first"] = {k: dict(v) for k, v in w["etsy"].listings.items()}
    return w


async def _nothing_reads_empty(w: dict, shop) -> dict:  # noqa: ANN001
    """The review page's and the workspace's view for ``shop``: every listing has its title and
    tags, is approved, nothing is empty for Generate, Approve all approves nothing new."""
    a, batch = w["client"], w["batch"]
    cards = (await a.get(f"/api/batches/{batch}/content")).json()
    assert all(c["title"] and len(c["tags"]) == 13 and c["approved"] and c["edited_for"] == [] for c in cards), cards
    approved = (await a.post(f"/api/batches/{batch}/approve-all")).json()
    assert approved["approved"] == 0 and approved["already_approved"] == 3 and approved["skipped"] == [], approved
    assert not approved.get("summary"), approved
    async with w["sm"]() as s:
        rows = list((await s.execute(select(GeneratedContent))).scalars())
        profiles = {p.id: p for p in (await s.execute(select(ListingProfile))).scalars()}
        assert not any(manual.is_empty(c, profiles.get(c.listing_profile_id)) for c in rows)
    preview = (await a.post(f"/api/batches/{batch}/another-shop/preview", json={"connection_id": str(shop)})).json()
    for row in preview["rows"]:
        assert row["title"] and len(row["tags"]) == 13 and row["approved"] and row["errors"] == [], row
        assert row["approval_note"] is None and row["partner"] != shop_listing.PARTNER_REQUIRED
    return preview


@pytest.mark.parametrize("source", ["manual", "ai"])
async def test_flow_publish_in_a_then_send_to_b(tmp_path, test_settings: Settings, monkeypatch, source: str) -> None:
    w = await _batch_in_shop_a(tmp_path, test_settings, monkeypatch, source=source)
    a, batch, (one, two) = w["client"], w["batch"], w["shops"]
    first, used = await _first_shop_rows(w), await _allowance(w)
    preview = await _nothing_reads_empty(w, two)
    assert [r["state"] for r in preview["rows"]] == ["new"] * 3 and preview["total"] == 3
    status = {s["shop_id"]: s for s in (await a.get(f"/api/batches/{batch}/shops")).json()["shops"]}
    assert (status[str(one)]["published"], status[str(two)]["not_sent"]) == (3, 3)
    # Bulk SKU prefix PCS -> ILC for shop B, then send.
    c = [str(x) for x in w["contents"]]
    new = {cid: sku.replace("PCS", "ILC") for cid, sku in zip(c, SKUS)}
    assert (await a.post(f"/api/batches/{batch}/shops/{two}/skus/check", json={"skus": new})).json()["refused"] == []
    assert (await a.put(f"/api/batches/{batch}/shops/{two}/skus", json={"skus": new})).json()["saved"] == 3
    summary = (await a.post(f"/api/batches/{batch}/another-shop/preview",
                            json={"connection_id": str(two), "content_ids": c})).json()
    assert summary["total"] == 3 and summary["refused"] == [] and summary["text_problems"] == 0
    res = await a.post(f"/api/batches/{batch}/another-shop", json={"connection_id": str(two), "content_ids": c})
    assert res.status_code == 200, res.text
    await _run_drafts(w, [j["job_id"] for j in res.json()["jobs"]])
    async with w["sm"]() as s:
        contents = {str(x.id): x for x in (await s.execute(select(GeneratedContent))).scalars()}
        pubs = {str(p.content_id): p for p in (await s.execute(select(ListingPublication).where(
            ListingPublication.connection_id == two))).scalars()}
    for cid in c:
        listing = w["etsy"].listings[pubs[cid].etsy_listing_id]
        assert listing["title"] == contents[cid].title  # the listing's own text
        assert _skus_of(listing) == {new[cid]}
    assert await _first_shop_rows(w) == first
    assert {k: v for k, v in w["etsy"].listings.items() if v["shop"] == 900} == w["first"]
    assert await _allowance(w) == used
    status = {s["shop_id"]: s for s in (await a.get(f"/api/batches/{batch}/shops")).json()["shops"]}
    assert (status[str(one)]["published"], status[str(two)]["drafted"]) == (3, 3)
    await w["engine"].dispose()


async def test_flow_one_listing_with_its_own_text_for_b(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _batch_in_shop_a(tmp_path, test_settings, monkeypatch, source="manual")
    a, batch, two = w["client"], w["batch"], w["shops"][1]
    c = [str(x) for x in w["contents"]]
    await _nothing_reads_empty(w, two)
    own = "Juniper Retro Camping Tee For Hikers"
    await a.put(f"/api/batches/{batch}/shops/{two}/texts", json={"texts": {c[0]: {"title": own}}})
    rows = {r["content_id"]: r for r in (await a.post(f"/api/batches/{batch}/another-shop/preview",
                                                     json={"connection_id": str(two)})).json()["rows"]}
    # Edited for B after approval: B asks for approval again; the others are fine.
    assert rows[c[0]]["approved"] is False and rows[c[0]]["approval_note"] == "Edited for Shop 02: approve again"
    assert rows[c[0]]["state"] == "cannot" and rows[c[1]]["approved"] is True
    approved = (await a.post(f"/api/batches/{batch}/shops/{two}/approve", json={"content_ids": [c[0]]})).json()
    row = next(r for r in approved["rows"] if r["content_id"] == c[0])
    assert row["approved"] is True and row["approval_note"] is None and row["state"] == "new"
    res = await a.post(f"/api/batches/{batch}/another-shop", json={"connection_id": str(two), "content_ids": c})
    await _run_drafts(w, [j["job_id"] for j in res.json()["jobs"]])
    async with w["sm"]() as s:
        pubs = {str(p.content_id): p for p in (await s.execute(select(ListingPublication).where(
            ListingPublication.connection_id == two))).scalars()}
        original = (await s.get(GeneratedContent, w["contents"][0])).title
    assert w["etsy"].listings[pubs[c[0]].etsy_listing_id]["title"] == own
    first = [v for v in w["first"].values() if v["shop"] == 900]
    assert original in {v["title"] for v in first} and own not in {v["title"] for v in first}
    await w["engine"].dispose()


async def test_flow_a_batch_never_sent_works_for_its_first_shop(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _batch_in_shop_a(tmp_path, test_settings, monkeypatch, source="manual", sent=False)
    a, batch, one = w["client"], w["batch"], w["shops"][0]
    preview = (await a.post(f"/api/batches/{batch}/another-shop/preview", json={"connection_id": str(one)})).json()
    assert [r["state"] for r in preview["rows"]] == ["new"] * 3 and all(r["approved"] and r["title"] for r in preview["rows"])
    res = await a.post(f"/api/batches/{batch}/another-shop", json={"connection_id": str(one)})
    assert res.status_code == 200 and len(res.json()["jobs"]) == 3, res.text
    await _run_drafts(w, [j["job_id"] for j in res.json()["jobs"]])
    status = {s["shop_id"]: s for s in (await a.get(f"/api/batches/{batch}/shops")).json()["shops"]}
    assert status[str(one)]["drafted"] == 3
    await w["engine"].dispose()


async def test_production_partner_blocks_only_when_etsy_would_refuse(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _batch_in_shop_a(tmp_path, test_settings, monkeypatch, source="manual")
    a, batch, two = w["client"], w["batch"], w["shops"][1]
    async with w["sm"]() as s:
        profile = await s.get(ListingProfile, w["profile"])
        profile.cached_payload = {**profile.cached_payload, "production_partner_ids": [], "who_made": "i_did"}
        await s.commit()
    rows = (await a.post(f"/api/batches/{batch}/another-shop/preview", json={"connection_id": str(two)})).json()["rows"]
    # Print-on-demand looking, the seller made it: a quiet note; it is sent.
    assert {r["partner"] for r in rows} == {"note"} and {r["state"] for r in rows} == {"new"}
    async with w["sm"]() as s:
        profile = await s.get(ListingProfile, w["profile"])
        profile.cached_payload = {**profile.cached_payload, "who_made": "someone_else"}
        from app.db.models import ProfileShopLink

        for link in (await s.execute(select(ProfileShopLink))).scalars():
            link.production_partner_ids = []
        await s.commit()
    preview = (await a.post(f"/api/batches/{batch}/another-shop/preview", json={"connection_id": str(two)})).json()
    assert {r["partner"] for r in preview["rows"]} == {"required"} and preview["total"] == 0
    assert all("set a production partner on profile \"Standard Tee\"" in r["reason"] for r in preview["rows"]), preview["rows"]
    await w["engine"].dispose()


async def test_generate_never_takes_a_listing_with_text_anywhere(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _batch_in_shop_a(tmp_path, test_settings, monkeypatch, source="manual", sent=False)
    async with w["sm"]() as s:
        content = await s.get(GeneratedContent, w["contents"][0])
        profile = await s.get(ListingProfile, w["profile"])
        content.title, content.tags, content.description = "", [], manual.description_for(profile, "")
        assert manual.is_empty(content, profile)  # truly empty: Generate may write it
        shop_listing.set_override(content, w["shops"][1], title="Typed For Shop B")
        assert not manual.is_empty(content, profile)  # text typed for a shop: never written over
    await w["engine"].dispose()


def test_approval_per_shop_rule() -> None:
    content = GeneratedContent(title="T", tags=["a"], description="d", approved=True, approved_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    shop = "11111111-1111-1111-1111-111111111111"
    assert shop_listing.approved_in(content, shop)  # approved once: every shop
    shop_listing.set_override(content, shop, sku="X1")
    assert shop_listing.approved_in(content, shop)  # a SKU is not its text
    shop_listing.set_override(content, shop, title="New")
    assert not shop_listing.approved_in(content, shop) and shop_listing.edited_after_approval(content, shop)
    shop_listing.approve_in(content, shop)
    assert shop_listing.approved_in(content, shop)
    shop_listing.set_override(content, shop, title=None)  # back to the listing's text
    assert shop_listing.approved_in(content, shop) and shop_listing.TEXT_EDITED_AT not in shop_listing.entry(content, shop)
    content.approved = False
    assert not shop_listing.approved_in(content, shop)


async def test_workspace_check_reads_and_says_where_each_comes_from(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _batch_in_shop_a(tmp_path, test_settings, monkeypatch, source="manual")
    a, batch, two = w["client"], w["batch"], w["shops"][1]
    await a.put(f"/api/batches/{batch}/shops/{two}/texts", json={"texts": {str(w["contents"][1]): {"title": "Own Title For B"}}})
    out = await workspace_check.run(w["sm"], "pcs417", "Shop 02")
    assert "listing D00" in out and "13 from the listing's" in out and "from the listing's" in out
    assert "approval for Shop 02: approved" in out and "can be sent: yes" in out and "production partner:" in out
    assert "\"Own Title For B\"" in out and "from the shop's own" in out and "edited for Shop 02 after approval" in out
    assert "summary:" in out
    by_id = await workspace_check.run(w["sm"], str(batch), "Shop 01")
    assert "already in Shop 01 (published)" in by_id
    assert "no shop called" in await workspace_check.run(w["sm"], str(batch), "Nowhere")
    assert "no batch" in await workspace_check.run(w["sm"], "NOSUCHSKU", "Shop 02")
    async with w["sm"]() as s:  # read only: nothing changed
        assert (await s.get(GeneratedContent, w["contents"][0])).shop_overrides is None
    await w["engine"].dispose()


async def test_designs_written_in_another_batch_are_pointed_to(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """The likely production case: the same designs uploaded again as a new batch and "Write
    them myself" pressed, expecting the first batch's text. The new batch says where they are."""
    from app.db.models import UploadBatch, UploadBatchStatus

    w = await _batch_in_shop_a(tmp_path, test_settings, monkeypatch, source="manual")
    async with w["sm"]() as s:
        again = UploadBatch(tenant_id=w["tenant"], status=UploadBatchStatus.ready, file_count=2, name="ILC upload")
        s.add(again)
        await s.flush()
        for sku in SKUS[:2]:
            asset = Asset(batch_id=again.id, tenant_id=w["tenant"], original_filename=f"{sku}/front.png", parsed_sku=sku,
                          storage_key="p/00.png", processed_key="p/00.png", status=AssetStatus.processed,
                          rank=1, group_key=sku)
            s.add(asset)
            await s.flush()
            s.add(GeneratedContent(tenant_id=w["tenant"], batch_id=again.id, asset_id=asset.id, approved=False, source="manual",
                                   listing_profile_id=w["profile"], title="", tags=[], description=""))
        await s.commit()
        again_id = again.id
    out = (await w["client"].get(f"/api/batches/{again_id}/shops")).json()["written_elsewhere"]
    assert len(out) == 1 and out[0]["batch_id"] == str(w["batch"]) and out[0]["count"] == 2
    assert out[0]["skus"] == SKUS[:2] and out[0]["shops"] == ["Shop 01"]
    assert (await w["client"].get(f"/api/batches/{w['batch']}/shops")).json()["written_elsewhere"] == []
    await w["engine"].dispose()
