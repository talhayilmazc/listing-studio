"""Personalization never stops a publish silently (2026-10-25).

A question Etsy would refuse is never sent: the draft waits, held, with why and the fix on the
card; "Use this" (or any valid save) carries on what was held, for approved listings only. Going
live never waits on personalization. A go-live Etsy answered but kept as a draft is never recorded
live. A schedule whose time passed is shown as missed, never as waiting.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api import personalization_fix as fix_api
from app.core.config import Settings
from app.db.models import GeneratedContent, Job, JobStatus, JobType, ListingProfile, ListingPublication, PlannedSlot
from app.pipeline import personalization as P
from app.pipeline import personalization_fix as F
from app.workers import publish as publish_worker
from app.workers import recovery
from app.workers.recovery import WAIT_PERSONALIZATION
from tests.auth_support import authenticate, make_tenant, open_session
from tests.test_end_to_end import _run_drafts, _send_drafts, _world

LONG = "Please type the name exactly as it should appear on the shirt, first name only, no emoji, and check the spelling twice"  # 118: within 120
TOO_LONG = LONG + ", thank you!!"  # 131: over 120
NOW = datetime.now(timezone.utc)


async def _profile_question(w: dict, instructions: str, *, required: bool = True) -> None:
    """The profile copied this question from its reference (it is never validated on the way in)."""
    async with w["sm"]() as s:
        profile = await s.get(ListingProfile, w["profile"])
        payload = dict(profile.cached_payload or {})
        payload["personalization"] = [{"question_text": "Name", "instructions": instructions, "required": required,
                                       "max_allowed_characters": 20}]
        profile.cached_payload = payload
        profile.personalization = None
        await s.commit()


async def _jobs(w: dict) -> list[Job]:
    async with w["sm"]() as s:
        return list((await s.execute(select(Job).where(Job.type == JobType.create_draft).order_by(Job.created_at))).scalars())


# --- held, never sent, and said on the card --------------------------------------------------------


async def test_a_question_etsy_would_refuse_is_never_sent_the_draft_waits_held(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    await _profile_question(w, TOO_LONG)
    preview = (await w["client"].post(f"/api/batches/{w['batch']}/send/preview", json={"when": "drafts"})).json()
    assert preview["rows"][0]["cells"][0]["note"] == P.HELD_NOTE  # said before it is pressed
    [job_id] = await _send_drafts(w)
    assert await publish_worker.run_publish_job(w["ctx"], job_id) == "held"
    assert not w["etsy"].requests and not w["etsy"].listings  # nothing reached Etsy
    [job] = await _jobs(w)
    assert job.status is JobStatus.queued and job.paused_reason == WAIT_PERSONALIZATION
    assert job.last_error.startswith(P.HELD) and "120 characters" in job.last_error and job.attempts in (0, 1, None)
    [card] = (await w["client"].get(f"/api/batches/{w['batch']}/content")).json()
    hold = card["personalization_hold"]
    assert hold["source"] == "profile" and hold["profile_name"] == "Standard Tee"
    [field] = hold["fields"]
    assert field["field"] == "instructions" and field["length"] == len(TOO_LONG) and field["needs_shortening"] is True
    [work] = card["work"]
    assert work["kind"] == "draft" and work["error"].startswith(P.HELD) and work["pause"]["reason"] == WAIT_PERSONALIZATION
    # The lost-job sweep never runs a held job again by itself (it waits for the seller, not for time).
    async with w["sm"]() as s:
        (await s.get(Job, job.id)).created_at = NOW - timedelta(hours=3)
        await s.commit()
        assert await recovery._requeue_lost(w["ctx"], s) == 0
    await w["engine"].dispose()


async def test_too_many_capitals_offers_the_caps_fix_on_the_card(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    await _profile_question(w, "PLEASE ENTER THE NAME")
    [card] = (await w["client"].get(f"/api/batches/{w['batch']}/content")).json()
    [field] = card["personalization_hold"]["fields"]
    assert field["extra_caps"] == 3 and field["caps_fix"] == "PLEASE enter the name" and field["needs_shortening"] is False
    profile = (await w["client"].get(f"/api/profiles/{w['profile']}")).json()
    assert profile["personalization_hold"]["fields"][0]["caps_fix"] == "PLEASE enter the name"
    await w["engine"].dispose()


# --- the fix: offered, applied only after "Use this", carrying on approved listings only -------------


async def test_the_shorter_version_is_only_an_offer(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    await _profile_question(w, TOO_LONG)
    calls: list[str] = []

    async def fake_shorten(client, text, field="instructions"):  # noqa: ANN001
        calls.append(text)
        return F.acceptable("Type the first name exactly as it should appear, no emoji; check the spelling", field)

    monkeypatch.setattr(F, "shorten", fake_shorten)
    monkeypatch.setattr("app.pipeline.llm.client_for", lambda *a, **k: object())
    r = (await w["client"].post("/api/personalization/shorten", json={"field": "instructions", "text": TOO_LONG})).json()
    assert r["text"] and len(r["text"]) <= 120 and calls == [TOO_LONG]
    again = (await w["client"].post("/api/personalization/shorten", json={"field": "instructions", "text": TOO_LONG})).json()
    assert again["text"] == r["text"] and calls == [TOO_LONG]  # kept: one model call per text
    # Capitals only: fixed here, no model call.
    caps = (await w["client"].post("/api/personalization/shorten", json={"field": "instructions", "text": "ENTER THE NAME"})).json()
    assert caps["text"] == "ENTER the name" and len(calls) == 1
    # Nothing changed until "Use this".
    async with w["sm"]() as s:
        assert (await s.get(ListingProfile, w["profile"])).personalization is None
    await w["engine"].dispose()


async def test_a_model_that_cannot_shorten_says_so(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)

    async def nothing(client, text, field="instructions"):  # noqa: ANN001
        return None

    monkeypatch.setattr(F, "shorten", nothing)
    monkeypatch.setattr("app.pipeline.llm.client_for", lambda *a, **k: object())
    r = (await w["client"].post("/api/personalization/shorten", json={"field": "instructions", "text": TOO_LONG})).json()
    assert r["text"] is None and "120 characters or fewer" in r["message"]
    await w["engine"].dispose()


async def test_the_model_offer_is_checked_against_etsys_rules() -> None:
    class Client:
        def __init__(self, answers: list[str]) -> None:
            self.answers = answers

        async def complete_json(self, **kwargs):  # noqa: ANN003, ANN201
            class R:
                data = {"text": self.answers.pop(0)}
            return R()

    # Too long first, then fine: the second try is used; capitals fixed on the way.
    assert await F.shorten(Client(["x" * 130, "TYPE THE NAME here"]), TOO_LONG) == "TYPE the name here"
    assert await F.shorten(Client(["x" * 130, "y" * 125]), TOO_LONG) is None


async def test_use_this_fixes_the_profile_and_carries_on_only_approved_listings(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=2)
    await _profile_question(w, TOO_LONG)
    first, second = w["contents"]
    jobs = await _send_drafts(w)
    for job_id in jobs:
        assert await publish_worker.run_publish_job(w["ctx"], job_id) == "held"
    # The second loses its approval meanwhile: it must not go on.
    assert (await w["client"].post(f"/api/content/{second}/approve", json={"approved": False})).status_code == 200
    w["queue"].calls.clear()
    res = await w["client"].post(f"/api/content/{first}/personalization/fix", json={"instructions": LONG})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["requeued"]["listings"] == [str(first)] and body["message"] == "1 held listing going ahead now."
    assert body["content"]["personalization_hold"] is None
    async with w["sm"]() as s:
        profile = await s.get(ListingProfile, w["profile"])
        assert profile.personalization["instructions"] == LONG  # the profile's own setting now
        held = {str(j.payload["content_id"]): j for j in (await s.execute(select(Job).where(Job.type == JobType.create_draft))).scalars()}
    assert held[str(first)].paused_reason is None and held[str(second)].paused_reason == WAIT_PERSONALIZATION
    assert [(a[0], a[1][0]) for a in w["queue"].calls if a[0] == "run_publish_job"] == [("run_publish_job", str(held[str(first)].id))]
    await _run_drafts(w, [str(held[str(first)].id)])
    [listing] = w["etsy"].listings.values()
    assert listing["state"] == "draft"
    await w["engine"].dispose()


async def test_saving_text_etsy_would_refuse_is_refused_and_a_valid_save_carries_on(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    await _profile_question(w, TOO_LONG)
    [job_id] = await _send_drafts(w)
    assert await publish_worker.run_publish_job(w["ctx"], job_id) == "held"
    url = f"/api/profiles/{w['profile']}"
    base = {"enabled": True, "question_text": "Name", "required": True, "max_allowed_characters": 20}
    for bad in (TOO_LONG, "PLEASE ENTER THE NAME"):
        r = await w["client"].patch(url, json={"personalization": {**base, "instructions": bad}})
        assert r.status_code == 422, bad
    # "Use this" with text that still breaks the rules is refused too, and changes nothing.
    r = await w["client"].post(f"/api/profiles/{w['profile']}/personalization/fix", json={"instructions": "A" * 121})
    assert r.status_code == 422 and "still refuse" in r.json()["detail"]
    w["queue"].calls.clear()
    ok = await w["client"].patch(url, json={"personalization": {**base, "instructions": LONG}})
    assert ok.status_code == 200 and ok.json()["personalization_hold"] is None
    assert ("run_publish_job", job_id) in [(a[0], a[1][0]) for a in w["queue"].calls]  # the ordinary save carries the held draft on too
    await w["engine"].dispose()


async def test_another_account_cannot_fix_or_see_a_hold(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    await _profile_question(w, TOO_LONG)
    from httpx import ASGITransport, AsyncClient

    bob = await make_tenant(w["sm"], "bob@example.com")
    transport = w["client"]._transport
    async with AsyncClient(transport=transport, base_url="http://test", headers=dict(w["client"].headers)) as b:
        from app.api.deps import get_redis

        redis = w["ctx"]["redis"]
        authenticate(b, await open_session(redis, bob))
        for url in (f"/api/content/{w['contents'][0]}/personalization/fix", f"/api/profiles/{w['profile']}/personalization/fix"):
            assert (await b.post(url, json={"instructions": LONG})).status_code == 404, url
        assert (await b.get(f"/api/batches/{w['batch']}/content")).status_code == 404
    async with w["sm"]() as s:
        assert (await s.get(ListingProfile, w["profile"])).personalization is None
    await w["engine"].dispose()


# --- going live never waits on personalization, and is never recorded when Etsy kept a draft ---------


async def test_going_live_does_not_wait_on_personalization(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """A draft made before its profile's question went bad (a later reference read) still goes live."""
    from app.workers.schedule import release_scheduled_publishes

    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    await _run_drafts(w, await _send_drafts(w))
    await _profile_question(w, TOO_LONG)
    async with w["sm"]() as s:
        [pub] = (await s.execute(select(ListingPublication))).scalars()
        pub.scheduled_for = NOW - timedelta(minutes=1)
        await s.commit()
    assert (await release_scheduled_publishes(w["ctx"]))["released"] == 1
    [job] = [a[1] for a in w["enqueued"] if a[0] == "run_publish_live_job"]
    assert await publish_worker.run_publish_live_job(w["ctx"], job) == "succeeded"
    assert {d["state"] for d in w["etsy"].listings.values()} == {"active"}
    await w["engine"].dispose()


