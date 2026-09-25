"""Create drafts or publish across several batches from the Batches page.

Rule 3 holds: only approved listings are acted on, and publishing only makes
existing drafts live. The preview lists what will be skipped, and why.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import GeneratedContent, Job, JobType
from tests.auth_support import authenticate, make_tenant, open_session
from tests.test_publish_api import _add_content, ctx  # noqa: F401  (fixture)


async def _batch_of(ctx, content_id):  # noqa: F811
    async with ctx["sm"]() as s:
        return (await s.get(GeneratedContent, content_id)).batch_id


async def _world(ctx):  # noqa: F811
    """Two batches: ready to draft, unapproved, invalid, drafted, and live listings."""
    ready = await _add_content(ctx["sm"], ctx["tenant_id"], approved=True)
    unapproved = await _add_content(ctx["sm"], ctx["tenant_id"], approved=False, listing_id=701)
    invalid = await _add_content(ctx["sm"], ctx["tenant_id"], approved=True, valid=False)
    drafted = await _add_content(ctx["sm"], ctx["tenant_id"], approved=True, listing_id=702)
    live = await _add_content(ctx["sm"], ctx["tenant_id"], approved=True, listing_id=703, listing_state="active")
    ids = dict(ready=ready, unapproved=unapproved, invalid=invalid, drafted=drafted, live=live)
    batches = sorted({str(await _batch_of(ctx, c)) for c in ids.values()})
    return ids, batches


async def _jobs(ctx):  # noqa: F811
    async with ctx["sm"]() as s:
        return list((await s.execute(select(Job))).scalars())


def _reasons(preview) -> dict[str, str]:
    return {i["content_id"]: i["reason"] for i in preview["skipped"]}


async def test_the_drafts_preview_names_what_it_will_do_and_skip(ctx) -> None:  # noqa: F811
    ids, batches = await _world(ctx)
    preview = (await ctx["client"].post("/api/batch-actions/preview", json={"batch_ids": batches, "action": "drafts"})).json()
    assert [i["content_id"] for i in preview["act"]] == [str(ids["ready"])]
    assert preview["act"][0]["original_filename"] == "tasarim_BR5475.png"
    reasons = _reasons(preview)
    assert reasons[str(ids["unapproved"])] == "not approved"
    assert "at least 110" in reasons[str(ids["invalid"])]
    assert reasons[str(ids["drafted"])] == "already has a draft in this shop"
    assert preview["fits"] is True and preview["estimated_calls"] == 15
    assert await _jobs(ctx) == [] and ctx["enqueuer"].calls == []  # a preview queues nothing


async def test_running_drafts_queues_only_the_approved_ready_ones(ctx) -> None:  # noqa: F811
    ids, batches = await _world(ctx)
    body = (await ctx["client"].post("/api/batch-actions/run", json={"batch_ids": batches, "action": "drafts"})).json()
    assert [j["content_id"] for j in body["jobs"]] == [str(ids["ready"])]
    assert {j.type for j in await _jobs(ctx)} == {JobType.create_draft}


async def test_publishing_only_makes_approved_existing_drafts_live(ctx) -> None:  # noqa: F811
    ids, batches = await _world(ctx)
    preview = (await ctx["client"].post("/api/batch-actions/preview", json={"batch_ids": batches, "action": "publish"})).json()
    assert [i["content_id"] for i in preview["act"]] == [str(ids["drafted"])]
    reasons = _reasons(preview)
    assert reasons[str(ids["unapproved"])] == "not approved"  # has a draft, but not approved
    assert "create the draft first" in reasons[str(ids["ready"])]
    assert reasons[str(ids["live"])] == "already published"
    assert await _jobs(ctx) == []

    body = (await ctx["client"].post("/api/batch-actions/run", json={"batch_ids": batches, "action": "publish"})).json()
    assert [j["content_id"] for j in body["jobs"]] == [str(ids["drafted"])]
    assert {j.type for j in await _jobs(ctx)} == {JobType.publish_live}


async def test_another_accounts_batch_is_refused_whole(ctx) -> None:  # noqa: F811
    ids, batches = await _world(ctx)
    other = await make_tenant(ctx["sm"], "other@example.com")
    ctx["client"].cookies.clear()
    authenticate(ctx["client"], await open_session(ctx["redis"], other))
    for path in ("/api/batch-actions/preview", "/api/batch-actions/run"):
        resp = await ctx["client"].post(path, json={"batch_ids": batches, "action": "publish"})
        assert resp.status_code == 404
    assert await _jobs(ctx) == []
