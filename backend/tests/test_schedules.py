"""Scheduled publishing (docs/duzeltmeler-v6.md §G).

A schedule is the seller's explicit confirmation (CLAUDE.md rule 3): only the
existing draft of a listing the seller approved can be scheduled, and approval
and compliance are checked again when it is due.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.models import (
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
)
from app.etsy.scheduling import NOT_APPROVED, state_of
from app.workers.schedule import release_scheduled_publishes
from tests.auth_support import authenticate, make_tenant, open_session
from tests.test_publish_api import _add_content, _shop, ctx  # noqa: F401  (fixture)


def _in(minutes: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


async def _draft(ctx, *, approved=True, listing_id=777):
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], approved=approved, listing_id=listing_id)
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
    return content_id, shop


async def _post(ctx, content_id, shop, run_at):
    body = {"items": [{"content_id": str(content_id), "connection_id": str(shop), "run_at": run_at}]}
    return (await ctx["client"].post("/api/schedules", json=body)).json()


async def _pub(ctx, content_id) -> ListingPublication:
    async with ctx["sm"]() as s:
        rows = await s.execute(select(ListingPublication).where(ListingPublication.content_id == content_id))
        return rows.scalars().one()


class _Queue:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def __call__(self, function, *args):  # noqa: ANN001, ANN201
        self.calls.append((function, *args))


# --- scheduling -----------------------------------------------------------------------
async def test_an_approved_draft_is_scheduled_in_utc_and_listed(ctx) -> None:
    content_id, shop = await _draft(ctx)
    # Sent with the seller's own offset; stored as the same instant in UTC.
    local = (datetime.now(timezone(timedelta(hours=3))) + timedelta(days=1)).replace(microsecond=0)
    body = await _post(ctx, content_id, shop, local.isoformat())
    assert body["skipped"] == [] and len(body["scheduled"]) == 1

    pub = await _pub(ctx, content_id)
    stored = pub.scheduled_for if pub.scheduled_for.tzinfo else pub.scheduled_for.replace(tzinfo=timezone.utc)
    assert stored == local

    [row] = (await ctx["client"].get("/api/schedules")).json()
    assert row["status"] == "scheduled" and row["etsy_listing_id"] == 777
    assert "listing-editor" in row["listing_link"]  # back link to the draft on Etsy
    # The review page shows it on the draft too.
    [item] = (await ctx["client"].get(f"/api/batches/{row['batch_id']}/content")).json()
    assert item["publications"][0]["schedule_status"] == "scheduled"


async def test_only_an_approved_listings_existing_draft_can_be_scheduled(ctx) -> None:
    unapproved, shop = await _draft(ctx, approved=False)
    no_draft = await _add_content(ctx["sm"], ctx["tenant_id"], approved=True)
    body = {"items": [
        {"content_id": str(unapproved), "connection_id": str(shop), "run_at": _in(60)},
        {"content_id": str(no_draft), "connection_id": str(shop), "run_at": _in(60)},
    ]}
    resp = (await ctx["client"].post("/api/schedules", json=body)).json()
    assert resp["scheduled"] == []
    reasons = {s["content_id"]: s["reason"] for s in resp["skipped"]}
    assert reasons[str(unapproved)] == "approve the listing first"
    assert "create the draft first" in reasons[str(no_draft)]
    assert (await _pub(ctx, unapproved)).scheduled_for is None


async def test_times_in_the_past_too_far_ahead_or_without_a_zone_are_refused(ctx) -> None:
    content_id, shop = await _draft(ctx)
    naive = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None).isoformat()
    for run_at, reason in [
        (_in(-30), "already passed"),
        (_in(61 * 24 * 60), "at most 60 days"),
        (naive, "time zone"),
    ]:
        [skip] = (await _post(ctx, content_id, shop, run_at))["skipped"]
        assert reason in skip["reason"], skip
    assert (await _pub(ctx, content_id)).scheduled_for is None


async def test_another_accounts_draft_cannot_be_scheduled_or_cancelled(ctx) -> None:
    content_id, shop = await _draft(ctx)
    await _post(ctx, content_id, shop, _in(60))
    other = await make_tenant(ctx["sm"], "other@example.com")
    ctx["client"].cookies.clear()
    authenticate(ctx["client"], await open_session(ctx["redis"], other))

    resp = await _post(ctx, content_id, shop, _in(120))
    assert resp["scheduled"] == [] and len(resp["skipped"]) == 1
    assert (await ctx["client"].delete(f"/api/schedules/{content_id}/{shop}")).status_code == 404
    assert (await ctx["client"].get("/api/schedules")).json() == []


async def test_moving_and_cancelling_a_schedule(ctx) -> None:
    content_id, shop = await _draft(ctx)
    await _post(ctx, content_id, shop, _in(60))
    later = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    await _post(ctx, content_id, shop, later.isoformat())
    pub = await _pub(ctx, content_id)
    assert pub.scheduled_for.replace(tzinfo=timezone.utc) == later

    assert (await ctx["client"].delete(f"/api/schedules/{content_id}/{shop}")).status_code == 204
    assert (await _pub(ctx, content_id)).scheduled_for is None
    assert (await ctx["client"].get("/api/schedules")).json() == []


async def test_withdrawing_approval_withdraws_the_schedule(ctx) -> None:
    content_id, shop = await _draft(ctx)
    await _post(ctx, content_id, shop, _in(60))
    await ctx["client"].post(f"/api/content/{content_id}/approve", json={"approved": False})
    assert (await _pub(ctx, content_id)).scheduled_for is None


# --- release --------------------------------------------------------------------------
async def _due(ctx, content_id) -> None:
    """Move a schedule into the past, as the clock would."""
    async with ctx["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == content_id))).scalars().one()
        pub.scheduled_for = datetime.now(timezone.utc) - timedelta(minutes=1)
        await s.commit()


async def test_a_due_schedule_becomes_the_publish_now_job(ctx) -> None:
    due, shop = await _draft(ctx)
    later, _ = await _draft(ctx, listing_id=778)
    await _post(ctx, due, shop, _in(60))
    await _post(ctx, later, shop, _in(600))
    await _due(ctx, due)

    queue = _Queue()
    counts = await release_scheduled_publishes({"sessionmaker": ctx["sm"], "enqueue": queue})
    assert counts == {"released": 1, "held": 0}

    pub = await _pub(ctx, due)
    async with ctx["sm"]() as s:
        job = await s.get(Job, pub.schedule_job_id)
    assert job.type is JobType.publish_live and job.status is JobStatus.queued
    assert job.payload == {"content_id": str(due), "scheduled": True}
    assert queue.calls == [("run_publish_live_job", str(job.id))]
    assert (await _pub(ctx, later)).schedule_job_id is None  # not due yet

    # Released once: the next minute's run leaves it alone.
    again = _Queue()
    assert await release_scheduled_publishes({"sessionmaker": ctx["sm"], "enqueue": again}) == {"released": 0, "held": 0}
    assert again.calls == []
    [row] = [r for r in (await ctx["client"].get("/api/schedules")).json() if r["content_id"] == str(due)]
    assert row["status"] == "publishing"


async def test_approval_and_compliance_are_checked_again_when_due(ctx, tmp_path, test_settings) -> None:
    unapproved, shop = await _draft(ctx)
    marked, _ = await _draft(ctx, listing_id=778)
    await _post(ctx, unapproved, shop, _in(60))
    await _post(ctx, marked, shop, _in(60))
    async with ctx["sm"]() as s:  # approval gone behind the API's back
        (await s.get(GeneratedContent, unapproved)).approved = False
        await s.commit()
    marks = tmp_path / "marks.txt"
    marks.write_text("Adventure Awaits\n", encoding="utf-8")  # in the listings' title
    test_settings.trademark_list_path = str(marks)
    await _due(ctx, unapproved)
    await _due(ctx, marked)

    queue = _Queue()
    assert await release_scheduled_publishes({"sessionmaker": ctx["sm"], "enqueue": queue}) == {"released": 0, "held": 2}
    assert queue.calls == []
    assert (await _pub(ctx, unapproved)).schedule_note == NOT_APPROVED
    assert "trademark 'Adventure Awaits'" in (await _pub(ctx, marked)).schedule_note
    statuses = {r["content_id"]: r["status"] for r in (await ctx["client"].get("/api/schedules")).json()}
    assert statuses == {str(unapproved): "not_published", str(marked): "not_published"}


async def test_cancelling_a_released_job_that_has_not_started(ctx) -> None:
    content_id, shop = await _draft(ctx)
    await _post(ctx, content_id, shop, _in(60))
    await _due(ctx, content_id)
    await release_scheduled_publishes({"sessionmaker": ctx["sm"], "enqueue": _Queue()})
    job_id = (await _pub(ctx, content_id)).schedule_job_id

    assert (await ctx["client"].delete(f"/api/schedules/{content_id}/{shop}")).status_code == 204
    async with ctx["sm"]() as s:
        assert (await s.get(Job, job_id)).status is JobStatus.cancelled  # the worker will skip it


async def test_a_running_job_cannot_be_cancelled(ctx) -> None:
    content_id, shop = await _draft(ctx)
    await _post(ctx, content_id, shop, _in(60))
    await _due(ctx, content_id)
    await release_scheduled_publishes({"sessionmaker": ctx["sm"], "enqueue": _Queue()})
    async with ctx["sm"]() as s:
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.content_id == content_id))).scalars().one()
        (await s.get(Job, pub.schedule_job_id)).status = JobStatus.running
        await s.commit()
    assert (await ctx["client"].delete(f"/api/schedules/{content_id}/{shop}")).status_code == 409


def test_what_the_seller_sees_for_each_outcome() -> None:
    at = datetime.now(timezone.utc)
    pub = ListingPublication(state="draft", scheduled_for=at, etsy_listing_id=1)
    assert state_of(pub, None).status == "scheduled"
    waiting = Job(status=JobStatus.queued, paused_reason="The app's daily Etsy budget is almost used up.")
    assert state_of(pub, waiting).status == "waiting"  # quota full: deferred, and says why
    assert state_of(pub, waiting).note.startswith("The app's daily")
    assert state_of(pub, Job(status=JobStatus.failed, last_error="Etsy refused")).status == "failed"
    assert state_of(pub, Job(status=JobStatus.succeeded)).status == "published"
    live = ListingPublication(state="active", scheduled_for=at, etsy_listing_id=1)
    assert state_of(live, None).status == "published"  # published by hand before its time
    assert state_of(ListingPublication(state="draft", etsy_listing_id=1), None) is None


async def test_bulk_scheduling_leaves_an_existing_time_alone(ctx) -> None:
    content_id, shop = await _draft(ctx)
    first = (datetime.now(timezone.utc) + timedelta(hours=5)).replace(microsecond=0)
    await _post(ctx, content_id, shop, first.isoformat())
    body = {"replace": False, "items": [{"content_id": str(content_id), "connection_id": str(shop), "run_at": _in(60)}]}
    resp = (await ctx["client"].post("/api/schedules", json=body)).json()
    assert resp["scheduled"] == [] and "already scheduled" in resp["skipped"][0]["reason"]
    assert (await _pub(ctx, content_id)).scheduled_for.replace(tzinfo=timezone.utc) == first
