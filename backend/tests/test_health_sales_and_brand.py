"""Listing health: reading sales from the page, the shop's sync on opening, and brand names the seller keeps
(fix/health-sales-and-brand; api/listing_health.py, workers/sales.py::queue_reads/advance_queue).

The sales line only for shops never read; "Read sales for all" queues them one after another as upkeep, each with
Reports' estimate and first read; a finished read moves listings out of "Not judged"; opening the page with a stale
copy asks for one sync (the ten-minute throttle holds). "Keep it, I know the risk": out of Fix now, a new brand word
brings it back, "Show the warning again", the audit row, only for brand names, another account 404. The report.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import AuditLog, BrandKept, EtsyConnection, SalesSync, ShopListingCache
from app.ops import listing_health_report as report
from app.pipeline import listing_health as H
from app.workers import gate
from app.workers import sales as sales_worker
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_listing_health import GOOD_TAGS, Queue, listing, world

SCOPES = ["listings_r", "listings_w", "shops_r", "shops_w", "transactions_r"]


async def _shops(client: AsyncClient, states: list[str | None]) -> tuple[Queue, list[Any]]:  # noqa: F811
    """One shop per state (None: never read). Returns the queue and the shops' ids, in order."""
    from app.api import deps

    queue = Queue()
    client.app.dependency_overrides[deps.get_enqueuer] = lambda: queue  # type: ignore[attr-defined]
    tid = client.tenant_id  # type: ignore[attr-defined]
    ids = []
    async with client.sm() as s:  # type: ignore[attr-defined]
        for n, state in enumerate(states):
            shop = EtsyConnection(tenant_id=tid, etsy_user_id=100 + n, shop_id=100 + n, shop_name=f"Shop {n}", scopes=SCOPES, position=n)
            s.add(shop)
            await s.flush()
            if state is not None:
                s.add(SalesSync(connection_id=shop.id, tenant_id=tid, state=state, window_count=900 if state == "estimated" else None,
                                pages_estimate=9 if state == "estimated" else None, window_start=datetime(2025, 11, 1).date()))
            ids.append(shop.id)
        await s.commit()
    return queue, ids


async def _sales(client: AsyncClient) -> dict:  # noqa: F811
    return (await client.get("/api/listing-health/sales")).json()


# --- the sales line -----------------------------------------------------------------------------------------


async def test_the_sales_line_is_only_for_shops_whose_sales_were_never_read(client: AsyncClient) -> None:  # noqa: F811
    _, ids = await _shops(client, ["complete", None, "estimated", "reading"])
    page = await _sales(client)
    by = {s["connection_id"]: s for s in page["shops"]}
    assert [by[str(i)]["needs_read"] for i in ids] == [False, True, True, False]
    assert [by[str(i)]["state"] for i in ids] == ["complete", "never", "counted", "reading"]
    assert page["to_read"] == 2 and "read this shop's sales first" in page["read_line"]
    # Reports' own estimate for a counted shop, and no Etsy request count to the seller (A3).
    assert by[str(ids[2])]["window_count"] == 900 and by[str(ids[2])]["days_estimate"] == 1
    assert "pages_estimate" not in str(page) and "requests" not in str(page)
    assert by[str(ids[0])]["known"] is True and by[str(ids[1])]["known"] is False


