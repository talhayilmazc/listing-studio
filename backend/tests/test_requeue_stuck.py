"""``requeue-stuck`` and the publish report's additions (2026-10-26: listings with text the seller
wrote never reached a draft). Approved listings the old rules left behind go on the normal path;
nothing unapproved is touched; the dry run changes nothing; one account at a time unless --all."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import Settings
from app.db.models import (
    GeneratedContent,
    GroupPlan,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    PlannedSlot,
    Tenant,
)
from app.ops import publish_report, requeue_stuck
from tests.test_end_to_end import TAGS, _run_drafts, _world

NOW = datetime.now(timezone.utc)
OLD_RULE = "title must be at least 110 characters (yours was 40); target 110-140"


async def _scene(tmp_path, test_settings: Settings, monkeypatch) -> dict:
    """Six approved-or-not listings, one shop, each stuck a different way."""
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=6)
    c = w["contents"]
    shop = w["shops"][0]
    async with w["sm"]() as s:
        rows = {r.id: r for r in (await s.execute(select(GeneratedContent))).scalars()}
        for i in (0, 1, 3, 4, 5):
            rows[c[i]].source = "manual"
            rows[c[i]].title, rows[c[i]].tags = "Custom Name Teacher Tee", ["teacher tee", "custom name"]
        rows[c[4]].approved = False  # never approved: never touched
        rows[c[5]].tags = ["teacher’s gift"]  # a curly apostrophe: Etsy refuses it, so it stays stopped
        for i in (0, 4, 5):  # a draft the app stopped under the old rules
            s.add(Job(tenant_id=w["tenant"], connection_id=shop, type=JobType.create_draft, batch_id=w["batch"],
                      payload={"content_id": str(c[i])}, status=JobStatus.failed, last_error=OLD_RULE))
        plan = GroupPlan(tenant_id=w["tenant"], batch_id=w["batch"], settings={})
        s.add(plan)
        await s.flush()
        s.add(PlannedSlot(tenant_id=w["tenant"], plan_id=plan.id, content_id=c[1], connection_id=shop,
                          draft_at=NOW - timedelta(hours=5), publish_at=NOW - timedelta(hours=3), state="failed",
                          note=OLD_RULE))
        await s.commit()
    # c[2] (by the app) has a draft whose go-live the release held for its text.
    async with w["sm"]() as s:
        s.add(ListingPublication(tenant_id=w["tenant"], content_id=c[2], connection_id=shop, etsy_listing_id=99001,
                                 state="draft", scheduled_for=NOW - timedelta(hours=2),
                                 schedule_note="Not published: expected exactly 13 tags, got 5"))
        other = Tenant(email="other@example.com", password_hash="!")  # another account: never read or touched
        s.add(other)
        await s.commit()
    return w


async def _state(sm) -> list:  # noqa: ANN001
    async with sm() as s:
        return sorted(
            [("job", str(j.id), j.status.value) for j in (await s.execute(select(Job))).scalars()]
            + [("slot", str(p.id), p.state, str(p.publish_at)) for p in (await s.execute(select(PlannedSlot))).scalars()]
            + [("pub", str(p.id), str(p.scheduled_for), p.schedule_note) for p in (await s.execute(select(ListingPublication))).scalars()]
        )


async def test_the_dry_run_lists_and_changes_nothing(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _scene(tmp_path, test_settings, monkeypatch)
    before = await _state(w["sm"])
    out = await requeue_stuck.run(w["sm"], "seller@example.com")
    assert "dry run" in out and await _state(w["sm"]) == before
    assert f"{requeue_stuck.DRAFT}: 1 (ai 0, manual 1)" in out
    assert f"{requeue_stuck.SLOT}: 1 (ai 0, manual 1)" in out
    assert f"{requeue_stuck.GO_LIVE}: 1 (ai 1, manual 0)" in out
    assert f"{requeue_stuck.NEVER_SENT}: 1 (ai 0, manual 1)" in out
    assert f"{requeue_stuck.STILL}: 1 (ai 0, manual 1)" in out and "straight apostrophe" in out
    assert str(w["contents"][4])[:8] not in out  # not approved: not even listed
    assert "nothing stuck" in await requeue_stuck.run(w["sm"], "other@example.com")
    assert "no account" in await requeue_stuck.run(w["sm"], "nobody@example.com")
    await w["engine"].dispose()


async def test_apply_sends_them_on_the_normal_path_and_only_approved_ones(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _scene(tmp_path, test_settings, monkeypatch)
    c = w["contents"]
    queued: list[str] = []

    async def enqueue(function: str, *args) -> None:  # noqa: ANN002
        assert function == "run_publish_job"
        queued.append(args[0])

    out = await requeue_stuck.run(w["sm"], "seller@example.com", apply=True, enqueue=enqueue, now=NOW)
    assert "applied" in out and len(queued) == 1
    async with w["sm"]() as s:
        job = await s.get(Job, uuid.UUID(queued[0]))
        slot = (await s.execute(select(PlannedSlot))).scalar_one()
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == c[2]))).scalar_one()
        failed = {(j.payload or {}).get("content_id") for j in (await s.execute(select(Job).where(
            Job.status == JobStatus.failed))).scalars()}
    assert job.payload["content_id"] == str(c[0]) and job.status is JobStatus.queued
    # The slot waits again, drafts now, and goes live at the next allowed time (its own passed).
    assert slot.state == "waiting" and slot.note is None and slot.publish_at.replace(tzinfo=timezone.utc) >= NOW
    # The held go-live is released, at the next allowed time.
    assert pub.schedule_note is None and pub.scheduled_for.replace(tzinfo=timezone.utc) >= NOW
    # Not approved, and still stopped by Etsy's rule: left as they were.
    assert {str(c[4]), str(c[5])} <= failed
    await _run_drafts(w, queued)
    async with w["sm"]() as s:
        assert (await s.execute(select(ListingPublication).where(ListingPublication.content_id == c[0]))).scalar_one()
    # Run again: what was sent on is no longer stuck.
    again = await requeue_stuck.run(w["sm"], "seller@example.com", now=NOW)
    assert requeue_stuck.DRAFT not in again and requeue_stuck.GO_LIVE not in again
    await w["engine"].dispose()


async def test_the_report_says_who_wrote_each_listing_and_why_it_has_no_draft(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _scene(tmp_path, test_settings, monkeypatch)
    out = await publish_report.report(w["sm"], "seller@example.com")
    summary = out.split("\n\n")[1]
    assert "written by the app" in summary and "by hand" in summary
    assert f"{publish_report.NEVER_SENT}: " in summary and f"{publish_report.SEND_REFUSED}: " in summary
    assert "written: by hand (manual)" in out and "written: by the app (ai)" in out
    assert "approved, never sent to a shop; nothing in its text stops it" in out
    assert "what stops it: tag has characters Etsy does not allow" in out
    assert "its draft was stopped before Etsy: title must be at least 110" in out

    everyone = await publish_report.report_all(w["sm"])
    assert "== seller@example.com" in everyone and "== all accounts" in everyone
    assert "other@example.com" not in everyone  # no listings in the period: not listed
    assert "written: " not in everyone  # a summary per account, no listing blocks
    await w["engine"].dispose()


async def test_a_listing_rewritten_by_hand_with_five_tags_is_approved_and_drafted(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """The reported case: the seller rewrote an AI listing's title and tags (short, 5 tags). It used
    to stay unapproved for ever; now it is approved with notes and its draft is made."""
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    [cid] = w["contents"]
    async with w["sm"]() as s:
        (await s.get(GeneratedContent, cid)).approved = False
        await s.commit()
    saved = await w["client"].patch(f"/api/content/{cid}", json={"title": "Custom Name Teacher Tee", "tags": TAGS[:5]})
    assert saved.status_code == 200 and saved.json()["validation"]["valid"] is True
    assert (await w["client"].post(f"/api/content/{cid}/approve", json={"approved": True})).status_code == 200
    res = await w["client"].post(f"/api/batches/{w['batch']}/send", json={"when": "drafts"})
    assert res.status_code == 200 and len(res.json()["jobs"]) == 1, res.text
    await _run_drafts(w, [j["job_id"] for j in res.json()["jobs"]])
    [listing] = w["etsy"].listings.values()
    assert listing["title"] == "Custom Name Teacher Tee"
    await w["engine"].dispose()
