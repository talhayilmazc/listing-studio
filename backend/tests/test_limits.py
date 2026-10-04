"""The three numbers that limit work, each computed in one place (core/limits.py,
core/allowance.py): every screen and endpoint shows the same values for the same
account at the same moment."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, update

from app.api import deps
from app.core import allowance, limits
from app.core.config import set_settings_override
from app.db.models import AllowanceUse, EtsyConnection, Job, JobStatus, JobType, ListingPublication, ShopListingCache, Tenant
from app.etsy.api import EtsyApiClient
from app.etsy.rate_limiter import DailyQuota
from app.workers import profiles as worker
from tests.test_admin import world  # noqa: F401  (fixture)

DAY = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d")  # noqa: E731


def _quota(world) -> DailyQuota:  # noqa: F811
    """The production shape: 5,000 a day for the app, new work pausing at 90%."""
    quota = DailyQuota(world["redis"], global_daily_limit=5000, pause_percent=90)
    world["app"].dependency_overrides[deps.get_quota] = lambda: quota
    return quota


async def _account(world, **values) -> None:  # noqa: F811
    async with world["sm"]() as s:
        await s.execute(update(Tenant).where(Tenant.id == world["bob"].tenant_id).values(time_zone="America/Chicago", **values))
        await s.execute(update(Job).values(status=JobStatus.succeeded))  # the seed's queued job is done
        await s.commit()


async def _spend(world, *, own: int, upkeep: int, everyone: int) -> None:  # noqa: F811
    bob, redis = world["bob"], world["redis"]
    await redis.set(f"quota:tenant:{bob.tenant_id}:{DAY()}", own)
    await redis.set(f"quota:upkeep:{bob.tenant_id}:{DAY()}", upkeep)
    await redis.set(f"quota:global:{DAY()}", everyone)
    await redis.set(f"quota:shop:{bob.connection_id}:{DAY()}", own)


async def _screens(world) -> dict:  # noqa: F811
    """What each screen's endpoint answers for bob, in one moment."""
    a, b, bob = world["a"], world["b"], world["bob"]
    sidebar_allowance = (await b.get("/api/account/allowance")).json()
    # The sidebar's Etsy line and the batch page's metric are both this endpoint.
    quota = (await b.get(f"/api/quota?shop={bob.connection_id}")).json()
    review = (await b.post(f"/api/batches/{bob.batch_id}/publish/preview", json={})).json()
    admin_row = next(u for u in (await a.get("/api/admin/users")).json() if u["email"] == "bob@example.com")
    admin_usage = (await a.get("/api/admin/usage")).json()
    usage_row = next(t for t in admin_usage["tenants"] if t["email"] == "bob@example.com")
    return {"allowance": sidebar_allowance, "quota": quota, "review": review, "admin": admin_row,
            "usage": admin_usage, "usage_row": usage_row}


def _assert_one_answer(seen: dict) -> None:
    # Listings generated: the seller's sidebar and the admin panel, the same object.
    assert seen["allowance"] == seen["admin"]["allowance"]
    # Etsy requests today: sidebar and batch metric, the review page, the admin row.
    ceiling = seen["quota"]["ceiling"]
    assert seen["review"]["ceiling"] == ceiling == seen["admin"]["etsy"]
    assert (seen["usage_row"]["used_today"], seen["usage_row"]["limit"], seen["usage_row"]["follows_default"]) == (
        ceiling["used"], ceiling["limit"], ceiling["follows_default"],
    )
    assert ceiling["remaining"] == ceiling["limit"] - ceiling["used"]
    assert ceiling["label"] == "Etsy requests today" and seen["allowance"]["label"] == "Listings generated"