async def test_read_sales_for_all_reads_them_one_after_another_as_upkeep(client: AsyncClient) -> None:  # noqa: F811
    queue, ids = await _shops(client, ["complete", None, None, "estimated"])
    page = (await client.post("/api/listing-health/sales/read", json={})).json()
    by = {s["connection_id"]: s for s in page["shops"]}
    # The first never-read shop is counted now; the others wait their turn. Nothing for the shop already read.
    assert [c[1][0] for c in queue.named("estimate_sales")] == [str(ids[1])]
    assert not queue.named("sync_sales")
    assert by[str(ids[1])]["state"] == "counting" and not by[str(ids[1])]["waiting_turn"]
    assert by[str(ids[2])]["state"] == "queued" and by[str(ids[2])]["waiting_turn"] and "one after another" in by[str(ids[2])]["note"]
    assert by[str(ids[3])]["state"] == "queued" and by[str(ids[0])]["state"] == "complete"
    # Every job on this path is upkeep: the app's Etsy budget and each shop's daily share, not the seller's ceiling.
    assert {"estimate_sales", "sync_sales", "estimate_ledger", "sync_ledger"} <= gate.UPKEEP

    async def counted(i: int) -> None:
        async with client.sm() as s:  # type: ignore[attr-defined]
            sync = await s.get(SalesSync, ids[i])
            sync.state, sync.window_count, sync.pages_estimate, sync.window_start = "estimated", 300, 3, datetime(2025, 11, 1).date()
            await s.commit()

    async def advance() -> Any:
        async with client.sm() as s:  # type: ignore[attr-defined]
            return await sales_worker.advance_queue(queue.enqueue, s, client.tenant_id)  # type: ignore[attr-defined]

    # Counted: Reports' "Start reading" path begins its read; still nobody else's turn.
    await counted(1)
    assert await advance() == ids[1]
    assert [c[1][0] for c in queue.named("sync_sales")] == [str(ids[1])]
    assert await advance() == ids[1] and len(queue.named("sync_sales")) == 1  # asking again starts nothing more
    # Paused until tomorrow (its own daily share is used): the next shop may go.
    async with client.sm() as s:  # type: ignore[attr-defined]
        (await s.get(SalesSync, ids[1])).state = "waiting"
        await s.commit()
    assert await advance() == ids[2]
    assert [c[1][0] for c in queue.named("estimate_sales")] == [str(ids[1]), str(ids[2])]
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.get(SalesSync, ids[1])).queued_at is None  # its turn is over; it carries on by itself
    # Read: the shop counted earlier on Reports starts without a second count.
    async with client.sm() as s:  # type: ignore[attr-defined]
        (await s.get(SalesSync, ids[2])).state = "complete"
        await s.commit()
    assert await advance() == ids[3]
    assert str(ids[3]) in [c[1][0] for c in queue.named("sync_sales")] and len(queue.named("estimate_sales")) == 2
    # Pressing it again changes nothing: each shop is read or in the line.
    assert (await client.post("/api/listing-health/sales/read", json={"connection_ids": [str(ids[0])]})).status_code == 202
    assert len(queue.named("estimate_sales")) == 2


async def test_the_count_finishing_in_the_worker_starts_the_queued_read(client: AsyncClient, monkeypatch) -> None:  # noqa: F811, ANN001
    queue, ids = await _shops(client, [None])
    await client.post("/api/listing-health/sales/read", json={"connection_ids": [str(ids[0])]})

    async def gated(ctx, function, arg, tenant_id, body):  # noqa: ANN001
        async with client.sm() as s:  # type: ignore[attr-defined]
            sync = await s.get(SalesSync, ids[0])
            sync.state, sync.window_count, sync.pages_estimate, sync.window_start = "estimated", 50, 1, datetime(2025, 11, 1).date()
            await s.commit()
        return "estimate:50"

    monkeypatch.setattr(sales_worker, "_run_gated", gated)
    ctx = {"sessionmaker": client.sm, "enqueue": queue.enqueue}  # type: ignore[attr-defined]
    assert await sales_worker.estimate_sales(ctx, str(ids[0])) == "estimate:50"
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.get(SalesSync, ids[0])).state == "reading"
    assert [c[1][0] for c in queue.named("sync_sales")] == [str(ids[0])]


async def test_a_shop_without_the_sales_permission_is_not_read_and_says_so(client: AsyncClient) -> None:  # noqa: F811
    _, ids = await _shops(client, [None])
    async with client.sm() as s:  # type: ignore[attr-defined]
        (await s.get(EtsyConnection, ids[0])).scopes = ["listings_r"]
        await s.commit()
    page = await _sales(client)
    assert page["shops"][0]["state"] == "no_permission" and page["to_read"] == 0 and "Connect it again" in page["shops"][0]["note"]
    assert (await client.post("/api/listing-health/sales/read", json={})).status_code == 409


async def test_once_the_read_ends_listings_move_out_of_not_judged(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(902, title="Cute Shirt, Free Shipping"), listing(904)], sales={904: 3}, sales_read=False)
    before = (await client.get("/api/listing-health", params={"shop": str(w["shop"]), "show": "all"})).json()
    assert before["sales_known"] is False and before["counts"]["not_judged"] == 2
    async with client.sm() as s:  # type: ignore[attr-defined]
        s.add(SalesSync(connection_id=w["shop"], tenant_id=w["tid"], state="complete"))
        await s.commit()
    after = (await client.get("/api/listing-health", params={"shop": str(w["shop"]), "show": "all"})).json()
    assert after["sales_known"] is True and after["counts"]["not_judged"] == 0
    assert {l["listing_id"]: l["bucket"] for l in after["listings"]}[904] == "selling"


async def test_another_accounts_shop_cannot_be_read(client: AsyncClient) -> None:  # noqa: F811
    from tests.auth_support import make_tenant

    await _shops(client, [None])
    other = await make_tenant(client.sm, "other@example.com")  # type: ignore[attr-defined]
    async with client.sm() as s:  # type: ignore[attr-defined]
        theirs = EtsyConnection(tenant_id=other, etsy_user_id=77, shop_id=77, shop_name="Theirs", scopes=SCOPES)
        s.add(theirs)
        await s.commit()
    assert (await client.post("/api/listing-health/sales/read", json={"connection_ids": [str(theirs.id)]})).status_code == 404
    assert "Theirs" not in str(await _sales(client))
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert await s.get(SalesSync, theirs.id) is None


