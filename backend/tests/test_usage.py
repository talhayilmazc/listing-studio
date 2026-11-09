"""The listing counter (fix/usage-and-admin-publishing, Part 1; core/usage.py).

A listing counts exactly once, when its first Etsy draft is created successfully in any shop, in the
same transaction as that draft's record. Each case below leaves exactly the count it names; the drafts
are made by the real draft worker against the fake Etsy of tests/test_end_to_end.py.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core import usage
from app.core.config import Settings
from app.db.models import (
    Asset,
    AssetStatus,
    EtsyConnection,
    GeneratedContent,
    ListingPublication,
    UploadBatch,
    UploadBatchStatus,
    UsageEvent,
)
from app.workers import publish as publish_worker
from tests.test_end_to_end import _png, _run_drafts, _send_drafts, _world
from tests.test_hard_cases import _drop_scratch_postgres, _scratch_postgres

ROOT = Path(__file__).resolve().parents[2]


async def _count(w: dict) -> int:
    async with w["sm"]() as s:
        return int(await s.scalar(select(func.count()).select_from(UsageEvent)) or 0)


async def _send_to(w: dict, shops: list) -> None:
    res = await w["client"].put(f"/api/batches/{w['batch']}/send-to", json={"send_to": {"connection_ids": [str(s) for s in shops]}})
    assert res.status_code == 200, res.text


async def test_an_ai_written_listing_counts_once(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    await _run_drafts(w, await _send_drafts(w))
    assert await _count(w) == 1
    # Shown by the one count, whatever USAGE_COUNTER says; "on" makes it the number used.
    async with w["sm"]() as s:
        from app.core import allowance
        from app.db.models import Tenant

        tenant = await s.get(Tenant, w["tenant"])
        shadow = await allowance.status(s, tenant)
        assert (shadow.counter, shadow.listings) == ("shadow", 1)
        assert shadow.used == shadow.old_used  # the old number while in shadow
        monkeypatch.setattr(usage, "mode", lambda: "on")
        on = await allowance.status(s, tenant)
        assert (on.counter, on.used) == ("on", 1)
    await w["engine"].dispose()


async def test_a_hand_written_listing_counts_once(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    async with w["sm"]() as s:
        content = await s.get(GeneratedContent, w["contents"][0])
        content.source = "manual"
        await s.commit()
    await _run_drafts(w, await _send_drafts(w))
    assert await _count(w) == 1
    await w["engine"].dispose()


async def test_one_listing_to_five_shops_at_once_counts_once(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=5, listings=1)
    await _send_to(w, w["shops"])
    jobs = await _send_drafts(w)
    assert len(jobs) == 5
    await _run_drafts(w, jobs)
    async with w["sm"]() as s:
        assert await s.scalar(select(func.count()).select_from(ListingPublication)) == 5
    assert await _count(w) == 1
    await w["engine"].dispose()


async def test_shop_a_this_month_and_shop_b_next_month_counts_once_in_the_first(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=2, listings=1)
    a, b = w["shops"]
    await _send_to(w, [a])
    await _run_drafts(w, await _send_drafts(w))
    last_month = datetime.now(timezone.utc) - timedelta(days=35)
    async with w["sm"]() as s:
        row = (await s.execute(select(UsageEvent))).scalar_one()
        row.at = last_month  # the first draft was made last month
        await s.commit()
    await _send_to(w, [a, b])
    await _run_drafts(w, await _send_drafts(w))
    async with w["sm"]() as s:
        now = datetime.now(timezone.utc)
        assert await usage.used(s, w["tenant"], last_month - timedelta(days=1), last_month + timedelta(days=1)) == 1
        assert await usage.used(s, w["tenant"], now - timedelta(days=20), now + timedelta(days=1)) == 0
    assert await _count(w) == 1
    await w["engine"].dispose()


async def test_a_draft_that_fails_then_succeeds_on_retry_counts_once(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    etsy = w["etsy"]
    real = etsy.handle
    broken = {"once": True}

    async def handle(request):  # noqa: ANN001
        if request.method == "PUT" and request.url.path.endswith("/inventory") and broken["once"]:
            broken["once"] = False
            import httpx

            return httpx.Response(400, json={"error": "inventory refused"})
        return await real(request)

    etsy.handle = handle  # type: ignore[method-assign]
    monkeypatch.setattr(publish_worker, "httpx", __import__("tests.test_end_to_end", fromlist=["_Httpx"])._Httpx(etsy))
    [job] = await _send_drafts(w)
    assert await publish_worker.run_publish_job(w["ctx"], job) != "succeeded"
    assert await _count(w) == 0  # a half-made draft counts nothing
    await _run_drafts(w, await _send_drafts(w))  # sent again: the same Etsy listing is finished
    assert len(etsy.listings) == 1
    assert await _count(w) == 1
    await w["engine"].dispose()


async def test_two_workers_making_the_first_draft_at_once_count_once(tmp_path, test_settings: Settings, monkeypatch) -> None:
    """Really at once, on Postgres: the same listing's first draft in two shops, by two jobs together."""
    url = _scratch_postgres()
    w = await _world(tmp_path, test_settings, monkeypatch, shops=2, listings=1, database_url=url)
    await _send_to(w, w["shops"])
    one, two = await _send_drafts(w)
    results = await asyncio.gather(publish_worker.run_publish_job(w["ctx"], one), publish_worker.run_publish_job(w["ctx"], two))
    assert results == ["succeeded", "succeeded"]
    async with w["sm"]() as s:
        assert await s.scalar(select(func.count()).select_from(ListingPublication)) == 2
    assert await _count(w) == 1
    await w["engine"].dispose()
    _drop_scratch_postgres(url)