async def test_a_go_live_etsy_kept_as_a_draft_is_never_recorded_live(tmp_path, test_settings: Settings, monkeypatch) -> None:
    from app.workers.schedule import release_scheduled_publishes

    monkeypatch.setattr("app.etsy.publisher.asyncio.sleep", _no_sleep)
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    await _run_drafts(w, await _send_drafts(w))
    [listing_id] = list(w["etsy"].listings)
    w["etsy"].keep_draft.add(listing_id)
    async with w["sm"]() as s:
        [pub] = (await s.execute(select(ListingPublication))).scalars()
        pub.scheduled_for = NOW - timedelta(minutes=1)
        await s.commit()
    await release_scheduled_publishes(w["ctx"])
    [job_id] = [a[1] for a in w["enqueued"] if a[0] == "run_publish_live_job"]
    assert await publish_worker.run_publish_live_job(w["ctx"], job_id) == "failed"
    async with w["sm"]() as s:
        [pub] = (await s.execute(select(ListingPublication))).scalars()
        job = await s.get(Job, uuid.UUID(job_id))
    assert pub.state == "draft" and pub.published_at is None
    assert "still a draft on Etsy" in job.last_error
    scheduled = (await w["client"].get("/api/scheduled")).json()
    [item] = [i for i in scheduled["items"] if i["kind"] == "draft"]
    assert item["status"] == "failed" and "still a draft" in item["note"]
    await w["engine"].dispose()


