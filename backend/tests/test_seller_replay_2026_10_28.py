"""Replay of the production sequence of 2026-10-28 (three shops OPA, PCS, ILC; hand-written
listings): text typed on the review card while one shop was picked, drafts in shop A, one
listing's text edited for B, then the workspace on C: SKU prefix PCS -> ILC for C, the send
summary, Approve all, the summary again, send.

Before: the typed text was stored as shop A's own and the listing's own title and tags stayed
empty, so every other screen showed them empty and C's rows could not go ("no title yet");
Approve all skipped listings already approved, so "approve again" for a shop stayed; every
SKU editor POSTed /sku/etsy on each render. Now: the listing's empty text takes what was typed,
Approve all approves for the shop in view, the summary offers to approve what is stopped only
by approval, nothing asks to update SKUs on Etsy, and C gets its drafts with A untouched.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import AuditLog, GeneratedContent, ListingPublication
from app.ops import listing_text
from app.pipeline import shop_listing
from tests.test_another_shop import _skus_of
from tests.test_end_to_end import TAGS, _run_drafts, _send_drafts, _world

SKUS = ["PCS417", "PCS418", "PCS419"]
TYPED = ["Retro Camping Tee For Hikers {i}", "Vintage Sunset Shirt Outdoor {i}", "Mountain Graphic Tee Gift {i}"]


async def _hand_written_in_a(tmp_path, test_settings: Settings, monkeypatch) -> dict:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=3, listings=3)
    from app.db.models import Asset

    async with w["sm"]() as s:
        for asset in (await s.execute(select(Asset))).scalars():
            asset.parsed_sku = SKUS[int(asset.group_key[1:])]
        for content in (await s.execute(select(GeneratedContent))).scalars():
            content.source, content.title, content.tags, content.approved = "manual", None, [], False
        await s.commit()
    return w


async def test_the_sequence_of_2026_10_28(tmp_path, test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    w = await _hand_written_in_a(tmp_path, test_settings, monkeypatch)
    a, batch, (shop_a, shop_b, shop_c) = w["client"], w["batch"], w["shops"]
    c = [str(x) for x in w["contents"]]

    # 1. Typed on the review card while shop A was picked (what the old page sent: ``shop``).
    for i, cid in enumerate(c):
        res = await a.patch(f"/api/content/{cid}", json={"title": TYPED[i].format(i=i), "tags": TAGS, "shop": str(shop_a)})
        assert res.status_code == 200, res.text
    async with w["sm"]() as s:
        rows = {str(x.id): x for x in (await s.execute(select(GeneratedContent))).scalars()}
    for i, cid in enumerate(c):  # the listing's own text holds what was typed; A keeps its copy
        assert rows[cid].title == TYPED[i].format(i=i) and rows[cid].tags == TAGS
        assert shop_listing.text_in(rows[cid], shop_a)["title"] == TYPED[i].format(i=i)

    # 2. Approve all with A in view, drafts in A.
    approved = (await a.post(f"/api/batches/{batch}/approve-all", json={"connection_id": str(shop_a)})).json()
    assert approved["approved"] == 3 and approved["skipped"] == [], approved
    await _run_drafts(w, await _send_drafts(w))
    first = {k: dict(v) for k, v in w["etsy"].listings.items()}
    assert len(first) == 3 and all(v["title"] in [t.format(i=i) for i, t in enumerate(TYPED)] for v in first.values())

    # 3. One listing's text edited for B (on Etsy in A, not in B).
    b_title = "Juniper Retro Camping Tee For Hikers"
    await a.put(f"/api/batches/{batch}/shops/{shop_b}/texts", json={"texts": {c[0]: {"title": b_title}}})

    # 4. Text visible everywhere: no shop, A, B, C.
    cards = (await a.get(f"/api/batches/{batch}/content")).json()
    assert all(x["title"] and len(x["tags"]) == 13 for x in cards), cards
    for shop in (shop_b, shop_c):
        rows_there = (await a.get(f"/api/batches/{batch}/shops/{shop}/rows")).json()["rows"]
        assert all(r["title"] and len(r["tags"]) == 13 for r in rows_there), rows_there
    async with w["sm"]() as s:
        from app.db.models import Tenant

        email = (await s.get(Tenant, w["tenant"])).email
    report = await listing_text.run(w["sm"], email, batch=str(batch))
    assert "0 shown with an EMPTY title or no tags somewhere" in report, report
    assert b_title[:40] in report and "approve again for this shop" in report

    # 5. The workspace on C: one listing edited for C after approval, SKU prefix PCS -> ILC.
    c_title = "Ironwood Mountain Graphic Tee Gift"
    await a.put(f"/api/batches/{batch}/shops/{shop_c}/texts", json={"texts": {c[2]: {"title": c_title}}})
    new = {cid: sku.replace("PCS", "ILC") for cid, sku in zip(c, SKUS)}
    assert (await a.put(f"/api/batches/{batch}/shops/{shop_c}/skus", json={"skus": new})).json()["saved"] == 3
    async with w["sm"]() as s:  # a SKU-only change never needs approval again
        rows = {str(x.id): x for x in (await s.execute(select(GeneratedContent))).scalars()}
    assert not shop_listing.edited_after_approval(rows[c[0]], shop_c)
    assert not shop_listing.edited_after_approval(rows[c[1]], shop_c)

    query = "&".join(f"content_ids={x}" for x in c)
    summary = (await a.get(f"/api/batches/{batch}/shops/{shop_c}/rows?{query}")).json()
    stopped = [r for r in summary["rows"] if r["state"] == "cannot"]
    assert [r["content_id"] for r in stopped] == [c[2]] and stopped[0]["approval_note"].startswith("Edited for")
    assert summary["total"] == 2  # never a dead end: the one stopped is said, with why

    # 6. Approve all with C in view approves it for C; the summary again: all three go.
    approved = (await a.post(f"/api/batches/{batch}/approve-all", json={"connection_id": str(shop_c)})).json()
    assert approved["approved_for_shop"] == 1 and approved["skipped"] == [], approved
    summary = (await a.get(f"/api/batches/{batch}/shops/{shop_c}/rows?{query}")).json()
    assert summary["total"] == 3 and summary["refused"] == [] and summary["disabled"] is None, summary

    # 7. Send to C: drafts in C with C's SKUs and text; A untouched; nothing asked Etsy to update a SKU.
    res = await a.post(f"/api/batches/{batch}/another-shop", json={"connection_id": str(shop_c), "content_ids": c})
    assert res.status_code == 200, res.text
    await _run_drafts(w, [j["job_id"] for j in res.json()["jobs"]])
    async with w["sm"]() as s:
        pubs = {str(p.content_id): p for p in (await s.execute(select(ListingPublication).where(
            ListingPublication.connection_id == shop_c))).scalars()}
        asked = (await s.execute(select(AuditLog).where(AuditLog.action == "listing.sku_etsy_requested"))).scalars().all()
    assert len(pubs) == 3
    for i, cid in enumerate(c):
        listing = w["etsy"].listings[pubs[cid].etsy_listing_id]
        assert _skus_of(listing) == {new[cid]}
        assert listing["title"] == (c_title if i == 2 else TYPED[i].format(i=i))
    assert {k: v for k, v in w["etsy"].listings.items() if k in first} == first
    assert not asked and not any(call[0] == "update_sku_on_etsy" for call in w["queue"].calls)
    await w["engine"].dispose()


async def test_typed_text_is_never_lost_or_overwritten_by_a_shop_edit(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """Text typed for a shop fills only an EMPTY listing field; the listing's own text is never
    overwritten by a shop's, and a stale save is refused (409), not written over."""
    w = await _hand_written_in_a(tmp_path, test_settings, monkeypatch)
    a, (shop_a, shop_b, _) = w["client"], w["shops"]
    cid = str(w["contents"][0])
    await a.patch(f"/api/content/{cid}", json={"title": "Its own title here", "tags": TAGS})
    await a.patch(f"/api/content/{cid}", json={"title": "For shop A only", "shop": str(shop_a)})
    stale = await a.patch(f"/api/content/{cid}", json={"title": "Older tab", "shop": str(shop_b),
                                                        "base": {"title": "something else"}})
    assert stale.status_code == 409
    async with w["sm"]() as s:
        row = await s.get(GeneratedContent, w["contents"][0])
    assert row.title == "Its own title here" and shop_listing.text_in(row, shop_a)["title"] == "For shop A only"
    assert shop_listing.text_in(row, shop_b) == {}
    await w["engine"].dispose()


