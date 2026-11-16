"""The review page's two regressions of 14 November 2026 (hotfix/review-regressions).

1. **A section chosen on a card.** Its id goes all the way: saved, read back on the card as the seller's
   choice, and written on the draft. "No section, as you chose" is stored only when the seller picked
   "No section" (the value "none"); a null (a value lost on its way) is refused, never saved as that.
2. **Approve.** One listing (its card's button), the listing the bar names (approve-all with one id),
   the selected ones and all of them: each approves what it can and says, per listing, why it could not.
   A brand in a form the filter lets through is a warning and does not stop approval; a brand the
   account's filter stops does, with its reason, exactly as before (compliance is unchanged).
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.api.item_options import NOT_CHOSEN
from app.db.models import AuditLog, EtsyConnection, GeneratedContent, Job, JobType
from app.pipeline import item_options as opts
from tests.support import VALID_TITLE
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_item_options import Shop, _draft, _world

TAGS = [f"tag{i}" for i in range(13)]


def _shop(out: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in out["shops"] if s["shop_name"] == name)


# --- 1. the section -------------------------------------------------------------------------------------


async def test_a_chosen_section_is_saved_read_back_and_written_on_the_draft(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    url = f"/api/content/{w['teacher']}/options"
    # The suggestion first (the design's theme): not the seller's choice.
    before = _shop((await client.get(url)).json(), "Shop A")
    assert (before["selected_title"], before["source"]) == ("Teacher Tees", "suggested")
    # The seller picks the other one: the section's own id, as the card's select sends it.
    out = (await client.put(url, json={"sections": {str(w["a"]): 10}})).json()
    assert (_shop(out, "Shop A")["selected_id"], _shop(out, "Shop A")["selected_title"], _shop(out, "Shop A")["source"]) == (10, "Nurse Collection", "seller")
    # Read again (a reload, another request): still the seller's, still that section.
    again = _shop((await client.get(url)).json(), "Shop A")
    assert (again["selected_id"], again["selected_title"], again["source"], again["reason"]) == (10, "Nurse Collection", "seller", "")
    # What the draft is built from, and what Etsy is sent.
    async with client.sm() as s:  # type: ignore[attr-defined]
        content = await s.get(GeneratedContent, w["teacher"])
        assert opts.section_choice(content.item_options, w["a"]) == {"id": 10, "title": "Nurse Collection", "explicit": True}
        content.title, content.tags, content.approved = VALID_TITLE, TAGS, True
        job = Job(tenant_id=content.tenant_id, connection_id=w["a"], type=JobType.create_draft, payload={}, batch_id=content.batch_id)
        s.add(job)
        await s.commit()
        choice, job_id, tenant_id = opts.section_choice(content.item_options, w["a"]), job.id, content.tenant_id
    shop = Shop()
    result = await _draft(client.sm, (tenant_id, w["a"], w["teacher"], job_id), shop, section_choice=choice)  # type: ignore[attr-defined]
    assert shop.last_listing["shop_section_id"] == 10 and result.section_id == 10
    assert "create_section" not in shop.calls


async def test_no_section_as_you_chose_only_when_the_seller_picked_no_section(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    url = f"/api/content/{w['teacher']}/options"
    # A null is a value that was lost on its way: refused, and nothing is stored as the seller's choice.
    refused = await client.put(url, json={"sections": {str(w["a"]): None}})
    assert refused.status_code == 422 and refused.json()["detail"] == NOT_CHOSEN
    now = _shop((await client.get(url)).json(), "Shop A")
    assert now["source"] == "suggested" and "as you chose" not in now["reason"]
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.get(GeneratedContent, w["teacher"])).item_options is None
    # "No section" picked: the select's own value.
    out = _shop((await client.put(url, json={"sections": {str(w["a"]): "none"}})).json(), "Shop A")
    assert (out["selected_id"], out["source"], out["reason"]) == (None, "seller", "no section, as you chose")
    async with client.sm() as s:  # type: ignore[attr-defined]
        content = await s.get(GeneratedContent, w["teacher"])
        assert opts.section_choice(content.item_options, w["a"]) == {"id": None, "title": None, "explicit": True}
    # A section after that replaces it; the suggestion again clears the choice.
    out = _shop((await client.put(url, json={"sections": {str(w["a"]): 11}})).json(), "Shop A")
    assert (out["selected_title"], out["source"]) == ("Teacher Tees", "seller")
    out = _shop((await client.put(url, json={"sections": {str(w["a"]): "default"}})).json(), "Shop A")
    assert out["source"] == "suggested"


async def test_set_for_selected_takes_none_for_no_section_and_refuses_a_null(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    url = f"/api/batches/{w['batch']}/options"
    body = {"content_ids": [str(w["nurse"]), str(w["teacher"])], "section_connection_id": str(w["a"])}
    lost = (await client.post(url, json={**body, "section_id": None})).json()
    assert lost["updated"] == 0 and {s["reason"] for s in lost["skipped"]} == {NOT_CHOSEN}
    done = (await client.post(url, json={**body, "section_id": "none"})).json()
    assert done["updated"] == 2 and done["skipped"] == []
    out = _shop((await client.get(f"/api/content/{w['nurse']}/options")).json(), "Shop A")
    assert out["reason"] == "no section, as you chose"


# --- 2. Approve -------------------------------------------------------------------------------------------


async def _listings(client: AsyncClient) -> dict[str, Any]:  # noqa: F811
    """Two listings nobody approved: one names a brand in a form the filter lets through (a warning), with a
    section set; the other names a brand the account's filter stops."""
    w = await _world(client)
    async with client.sm() as s:  # type: ignore[attr-defined]
        warned = await s.get(GeneratedContent, w["nurse"])
        warned.title, warned.tags, warned.description = "DisneyWorld Nurse Trip Shirt", TAGS, "A soft cotton shirt."
        stopped = await s.get(GeneratedContent, w["teacher"])
        stopped.title, stopped.tags, stopped.description = "Mickey Ears Teacher Shirt", TAGS, "A soft cotton shirt."
        await s.commit()
    await client.put(f"/api/content/{w['nurse']}/options", json={"sections": {str(w["a"]): 11}})
    return w