async def _no_sleep(_seconds: float) -> None:
    return None


# --- the Scheduled page: missed is missed, held is held, a finished draft is not left "drafting" -----


async def test_a_missed_schedule_is_shown_as_missed_never_waiting(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=2)
    await _run_drafts(w, await _send_drafts(w))
    first, second = w["contents"]
    async with w["sm"]() as s:
        pubs = {p.content_id: p for p in (await s.execute(select(ListingPublication))).scalars()}
        pubs[first].scheduled_for = NOW - timedelta(hours=1)  # its time passed; nothing released it
        await s.commit()
        plan_id = uuid.uuid4()
        from app.db.models import GroupPlan

        s.add(GroupPlan(id=plan_id, tenant_id=w["tenant"], settings={}))
        s.add(PlannedSlot(tenant_id=w["tenant"], plan_id=plan_id, content_id=second, connection_id=w["shops"][0],
                          draft_at=NOW - timedelta(hours=3), publish_at=NOW - timedelta(hours=1), state="waiting"))
        await s.commit()
    items = (await w["client"].get("/api/scheduled")).json()["items"]
    statuses = sorted(i["status"] for i in items)
    assert statuses == ["missed", "missed"], items
    assert all(i["movable"] for i in items)  # the seller can move a missed one
    await w["engine"].dispose()


