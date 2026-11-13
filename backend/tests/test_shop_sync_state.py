"""A shop's listing sync says while it runs and when it ends (fix/onboarding-followups, Part I).

Before: pages guessed. "Sync shop listings" looked again at 5 and 15 seconds and took the old,
still-fresh copy for the synced one; a shop with no listings had no copy, so it was "stale" forever,
its listings said "0 · syncing…" for good and every look asked for another sync. Now the worker marks
the sync (etsy/shop_sync.py), the API reports it, and the page's watcher (frontend/lib/shopSync.ts)
updates every page when it ends.
"""

from __future__ import annotations

from typing import Any

from fakeredis import FakeAsyncRedis

from app.etsy import shop_sync
from app.workers import profiles as worker
from tests.test_profiles_api import ctx  # noqa: F401  (fixture)
from tests.test_workers_profiles import FakeEtsy, _patch, _seed, _shop_of


class EmptyShop(FakeEtsy):
    """A shop with no listings; it can look at the sync state while the sync is running."""

    def __init__(self, redis: FakeAsyncRedis, shop: str) -> None:
        super().__init__()
        self.redis, self.shop, self.seen_running = redis, shop, []

    async def get_listings_by_shop(self, shop_id: int, *, state: str, **_: Any) -> dict[str, Any]:
        self.seen_running.append((await shop_sync.state(self.redis, self.shop)).syncing)
        return {"count": 0, "results": []}


class BrokenShop(FakeEtsy):
    async def get_listings_by_shop(self, shop_id: int, *, state: str, **_: Any) -> dict[str, Any]:
        raise RuntimeError("Etsy did not answer")


async def test_the_worker_says_running_then_ended_and_read(async_sm, monkeypatch) -> None:
    tenant_id, _ = await _seed(async_sm, with_profile=False)
    shop = str(await _shop_of(async_sm, tenant_id))
    redis = FakeAsyncRedis()
    await shop_sync.requested(redis, shop)  # a page asked for it
    fake = EmptyShop(redis, shop)
    _patch(monkeypatch, tenant_id, fake)
    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None, "redis": redis}

    assert await worker.sync_shop_listings(ctx, shop) == "synced:0"
    assert fake.seen_running and all(fake.seen_running)  # "syncing" the whole time it ran
    after = await shop_sync.state(redis, shop)
    assert after.syncing is False and after.synced_at is not None and after.ended_at is not None


async def test_a_sync_that_fails_still_stops_saying_syncing(async_sm, monkeypatch) -> None:
    tenant_id, _ = await _seed(async_sm, with_profile=False)
    shop = str(await _shop_of(async_sm, tenant_id))
    redis = FakeAsyncRedis()
    _patch(monkeypatch, tenant_id, BrokenShop())
    ctx = {"sessionmaker": async_sm, "bucket": None, "quota": None, "redis": redis}
    try:
        await worker.sync_shop_listings(ctx, shop)
    except RuntimeError:
        pass
    after = await shop_sync.state(redis, shop)
    assert after.syncing is False and after.ended_at is not None
    assert after.synced_at is None  # it did not read the shop: not current


async def test_an_empty_shop_is_not_syncing_forever(ctx) -> None:  # noqa: F811
    client, shop, redis = ctx["client"], str(ctx["shop"]), ctx["redis"]
    first = (await client.get("/api/shop/listings")).json()
    assert (first["listings"], first["stale"], first["syncing"]) == ([], True, True)
    assert ctx["enqueuer"].calls == [("sync_shop_listings", (shop,))]
    # Asked again while it is queued: no second sync.
    assert (await client.get("/api/shop/listings")).json()["syncing"] is True
    assert len(ctx["enqueuer"].calls) == 1

    await shop_sync.started(redis, shop)
    await shop_sync.ended(redis, shop, read=True)  # the shop has no listings: nothing to cache
    done = (await client.get("/api/shop/listings")).json()
    assert (done["listings"], done["stale"], done["syncing"]) == ([], False, False)
    assert done["synced_at"] is not None
    assert len(ctx["enqueuer"].calls) == 1  # before: a sync asked for on every look, "syncing…" for good


async def test_sync_status_follows_the_button_to_the_end(ctx) -> None:  # noqa: F811
    client, shop, redis = ctx["client"], str(ctx["shop"]), ctx["redis"]
    assert [(r["connection_id"], r["syncing"]) for r in (await client.get("/api/shop/sync")).json()] == [(shop, False)]
    assert (await client.post("/api/shop/listings/sync")).status_code == 202
    assert (await client.get("/api/shop/sync")).json()[0]["syncing"] is True
    await shop_sync.started(redis, shop)
    assert (await client.get("/api/shop/sync")).json()[0]["syncing"] is True
    await shop_sync.ended(redis, shop, read=True)
    row = (await client.get("/api/shop/sync")).json()[0]
    assert row["syncing"] is False and row["ended_at"] and row["synced_at"]


async def test_a_queued_sync_that_never_runs_stops_saying_syncing(ctx) -> None:  # noqa: F811
    redis, shop = ctx["redis"], str(ctx["shop"])
    await shop_sync.requested(redis, shop)
    assert await redis.ttl(f"shop-sync-asked:{shop}") <= shop_sync.REQUESTED_SECONDS
    await redis.delete(f"shop-sync-asked:{shop}")  # the mark runs out (90 s)
    assert (await ctx["client"].get("/api/shop/sync")).json()[0]["syncing"] is False


async def test_a_sync_that_fails_is_not_asked_again_on_every_look(ctx) -> None:  # noqa: F811
    """With Etsy unreachable (a lost sign-in) the sync ends at once without reading the shop. Each page
    view used to ask again, so every page reloaded every minute or two; now one try per ten minutes."""
    client, shop, redis = ctx["client"], str(ctx["shop"]), ctx["redis"]
    assert (await client.get("/api/shop/listings")).json()["syncing"] is True
    await shop_sync.started(redis, shop)
    await shop_sync.ended(redis, shop, read=False)
    for _ in range(3):
        body = (await client.get("/api/shop/listings")).json()
        assert (body["stale"], body["syncing"]) == (True, False)
    assert len(ctx["enqueuer"].calls) == 1
    await redis.delete(f"shop-sync-tried:{shop}")  # ten minutes later
    assert (await client.get("/api/shop/listings")).json()["syncing"] is True
    assert len(ctx["enqueuer"].calls) == 2