async def test_every_screen_shows_the_same_numbers_for_an_account_with_its_own_limits(world) -> None:  # noqa: F811
    _quota(world)
    await _account(world, etsy_ceiling_override=1500, allowance_amount=1500, allowance_period="daily")
    await _spend(world, own=21, upkeep=189, everyone=264)
    async with world["sm"]() as s:
        allowance.record(s, world["bob"].tenant_id, allowance.GENERATION)
        await s.commit()

    seen = await _screens(world)
    _assert_one_answer(seen)
    a = seen["allowance"]
    assert (a["amount"], a["period"], a["used"], a["remaining"], a["custom"]) == (1500, "daily", 1, 1499, True)
    assert a["resets_label"].endswith("12:00 AM CDT") or a["resets_label"].endswith("12:00 AM CST")  # the seller's midnight
    c = seen["quota"]["ceiling"]
    assert (c["limit"], c["used"], c["remaining"], c["follows_default"], c["default"], c["upkeep"]) == (1500, 21, 1479, False, 4500, 189)
    # 00:00 UTC, said in the seller's own time.
    assert c["resets_at"].endswith("T00:00:00Z") and c["resets_label"] in ("7:00 PM CDT", "6:00 PM CST")
    # The review page's "left" is the account's own remaining: its ceiling is the smaller here.
    assert (seen["review"]["budget_remaining"], seen["review"]["limited_by"]) == (1479, "account")
    # The shop's part of the account's requests; upkeep is in neither.
    assert seen["quota"]["shop_used"] == 21
    # The app's budget is the admin's to see, and holds everything.
    assert (seen["usage"]["global_used"], seen["usage"]["global_limit"], seen["usage"]["pause_at"], seen["usage"]["until_pause"]) == (264, 5000, 4500, 4236)
    assert seen["usage"]["ceiling_default"] == 4500
    assert not any(key.startswith("global") for key in seen["quota"])


async def test_every_screen_shows_the_same_numbers_for_an_account_on_the_defaults(world) -> None:  # noqa: F811
    _quota(world)
    await _account(world, etsy_ceiling_override=None, allowance_amount=None, allowance_period=None)
    await _spend(world, own=21, upkeep=189, everyone=264)
    async with world["sm"]() as s:
        allowance.record(s, world["bob"].tenant_id, allowance.GENERATION)
        await s.commit()

    seen = await _screens(world)
    _assert_one_answer(seen)
    a = seen["allowance"]
    assert (a["amount"], a["period"], a["used"], a["remaining"], a["custom"]) == (500, "monthly", 1, 499, False)
    c = seen["quota"]["ceiling"]
    assert (c["limit"], c["used"], c["remaining"], c["follows_default"]) == (4500, 21, 4479, True)
    # Here the app's budget is the smaller (4,500 - 264 used by everyone, upkeep included):
    # the review page may spend 4,236, says which number that is, and still shows the same ceiling.
    assert (seen["review"]["budget_remaining"], seen["review"]["limited_by"]) == (4236, "app")
    assert seen["review"]["ceiling"]["remaining"] == 4479


async def test_accounts_on_the_default_pick_up_a_change_of_it_and_overrides_do_not(world, test_settings) -> None:  # noqa: F811
    _quota(world)
    await _account(world, etsy_ceiling_override=None)
    async with world["sm"]() as s:
        await s.execute(update(Tenant).where(Tenant.id == world["admin"].tenant_id).values(etsy_ceiling_override=1500))
        await s.commit()
    limit = lambda rows, email: next(u["etsy"]["limit"] for u in rows if u["email"] == email)  # noqa: E731

    rows = (await world["a"].get("/api/admin/users")).json()
    assert (limit(rows, "bob@example.com"), limit(rows, "admin@example.com")) == (4500, 1500)
    set_settings_override(test_settings.model_copy(update={"account_daily_ceiling": 3000}))
    rows = (await world["a"].get("/api/admin/users")).json()
    assert (limit(rows, "bob@example.com"), limit(rows, "admin@example.com")) == (3000, 1500)
    seller = (await world["b"].get("/api/quota")).json()["ceiling"]
    assert (seller["limit"], seller["follows_default"], seller["default"]) == (3000, True, 3000)
    # The worker enforces the same number: there is no second place it is decided.
    async with world["sm"]() as s:
        assert limits.ceiling_limit(await s.get(Tenant, world["bob"].tenant_id)) == 3000

    # "Follow default" in the admin panel, audited.
    back = await world["a"].put(f"/api/admin/users/{world['admin'].tenant_id}/quota", json={"daily_quota": None})
    assert back.status_code == 200 and (back.json()["etsy"]["limit"], back.json()["etsy"]["follows_default"]) == (3000, True)