# --- the shop's sync on opening -----------------------------------------------------------------------------


async def test_opening_listing_health_with_a_stale_copy_asks_for_one_sync(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client)
    async with client.sm() as s:  # type: ignore[attr-defined]
        for row in (await s.execute(select(ShopListingCache))).scalars():
            row.fetched_at = datetime.now(timezone.utc) - timedelta(hours=7)
        await s.commit()
    for _ in range(3):
        page = (await client.get("/api/listing-health", params={"shop": str(w["shop"])})).json()
        assert page["reading"] is True and page["syncing"] is True
    assert len(w["queue"].named("sync_shop_listings")) == 1  # once, however often the page asks


async def test_an_empty_copy_is_read_too_and_a_shop_just_read_with_no_listings_is_current(client: AsyncClient) -> None:  # noqa: F811
    from app.etsy import shop_sync

    w = await world(client, rows=[])
    async with client.sm() as s:  # type: ignore[attr-defined]
        for row in (await s.execute(select(ShopListingCache))).scalars():
            await s.delete(row)
        await s.commit()
    page = (await client.get("/api/listing-health", params={"shop": str(w["shop"])})).json()
    assert page["reading"] is True and len(w["queue"].named("sync_shop_listings")) == 1
    await shop_sync.ended(client.redis, w["shop"], read=True)  # type: ignore[attr-defined]
    page = (await client.get("/api/listing-health", params={"shop": str(w["shop"])})).json()
    assert page["reading"] is False and page["total"] == 0 and len(w["queue"].named("sync_shop_listings")) == 1


# --- "Keep it, I know the risk" ---------------------------------------------------------------------------------


async def _keep(client: AsyncClient, w: dict, listing_id: int, path: str = "keep") -> Any:  # noqa: F811
    return await client.post(f"/api/listing-health/brands/{path}", json={"connection_id": str(w["shop"]), "listing_id": listing_id})


async def _row(client: AsyncClient, w: dict, listing_id: int) -> dict:  # noqa: F811
    page = (await client.get("/api/listing-health", params={"shop": str(w["shop"]), "show": "all"})).json()
    return next(l for l in page["listings"] if l["listing_id"] == listing_id)


async def test_keeping_a_brand_moves_the_listing_out_of_fix_now_until_a_new_brand_word(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(905, title="Disneyland Trip Family Shirt"), listing(906, title="Epcot Trip Shirt")], sales={905: 4})
    row = await _row(client, w, 905)
    assert row["bucket"] == "fix_now" and row["problems"][0]["id"] == "brand" and "keep_brand" in row["problems"][0]["fixes"]
    assert row["problems"][0]["sentence"].startswith("Uses the name 'Disneyland', a trademark of another company.")
    res = await _keep(client, w, 905)
    assert res.status_code == 200 and res.json()["kept_brands"] == ["Disneyland"]
    row = await _row(client, w, 905)
    assert row["bucket"] == "selling" and row["problems"] == [] and row["kept_brands"] == ["Disneyland"] and row["brands"] == ["Disneyland"]
    # One that does not sell: nothing to fix (it has no other problem).
    await _keep(client, w, 906)
    assert (await _row(client, w, 906))["bucket"] == "nothing_to_fix"
    # The audit names who, which listing and which word.
    async with client.sm() as s:  # type: ignore[attr-defined]
        rows = list((await s.execute(select(AuditLog).where(AuditLog.action == "listing.brand_kept"))).scalars())
        kept = list((await s.execute(select(BrandKept).where(BrandKept.listing_id == 905))).scalars())
    assert len(rows) == 2 and rows[0].actor_tenant_id == w["tid"]
    assert {(r.details["listing_id"], tuple(r.details["brands"])) for r in rows} == {(905, ("Disneyland",)), (906, ("Epcot",))}
    assert [(k.connection_id, k.brand) for k in kept] == [(w["shop"], "Disneyland")]
    # A new brand word on the same listing brings the warning back, for that word only.
    async with client.sm() as s:  # type: ignore[attr-defined]
        cache = await s.get(ShopListingCache, (w["tid"], 905))
        cache.payload = {**cache.payload, "tags": ["mickey ears", *GOOD_TAGS[:12]]}
        await s.commit()
    row = await _row(client, w, 905)
    assert row["bucket"] == "fix_now" and row["kept_brands"] == ["Disneyland"]
    assert row["problems"][0]["sentence"].startswith("Uses the name 'Mickey'")