async def test_send_summaries_and_page_errors_are_recorded_without_personal_data(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """Every send summary's result (counts per reason) and every reported page error or failed
    action is recorded; ``seller-events`` reads them; another account sees nothing."""
    from datetime import datetime, timedelta, timezone

    from app.db.models import SellerEvent, Tenant
    from app.ops import seller_events

    w = await _hand_written_in_a(tmp_path, test_settings, monkeypatch)
    a, batch, (shop_a, _, _) = w["client"], w["batch"], w["shops"]
    c = [str(x) for x in w["contents"]]
    since = datetime.now(timezone.utc) - timedelta(minutes=1)
    await a.patch(f"/api/content/{c[0]}", json={"title": "Retro Camping Tee For Hikers", "tags": TAGS})
    await a.post(f"/api/content/{c[0]}/approve", json={"approved": True})
    query = "&".join(f"content_ids={x}" for x in c)
    summary = (await a.get(f"/api/batches/{batch}/shops/{shop_a}/rows?{query}")).json()
    assert summary["total"] == 1
    await a.get(f"/api/batches/{batch}/shops/{shop_a}/rows")  # the rows alone: not a summary, not recorded
    for kind, message in (("action_failed", "409: the SKU is taken, write to me at seller@example.com"),
                          ("frontend_error", "Cannot read properties of undefined")):
        res = await a.post("/api/events", json={"kind": kind, "page": f"/batches/{batch}?shop=x", "action": "PUT /api/x",
                                                "message": message})
        assert res.status_code == 204
    async with w["sm"]() as s:
        events = list((await s.execute(select(SellerEvent).order_by(SellerEvent.at))).scalars())
        email = (await s.get(Tenant, w["tenant"])).email
    assert [e.kind for e in events] == ["send_summary", "action_failed", "frontend_error"]
    assert events[0].counts == {"chosen": 3, "going": 1, "text_etsy_would_refuse": 2}  # their titles are empty
    assert events[1].page == f"/batches/{batch}" and "[email]" in events[1].message and "@" not in events[1].message
    report = await seller_events.run(w["sm"], email, since)
    assert "send summaries: 1 · listings chosen 3 · could go 1" in report and "could not go, text etsy would refuse: 2" in report
    assert "failed actions: 1" in report and "frontend errors: 1" in report
    # Flood-safe: at most 30 a minute are kept.
    for _ in range(40):
        await a.post("/api/events", json={"kind": "frontend_error", "page": "/x", "message": "boom"})
    async with w["sm"]() as s:
        assert len((await s.execute(select(SellerEvent))).scalars().all()) <= 33
    await w["engine"].dispose()