async def test_a_held_draft_shows_held_and_goes_at_the_next_allowed_time(tmp_path, test_settings: Settings, monkeypatch) -> None:
    from app.workers.plans import release_due

    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    await _profile_question(w, TOO_LONG)
    [content] = w["contents"]
    async with w["sm"]() as s:
        from app.db.models import GroupPlan

        plan_id = uuid.uuid4()
        s.add(GroupPlan(id=plan_id, tenant_id=w["tenant"], settings={}))
        slot = PlannedSlot(tenant_id=w["tenant"], plan_id=plan_id, content_id=content, connection_id=w["shops"][0],
                           draft_at=NOW - timedelta(hours=3), publish_at=NOW - timedelta(hours=1), state="waiting")
        s.add(slot)
        await s.commit()
        slot_id = slot.id

        async def enqueue(*_a):  # noqa: ANN002
            return None

        assert await release_due(s, enqueue) == 1
    [job] = await _jobs(w)
    assert await publish_worker.run_publish_job(w["ctx"], str(job.id)) == "held"
    [item] = (await w["client"].get("/api/scheduled")).json()["items"]
    assert item["status"] == "held" and item["note"].startswith(P.HELD)
    res = await w["client"].post(f"/api/profiles/{w['profile']}/personalization/fix", json={"instructions": LONG})
    assert res.json()["requeued"]["schedules"] == 1 and res.json()["requeued"]["drafts"] == 1
    async with w["sm"]() as s:
        slot = await s.get(PlannedSlot, slot_id)
    at = slot.publish_at if slot.publish_at.tzinfo else slot.publish_at.replace(tzinfo=timezone.utc)
    assert at > datetime.now(timezone.utc)  # the next time the shop's rules allow, not the past
    await _run_drafts(w, [str(job.id)])
    async with w["sm"]() as s:
        slot = await s.get(PlannedSlot, slot_id)
        [pub] = (await s.execute(select(ListingPublication))).scalars()
    assert slot.state == "scheduled" and pub.scheduled_for is not None
    await w["engine"].dispose()


