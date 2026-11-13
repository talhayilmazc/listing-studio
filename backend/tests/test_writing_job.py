"""Writing a batch's listings keeps going when the tab is closed (fix/onboarding-followups, Part D).

Before: the batch page looped over its groups in the browser, one ``/generate`` request each, so closing
the tab stopped the writing after the group in flight (``test_the_old_page_wrote_in_the_browser`` pins
that the loop is gone). Now one request queues ``write_batch_listings`` and the worker writes every group;
nothing more comes from the browser.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select

from app.api import deps
from app.db.models import AllowanceUse, Asset, AssetStatus, GeneratedContent, Tenant
from app.workers import writing
from tests.support import fake_response
from tests.test_allowance import _unwritten_group
from tests.test_publish_api import ctx  # noqa: F401  (fixture)
from tests.test_regenerate import VISION, _content_reply, llm  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]


async def _two_groups(ctx):  # noqa: F811
    """A batch with two designs and no listing yet."""
    batch, profile = await _unwritten_group(ctx)
    async with ctx["sm"]() as s:
        s.add(Asset(batch_id=batch, tenant_id=ctx["tenant_id"], original_filename="BR6000.png", parsed_sku="BR6000",
                    storage_key=None, processed_key="p/1.png", mime_type="image/png", group_key="G2",
                    status=AssetStatus.processed, rank=1))
        await s.commit()
    return batch, profile


def _job_ctx(ctx) -> dict:  # noqa: F811
    return {"redis": ctx["redis"], "sessionmaker": ctx["sm"], "enqueuer": ctx["enqueuer"],
            "storage": ctx["app"].dependency_overrides[deps.get_storage]()}


async def _written(ctx, batch) -> int:  # noqa: F811
    async with ctx["sm"]() as s:
        return len(list((await s.execute(select(GeneratedContent.id).where(GeneratedContent.batch_id == batch))).scalars()))


async def _counted(ctx) -> int:  # noqa: F811
    async with ctx["sm"]() as s:
        return len(list((await s.execute(select(AllowanceUse.id).where(AllowanceUse.kind == "generation"))).scalars()))


async def test_one_request_and_the_worker_writes_every_group_with_the_tab_closed(ctx, llm) -> None:  # noqa: F811
    batch, profile = await _two_groups(ctx)
    for _ in range(2):
        llm._responses.extend([fake_response(VISION), fake_response(_content_reply())])
    resp = await ctx["client"].post(f"/api/batches/{batch}/write", json={"profile_id": str(profile)})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["state"], resp.json()["total"]) == ("queued", 2)
    assert ("write_batch_listings", (str(batch),)) in ctx["enqueuer"].calls
    assert await _written(ctx, batch) == 0  # nothing written in the request itself

    # The seller closes the tab here: no further request comes. The worker runs the job.
    assert await writing.write_batch_listings(_job_ctx(ctx), str(batch)) == "done"
    assert await _written(ctx, batch) == 2
    assert await _counted(ctx) == 2

    # Progress lives on the server; the next visit says "Listings ready", once.
    progress = (await ctx["client"].get(f"/api/batches/{batch}/writing")).json()
    assert (progress["state"], progress["done"], progress["total"], progress["written"]) == ("done", 2, 2, 2)
    ready = (await ctx["client"].get("/api/writing/ready")).json()
    assert [(r["batch_id"], r["written"]) for r in ready] == [(str(batch), 2)]
    assert (await ctx["client"].get("/api/writing/ready")).json() == []


async def test_running_it_again_writes_and_counts_nothing_twice(ctx, llm) -> None:  # noqa: F811
    batch, profile = await _two_groups(ctx)
    for _ in range(2):
        llm._responses.extend([fake_response(VISION), fake_response(_content_reply())])
    await ctx["client"].post(f"/api/batches/{batch}/write", json={"profile_id": str(profile)})
    job = _job_ctx(ctx)
    await writing.write_batch_listings(job, str(batch))
    calls = len(llm.calls)
    # A retry of the same job (arq re-runs it after a crash), and a second "Write listings".
    assert await writing.write_batch_listings(job, str(batch)) == "nothing"
    again = (await ctx["client"].post(f"/api/batches/{batch}/write", json={"profile_id": str(profile)})).json()
    assert again["state"] == "done" and again["message"] == "Every group is already written."
    assert (len(llm.calls), await _written(ctx, batch), await _counted(ctx)) == (calls, 2, 2)


async def test_a_job_cut_off_half_way_finishes_only_what_is_left(ctx, llm) -> None:  # noqa: F811
    batch, profile = await _two_groups(ctx)
    for _ in range(2):
        llm._responses.extend([fake_response(VISION), fake_response(_content_reply())])
    await ctx["client"].post(f"/api/batches/{batch}/write", json={"profile_id": str(profile)})
    state = await writing.read(ctx["redis"], batch)
    # The worker died after the first group, before it could save its progress.
    job = _job_ctx(ctx)
    state["groups"] = ["G1"]
    await writing._save(ctx["redis"], batch, state)
    await writing.write_batch_listings(job, str(batch))
    state = await writing.read(ctx["redis"], batch)
    state.update(groups=["G1", "G2"], state="running", total=2)
    await writing._save(ctx["redis"], batch, state)
    await writing.write_batch_listings(job, str(batch))
    assert (await _written(ctx, batch), await _counted(ctx)) == (2, 2)


async def test_the_allowance_still_stops_it_and_says_why(ctx, llm) -> None:  # noqa: F811
    batch, profile = await _two_groups(ctx)
    async with ctx["sm"]() as s:
        t = await s.get(Tenant, ctx["tenant_id"])
        t.allowance_amount, t.allowance_period = 1, "monthly"
        await s.commit()
    llm._responses.extend([fake_response(VISION), fake_response(_content_reply())])
    await ctx["client"].post(f"/api/batches/{batch}/write", json={"profile_id": str(profile)})
    assert await writing.write_batch_listings(_job_ctx(ctx), str(batch)) == "paused"
    progress = (await ctx["client"].get(f"/api/batches/{batch}/writing")).json()
    assert progress["state"] == "paused" and progress["written"] == 1
    assert progress["message"].startswith("You've generated your 1 listings this month.")
    assert (await _written(ctx, batch), await _counted(ctx)) == (1, 1)


async def test_another_accounts_batch_is_not_found(ctx, llm) -> None:  # noqa: F811
    batch, profile = await _two_groups(ctx)
    other = uuid.uuid4()
    assert (await ctx["client"].post(f"/api/batches/{other}/write", json={})).status_code == 404
    assert (await ctx["client"].get(f"/api/batches/{other}/writing")).status_code == 404


def test_the_old_page_wrote_in_the_browser() -> None:
    """The batch page asks once and reads progress from the server; it no longer loops over groups."""
    page = (ROOT / "frontend/app/(app)/batches/[id]/page.tsx").read_text(encoding="utf-8")
    start = page.index("async function generateAll")
    body = page[start:page.index("\n  }\n", start)]
    assert "api.writeBatch(" in body
    assert "api.generate(" not in body
    assert "for (const" not in body
