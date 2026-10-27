"""A listing's own title, tags and description in one shop (seller request, 2026-10-27): set in
"Publish in another shop" (its text step) or on the review card while that shop is shown.

The shop's draft uses its own text, every other shop the listing's; shops it is already in never
change; the text is held to the one rule set; "Keep as it is" and skipping the step; "Rewrite for
this shop" under the allowance's rewrite rule; the card per shop; publish-report --all with the
same date filter as one account; another account 404.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.api import another_shop
from app.core.config import Settings
from app.db.models import GeneratedContent, ListingPublication, Tenant, UploadBatch, UploadBatchStatus
from app.ops import publish_report
from app.pipeline import shop_listing
from tests.auth_support import authenticate, open_session
from tests.test_another_shop import _allowance, _first_shop_rows, _published_in_first_shop
from tests.test_end_to_end import TAGS, _run_drafts

SHOP_TITLE = "Juniper Retro Sunset Tee, Vintage Mountain Shirt"
SHOP_TAGS = ["juniper tee", "retro sunset", "mountain shirt"]


async def test_the_shops_own_text_goes_to_its_draft_and_nowhere_else(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch)
    a, batch, two = w["client"], w["batch"], w["shops"][1]
    c = [str(x) for x in w["contents"]]
    first = await _first_shop_rows(w)
    body = {"connection_id": str(two), "texts": {c[0]: {"title": SHOP_TITLE, "tags": SHOP_TAGS}}}
    preview = (await a.post(f"/api/batches/{batch}/another-shop/preview", json=body)).json()
    row = preview["rows"][0]
    assert row["title"] == SHOP_TITLE and row["tags"] == SHOP_TAGS and row["own_text"] is True
    assert row["errors"] == [] and row["warnings"], row  # 3 tags: a note, never a stop
    assert preview["rows"][1]["own_text"] is False and preview["disabled"] is None
    res = await a.post(f"/api/batches/{batch}/another-shop", json=body)
    assert res.status_code == 200, res.text
    await _run_drafts(w, [j["job_id"] for j in res.json()["jobs"]])
    async with w["sm"]() as s:
        pubs = {str(p.content_id): p for p in (await s.execute(select(ListingPublication).where(
            ListingPublication.connection_id == two))).scalars()}
        contents = {str(x.id): x for x in (await s.execute(select(GeneratedContent))).scalars()}
    mine = w["etsy"].listings[pubs[c[0]].etsy_listing_id]
    assert mine["title"] == SHOP_TITLE
    other = w["etsy"].listings[pubs[c[1]].etsy_listing_id]
    assert other["title"] == contents[c[1]].title  # no text of its own there: the listing's
    assert contents[c[0]].title != SHOP_TITLE and contents[c[0]].tags == TAGS  # the listing's own text stays
    assert await _first_shop_rows(w) == first
    assert {k: v for k, v in w["etsy"].listings.items() if v["shop"] == 900} == w["first"]
    # The draft's tags: the shop's own (the fake keeps the create form's fields).
    async with w["sm"]() as s:
        from app.db.models import ContentVersion

        version = (await s.execute(select(ContentVersion).where(
            ContentVersion.publication_id == pubs[c[0]].id))).scalar_one()
    assert version.tags == SHOP_TAGS and version.title == SHOP_TITLE
    await w["engine"].dispose()


async def test_text_etsy_would_refuse_stops_only_that_listing_and_keep_and_skip(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch)
    a, batch, two = w["client"], w["batch"], w["shops"][1]
    c = [str(x) for x in w["contents"]]
    bad = {"connection_id": str(two), "texts": {c[1]: {"tags": ["teacher’s gift", "nurse tee"]}}}
    preview = (await a.post(f"/api/batches/{batch}/another-shop/preview", json=bad)).json()
    assert preview["text_problems"] == 1 and preview["disabled"].startswith("Fix the text marked below first")
    assert any("straight apostrophe" in e for e in preview["rows"][1]["errors"]), preview["rows"][1]
    assert (await a.post(f"/api/batches/{batch}/another-shop", json=bad)).status_code == 409
    async with w["sm"]() as s:
        assert all(x.shop_overrides is None for x in (await s.execute(select(GeneratedContent))).scalars())
    # Skipped: every listing goes with its own text.
    skipped = (await a.post(f"/api/batches/{batch}/another-shop/preview", json={"connection_id": str(two)})).json()
    assert not any(r["own_text"] for r in skipped["rows"]) and skipped["disabled"] is None
    # "Keep as it is" drops a shop's own text kept earlier (here: from the card).
    saved = await a.patch(f"/api/content/{c[2]}", json={"title": SHOP_TITLE, "shop": str(two)})
    assert saved.status_code == 200, saved.text
    kept = (await a.post(f"/api/batches/{batch}/another-shop/preview",
                         json={"connection_id": str(two), "texts": {c[2]: {"reset": True}}})).json()
    assert kept["rows"][2]["own_text"] is False and kept["rows"][2]["title"] != SHOP_TITLE
    # A field set back to the listing's own is not kept as the shop's.
    async with w["sm"]() as s:
        listing_title = (await s.get(GeneratedContent, w["contents"][0])).title
    same = (await a.post(f"/api/batches/{batch}/another-shop/preview",
                         json={"connection_id": str(two), "texts": {c[0]: {"title": listing_title}}})).json()
    assert same["rows"][0]["own_text"] is False
    await w["engine"].dispose()


async def test_rewrite_for_the_shop_follows_the_rewrite_rule(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch)
    a, batch, (one, two) = w["client"], w["batch"], w["shops"]
    c = [str(x) for x in w["contents"]]
    calls: list[str] = []

    async def fake_write(session, tenant, content, profile, storage):  # noqa: ANN001, ANN202
        calls.append(profile.name)
        return f"Rewritten {len(calls)} Retro Tee", ["rewritten tee", "retro shirt"], "Rewritten body."

    monkeypatch.setattr(another_shop, "_write", fake_write)
    preview = (await a.post(f"/api/batches/{batch}/another-shop/preview", json={"connection_id": str(two)})).json()
    assert preview["rows"][0]["free_rewrites_left"] == 3 and "first 3 of each design" in preview["rewrite_rule"]
    used = await _allowance(w)
    for n in range(3):
        res = (await a.post(f"/api/batches/{batch}/another-shop/rewrite",
                            json={"connection_id": str(two), "content_ids": [c[0]], "profile_id": str(w["pcs"])})).json()
        assert res["rows"][0]["counted"] is False and res["rows"][0]["title"] == f"Rewritten {n + 1} Retro Tee", res
    assert await _allowance(w) == used  # three free rewrites of the design
    fourth = (await a.post(f"/api/batches/{batch}/another-shop/rewrite",
                           json={"connection_id": str(two), "content_ids": [c[0]]})).json()
    assert fourth["rows"][0]["counted"] is True and "counted" in fourth["message"]
    assert await _allowance(w) == used + 1
    assert calls == ["REGULAR - PCS"] * 4  # the shop's profile, kept for the later rewrite too
    async with w["sm"]() as s:
        content = await s.get(GeneratedContent, w["contents"][0])
    assert shop_listing.text_in(content, two)["title"] == "Rewritten 4 Retro Tee"
    assert content.title != "Rewritten 4 Retro Tee"  # the listing's own text stays
    after = (await a.post(f"/api/batches/{batch}/another-shop/preview", json={"connection_id": str(two)})).json()
    assert after["rows"][0]["free_rewrites_left"] == 0 and after["rows"][0]["own_text"] is True
    # A shop it is already in is never rewritten.
    there = (await a.post(f"/api/batches/{batch}/another-shop/rewrite",
                          json={"connection_id": str(one), "content_ids": [c[1]]})).json()
    assert there["rows"][0]["reason"].startswith("already in") and len(calls) == 4
    await w["engine"].dispose()


async def test_the_card_edits_the_shown_shops_text_only(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch)
    a, (one, two) = w["client"], w["shops"]
    cid = str(w["contents"][0])
    async with w["sm"]() as s:
        before = await s.get(GeneratedContent, w["contents"][0])
        listing = (before.title, list(before.tags), before.description)
    res = await a.patch(f"/api/content/{cid}", json={"title": SHOP_TITLE, "tags": SHOP_TAGS, "shop": str(two),
                                                     "base": {"title": listing[0], "tags": listing[1]}})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["content"]["title"] == SHOP_TITLE and out["content"]["tags"] == SHOP_TAGS
    assert out["content"]["shop_texts"][str(two)]["title"] == SHOP_TITLE
    assert out["validation"]["valid"] is True
    async with w["sm"]() as s:
        after = await s.get(GeneratedContent, w["contents"][0])
    assert (after.title, list(after.tags), after.description) == listing  # the listing's own text stays
    # Another tab changed that shop's title meanwhile: not overwritten.
    stale = await a.patch(f"/api/content/{cid}", json={"title": "Mine", "shop": str(two), "base": {"title": listing[0]}})
    assert stale.status_code == 409
    # The shop it is already in: edited on Etsy, never here.
    there = await a.patch(f"/api/content/{cid}", json={"title": "No", "shop": str(one)})
    assert there.status_code == 409 and "already in" in there.json()["detail"]
    # No shop shown: the listing's own text, as before.
    plain = await a.patch(f"/api/content/{cid}", json={"title": listing[0] + " Plus"})
    assert plain.status_code == 200 and plain.json()["content"]["title"] == listing[0] + " Plus"
    await w["engine"].dispose()


async def test_report_all_uses_the_same_date_filter_as_one_account(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """Listings written before --since but published after it: in both reports."""
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch)
    old = datetime.now(timezone.utc) - timedelta(days=40)
    async with w["sm"]() as s:
        for content in (await s.execute(select(GeneratedContent))).scalars():
            content.created_at = old
        for pub in (await s.execute(select(ListingPublication))).scalars():
            pub.created_at = old
        from app.db.models import Job

        for job in (await s.execute(select(Job))).scalars():
            job.created_at = old
        await s.commit()
    since = (datetime.now(timezone.utc) - timedelta(days=3)).date()
    one = await publish_report.report(w["sm"], "seller@example.com", since)
    everyone = await publish_report.report_all(w["sm"], since)
    assert "published" in one
    assert "== seller@example.com" in everyone and ": 3 listing(s)" in everyone, everyone
    await w["engine"].dispose()


async def test_another_account_cannot_reach_the_text(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _published_in_first_shop(tmp_path, test_settings, monkeypatch)
    a, batch, two = w["client"], w["batch"], w["shops"][1]
    cid = str(w["contents"][0])
    async with w["sm"]() as s:
        other = Tenant(email="other@example.com", password_hash="!")
        s.add(other)
        await s.flush()
        own = UploadBatch(tenant_id=other.id, status=UploadBatchStatus.ready, file_count=0)
        s.add(own)
        await s.commit()
        other_id, own_batch = other.id, own.id
    authenticate(a, await open_session(w["ctx"]["redis"], other_id))
    assert (await a.patch(f"/api/content/{cid}", json={"title": "x", "shop": str(two)})).status_code == 404
    assert (await a.post(f"/api/batches/{batch}/another-shop/rewrite",
                         json={"connection_id": str(two), "content_ids": [cid]})).status_code == 404
    assert (await a.post(f"/api/batches/{own_batch}/another-shop/rewrite",
                         json={"connection_id": str(two), "content_ids": [cid]})).status_code == 404
    authenticate(a, await open_session(w["ctx"]["redis"], w["tenant"]))
    async with w["sm"]() as s:
        assert all(c.shop_overrides is None for c in (await s.execute(select(GeneratedContent))).scalars())
    await w["engine"].dispose()