async def test_show_the_warning_again(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(905, title="Disneyland Trip Family Shirt")], sales={905: 4})
    await _keep(client, w, 905)
    assert (await _row(client, w, 905))["bucket"] == "selling"
    res = await _keep(client, w, 905, "warn")
    assert res.status_code == 200 and res.json()["kept_brands"] == []
    assert (await _row(client, w, 905))["bucket"] == "fix_now"
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert not list((await s.execute(select(BrandKept))).scalars())
        assert (await s.scalar(select(AuditLog).where(AuditLog.action == "listing.brand_warning_restored"))) is not None
    assert (await _keep(client, w, 905, "warn")).status_code == 409


async def test_keeping_is_only_for_brand_names_never_another_fix_now_kind(client: AsyncClient) -> None:  # noqa: F811
    rows = [listing(905, title="Disneyland Trip Shirt", images=[]), listing(907, title="Mom & Dad & Kids Shirt"), listing(908, images=[])]
    w = await world(client, rows, sales={905: 2, 907: 2, 908: 2})
    await _keep(client, w, 905)
    row = await _row(client, w, 905)
    # Still Fix now: its missing main photo is not a brand name.
    assert row["bucket"] == "fix_now" and [p["id"] for p in row["problems"]] == ["main_photo"]
    for listing_id in (907, 908):
        row = await _row(client, w, listing_id)
        assert row["bucket"] == "fix_now" and all("keep_brand" not in p["fixes"] for p in row["problems"])
        assert (await _keep(client, w, listing_id)).status_code == 409  # nothing to keep
        assert (await _row(client, w, listing_id))["bucket"] == "fix_now"
    # Only the brand problem offers it, in the classifier as well.
    verdict = H.classify(listing(title="Mom & Dad & Kids Shirt", images=[]), recent_units=1, past_units=1, sales_known=True)
    assert all(H.KEEP_BRAND not in p.fixes for p in verdict.problems)


async def test_a_kept_brand_selling_listing_gets_no_title_or_tag_fix(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(905, title="Disneyland Trip Family Shirt")], sales={905: 4})
    await _keep(client, w, 905)
    res = (await client.post("/api/listing-health/preview", json={"connection_id": str(w["shop"]), "listing_ids": [905],
                                                                   "kinds": ["rewrite"], "accept_rewrite": True})).json()
    assert res["fixes"] == [] and "selling" in res["skipped"][0]["reason"]


async def test_another_account_cannot_keep_or_restore_a_brand(client: AsyncClient) -> None:  # noqa: F811
    from tests.auth_support import make_tenant

    w = await world(client, [listing(905, title="Disneyland Trip Shirt")])
    other = await make_tenant(client.sm, "other@example.com")  # type: ignore[attr-defined]
    async with client.sm() as s:  # type: ignore[attr-defined]
        theirs = EtsyConnection(tenant_id=other, etsy_user_id=77, shop_id=77, shop_name="Theirs")
        s.add(theirs)
        await s.flush()
        s.add(ShopListingCache(tenant_id=other, connection_id=theirs.id, listing_id=777, payload=listing(777, title="Disney Secret Shirt")))
        await s.commit()
    for path in ("keep", "warn"):
        res = await client.post(f"/api/listing-health/brands/{path}", json={"connection_id": str(theirs.id), "listing_id": 777})
        assert res.status_code == 404, path
        # Their listing id through our own shop is not there either.
        res = await client.post(f"/api/listing-health/brands/{path}", json={"connection_id": str(w["shop"]), "listing_id": 777})
        assert res.status_code == 404, path
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert not list((await s.execute(select(BrandKept))).scalars())


async def test_kept_brands_go_with_the_shop(client: AsyncClient) -> None:  # noqa: F811
    from app.workers.retention import purge_shop_etsy_content

    w = await world(client, [listing(905, title="Disneyland Trip Shirt")])
    await _keep(client, w, 905)
    async with client.sm() as s:  # type: ignore[attr-defined]
        await purge_shop_etsy_content(s, w["shop"])
        await s.commit()
        assert not list((await s.execute(select(BrandKept))).scalars())


# --- the report -------------------------------------------------------------------------------------------------


async def test_the_report_says_a_never_read_shop_plainly_and_counts_kept_brands(client: AsyncClient) -> None:  # noqa: F811
    w = await world(client, [listing(905, title="Disneyland Trip Shirt"), listing(906)], sales_read=False)
    await _keep(client, w, 905)
    async with client.sm() as s:  # type: ignore[attr-defined]
        from app.db.models import Tenant

        email = (await s.get(Tenant, w["tid"])).email
    text = await report.run(client.sm, email)  # type: ignore[attr-defined]
    assert "sales read: never started: Listing health cannot tell which listings sell" in text
    assert "kept by you (brand name): 1 listings" in text
    assert "Disneyland" not in text  # counts only