async def test_a_listing_sent_to_four_shops_is_one_generation_and_four_drafts_of_requests(world) -> None:  # noqa: F811
    quota = _quota(world)
    await _account(world, etsy_ceiling_override=None, allowance_amount=10, allowance_period="monthly")
    bob = world["bob"]
    async with world["sm"]() as s:
        shops = [bob.connection_id]
        for n in range(3):
            shop = EtsyConnection(tenant_id=bob.tenant_id, status="active", etsy_user_id=3000 + n, shop_id=3000 + n, position=n + 1)
            s.add(shop)
            await s.flush()
            shops.append(shop.id)
        # The design is written once...
        allowance.record(s, bob.tenant_id, allowance.GENERATION)
        # ...and sent to four shops: four draft jobs, then four drafts.
        for i, shop in enumerate(shops):
            s.add(Job(tenant_id=bob.tenant_id, connection_id=shop, type=JobType.create_draft, status=JobStatus.queued, payload={}))
            s.add(ListingPublication(tenant_id=bob.tenant_id, content_id=bob.content_id, connection_id=shop, etsy_listing_id=9000 + i))
        await s.commit()
        tenant = await s.get(Tenant, bob.tenant_id)
        ceiling = limits.ceiling_limit(tenant)

    # Each draft's Etsy requests go through the real client and the real counters.
    PER_DRAFT = 5
    for shop in shops:
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []})))
        client = EtsyApiClient(client_id="k", shared_secret="s", http_client=http, quota=quota, shop=shop)
        for n in range(PER_DRAFT):
            await client.get_listing(n, access_token="t", tenant_id=bob.tenant_id, tenant_limit=ceiling)

    mine = (await world["b"].get("/api/account/allowance")).json()
    # One listing generated, whatever the number of shops, drafts and queued jobs.
    assert (mine["used"], mine["generations"], mine["pending"], mine["remaining"]) == (1, 1, 0, 9)
    async with world["sm"]() as s:
        assert [u.kind for u in (await s.execute(select(AllowanceUse))).scalars()] == ["generation"]
    etsy = (await world["b"].get(f"/api/quota?shop={shops[1]}")).json()
    # Four drafts' requests against the account's ceiling; this shop made a quarter of them.
    assert (etsy["ceiling"]["used"], etsy["ceiling"]["remaining"], etsy["shop_used"]) == (4 * PER_DRAFT, 4500 - 4 * PER_DRAFT, PER_DRAFT)
    row = next(u for u in (await world["a"].get("/api/admin/users")).json() if u["email"] == "bob@example.com")
    assert row["etsy"] == etsy["ceiling"] and row["allowance"] == mine


async def test_upkeep_is_in_the_apps_budget_and_in_neither_the_ceiling_nor_a_shops_count(world) -> None:  # noqa: F811
    quota = _quota(world)
    await _account(world, etsy_ceiling_override=None)
    bob = world["bob"]
    for _ in range(72):  # a shop sync
        assert await quota.reserve_upkeep(bob.tenant_id, shop=bob.connection_id, category="shop_sync")
    for _ in range(21):  # the seller's own drafts
        assert await quota.reserve(bob.tenant_id, 4500, shop=bob.connection_id, category="drafts")
    seen = (await world["b"].get(f"/api/quota?shop={bob.connection_id}")).json()
    # "21 used" and "this shop: 21": the sidebar no longer says 72 for the shop beside 21 for the account.
    assert (seen["ceiling"]["used"], seen["shop_used"], seen["ceiling"]["upkeep"]) == (21, 21, 72)
    assert (await limits.app_budget(quota)).used == 93


