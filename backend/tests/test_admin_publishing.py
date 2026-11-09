"""Admin → Publishing (fix/usage-and-admin-publishing, Part 2).

The screen reads publish-report's own code, so its states and counts are the report's; it is read only and
admin only; times show in Istanbul and the seller's zone; the needs-attention rules; and the overview
loads in under a second with 10 accounts × 2,000 listings.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, insert, select

from app.api import admin_publishing as A
from app.db.models import (
    Asset,
    AssetStatus,
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    GroupPlan,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    PlannedSlot,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.ops import publish_report as R
from tests.auth_support import authenticate, open_session
from tests.test_publish_api import ctx  # noqa: F401  (fixture)

#: Per 100 listings: what each one is (the rest are published).
SHAPE = {0: "upcoming draft", 1: "upcoming draft", 2: "upcoming draft", 3: "upcoming draft", 4: "upcoming draft",
         5: "upcoming slot", 6: "upcoming slot", 7: "missed", 8: "refused", 9: "waiting draft", 10: "never sent",
         11: "not approved", 12: "not approved", 13: "fresh draft"}


async def build(sm, *, accounts: int, listings: int, now: datetime, zone: str = "America/Chicago") -> list[uuid.UUID]:  # noqa: ANN001
    """Fixture accounts shaped like the early-access shops, inserted in bulk."""
    tenants = []
    async with sm() as s:
        for n in range(accounts):
            tenant = Tenant(email=f"seller{n:02d}@example.test", password_hash="!", time_zone=zone)
            s.add(tenant)
            await s.flush()
            shop = EtsyConnection(tenant_id=tenant.id, status=ConnectionStatus.active, etsy_user_id=1000 + n,
                                  shop_id=2000 + n, shop_name=f"Shop {n:02d}")
            batch = UploadBatch(tenant_id=tenant.id, status=UploadBatchStatus.ready, file_count=listings)
            s.add_all([shop, batch])
            await s.flush()
            plan = GroupPlan(tenant_id=tenant.id, batch_id=batch.id, settings={})
            s.add(plan)
            await s.flush()
            assets, contents, pubs, slots, jobs = [], [], [], [], []
            for i in range(listings):
                kind = SHAPE.get(i % 100, "published")
                aid, cid = uuid.uuid4(), uuid.uuid4()
                assets.append({"id": aid, "batch_id": batch.id, "tenant_id": tenant.id, "original_filename": f"D{i}.png",
                               "parsed_sku": f"SKU{n:02d}{i:04d}", "storage_key": "k", "status": AssetStatus.processed, "rank": 1})
                contents.append({"id": cid, "tenant_id": tenant.id, "batch_id": batch.id, "asset_id": aid,
                                 "title": f"Listing {i}", "tags": ["tee"], "description": "Body.",
                                 "approved": kind != "not approved", "created_at": now - timedelta(days=6)})
                pub = {"id": uuid.uuid4(), "tenant_id": tenant.id, "content_id": cid, "connection_id": shop.id,
                       "etsy_listing_id": 10_000_000 * (n + 1) + i, "state": "draft", "title": f"Listing {i}",
                       "created_at": now - timedelta(hours=20), "scheduled_for": None, "published_at": None, "published_by": None}
                if kind == "upcoming draft":
                    pubs.append({**pub, "scheduled_for": now + timedelta(days=1 + i % 6, hours=2)})
                elif kind == "upcoming slot":
                    slots.append({"id": uuid.uuid4(), "tenant_id": tenant.id, "plan_id": plan.id, "content_id": cid,
                                  "connection_id": shop.id, "draft_at": now + timedelta(days=2), "publish_at": now + timedelta(days=2, hours=2),
                                  "state": "waiting"})
                elif kind == "missed":
                    pubs.append({**pub, "scheduled_for": now - timedelta(hours=2)})
                elif kind == "refused":
                    jobs.append({"id": uuid.uuid4(), "tenant_id": tenant.id, "connection_id": shop.id, "type": JobType.create_draft,
                                 "status": JobStatus.failed, "payload": {"content_id": str(cid), "etsy_error": {"status": 400, "reason": "refused"}},
                                 "created_at": now - timedelta(days=1), "finished_at": now - timedelta(days=1), "last_error": "Etsy refused it"})
                elif kind == "waiting draft":
                    pubs.append({**pub, "created_at": now - timedelta(days=5)})
                elif kind == "fresh draft":
                    pubs.append(pub)
                elif kind == "published":
                    pubs.append({**pub, "state": "active", "published_at": now - timedelta(days=1 + i % 5),
                                 "published_by": "seller_on_etsy" if i % 50 == 20 else None})
            for model, rows in ((Asset, assets), (GeneratedContent, contents), (ListingPublication, pubs),
                                (PlannedSlot, slots), (Job, jobs)):
                if rows:
                    await s.execute(insert(model), rows)
            tenants.append(tenant.id)
        await s.commit()
    return tenants


async def _admin(ctx) -> None:  # noqa: F811
    async with ctx["sm"]() as s:
        admin = Tenant(email="admin@example.test", password_hash="!", is_admin=True)
        s.add(admin)
        await s.commit()
        admin_id = admin.id
    authenticate(ctx["client"], await open_session(ctx["redis"], admin_id))


async def test_a_seller_is_refused_every_publishing_screen(ctx) -> None:  # noqa: F811
    """The existing admin check: anyone else learns nothing, not even that the screens exist (404)."""
    seller = ctx["tenant_id"]
    for path in ("/api/admin/publishing", f"/api/admin/users/{seller}/publishing",
                 f"/api/admin/users/{seller}/publishing/detail?content={uuid.uuid4()}"):
        assert (await ctx["client"].get(path)).status_code == 404, path


async def test_the_states_and_counts_are_publish_reports(ctx) -> None:  # noqa: F811
    now = datetime.now(timezone.utc)
    [tenant_id] = await build(ctx["sm"], accounts=1, listings=100, now=now)
    await _admin(ctx)
    body = (await ctx["client"].get(f"/api/admin/users/{tenant_id}/publishing")).json()
    report = await R.report(ctx["sm"], "seller00@example.test")
    summary = report.split("== summary")[1].split("\n\n")[0]
    for line in body["summary_text"]:
        assert line in summary, line  # the same lines, word for word
    counts = {line["outcome"]: line["count"] for line in body["summary"]}
    assert counts[R.PUBLISHED] == 84 and counts[R.BY_SELLER] == 2 and counts[R.MISSED] == 1
    assert counts[R.WAITING] == 7 and counts[R.REFUSED] == 1 and counts[R.NOT_APPROVED] == 2
    # Filters and the search, and one listing's detail exactly as the report prints it.
    missed = (await ctx["client"].get(f"/api/admin/users/{tenant_id}/publishing", params={"state": R.MISSED})).json()
    assert [i["sku"] for i in missed["items"]] == ["SKU000007"]
    found = (await ctx["client"].get(f"/api/admin/users/{tenant_id}/publishing", params={"q": "sku000008"})).json()
    assert found["total"] == 1 and found["items"][0]["state"] == A.REFUSED
    by_id = (await ctx["client"].get(f"/api/admin/users/{tenant_id}/publishing", params={"q": "10000007"})).json()
    assert by_id["total"] == 1
    item = missed["items"][0]
    detail = (await ctx["client"].get(f"/api/admin/users/{tenant_id}/publishing/detail",
                                      params={"content": item["content_id"], "shop": item["shop_id"]})).json()
    block = report.split(f"== Shop 00 · {item['etsy_listing_id']}")[1].split("\n\n")[0]
    assert detail["lines"][0].startswith(f"== Shop 00 · {item['etsy_listing_id']}")
    assert "\n".join(detail["lines"][1:]) in block


async def test_times_show_in_istanbul_and_the_sellers_zone(ctx) -> None:  # noqa: F811
    at = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
    assert A.local(at, A.ISTANBUL) == "Nov 20, 21:00 GMT+3"
    assert A.local(at, "America/Chicago") == "Nov 20, 12:00 CST"
    now = datetime.now(timezone.utc)
    await build(ctx["sm"], accounts=1, listings=100, now=now, zone="America/Chicago")
    await _admin(ctx)
    body = (await ctx["client"].get("/api/admin/publishing")).json()
    row = body["upcoming"]["rows"][0]
    assert row["seller_zone"] == "America/Chicago"
    assert row["go_live_istanbul"].endswith("GMT+3") and row["go_live_seller"].endswith(("CST", "CDT"))
    # Grouped by account, then by go-live time.
    times = [r["go_live_at"] for r in body["upcoming"]["rows"]]
    assert times == sorted(times) and body["upcoming"]["total"] == 7
    assert {r["state"] for r in body["upcoming"]["rows"]} == {A.DRAFTED, A.WAITING}


async def test_the_needs_attention_rules(ctx) -> None:  # noqa: F811
    now = datetime.now(timezone.utc)
    await build(ctx["sm"], accounts=1, listings=100, now=now)
    await _admin(ctx)
    rows = (await ctx["client"].get("/api/admin/publishing")).json()["attention"]["rows"]
    by_sku = {r["sku"]: r for r in rows}
    assert by_sku["SKU000007"]["state"] == A.MISSED  # its go-live time passed
    assert by_sku["SKU000008"]["state"] == A.REFUSED  # Etsy refused the draft
    assert "approved draft waiting 5 days with no go-live time" in by_sku["SKU000009"]["why"]
    assert by_sku["SKU000010"]["outcome"] == R.NEVER_SENT  # what requeue-stuck lists
    # Not there: a draft made today, live listings, scheduled ones, listings not approved.
    assert "SKU000013" not in by_sku and "SKU000020" not in by_sku and "SKU000000" not in by_sku
    assert "SKU000011" not in by_sku
    assert len(rows) == 4


async def test_the_last_seven_days_and_the_counted_listings(ctx) -> None:  # noqa: F811
    now = datetime.now(timezone.utc)
    await build(ctx["sm"], accounts=1, listings=100, now=now)
    await _admin(ctx)
    body = (await ctx["client"].get("/api/admin/publishing")).json()
    totals = {k: sum(d[k] for d in body["activity"]) for k in ("drafts", "live_by_app", "by_seller", "failures")}
    # Drafts made in the 7 days: 5 upcoming + 1 missed + 1 waiting + 1 fresh + 86 published.
    assert totals == {"drafts": 94, "live_by_app": 84, "by_seller": 2, "failures": 1}
    assert isinstance(body["counted"], list)


async def test_the_overview_loads_in_under_a_second_with_ten_accounts_of_2000(ctx) -> None:  # noqa: F811
    now = datetime.now(timezone.utc)
    await build(ctx["sm"], accounts=10, listings=2000, now=now)
    async with ctx["sm"]() as s:
        assert await s.scalar(select(func.count()).select_from(GeneratedContent)) >= 20_000
    await _admin(ctx)
    await ctx["client"].get("/api/admin/publishing")  # warm
    started = time.perf_counter()
    res = await ctx["client"].get("/api/admin/publishing")
    took = time.perf_counter() - started
    assert res.status_code == 200
    body = res.json()
    assert body["upcoming"]["total"] == 10 * 20 * 7 and len(body["upcoming"]["rows"]) == A.PER_PAGE
    assert body["attention"]["total"] == 10 * 20 * 4
    print(f"\nadmin publishing overview: {took * 1000:.0f} ms for 10 accounts x 2,000 listings")
    assert took < 1.0, took
