"""STEP 0: publication records are never hard-deleted while their shop is
connected, and every destructive action is audited with actor, account, shop
and object id (no personal data).

Production lost a profile and two publication records between two backups
with nothing in the audit log; these pin that it cannot happen silently again.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.api import deps
from app.core import audit
from app.db.models import (
    Asset,
    AuditLog,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingPublication,
)
from app.etsy.errors import EtsyClientError
from app.etsy.publisher import publication_for, publish_live
from app.pipeline.storage import LocalStorage
from tests.test_publish_api import OWNER_EMAIL, _add_content, _shop, ctx  # noqa: F401  (fixture)
from tests.test_publisher import FakeEtsy

APP = pathlib.Path(__file__).resolve().parents[1] / "app"


async def _audit(ctx, action: str) -> list[AuditLog]:
    async with ctx["sm"]() as s:
        return list((await s.execute(select(AuditLog).where(AuditLog.action == action))).scalars())


def _assert_identifies(entry: AuditLog, *, tenant_id, shop_id, object_id, by="seller") -> None:
    assert entry.target_tenant_id == tenant_id
    assert entry.details["shop_id"] == (str(shop_id) if shop_id is not None else None)
    assert entry.details["object_id"] == str(object_id)
    assert entry.details["by"] == by
    if by == "seller":
        assert entry.actor_tenant_id == tenant_id
    assert OWNER_EMAIL not in str(entry.details)  # ids only, never personal data


async def _schedule(ctx, content_id, shop) -> None:
    run_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    body = {"items": [{"content_id": str(content_id), "connection_id": str(shop), "run_at": run_at}]}
    assert (await ctx["client"].post("/api/schedules", json=body)).json()["skipped"] == []


async def _publication(ctx, content_id) -> ListingPublication:
    async with ctx["sm"]() as s:
        return (await s.execute(
            select(ListingPublication).where(ListingPublication.content_id == content_id))).scalars().one()


# --- no hard delete while the shop is connected -------------------------------------------------


def test_only_the_disconnect_purge_deletes_publication_rows() -> None:
    """A static guard: no code path but the shop's disconnect purge deletes a
    publication. New code that deletes one breaks this test on purpose."""
    found: list[str] = []
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                text = ast.unparse(node)
                if text.startswith("delete(ListingPublication") or (
                    text.startswith("session.delete(") and "publication" in text.lower()
                ):
                    found.append(f"{path.relative_to(APP)}::{fn.name}")
    assert sorted(set(found)) == ["workers/retention.py::purge_shop_etsy_content"]


class _Gone(FakeEtsy):
    async def update_listing(self, shop_id, listing_id, **_):  # noqa: ANN001
        raise EtsyClientError(404, body="{}", path=f"/application/shops/900/listings/{listing_id}", method="PATCH")

    async def get_listing(self, listing_id, **_):  # noqa: ANN001
        raise EtsyClientError(404, body="{}", path=f"/application/listings/{listing_id}", method="GET")


async def test_a_draft_gone_on_etsy_is_marked_its_schedule_withdrawn_and_audited(ctx) -> None:
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
    await _schedule(ctx, content_id, shop)
    publication = await _publication(ctx, content_id)
    async with ctx["sm"]() as s:
        released = Job(tenant_id=ctx["tenant_id"], connection_id=shop, type=JobType.publish_live,
                       payload={"content_id": str(content_id)}, status=JobStatus.queued)
        s.add(released)
        await s.flush()
        (await s.get(ListingPublication, publication.id)).schedule_job_id = released.id
        runner = Job(tenant_id=ctx["tenant_id"], connection_id=shop, type=JobType.publish_live,
                     payload={"content_id": str(content_id)}, status=JobStatus.running)
        s.add(runner)
        await s.commit()
        released_id, runner_id = released.id, runner.id

    async with ctx["sm"]() as s:
        with pytest.raises(ValueError, match="no longer on Etsy"):
            await publish_live(s, job_id=runner_id, content=await s.get(GeneratedContent, content_id),
                               connection=await s.get(EtsyConnection, shop), client=_Gone(),
                               access_token="tok", tenant_limit=2000)

    async with ctx["sm"]() as s:
        kept = await s.get(ListingPublication, publication.id)
        assert kept is not None and kept.state == "deleted_on_etsy" and kept.deleted_on_etsy_at is not None
        assert kept.content_id is None and kept.etsy_listing_id == 777
        assert kept.scheduled_for is None and kept.schedule_job_id is None
        assert (await s.get(Job, released_id)).status is JobStatus.cancelled  # nothing left to publish
        assert await publication_for(s, content_id, shop) is None  # it can be drafted again
    [entry] = await _audit(ctx, "publication.deleted_on_etsy")
    _assert_identifies(entry, tenant_id=ctx["tenant_id"], shop_id=shop, object_id=publication.id, by="system")
    assert entry.actor_tenant_id is None
    assert entry.details["etsy_listing_id"] == 777 and entry.details["previous_state"] == "draft"
    assert entry.details["schedule_cleared"] is True and entry.details["job_id"] == str(runner_id)

    # Drafting the same listing again makes a second record beside the kept one.
    async with ctx["sm"]() as s:
        s.add(ListingPublication(tenant_id=ctx["tenant_id"], content_id=content_id, connection_id=shop,
                                 etsy_listing_id=778, state="draft"))
        await s.commit()
        states = sorted(p.state for p in (await s.execute(select(ListingPublication))).scalars())
    assert states == ["deleted_on_etsy", "draft"]
    # Each page that lists the listing's drafts shows only the live one.
    batch = (await ctx["client"].get("/api/batches")).json()
    rows = (await ctx["client"].get(f"/api/batches/{batch[0]['id']}/content")).json()
    [item] = [r for r in rows if r["id"] == str(content_id)]
    assert [p["etsy_listing_id"] for p in item["publications"]] == [778]


async def test_marking_twice_writes_one_audit_row(ctx) -> None:
    from app.etsy.publisher import mark_deleted_on_etsy

    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    publication = await _publication(ctx, content_id)
    for _ in range(2):
        async with ctx["sm"]() as s:
            await mark_deleted_on_etsy(s, await s.get(ListingPublication, publication.id))
            await s.commit()
    assert len(await _audit(ctx, "publication.deleted_on_etsy")) == 1


# --- every destructive action is audited ----------------------------------------------------------


async def test_deleting_a_profile_is_audited(ctx) -> None:
    await _add_content(ctx["sm"], ctx["tenant_id"])
    async with ctx["sm"]() as s:
        profile = (await s.execute(select(ListingProfile))).scalars().one()
    assert (await ctx["client"].delete(f"/api/profiles/{profile.id}")).status_code == 204
    [entry] = await _audit(ctx, "profile.deleted")
    _assert_identifies(entry, tenant_id=ctx["tenant_id"], shop_id=profile.connection_id, object_id=profile.id)
    assert entry.details["confirmed"] is True


async def test_deleting_a_batch_is_audited_with_its_shop_and_cancelled_schedules(ctx, tmp_path) -> None:
    ctx["app"].dependency_overrides[deps.get_storage] = lambda: LocalStorage(tmp_path)
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
        batch_id = (await s.get(GeneratedContent, content_id)).batch_id
    await _schedule(ctx, content_id, shop)
    publication = await _publication(ctx, content_id)

    assert (await ctx["client"].delete(f"/api/batches/{batch_id}")).status_code == 200
    [entry] = await _audit(ctx, "batch.deleted")
    _assert_identifies(entry, tenant_id=ctx["tenant_id"], shop_id=None, object_id=batch_id)
    [cancelled] = await _audit(ctx, "schedule.cancelled")
    _assert_identifies(cancelled, tenant_id=ctx["tenant_id"], shop_id=shop, object_id=publication.id)
    assert cancelled.details["reason"] == "batch deleted"
    async with ctx["sm"]() as s:
        assert await s.get(ListingPublication, publication.id) is not None  # detached, kept


async def test_deleting_an_image_is_audited(ctx, tmp_path) -> None:
    ctx["app"].dependency_overrides[deps.get_storage] = lambda: LocalStorage(tmp_path)
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
        asset_id = (await s.get(GeneratedContent, content_id)).asset_id
        (await s.get(Asset, asset_id)).processed_key = "p.jpg"
        await s.commit()
    await _schedule(ctx, content_id, shop)

    resp = await ctx["client"].delete(f"/api/assets/{asset_id}")
    assert resp.status_code == 200, resp.text
    [entry] = await _audit(ctx, "image.deleted")
    _assert_identifies(entry, tenant_id=ctx["tenant_id"], shop_id=None, object_id=asset_id)
    assert entry.details["group_removed"] is True
    [cancelled] = await _audit(ctx, "schedule.cancelled")
    assert cancelled.details["reason"] == "images deleted" and cancelled.details["shop_id"] == str(shop)


async def test_cancelling_a_schedule_is_audited_by_the_seller_and_on_unapproval(ctx) -> None:
    first = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    second = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=778)
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
    await _schedule(ctx, first, shop)
    await _schedule(ctx, second, shop)

    assert (await ctx["client"].delete(f"/api/schedules/{first}/{shop}")).status_code == 204
    resp = await ctx["client"].post(f"/api/content/{second}/approve", json={"approved": False})
    assert resp.status_code == 200, resp.text

    entries = {e.details["reason"]: e for e in await _audit(ctx, "schedule.cancelled")}
    assert set(entries) == {"seller cancelled", "approval withdrawn"}
    _assert_identifies(entries["seller cancelled"], tenant_id=ctx["tenant_id"], shop_id=shop,
                       object_id=(await _publication(ctx, first)).id)
    _assert_identifies(entries["approval withdrawn"], tenant_id=ctx["tenant_id"], shop_id=shop,
                       object_id=(await _publication(ctx, second)).id)


async def test_disconnecting_a_shop_is_audited_with_what_was_removed(ctx) -> None:
    await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
    assert (await ctx["client"].post(f"/api/shops/{shop}/disconnect")).status_code == 200
    [entry] = await _audit(ctx, "shop.disconnected")
    _assert_identifies(entry, tenant_id=ctx["tenant_id"], shop_id=shop, object_id=shop)
    assert entry.details["publications"] == 1 and entry.details["profiles"] == 1
    async with ctx["sm"]() as s:  # the shop is no longer connected: its Etsy content goes (CLAUDE.md)
        assert (await s.execute(select(ListingPublication))).first() is None


def test_an_unknown_destructive_action_is_refused() -> None:
    with pytest.raises(ValueError):
        audit.destructive(None, "thing.deleted", actor=None, tenant_id=None, shop_id=None, object_id="x")  # type: ignore[arg-type]
