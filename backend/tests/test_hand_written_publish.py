"""A listing the seller writes by hand goes the same way as one the AI wrote (production report,
2026-10-26: two sellers' hand-written listings never went live).

create ("Write title and tags myself") → type title, tags, description → Approve all → Where and
when → draft → the seller's schedule → go-live, against the fake Etsy, next to an AI-written
listing of the same batch; and every list and count includes it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core.config import Settings
from app.db.models import GeneratedContent, ListingGroupSetting, ListingPublication
from app.workers import publish as publish_worker
from app.workers.schedule import release_scheduled_publishes
from tests.test_end_to_end import TAGS, _run_drafts, _send_drafts, _world

NOW = datetime.now(timezone.utc)
#: What a seller types: shorter than the AI writes and not the profile's style (warnings, not errors).
HAND_TITLE = "Custom Name Retro Sunset Tee"
HAND_TAGS = ["custom name tee", "retro sunset", "name shirt"]


async def _hand_written(w: dict, group: str = "D01") -> str:
    """Free the group, give it the profile, and write its listing by hand through the API."""
    async with w["sm"]() as s:
        index = int(group[1:])
        await s.execute(delete(GeneratedContent).where(GeneratedContent.id == w["contents"][index]))
        s.add(ListingGroupSetting(tenant_id=w["tenant"], batch_id=w["batch"], group_key=group, profile_id=w["profile"],
                                  connection_id=w["shops"][0]))
        await s.commit()
    res = await w["client"].post(f"/api/batches/{w['batch']}/manual", json={"group_key": group})
    assert res.status_code == 200 and res.json()["generated"] == 1, res.text
    cards = (await w["client"].get(f"/api/batches/{w['batch']}/content")).json()
    [card] = [c for c in cards if c["source"] == "manual"]
    saved = await w["client"].patch(f"/api/content/{card['id']}", json={
        "title": HAND_TITLE, "tags": HAND_TAGS, "description": "A sunset tee with your own name on it."})
    assert saved.status_code == 200, saved.text
    return card["id"]


async def test_a_hand_written_listing_goes_the_whole_way_like_an_ai_one(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=2)
    a = w["client"]
    manual_id = await _hand_written(w)
    ai_id = str(w["contents"][0])
    cards = {c["id"]: c for c in (await a.get(f"/api/batches/{w['batch']}/content")).json()}
    assert cards[manual_id]["source"] == "manual" and cards[ai_id]["source"] == "ai"
    # Its notes (short title, 3 tags) are warnings: they never stop approval.
    assert cards[manual_id]["validation"]["valid"] is True and cards[manual_id]["validation"]["warnings"]

    approved = (await a.post(f"/api/batches/{w['batch']}/approve-all")).json()
    assert approved["approved"] == 1 and approved["skipped"] == [], approved

    preview = (await a.post(f"/api/batches/{w['batch']}/send/preview", json={"when": "drafts"})).json()
    assert not preview["left_out"], preview["left_out"]
    assert {r["content_id"] for r in preview["rows"]} == {manual_id, ai_id}
    await _run_drafts(w, await _send_drafts(w))
    async with w["sm"]() as s:
        pubs = {str(p.content_id): p for p in (await s.execute(select(ListingPublication))).scalars()}
    assert set(pubs) == {manual_id, ai_id}
    hand = w["etsy"].listings[pubs[manual_id].etsy_listing_id]
    assert hand["title"] == HAND_TITLE  # exactly what the seller typed, no prefix added

    # The seller's schedule, then go-live.
    schedule = {"start_date": (NOW + timedelta(days=2)).date().isoformat(), "per_shop_per_day": 5,
                "window_start": "09:00", "window_end": "17:00", "spacing_minutes": 30, "stagger_minutes": 5}
    spread = await a.post(f"/api/batches/{w['batch']}/send", json={"when": "spread", "schedule": schedule})
    assert spread.status_code == 200 and spread.json()["scheduled"] == 2, spread.text
    items = (await a.get("/api/scheduled")).json()["items"]
    assert {i["content_id"] for i in items} == {manual_id, ai_id}
    async with w["sm"]() as s:
        for pub in (await s.execute(select(ListingPublication))).scalars():
            pub.scheduled_for = NOW - timedelta(minutes=1)  # their time has come
        await s.commit()
    w["enqueued"].clear()
    assert (await release_scheduled_publishes(w["ctx"]))["released"] == 2
    for job_id in [x[1] for x in w["enqueued"] if x[0] == "run_publish_live_job"]:
        assert await publish_worker.run_publish_live_job(w["ctx"], job_id) == "succeeded"
    assert {d["state"] for d in w["etsy"].listings.values()} == {"active"}

    # Every count and list includes it.
    summary = (await a.get(f"/api/batches/{w['batch']}/summary")).json()
    assert summary["headline"] == "published", summary
    await w["engine"].dispose()


async def test_findings_are_the_same_for_ai_and_hand_written_text(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """The same text gets the same answer whoever wrote it (2026-10-26): what Etsy refuses blocks
    both; the app's own quality rules are notes on both, never a stop."""
    from app.compliance.check import listing_problem
    from app.pipeline.content import GeneratedListing, check_listing
    from tests.test_end_to_end import TITLE

    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=2)
    async with w["sm"]() as s:
        ai, hand = [await s.get(GeneratedContent, c) for c in w["contents"]]
        hand.source = "manual"
        for c in (ai, hand):
            c.title, c.tags = TITLE.format(i=1), list(TAGS)
        assert await listing_problem(s, ai) is None and await listing_problem(s, hand) is None
        for c in (ai, hand):
            c.title = TITLE.format(i=1).replace("Retro", "Mom & Dad & Me Retro")  # Etsy allows & once
        assert "more than once" in (await listing_problem(s, ai) or "") and "more than once" in (await listing_problem(s, hand) or "")
        for c in (ai, hand):
            c.title = "Retro Sunset Tee"  # short for the profile: the app's rule, not Etsy's
        assert await listing_problem(s, ai) is None and await listing_problem(s, hand) is None
    check = check_listing(GeneratedListing(title="Tee", tags=["tee"], description="x"), None, hand_written=True)
    assert check.valid and check.warnings
    await w["engine"].dispose()