async def test_replace_images_a_crop_or_a_text_edit_count_nothing_more(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    await _run_drafts(w, await _send_drafts(w))
    c = w["client"]
    async with w["sm"]() as s:
        content = await s.get(GeneratedContent, w["contents"][0])
        asset = await s.get(Asset, content.asset_id)
        asset.width, asset.height = 64, 64
        await s.commit()
    assert (await c.put(f"/api/assets/{asset.id}/cover-crop", json={"x": 0, "y": 0, "size": 32})).status_code == 200
    edited = await c.patch(f"/api/content/{content.id}", json={"description": "A new description."})
    assert edited.status_code in (200, 409), edited.text  # on Etsy already: whatever the answer, no count
    await _run_drafts(w, await _send_drafts(w) if (await c.post(
        f"/api/batches/{w['batch']}/send/preview", json={"when": "drafts"})).json()["total"] else [])
    assert await _count(w) == 1
    # Replace images rewrites a listing already on Etsy: it never makes a first draft, never counts.
    replace = (ROOT / "backend/app/workers/replace.py").read_text(encoding="utf-8")
    assert "core import usage" not in replace and "record_first_draft" not in replace and "publish_content(" not in replace
    await w["engine"].dispose()


async def test_an_example_listing_counts_nothing(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    async with w["sm"]() as s:
        content = await s.get(GeneratedContent, w["contents"][0])
        batch = await s.get(UploadBatch, content.batch_id)
        batch.is_example = True
        publication = ListingPublication(tenant_id=w["tenant"], content_id=content.id, connection_id=w["shops"][0],
                                         etsy_listing_id=1, state="draft")
        s.add(publication)
        await s.flush()
        await usage.record_first_draft(s, publication, content)
        await s.commit()
    assert await _count(w) == 0
    await w["engine"].dispose()


async def test_the_same_design_uploaded_again_is_a_new_listing(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    await _run_drafts(w, await _send_drafts(w))
    async with w["sm"]() as s:
        first = await s.get(GeneratedContent, w["contents"][0])
        old = await s.get(Asset, first.asset_id)
        batch = UploadBatch(tenant_id=w["tenant"], status=UploadBatchStatus.ready, file_count=1)
        s.add(batch)
        await s.flush()
        asset = Asset(batch_id=batch.id, tenant_id=w["tenant"], original_filename=old.original_filename, parsed_sku=old.parsed_sku,
                      storage_key=old.storage_key, processed_key=old.processed_key, status=AssetStatus.processed, rank=1,
                      group_key=old.group_key)
        s.add(asset)
        await s.flush()
        s.add(GeneratedContent(tenant_id=w["tenant"], batch_id=batch.id, asset_id=asset.id, approved=True,
                               listing_profile_id=first.listing_profile_id, title=first.title, tags=first.tags,
                               description=first.description))
        await s.commit()
    res = await w["client"].post(f"/api/batches/{batch.id}/send", json={"when": "drafts"})
    assert res.status_code == 200, res.text
    await _run_drafts(w, [j["job_id"] for j in res.json()["jobs"]])
    assert await _count(w) == 2
    await w["engine"].dispose()


# --- backfill and reconciliation ----------------------------------------------------------------------
async def _history(w: dict) -> None:
    """Fixture history made before the counter: drafts in two shops for one listing, one draft whose batch
    is gone, one deleted on Etsy; no usage rows."""
    async with w["sm"]() as s:
        content = await s.get(GeneratedContent, w["contents"][0])
        a, b = w["shops"]
        t0 = datetime.now(timezone.utc) - timedelta(days=3)
        s.add_all([
            ListingPublication(tenant_id=w["tenant"], content_id=content.id, connection_id=a, etsy_listing_id=1, state="draft", created_at=t0),
            ListingPublication(tenant_id=w["tenant"], content_id=content.id, connection_id=b, etsy_listing_id=2, state="active",
                               created_at=t0 + timedelta(hours=1)),
            ListingPublication(tenant_id=w["tenant"], content_id=None, connection_id=a, etsy_listing_id=3, state="draft", created_at=t0),
            ListingPublication(tenant_id=w["tenant"], content_id=None, former_content_id=w["contents"][1], connection_id=a,
                               etsy_listing_id=4, state="deleted_on_etsy", created_at=t0),
        ])
        await s.commit()


async def test_backfill_matches_the_publication_records(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=2, listings=2)
    await _history(w)
    async with w["sm"]() as s:
        assert await usage.backfill(s) == 3  # listing 1 (two shops), the orphaned draft, the deleted one
        await s.commit()
        rows = list((await s.execute(select(UsageEvent))).scalars())
        first = next(r for r in rows if r.content_id == w["contents"][0])
        pub = (await s.execute(select(ListingPublication).where(ListingPublication.etsy_listing_id == 1))).scalar_one()
        assert first.publication_id == pub.id  # its first draft, and that draft's time
        assert (await usage.reconcile(s)).clean
        assert await usage.backfill(s) == 0  # again: nothing more
    await w["engine"].dispose()


def test_the_migration_backfills_with_the_same_query() -> None:
    migration = (ROOT / "backend/alembic/versions/0099_usage_event.py").read_text(encoding="utf-8")
    assert usage.BACKFILL.strip() in migration


async def test_reconciliation_finds_a_missing_row_and_an_orphan(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=2, listings=2)
    await _history(w)
    async with w["sm"]() as s:
        await usage.backfill(s)
        missing = (await s.execute(select(UsageEvent).where(UsageEvent.content_id == w["contents"][0]))).scalar_one()
        await s.delete(missing)
        s.add(UsageEvent(tenant_id=w["tenant"], listing_key=str(uuid.uuid4()), connection_id=w["shops"][0],
                         at=datetime.now(timezone.utc), source="draft"))
        await s.commit()
        found = await usage.reconcile(s)
    assert [k for _, k in found.missing] == [str(w["contents"][0])]
    assert len(found.orphans) == 1
    # The daily job tells the operator, counts and ids only; it changes nothing.
    from app.workers import usage as usage_job

    sent: list[str] = []

    async def capture(text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(usage_job.alerts, "send", capture)
    assert await usage_job.reconcile_usage(w["ctx"]) == "missing:1 orphans:1"
    assert "Drafted, not counted: 1" in sent[0] and "Counted, no draft: 1" in sent[0]
    async with w["sm"]() as s:
        assert await s.scalar(select(func.count()).select_from(UsageEvent)) == 3  # nothing added or deleted
        # usage-reconcile --apply adds the missing row and only lists the orphan.
        assert await usage.backfill(s, source="reconcile") == 1
        await s.commit()
        again = await usage.reconcile(s)
    assert not again.missing and len(again.orphans) == 1
    await w["engine"].dispose()


async def test_a_disconnected_shops_counts_are_history_not_orphans(tmp_path, test_settings: Settings, monkeypatch) -> None:
    w = await _world(tmp_path, test_settings, monkeypatch, shops=1, listings=1)
    await _run_drafts(w, await _send_drafts(w))
    async with w["sm"]() as s:
        for p in (await s.execute(select(ListingPublication))).scalars():
            await s.delete(p)  # purged with the shop
        shop = await s.get(EtsyConnection, w["shops"][0])
        from app.db.models import ConnectionStatus

        shop.status = ConnectionStatus.revoked
        await s.commit()
        assert (await usage.reconcile(s)).clean
    await w["engine"].dispose()


_ = _png  # imported for the world's images
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")