async def test_when_the_apps_budget_stops_the_work_the_seller_is_told_exactly_that(world) -> None:  # noqa: F811
    _quota(world)
    await _account(world, etsy_ceiling_override=None)
    await _spend(world, own=21, upkeep=0, everyone=4500)
    seen = (await world["b"].get("/api/quota")).json()
    assert seen["pause"]["reason"] == "global_quota"
    message = seen["pause"]["message"]
    assert message.startswith("The app's shared Etsy budget for today is used up, so new work waits. It is not your own limit:")
    assert "you have 4,479 of your 4,500 Etsy requests left today" in message
    assert ("7:00 PM CDT" in message or "6:00 PM CST" in message) and "(00:00 UTC)" in message
    refused = await world["b"].post(f"/api/batches/{world['bob'].batch_id}/publish/preview", json={})
    assert (refused.json()["budget_remaining"], refused.json()["limited_by"]) == (0, "app")

    # Their own ceiling: said as theirs, with its true reset in their own time.
    await _account(world, etsy_ceiling_override=20)
    await _spend(world, own=21, upkeep=0, everyone=100)
    own = (await world["b"].get("/api/quota")).json()["pause"]
    assert own["reason"] == "tenant_quota" and own["message"].startswith("Your account has used its 20 Etsy requests for today.")


async def test_the_top_strip_reads_counts_once_a_day_and_never_queues_a_full_sync(world, monkeypatch) -> None:  # noqa: F811
    """The strip is on every page. It used to queue a full sync of every listing
    whenever the six-hour cache had run out: 18 requests four times a day for a
    1,300-listing shop nobody was looking at."""
    bob = world["bob"]
    queued: list[tuple] = []

    class Queue:
        async def enqueue(self, function, *args, **options):
            queued.append((function, args, options))

    world["app"].dependency_overrides[deps.get_enqueuer] = lambda: Queue()
    async with world["sm"]() as s:
        await s.execute(update(ShopListingCache).values(fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))  # long expired
        await s.commit()

    first = (await world["b"].get(f"/api/shop/summary?shop={bob.connection_id}")).json()
    assert (first["shop_counts_known"], first["syncing"]) == (False, True)
    assert [(q[0], q[2]) for q in queued] == [("sync_shop_counts", {"_job_id": f"counts:{bob.connection_id}"})]
    await world["b"].get(f"/api/shop/summary?shop={bob.connection_id}")
    assert len(queued) == 1  # however many pages ask

    # The counts job: one request per listing state, and nothing else.
    calls: list[dict] = []

    class Etsy:
        async def get_listings_by_shop(self, shop_id, *, state, limit, offset, **_):
            calls.append({"state": state, "limit": limit})
            return {"count": {"active": 1290, "draft": 12}.get(state, 0), "results": []}

    class Service:
        async def get_valid_access_token(self, session, connection):
            return "token"

    monkeypatch.setattr(worker, "_connection_service", lambda settings: Service())
    monkeypatch.setattr(worker, "_build_client", lambda ctx, http, settings, **_: Etsy())
    assert await worker.sync_shop_counts({"sessionmaker": world["sm"], "bucket": None, "quota": None}, str(bob.connection_id)) == "counted:1302"
    assert calls == [{"state": s, "limit": 1} for s in ("active", "draft", "inactive", "sold_out", "expired")]

    known = (await world["b"].get(f"/api/shop/summary?shop={bob.connection_id}")).json()
    assert (known["shop_counts_known"], known["active"], known["draft"], known["total"], known["syncing"]) == (True, 1290, 12, 1302, False)
    # How many went live this month needs the listings themselves: unknown, not zero.
    assert known["published_known"] is False
    assert len(queued) == 1

    # A page that lists listings still asks for them when its copy is stale, once however often it is opened.
    await world["b"].get(f"/api/shop/listings?shop={bob.connection_id}")
    await world["b"].get(f"/api/shop/listings?shop={bob.connection_id}")
    assert [(q[0], q[2]) for q in queued[1:]] == [("sync_shop_listings", {"_job_id": f"sync:{bob.connection_id}"})] * 2