async def _approved(client: AsyncClient, w: dict[str, Any]) -> dict[str, bool]:  # noqa: F811
    rows = (await client.get(f"/api/batches/{w['batch']}/content")).json()
    return {r["id"]: r["approved"] for r in rows}


async def test_one_listing_its_cards_button_approves_or_says_why(client: AsyncClient) -> None:  # noqa: F811
    w = await _listings(client)
    # A brand warning (and a section set) does not stop approval.
    ok = await client.post(f"/api/content/{w['nurse']}/approve", json={"approved": True})
    assert ok.status_code == 200 and ok.json()["content"]["approved"] is True
    assert any("Disney" in line for line in ok.json()["validation"]["brand_warnings"])
    # A brand the account's filter stops: refused with the reason, as before.
    no = await client.post(f"/api/content/{w['teacher']}/approve", json={"approved": True})
    assert no.status_code == 422
    assert no.json()["detail"] == {"message": "cannot approve invalid content", "errors": ["Remove: Mickey (title)"]}
    assert await _approved(client, w) == {str(w["nurse"]): True, str(w["teacher"]): False}


async def test_the_bars_approve_this_listing_is_approve_all_with_one_id(client: AsyncClient) -> None:  # noqa: F811
    w = await _listings(client)
    url = f"/api/batches/{w['batch']}/approve-all"
    one = (await client.post(url, json={"content_ids": [str(w["nurse"])]})).json()
    assert (one["approved"], one["skipped"]) == (1, [])
    assert await _approved(client, w) == {str(w["nurse"]): True, str(w["teacher"]): False}  # only the one named
    blocked = (await client.post(url, json={"content_ids": [str(w["teacher"])]})).json()
    assert blocked["approved"] == 0 and len(blocked["skipped"]) == 1
    skipped = blocked["skipped"][0]
    # The reason its card shows, in full; the short form names the kind.
    assert (skipped["content_id"], skipped["reason"]) == (str(w["teacher"]), "Remove: Mickey (title)")
    assert skipped["reasons"] == ["has a trademark to remove (see its card)"]
    assert await _approved(client, w) == {str(w["nurse"]): True, str(w["teacher"]): False}