async def test_a_draft_that_finished_without_its_go_live_is_scheduled(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """The worker stopped between the draft and setting its go-live: the slot is not left "drafting"."""
    from app.workers.plans import release_due

    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    [job_id] = await _send_drafts(w)
    await _run_drafts(w, [job_id])
    [content] = w["contents"]
    async with w["sm"]() as s:
        from app.db.models import GroupPlan

        plan_id = uuid.uuid4()
        s.add(GroupPlan(id=plan_id, tenant_id=w["tenant"], settings={}))
        slot = PlannedSlot(tenant_id=w["tenant"], plan_id=plan_id, content_id=content, connection_id=w["shops"][0],
                           draft_at=NOW - timedelta(hours=2), publish_at=NOW + timedelta(hours=1), state="drafting",
                           job_id=uuid.UUID(job_id))
        s.add(slot)
        await s.commit()

        async def enqueue(*_a):  # noqa: ANN002
            return None

        await release_due(s, enqueue)
        await s.refresh(slot)
        [pub] = (await s.execute(select(ListingPublication))).scalars()
    assert slot.state == "scheduled" and pub.scheduled_for is not None
    await w["engine"].dispose()


# --- small rules --------------------------------------------------------------------------------------


def test_the_read_back_ignores_entities_and_spacing() -> None:
    sent = {"question_text": "Name & date", "instructions": "Type it  here\nplease", "required": True, "max_allowed_characters": 20}
    back = {"personalization_questions": [{"question_text": "Name &amp; date", "instructions": "Type it here please",
                                           "required": True, "max_allowed_characters": 20}]}
    assert P.differs(sent, back) == []
    back["personalization_questions"][0]["instructions"] = "Something else"
    assert P.differs(sent, back) == ["instructions"]


def test_the_caps_fix_matches_the_shared_cases() -> None:
    import json
    from pathlib import Path

    for case in json.loads((Path(__file__).parent / "fixtures" / "personalization_caps_cases.json").read_text(encoding="utf-8")):
        assert P.fix_caps(case["text"]) == case["fixed"], case


def test_the_schedule_state_says_missed_after_its_time() -> None:
    from app.etsy.scheduling import state_of

    pub = ListingPublication(etsy_listing_id=1, scheduled_for=NOW - timedelta(minutes=30), state="draft")
    assert state_of(pub, None, NOW).status == "missed"
    pub.scheduled_for = NOW - timedelta(minutes=2)
    assert state_of(pub, None, NOW).status == "scheduled"  # just due: the next release takes it
    pub.schedule_note = "Not published: the listing is no longer approved."
    assert state_of(pub, None, NOW).status == "not_published"


async def test_approval_records_when_and_by_which_path(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=2)
    first, second = w["contents"]
    async with w["sm"]() as s:
        for c in (await s.execute(select(GeneratedContent))).scalars():
            c.approved, c.approved_at, c.approved_via = False, None, None
        await s.commit()
    assert (await w["client"].post(f"/api/content/{first}/approve", json={"approved": True})).status_code == 200
    assert (await w["client"].post(f"/api/batches/{w['batch']}/approve-all")).status_code == 200
    async with w["sm"]() as s:
        a, b = await s.get(GeneratedContent, first), await s.get(GeneratedContent, second)
        assert (a.approved_via, b.approved_via) == ("card", "approve_all") and a.approved_at and b.approved_at
    assert (await w["client"].post(f"/api/content/{first}/approve", json={"approved": False})).status_code == 200
    async with w["sm"]() as s:
        a = await s.get(GeneratedContent, first)
        assert (a.approved, a.approved_at, a.approved_via) == (False, None, None)
    await w["engine"].dispose()


def test_hold_out_only_when_etsy_would_refuse() -> None:
    assert fix_api.hold_out({"enabled": True, "question_text": "Name", "instructions": LONG}, "profile", None) is None
    assert fix_api.hold_out({"enabled": False}, "listing", None) is None
    hold = fix_api.hold_out({"enabled": True, "question_text": "Name", "instructions": TOO_LONG}, "listing", None)
    assert hold is not None and hold.source == "listing" and hold.fields[0].needs_shortening
    # The panel's sentence is true before any draft is tried and when one is already on Etsy.
    assert "drafts already on Etsy are not changed" in hold.reason and "Nothing was sent" not in hold.reason
    assert "(yours are 131)" in hold.reason
