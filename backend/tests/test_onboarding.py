"""The first-run checklist, the example designs and the sign-up funnel (app/onboarding-v1).

* Each funnel step is written once, from where it happens, never from a click on the checklist.
* The checklist ticks itself from those rows and links each step to where it is done.
* The example designs make no model call, change no usage count, write no funnel step and can never
  be sent to Etsy (every path refuses them; so do the draft and publish workers).
* The backfill finds each step in existing data, writes it once, and is what migration 0098 runs.
* The admin's funnel is admin-only and holds counts and times, nothing personal.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.api import deps
from app.db.models import (
    AiCall, AllowanceUse, Asset, AssetStatus, EtsyConnection, FunnelEvent, GeneratedContent, GroupPlan, Job,
    ListingPublication, Tenant, UploadBatch, UploadBatchStatus,
)
from app.pipeline import examples, funnel
from app.pipeline.images import ImageProcessor, PillowBackend
from app.pipeline.ingest import BatchIngestor
from app.pipeline.sku import SkuParser
from app.pipeline.storage import LocalStorage
from tests.auth_support import authenticate, make_tenant, open_session
from tests.test_accounts import app_ctx  # noqa: F401  (fixture)
from tests.test_publish_api import _add_content, _shop, ctx  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[1]


def _with_ingestor(ctx, tmp_path) -> None:  # noqa: ANN001
    ctx["app"].dependency_overrides[deps.get_ingestor] = lambda: BatchIngestor(
        sessionmaker=ctx["sm"], storage=LocalStorage(tmp_path),
        processor=ImageProcessor(backend=PillowBackend()), sku_parser=SkuParser(),
    )


async def _events(sm, tenant_id) -> dict[str, datetime]:  # noqa: ANN001
    async with sm() as s:
        return await funnel.done(s, tenant_id)


# --- Funnel rows -------------------------------------------------------------------------------------
async def test_a_step_is_written_once_and_an_unknown_name_is_refused(ctx) -> None:  # noqa: F811
    first = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
    async with ctx["sm"]() as s:
        await funnel.record(s, ctx["tenant_id"], funnel.FIRST_UPLOAD, at=first)
        await funnel.record(s, ctx["tenant_id"], funnel.FIRST_UPLOAD)  # again: nothing changes
        await s.commit()
        with pytest.raises(ValueError):
            await funnel.record(s, ctx["tenant_id"], "clicked_something")
    rows = await _events(ctx["sm"], ctx["tenant_id"])
    assert list(rows) == [funnel.FIRST_UPLOAD]
    assert rows[funnel.FIRST_UPLOAD].replace(tzinfo=timezone.utc) == first


def test_a_funnel_row_holds_the_account_the_step_and_its_time_and_nothing_else() -> None:
    assert {c.name for c in FunnelEvent.__table__.columns} == {"id", "tenant_id", "name", "at"}
    assert funnel.NAMES == ("signed_up", "shop_connected", "first_upload", "first_listing_ready", "first_approved", "first_sent")
    assert set(funnel.LABELS) == set(funnel.NAMES)


async def test_uploading_approving_and_sending_write_their_steps(ctx, tmp_path) -> None:  # noqa: F811
    _with_ingestor(ctx, tmp_path)
    client = ctx["client"]
    batch = (await client.post("/api/batches")).json()["id"]
    from PIL import Image
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (900, 900), (200, 80, 40)).save(buffer, format="PNG")
    added = await client.post(f"/api/batches/{batch}/assets", files={"file": ("BR100.png", buffer.getvalue(), "image/png")})
    assert added.status_code == 201, added.text
    assert funnel.FIRST_UPLOAD in await _events(ctx["sm"], ctx["tenant_id"])

    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], approved=False)
    approved = await client.post(f"/api/content/{content_id}/approve", json={"approved": True})
    assert approved.status_code == 200, approved.text
    assert funnel.FIRST_APPROVED in await _events(ctx["sm"], ctx["tenant_id"])

    sent = await client.post(f"/api/content/{content_id}/publish")
    assert sent.status_code == 200, sent.text
    assert funnel.FIRST_SENT in await _events(ctx["sm"], ctx["tenant_id"])


async def test_registering_writes_signed_up(app_ctx) -> None:  # noqa: F811
    from tests.test_accounts import GOOD_PASSWORD, _invite

    code = await _invite(app_ctx)
    res = await app_ctx["client"].post(
        "/api/account/register",
        json={"email": "new-seller@example.com", "password": GOOD_PASSWORD, "invite_code": code, "accept_terms": True},
    )
    assert res.status_code == 201, res.text
    async with app_ctx["sm"]() as s:
        tenant_id = await s.scalar(select(Tenant.id).where(Tenant.email == "new-seller@example.com"))
        assert list(await funnel.done(s, tenant_id)) == [funnel.SIGNED_UP]


# --- The checklist -----------------------------------------------------------------------------------
async def _confirmed_profile(ctx) -> None:  # noqa: F811
    from app.db.models import EtsyConnection, ListingProfile

    async with ctx["sm"]() as s:
        shop = await s.scalar(select(EtsyConnection.id).where(EtsyConnection.tenant_id == ctx["tenant_id"]))
        s.add(ListingProfile(tenant_id=ctx["tenant_id"], connection_id=shop, name="Tees", reference_listing_id=7,
                             confirmed=True))
        await s.commit()


async def test_the_checklist_ticks_itself_and_links_each_step_to_where_it_is_done(ctx) -> None:  # noqa: F811
    client = ctx["client"]
    first = (await client.get("/api/onboarding")).json()
    # The context's account has a shop already (connected before the funnel existed): step 1 is done.
    assert [s["key"] for s in first["steps"]] == ["shop", "profile", "upload", "approve", "send"]
    assert [s["done"] for s in first["steps"]] == [True, False, False, False, False]
    assert first["next"] == "profile" and not first["all_done"]
    assert [s["href"] for s in first["steps"]][:3] == ["/connect", "/profiles", "/upload"]
    assert [s["title"] for s in first["steps"]] == [
        "Connect your shop", "Confirm your listing settings", "Upload your first design", "Review and approve",
        "Send or schedule it"]
    # The shop is not read yet, so there is no suggestion to confirm; the step says it is coming.
    assert first["steps"][1]["confirm_profile_id"] is None
    await _confirmed_profile(ctx)
    assert (await client.get("/api/onboarding")).json()["next"] == "upload"

    # A listing written and waiting: "Review and approve" opens its batch.
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], approved=False)
    async with ctx["sm"]() as s:
        batch_id = (await s.get(GeneratedContent, content_id)).batch_id
        await funnel.record(s, ctx["tenant_id"], funnel.FIRST_UPLOAD)
        await s.commit()
    again = (await client.get("/api/onboarding")).json()
    assert again["steps"][3]["href"] == f"/batches/{batch_id}/review"
    assert again["next"] == "approve"

    # Not every step done: the note cannot be closed yet.
    assert (await client.post("/api/onboarding/dismiss")).status_code == 409
    async with ctx["sm"]() as s:
        for name in (funnel.FIRST_APPROVED, funnel.FIRST_SENT):
            await funnel.record(s, ctx["tenant_id"], name)
        await s.commit()
    done = (await client.get("/api/onboarding")).json()
    assert done["all_done"] and done["next"] is None and not done["dismissed"]
    assert (await client.post("/api/onboarding/dismiss")).status_code == 204
    assert (await client.get("/api/onboarding")).json()["dismissed"] is True


async def test_another_accounts_steps_tick_nothing_here(ctx) -> None:  # noqa: F811
    other = await make_tenant(ctx["sm"], "other@example.com")
    async with ctx["sm"]() as s:
        for name in funnel.NAMES:
            await funnel.record(s, other, name)
        await s.commit()
    mine = (await ctx["client"].get("/api/onboarding")).json()
    assert [s["done"] for s in mine["steps"]] == [True, False, False, False, False]


# --- The example designs -----------------------------------------------------------------------------
def test_the_saved_result_is_three_listings_of_our_own_designs() -> None:
    saved = examples.saved()
    assert [x["slug"] for x in saved["listings"]] == ["slow-mornings-club", "tomato-season", "small-kind-things"]
    for listing in saved["listings"]:
        assert 0 < len(listing["title"]) <= 140
        assert len(listing["tags"]) == 13 and all(len(t) <= 20 for t in listing["tags"])
        for kind in ("light", "dark"):
            assert (examples.HERE / f"{listing['slug']}-{kind}.jpg").is_file()
    assert "examples.json" in saved["how"] or "example_listings.py" in saved["how"]


async def test_trying_the_examples_makes_no_model_call_and_counts_for_nothing(ctx, tmp_path) -> None:  # noqa: F811
    _with_ingestor(ctx, tmp_path)
    client = ctx["client"]
    made = await client.post("/api/examples")
    assert made.status_code == 200, made.text
    batch_id = made.json()["batch_id"]
    # Again: the same batch, not a second one.
    assert (await client.post("/api/examples")).json()["batch_id"] == batch_id

    async with ctx["sm"]() as s:
        batch = await s.get(UploadBatch, uuid.UUID(batch_id))
        assert batch.is_example and batch.name == examples.NAME
        contents = list((await s.execute(select(GeneratedContent).where(GeneratedContent.batch_id == batch.id))).scalars())
        assert sorted(c.title for c in contents) == sorted(x["title"] for x in examples.saved()["listings"])
        assert all(c.model_used is None and c.input_tokens is None for c in contents)
        photos = await s.scalar(select(func.count()).select_from(Asset).where(Asset.batch_id == batch.id))
        assert photos == 6
        assert await s.scalar(select(func.count()).select_from(AiCall)) == 0
        assert await s.scalar(select(func.count()).select_from(AllowanceUse)) == 0
    assert await _events(ctx["sm"], ctx["tenant_id"]) == {}

    summary = (await client.get(f"/api/batches/{batch_id}")).json()
    assert summary["is_example"] is True
    checklist = (await client.get("/api/onboarding")).json()
    assert checklist["example_batch_id"] == batch_id
    assert [s["done"] for s in checklist["steps"]] == [True, False, False, False, False]


async def test_an_example_can_be_approved_but_never_sent_rewritten_or_scheduled(ctx, tmp_path) -> None:  # noqa: F811
    _with_ingestor(ctx, tmp_path)
    client = ctx["client"]
    batch_id = (await client.post("/api/examples")).json()["batch_id"]
    async with ctx["sm"]() as s:
        content = (await s.execute(select(GeneratedContent).where(GeneratedContent.batch_id == uuid.UUID(batch_id)))).scalars().first()
        shop = await _shop(s, ctx["tenant_id"])
    # Approving shows how the review works; it is not a funnel step.
    assert (await client.post(f"/api/content/{content.id}/approve", json={"approved": True})).status_code == 200
    assert funnel.FIRST_APPROVED not in await _events(ctx["sm"], ctx["tenant_id"])

    refused = [
        await client.post(f"/api/content/{content.id}/publish"),
        await client.post(f"/api/batches/{batch_id}/publish", json={}),
        await client.post(f"/api/batches/{batch_id}/send/preview", json={"when": "drafts"}),
        await client.post(f"/api/batches/{batch_id}/send", json={"when": "drafts"}),
        await client.post(f"/api/batches/{batch_id}/generate", json={"replace": True}),
        await client.post(f"/api/batches/{batch_id}/manual", json={}),
        await client.post(f"/api/batches/{batch_id}/another-shop/preview", json={"connection_id": str(shop), "content_ids": [str(content.id)]}),
    ]
    for res in refused:
        assert res.status_code == 409, (res.request.url, res.status_code, res.text)
        assert res.json()["detail"] in (examples.NOT_SENT, "The example listings are already written. Upload a design of your own to have Listyro write it.")
    async with ctx["sm"]() as s:
        assert await s.scalar(select(func.count()).select_from(Job)) == 0
        assert await s.scalar(select(func.count()).select_from(GroupPlan)) == 0
        assert await s.scalar(select(func.count()).select_from(AiCall)) == 0
    assert funnel.FIRST_SENT not in await _events(ctx["sm"], ctx["tenant_id"])


def test_the_draft_and_publish_workers_refuse_an_example_whatever_queued_it() -> None:
    source = (ROOT / "app" / "workers" / "publish.py").read_text(encoding="utf-8")
    assert source.count("raise ValueError(examples.NOT_SENT)") == 2  # run_publish_job and run_publish_live_job


# --- Backfill ----------------------------------------------------------------------------------------
async def test_backfill_finds_each_step_in_existing_data_once_and_skips_the_examples(ctx) -> None:  # noqa: F811
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], approved=True, listing_id=777)
    async with ctx["sm"]() as s:
        content = await s.get(GeneratedContent, content_id)
        content.approved_at = datetime(2026, 10, 2, tzinfo=timezone.utc)
        # The example batch of another account: none of it is a step.
        other = Tenant(email="example-only@example.com", password_hash="!")
        s.add(other)
        await s.flush()
        example = UploadBatch(tenant_id=other.id, status=UploadBatchStatus.ready, is_example=True, file_count=1)
        s.add(example)
        await s.flush()
        asset = Asset(batch_id=example.id, tenant_id=other.id, original_filename="x.jpg", status=AssetStatus.processed)
        s.add(asset)
        await s.flush()
        s.add(GeneratedContent(tenant_id=other.id, batch_id=example.id, asset_id=asset.id, title="t", tags=[],
                               approved=True, approved_at=datetime.now(timezone.utc)))
        await s.commit()
        written = await funnel.backfill(s)
        assert written[funnel.SIGNED_UP] == 2
        assert written[funnel.SHOP_CONNECTED] == 1
        assert written[funnel.FIRST_UPLOAD] == 1
        assert written[funnel.FIRST_LISTING_READY] == 1
        assert written[funnel.FIRST_APPROVED] == 1
        assert written[funnel.FIRST_SENT] == 1
        assert set(await funnel.done(s, other.id)) == {funnel.SIGNED_UP}
        mine = await funnel.done(s, ctx["tenant_id"])
        assert mine[funnel.FIRST_APPROVED].replace(tzinfo=timezone.utc) == datetime(2026, 10, 2, tzinfo=timezone.utc)
        # Run again: nothing more.
        assert set((await funnel.backfill(s)).values()) == {0}


def test_migration_0098_runs_the_same_backfill_queries() -> None:
    migration = (ROOT / "alembic" / "versions" / "0098_onboarding.py").read_text(encoding="utf-8")
    namespace: dict = {}
    start = migration.index("BACKFILL: list")
    end = migration.index("\n]\n", start) + 3
    exec(migration[start:end].replace("BACKFILL: list[tuple[str, str]] =", "BACKFILL ="), namespace)  # noqa: S102
    flat = lambda q: re.sub(r"\s+", " ", q).strip()  # noqa: E731
    assert [(n, flat(q)) for n, q in namespace["BACKFILL"]] == [(n, flat(funnel.BACKFILL[n])) for n in funnel.NAMES]


# --- The admin's funnel ------------------------------------------------------------------------------
async def test_the_funnel_is_admin_only_and_counts_a_cohort_with_its_median(ctx) -> None:  # noqa: F811
    client = ctx["client"]
    assert (await client.get("/api/admin/funnel?days=7")).status_code == 404  # a seller learns nothing

    now = datetime.now(timezone.utc)
    async with ctx["sm"]() as s:
        admin = Tenant(email="admin@example.com", password_hash="!", is_admin=True)
        s.add(admin)
        await s.flush()
        ids = []
        for i, minutes in enumerate((4, 10, None)):
            t = Tenant(email=f"seller{i}@example.com", password_hash="!")
            s.add(t)
            await s.flush()
            ids.append(t.id)
            signed = now - timedelta(days=2)
            await funnel.record(s, t.id, funnel.SIGNED_UP, at=signed)
            await funnel.record(s, t.id, funnel.SHOP_CONNECTED, at=signed + timedelta(minutes=1))
            if minutes is not None:
                await funnel.record(s, t.id, funnel.FIRST_APPROVED, at=signed + timedelta(minutes=minutes))
        # Outside the 7-day window, and a demo account: neither counted.
        old = Tenant(email="old@example.com", password_hash="!")
        demo = Tenant(email="demo-x@example.test", password_hash="!")
        s.add_all([old, demo])
        await s.flush()
        await funnel.record(s, old.id, funnel.SIGNED_UP, at=now - timedelta(days=20))
        await funnel.record(s, demo.id, funnel.SIGNED_UP, at=now - timedelta(days=1))
        await s.commit()
        admin_id = admin.id
    authenticate(client, await open_session(ctx["redis"], admin_id))
    answer = await client.get("/api/admin/funnel?days=7")
    assert answer.status_code == 200, answer.text
    week = answer.json()
    assert week["cohort"] == 3
    counts = {s["name"]: s["count"] for s in week["steps"]}
    assert counts["signed_up"] == 3 and counts["shop_connected"] == 3 and counts["first_approved"] == 2
    assert week["steps"][1]["from_previous"] == 1.0
    assert week["median_seconds_to_first_approval"] == 7 * 60  # 4 and 10 minutes
    month = (await client.get("/api/admin/funnel?days=30")).json()
    assert month["cohort"] == 4
    assert (await client.get("/api/admin/funnel?days=3")).status_code == 422
    assert "seller0@example.com" not in str(week)