async def test_selected_and_all_approve_what_they_can_and_report_the_rest(client: AsyncClient) -> None:  # noqa: F811
    w = await _listings(client)
    url = f"/api/batches/{w['batch']}/approve-all"
    selected = (await client.post(url, json={"content_ids": [str(w["nurse"]), str(w["teacher"])]})).json()
    assert selected["approved"] == 1 and [s["content_id"] for s in selected["skipped"]] == [str(w["teacher"])]
    assert selected["summary"] and "trademark" in selected["summary"]
    # All: the one already approved is counted as that, the blocked one is still reported, nothing else changes.
    everything = (await client.post(url)).json()
    assert (everything["approved"], everything["already_approved"]) == (0, 1)
    assert [s["reason"] for s in everything["skipped"]] == ["Remove: Mickey (title)"]
    # Fixed on its card, it is approved by the same call.
    async with client.sm() as s:  # type: ignore[attr-defined]
        (await s.get(GeneratedContent, w["teacher"])).title = "Mouse Ears Teacher Shirt"
        await s.commit()
    assert (await client.post(url)).json()["approved"] == 1
    assert await _approved(client, w) == {str(w["nurse"]): True, str(w["teacher"]): True}


async def test_approval_keeps_the_section_and_nothing_goes_to_etsy(client: AsyncClient) -> None:  # noqa: F811
    w = await _listings(client)
    await client.post(f"/api/batches/{w['batch']}/approve-all", json={"content_ids": [str(w["nurse"])]})
    out = _shop((await client.get(f"/api/content/{w['nurse']}/options")).json(), "Shop A")
    assert (out["selected_title"], out["source"]) == ("Teacher Tees", "seller")
    # Approving queues nothing for Etsy: sending stays the seller's own step.
    assert not [c for c in w["queue"].calls if c[0] in ("run_publish_job", "run_publish_live_job")]
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.get(EtsyConnection, w["a"])).sections is not None
        assert not list((await s.execute(select(AuditLog).where(AuditLog.action.like("listing.sent%")))).scalars())


# --- 3. "Keep it, I know the risk" on the review card ----------------------------------------------------


async def test_keep_it_records_the_decision_and_approves_and_the_warning_stays(client: AsyncClient) -> None:  # noqa: F811
    from app.compliance import trademarks
    from app.compliance.check import listing_problem

    w = await _listings(client)
    listed = trademarks.read_terms(trademarks.DEFAULT_LIST_PATH)
    kept = await client.post(f"/api/content/{w['teacher']}/brands/keep", json={})
    assert kept.status_code == 200
    out = kept.json()
    assert out["content"]["approved"] is True and out["content"]["kept_brands"] == ["Mickey"]
    assert out["content"]["title"] == "Mickey Ears Teacher Shirt"  # never edited for the seller
    # The warning is not hidden: the name is warned about from now on, as with the filter off.
    assert out["validation"]["valid"] is True
    assert out["validation"]["brand_warnings"] == [trademarks.BRAND_WARNING.format(brand="Mickey")]
    assert trademarks.read_terms(trademarks.DEFAULT_LIST_PATH) == listed  # the list is not changed
    async with client.sm() as s:  # type: ignore[attr-defined]
        content = await s.get(GeneratedContent, w["teacher"])
        assert content.attributes[trademarks.KEPT] == ["Mickey"] and content.attributes["vision"]  # nothing else lost
        row = (await s.execute(select(AuditLog).where(AuditLog.action == "listing.brand_kept"))).scalars().one()
        assert row.actor_tenant_id == content.tenant_id
        assert (row.details["content_id"], row.details["brands"], row.details["trigger"]) == (str(w["teacher"]), ["Mickey"], "seller_click")
        # Approved is not the end of it: the same decision lets it be sent (the draft and go-live gates).
        assert await listing_problem(s, content) is None
    # The card reads the same after a reload, and Approve all leaves it approved.
    rows = {r["id"]: r for r in (await client.get(f"/api/batches/{w['batch']}/content")).json()}
    assert rows[str(w["teacher"])]["kept_brands"] == ["Mickey"] and rows[str(w["teacher"])]["validation"]["valid"] is True
    assert (await client.post(f"/api/batches/{w['batch']}/approve-all")).json()["skipped"] == []


