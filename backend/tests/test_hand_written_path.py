"""A listing the seller wrote by hand goes the same way as one the app wrote (production, 2026-10-26).

Two sellers' hand-written listings never reached a draft while the app's own went through. Each
step is taken here with a hand-written listing next to an AI one, against the fake Etsy: written,
approved (one by one and "Approve all"), sent ("Where and when": drafts, and spread out on a
schedule), drafted, scheduled and taken live.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import Settings
from app.db.models import GeneratedContent, ListingProfile, ListingPublication, PlannedSlot
from app.pipeline import manual
from app.workers import publish as publish_worker
from app.workers.plans import release_due
from app.workers.schedule import release_scheduled_publishes
from tests.test_end_to_end import _run_drafts, _world

HAND_TITLE = "Custom Name Teacher Shirt, Personalized Teacher Tee"
HAND_TAGS = ["teacher shirt", "custom name tee", "teacher gift", "school shirt", "name shirt"]


async def _make_hand_written(w: dict, content_id: uuid.UUID) -> None:
    """The listing as "Write title and tags myself" leaves it, then filled in by the seller."""
    async with w["sm"]() as s:
        content = await s.get(GeneratedContent, content_id)
        profile = await s.get(ListingProfile, content.listing_profile_id)
        content.source, content.approved, content.attributes = manual.MANUAL, False, None
        content.written_from_asset_id = content.model_used = content.vision_model = None
        content.title, content.tags = "", []
        content.description = manual.description_for(profile, "")
        await s.commit()
    res = await w["client"].patch(f"/api/content/{content_id}", json={"title": HAND_TITLE, "tags": HAND_TAGS})
    assert res.status_code == 200, res.text


async def _unapprove(w: dict, content_id: uuid.UUID) -> None:
    async with w["sm"]() as s:
        (await s.get(GeneratedContent, content_id)).approved = False
        await s.commit()


async def test_a_hand_written_listing_is_approved_sent_drafted_scheduled_and_taken_live(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=2)
    hand, ai = w["contents"]
    await _make_hand_written(w, hand)
    await _unapprove(w, ai)
    a = w["client"]

    # Approve all takes both: the hand-written one with notes, never silently left out.
    res = await a.post(f"/api/batches/{w['batch']}/approve-all", json={})
    assert res.status_code == 200, res.text
    assert res.json()["approved"] == 2, res.json()
    cards = {c["id"]: c for c in (await a.get(f"/api/batches/{w['batch']}/content")).json()}
    assert cards[str(hand)]["source"] == "manual" and cards[str(hand)]["approved"] is True

    # Where and when: both go, as drafts.
    preview = (await a.post(f"/api/batches/{w['batch']}/send/preview", json={"when": "drafts"})).json()
    assert not preview["left_out"], preview["left_out"]
    assert preview["total"] == 2, preview
    res = await a.post(f"/api/batches/{w['batch']}/send", json={"when": "drafts"})
    assert res.status_code == 200, res.text
    await _run_drafts(w, [j["job_id"] for j in res.json()["jobs"]])
    titles = {d["title"] for d in w["etsy"].listings.values()}
    assert HAND_TITLE in titles and len(titles) == 2

    # Spread out: both drafts get a go-live, and both go live when it comes.
    schedule = {"start_date": (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(), "per_shop_per_day": 5,
                "window_start": "09:00", "window_end": "17:00", "spacing_minutes": 30, "stagger_minutes": 5}
    res = await a.post(f"/api/batches/{w['batch']}/send", json={"when": "spread", "schedule": schedule})
    assert res.status_code == 200 and res.json()["scheduled"] == 2, res.text
    items = (await a.get("/api/scheduled")).json()["items"]
    assert {i["content_id"] for i in items} == {str(hand), str(ai)}
    async with w["sm"]() as s:
        for pub in (await s.execute(select(ListingPublication))).scalars():
            pub.scheduled_for = datetime.now(timezone.utc) - timedelta(minutes=1)
        await s.commit()
    w["enqueued"].clear()
    assert (await release_scheduled_publishes(w["ctx"]))["released"] == 2
    for job_id in [x[1] for x in w["enqueued"] if x[0] == "run_publish_live_job"]:
        assert await publish_worker.run_publish_live_job(w["ctx"], job_id) == "succeeded"
    assert {d["state"] for d in w["etsy"].listings.values()} == {"active"}
    await w["engine"].dispose()


async def test_a_hand_written_listing_on_a_group_schedule_is_drafted_and_goes_live(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """Spread out before any draft exists: the slot makes the draft, then it goes live."""
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    [hand] = w["contents"]
    await _make_hand_written(w, hand)
    a = w["client"]
    assert (await a.post(f"/api/content/{hand}/approve", json={"approved": True})).status_code == 200
    schedule = {"start_date": (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat(), "per_shop_per_day": 5,
                "window_start": "09:00", "window_end": "17:00", "spacing_minutes": 30, "stagger_minutes": 5}
    res = await a.post(f"/api/batches/{w['batch']}/send", json={"when": "spread", "schedule": schedule})
    assert res.status_code == 200 and res.json()["scheduled"] == 1, res.text
    async with w["sm"]() as s:
        slot = (await s.execute(select(PlannedSlot))).scalar_one()
        slot.draft_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        slot.publish_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await s.commit()
    w["enqueued"].clear()

    async def enqueue(function: str, *args) -> None:  # noqa: ANN002
        w["enqueued"].append((function, *args))

    async with w["sm"]() as s:
        released = await release_due(s, enqueue)
    async with w["sm"]() as s:
        slot = (await s.execute(select(PlannedSlot))).scalar_one()
    assert released == 1, (slot.state, slot.note, slot.job_id)
    await _run_drafts(w, [x[1] for x in w["enqueued"] if x[0] == "run_publish_job"])
    async with w["sm"]() as s:
        pub = (await s.execute(select(ListingPublication))).scalar_one()
        # A draft made after its time goes at the next release (a minute on); that minute has come.
        assert pub.scheduled_for is not None
        pub.scheduled_for = datetime.now(timezone.utc) - timedelta(seconds=1)
        await s.commit()
    w["enqueued"].clear()
    assert (await release_scheduled_publishes(w["ctx"]))["released"] == 1
    [job_id] = [x[1] for x in w["enqueued"] if x[0] == "run_publish_live_job"]
    assert await publish_worker.run_publish_live_job(w["ctx"], job_id) == "succeeded"
    [listing] = w["etsy"].listings.values()
    assert (listing["title"], listing["state"]) == (HAND_TITLE, "active")
    await w["engine"].dispose()
