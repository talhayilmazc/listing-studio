"""``python -m app.cli publish-report``: per outcome, split by personalization, then one block per
listing; read only, one account, no tokens, no buyer data, no seller text beyond the title's start."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import Settings
from app.db.models import (
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    PlannedSlot,
    Tenant,
)
from app.ops import publish_report as R
from app.workers import publish as publish_worker
from tests.test_end_to_end import _send_drafts, _world
from tests.test_personalization_hold import TOO_LONG

NOW = datetime.now(timezone.utc)
SECRET_DESCRIPTION = "Only the seller wrote this sentence about the shirt"
LONG_TITLE = "A very long listing title that goes on past forty characters easily"


async def _scene(tmp_path, test_settings: Settings, monkeypatch) -> dict:
    """Seven listings, one shop: published, approved but not published, scheduled and waiting,
    scheduled and missed, never approved, refused by Etsy, held by our own check (personalization)."""
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=7)
    c = w["contents"]
    async with w["sm"]() as s:
        rows = {r.id: r for r in (await s.execute(select(GeneratedContent))).scalars()}
        rows[c[1]].title, rows[c[1]].description = LONG_TITLE, SECRET_DESCRIPTION
        rows[c[1]].personalization = {"enabled": True, "question_text": "Name", "instructions": "Type the name",
                                      "required": True, "max_allowed_characters": 20}  # on, and fine
        rows[c[4]].approved = False
        rows[c[6]].personalization = {"enabled": True, "question_text": "Name", "instructions": TOO_LONG,
                                      "required": True, "max_allowed_characters": 20}  # breaks Etsy's rules
        await s.commit()
    for job_id in await _send_drafts(w):
        await publish_worker.run_publish_job(w["ctx"], job_id)
    async with w["sm"]() as s:
        pubs = {p.content_id: p for p in (await s.execute(select(ListingPublication))).scalars()}
        pubs[c[0]].state, pubs[c[0]].published_at = "active", NOW - timedelta(hours=2)
        pubs[c[2]].scheduled_for = NOW + timedelta(days=1)
        pubs[c[3]].scheduled_for = NOW - timedelta(hours=1)
        s.add(Job(tenant_id=w["tenant"], connection_id=w["shops"][0], type=JobType.publish_live, batch_id=w["batch"],
                  status=JobStatus.failed, last_error="Etsy refused making it live",
                  payload={"content_id": str(c[5]), "etsy_error": {"status": 400, "step": "going live",
                                                                  "reason": "This listing cannot be activated"}}))
        await s.commit()
    return w


async def _state(sm) -> list:
    async with sm() as s:
        return sorted(
            [("job", str(j.id), j.status.value, j.paused_reason, j.last_error) for j in (await s.execute(select(Job))).scalars()]
            + [("pub", str(p.id), p.state, str(p.scheduled_for), p.schedule_note) for p in (await s.execute(select(ListingPublication))).scalars()]
            + [("slot", str(p.id), p.state, str(p.publish_at), p.note) for p in (await s.execute(select(PlannedSlot))).scalars()]
            + [("content", str(g.id), g.approved, str(g.approved_at), g.title) for g in (await s.execute(select(GeneratedContent))).scalars()]
        )


async def test_the_summary_counts_each_outcome_split_by_personalization(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _scene(tmp_path, test_settings, monkeypatch)
    out = await R.report(w["sm"], "seller@example.com")
    summary = out.split("\n\n")[1]
    for line in (
        "published: 1 (personalization on 0, off 1)",
        "approved but not published: 1 (personalization on 1, off 0)",
        "scheduled and waiting: 1 (personalization on 0, off 1)",
        "scheduled and missed: 1 (personalization on 0, off 1)",
        "never approved: 1 (personalization on 0, off 1)",
        "refused by Etsy: 1 (personalization on 0, off 1)",
        "held by our own check: 1 (personalization on 1, off 0)",
    ):
        assert line in summary, (line, summary)
    # Each listing's block: approval path, attempts with Etsy's answer, the held reason, the rules.
    assert "approval: approved, time not recorded, via not recorded" in out
    assert "Etsy 400 at going live: This listing cannot be activated" in out
    assert "draft   queued (personalization_held)" in out
    assert "instructions 131 characters (over 120)" in out and "holds new drafts (never sent)" in out
    await w["engine"].dispose()


async def test_no_tokens_no_seller_text_beyond_the_title_start(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _scene(tmp_path, test_settings, monkeypatch)
    out = await R.report(w["sm"], "seller@example.com")
    assert ".abc" not in out and "access_token" not in out  # the world's tokens are "500.abc"…
    assert SECRET_DESCRIPTION not in out and TOO_LONG not in out and "Type the name" not in out
    assert LONG_TITLE[:40] in out and LONG_TITLE[:41] not in out
    assert "seller@example.com" in out.splitlines()[0]  # the account it is about, and nothing of buyers
    await w["engine"].dispose()


async def test_it_reads_only_and_only_the_named_account(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _scene(tmp_path, test_settings, monkeypatch)
    async with w["sm"]() as s:
        other = Tenant(email="other@example.com", password_hash="!")
        s.add(other)
        await s.flush()
        from app.db.models import Asset, UploadBatch

        batch = UploadBatch(tenant_id=other.id, file_count=1)
        s.add(batch)
        await s.flush()
        asset = Asset(batch_id=batch.id, tenant_id=other.id, original_filename="x.png", rank=1)
        s.add(asset)
        await s.flush()
        s.add(GeneratedContent(tenant_id=other.id, batch_id=batch.id, asset_id=asset.id, title="Someone else's listing"))
        await s.commit()
    before = await _state(w["sm"])
    out = await R.report(w["sm"], "seller@example.com")
    assert "Someone else's listing" not in out
    assert await _state(w["sm"]) == before
    assert "no account" in await R.report(w["sm"], "nobody@example.com")
    await w["engine"].dispose()


async def test_check_etsy_reads_each_draft_and_finds_one_recorded_live_but_still_a_draft(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _scene(tmp_path, test_settings, monkeypatch)
    async with w["sm"]() as s:
        pubs = list((await s.execute(select(ListingPublication))).scalars())
        live = next(p for p in pubs if p.state == "active")
    read: list[int] = []

    class Client:
        async def get_listing(self, listing_id: int, **kw):  # noqa: ANN003, ANN201
            assert "access_token" in kw
            read.append(listing_id)
            return {"listing_id": listing_id, "state": "draft"}  # Etsy kept every one a draft

    class Tokens:
        async def get_valid_access_token(self, session, shop):  # noqa: ANN001, ANN201
            return "token"

    out = await R.report(w["sm"], "seller@example.com", check_etsy=True, client_factory=lambda shop, http: Client(),
                         token_service=Tokens())
    assert sorted(read) == sorted(p.etsy_listing_id for p in pubs)
    assert f"published here, still a draft on Etsy: 1" in out and str(live.etsy_listing_id) in out
    assert "on Etsy: draft" in out and "Etsy read: " in out and "token" not in out.replace("tokens", "")
    await w["engine"].dispose()


async def test_the_outcome_rules() -> None:
    content = GeneratedContent(id=uuid.uuid4(), approved=True, created_at=NOW)
    shop_id = uuid.uuid4()
    item = R.Item(content, None, None, None, "profile")
    assert R.classify(item, NOW)[0] == R.APPROVED
    item.slots = [PlannedSlot(state="waiting", publish_at=NOW + timedelta(hours=1), draft_at=NOW)]
    assert R.classify(item, NOW)[0] == R.WAITING
    item.slots[0].publish_at = NOW - timedelta(hours=1)
    assert R.classify(item, NOW)[0] == R.MISSED
    item.slots = [PlannedSlot(state="failed", note="Held: its personalization breaks Etsy's rules: …", publish_at=NOW, draft_at=NOW)]
    assert R.classify(item, NOW)[0] == R.HELD
    content.approved = False
    assert R.classify(item, NOW)[0] == R.NOT_APPROVED
    content.approved = True
    item.slots = []
    item.publication = ListingPublication(etsy_listing_id=1, state="draft", connection_id=shop_id, schedule_note="Not published: x")
    assert R.classify(item, NOW)[0] == R.HELD