async def test_a_name_not_kept_still_stops_the_listing(client: AsyncClient) -> None:  # noqa: F811
    w = await _listings(client)
    await client.post(f"/api/content/{w['teacher']}/brands/keep", json={})
    # A new brand word on the same listing: stopped again, for that word only.
    changed = await client.patch(f"/api/content/{w['teacher']}", json={"title": "Mickey Ears Disney Teacher Shirt"})
    assert changed.json()["validation"]["errors"] == ["Remove: Disney (title)"]
    await client.post(f"/api/content/{w['teacher']}/approve", json={"approved": False})
    refused = await client.post(f"/api/content/{w['teacher']}/approve", json={"approved": True})
    assert refused.status_code == 422 and refused.json()["detail"]["errors"] == ["Remove: Disney (title)"]
    # Another listing with the same name is not covered by this listing's decision.
    async with client.sm() as s:  # type: ignore[attr-defined]
        (await s.get(GeneratedContent, w["nurse"])).title = "Mickey Nurse Shirt"
        await s.commit()
    other = await client.post(f"/api/content/{w['nurse']}/approve", json={"approved": True})
    assert other.status_code == 422 and other.json()["detail"]["errors"] == ["Remove: Mickey (title)"]


async def test_keep_it_is_only_for_a_brand_name_and_only_for_the_sellers_own_listing(client: AsyncClient) -> None:  # noqa: F811
    from app.api.content import NOTHING_TO_KEEP
    from tests.auth_support import make_tenant
    from app.db.models import Asset, UploadBatch, UploadBatchStatus

    w = await _listings(client)
    # A listing the filter does not stop (a warning only): nothing to keep, nothing recorded.
    nothing = await client.post(f"/api/content/{w['nurse']}/brands/keep", json={})
    assert nothing.status_code == 409 and nothing.json()["detail"] == NOTHING_TO_KEEP
    # Something else stops it as well: the name is kept, and approval answers with what is left.
    async with client.sm() as s:  # type: ignore[attr-defined]
        (await s.get(GeneratedContent, w["teacher"])).title = "Mickey Ears " + "x" * 140
        await s.commit()
    both = await client.post(f"/api/content/{w['teacher']}/brands/keep", json={})
    assert both.status_code == 422 and all("Remove:" not in e for e in both.json()["detail"]["errors"])
    assert (await _approved(client, w))[str(w["teacher"])] is False
    # Another account's listing: not there.
    other = await make_tenant(client.sm, "other@example.com")  # type: ignore[attr-defined]
    async with client.sm() as s:  # type: ignore[attr-defined]
        batch = UploadBatch(tenant_id=other, status=UploadBatchStatus.ready, file_count=1)
        s.add(batch)
        await s.flush()
        asset = Asset(batch_id=batch.id, tenant_id=other, original_filename="x.png", storage_key="k", rank=1)
        s.add(asset)
        await s.flush()
        theirs = GeneratedContent(tenant_id=other, batch_id=batch.id, asset_id=asset.id, title="Mickey Shirt", tags=TAGS, description="d")
        s.add(theirs)
        await s.commit()
        their_id = theirs.id
    assert (await client.post(f"/api/content/{their_id}/brands/keep", json={})).status_code == 404
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.get(GeneratedContent, their_id)).attributes in (None, {})


async def test_stop_keeping_it_brings_the_stop_back_and_withdraws_the_approval(client: AsyncClient) -> None:  # noqa: F811
    from app.compliance import trademarks

    w = await _listings(client)
    await client.post(f"/api/content/{w['teacher']}/brands/keep", json={})
    back = await client.post(f"/api/content/{w['teacher']}/brands/unkeep")
    assert back.status_code == 200
    out = back.json()
    assert (out["content"]["approved"], out["content"]["kept_brands"]) == (False, [])
    assert out["validation"]["errors"] == ["Remove: Mickey (title)"]
    async with client.sm() as s:  # type: ignore[attr-defined]
        content = await s.get(GeneratedContent, w["teacher"])
        assert trademarks.KEPT not in (content.attributes or {})
        assert (await s.execute(select(AuditLog).where(AuditLog.action == "listing.brand_warning_restored"))).scalars().one().details["brands"] == ["Mickey"]
    assert (await client.post(f"/api/content/{w['teacher']}/brands/unkeep")).status_code == 409
