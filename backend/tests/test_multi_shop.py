"""Several Etsy shops per account (docs/duzeltmeler-v5.md §E).

* ceilings: per account (admin-adjustable) and app-wide; a shop belongs to one account;
* each shop publishes from its own profile, N shops = N drafts, one failing
  does not stop the others;
* quota protection: a publish that would not fit today's budget is refused,
  with how many listings would;
* isolation at shop level: another account's shop is a 404 everywhere;
* disconnecting one shop removes only that shop's Etsy content.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api import deps
from app.core.config import Settings, set_settings_override
from app.core.crypto import TokenCipher
from app.core.sessions import SessionStore
from app.db.base import Base
from app.db.models import (
    Asset,
    AssetStatus,
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    ListingProfile,
    ListingPublication,
    ShopListingCache,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.etsy.connection import ConnectionService
from app.etsy.oauth import TokenResponse
from app.etsy.rate_limiter import DailyQuota
from app.etsy.shops import ShopLimitReached, ShopTaken
from app.main import create_app
from app.pipeline.targets import resolve_target, retarget_title
from tests.auth_support import BROWSER_HEADERS, authenticate, open_session

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
PREFIX_A = "Comfort Colors®"
BODY = (
    "Retro Frog Tee, Cottagecore Shirt, Vintage Frog Graphic Top, Nature Lover Gift, "
    "Pond Life Crewneck Tee, Frog Mom"
)


class Queue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def enqueue(self, function: str, *args, **kwargs) -> None:
        self.calls.append((function, args))


def _service() -> ConnectionService:
    return ConnectionService(TokenCipher(Fernet.generate_key()), client_id="k", token_url="t")


def _tokens(etsy_user: int) -> TokenResponse:
    return TokenResponse(access_token=f"{etsy_user}.abc", refresh_token="r", expires_in=3600)


async def _shop(s, tenant_id, etsy_user: int, name: str, position: int = 0) -> EtsyConnection:
    shop = EtsyConnection(
        tenant_id=tenant_id,
        status=ConnectionStatus.active,
        etsy_user_id=etsy_user,
        shop_id=etsy_user,
        shop_name=name,
        position=position,
    )
    s.add(shop)
    await s.flush()
    return shop


async def _profile(s, tenant_id, shop, *, name="Standard Tee", prefix=None, body="Body text.") -> ListingProfile:
    profile = ListingProfile(
        tenant_id=tenant_id,
        connection_id=shop.id,
        name=name,
        reference_listing_id=shop.etsy_user_id * 10,
        content_template="apparel",
        confirmed=True,
        title_prefix=prefix,
        cached_payload={"description": f"Reference title\n\n{body}", "images": []},
        updated_at=datetime.now(timezone.utc),
    )
    s.add(profile)
    await s.flush()
    return profile


@pytest_asyncio.fixture()
async def world(test_settings: Settings) -> AsyncIterator[dict]:
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    set_settings_override(test_settings.model_copy(update={"max_shops_per_tenant": 3, "max_shops_app_wide": 4}))

    async with sm() as s:
        alice = Tenant(email="alice@example.com", password_hash="!", daily_quota=1000)
        bob = Tenant(email="bob@example.com", password_hash="!", daily_quota=1000)
        s.add_all([alice, bob])
        await s.flush()
        a1 = await _shop(s, alice.id, 101, "Frost Tees", 0)
        a2 = await _shop(s, alice.id, 102, "Frost Mugs", 1)
        b1 = await _shop(s, bob.id, 201, "Bob Shop")
        pa1 = await _profile(s, alice.id, a1, prefix=PREFIX_A, body="Sizes for shop one.")
        pa2 = await _profile(s, alice.id, a2, prefix="", body="Sizes for shop two.")
        pb1 = await _profile(s, bob.id, b1)
        batch = UploadBatch(tenant_id=alice.id, status=UploadBatchStatus.ready, file_count=2)
        s.add(batch)
        await s.flush()
        contents = []
        for i in range(2):
            asset = Asset(
                tenant_id=alice.id,
                batch_id=batch.id,
                original_filename=f"d{i}.png",
                storage_key=f"k{i}",
                status=AssetStatus.processed,
                rank=i + 1,
            )
            s.add(asset)
            await s.flush()
            content = GeneratedContent(
                tenant_id=alice.id,
                batch_id=batch.id,
                asset_id=asset.id,
                listing_profile_id=pa1.id,
                title=f"{PREFIX_A}, {BODY}",
                tags=["shirt", *[f"tag{n}" for n in range(12)]],
                description="Edited description for shop one.",
                approved=True,
            )
            s.add(content)
            await s.flush()
            contents.append(content.id)
        s.add_all(
            [
                ShopListingCache(tenant_id=alice.id, connection_id=a1.id, listing_id=1, payload={"listing_id": 1}, fetched_at=datetime.now(timezone.utc)),
                ShopListingCache(tenant_id=alice.id, connection_id=a2.id, listing_id=2, payload={"listing_id": 2}, fetched_at=datetime.now(timezone.utc)),
            ]
        )
        await s.commit()
        ids = {
            "alice": alice.id, "bob": bob.id, "a1": a1.id, "a2": a2.id, "b1": b1.id,
            "pa1": pa1.id, "pa2": pa2.id, "pb1": pb1.id, "batch": batch.id, "contents": contents,
        }

    redis = FakeAsyncRedis()
    queue = Queue()

    async def _session():
        async with sm() as s:
            yield s

    app = create_app()
    app.dependency_overrides[deps.get_session] = _session
    app.dependency_overrides[deps.get_enqueuer] = lambda: queue
    app.dependency_overrides[deps.get_redis] = lambda: redis
    app.dependency_overrides[deps.get_session_store] = lambda: SessionStore(redis)
    app.dependency_overrides[deps.get_quota] = lambda: DailyQuota(redis, global_daily_limit=5000, pause_percent=90)
    app.dependency_overrides[deps.get_connection_service] = _service

    transport = ASGITransport(app=app)
    a = AsyncClient(transport=transport, base_url="http://test", headers=BROWSER_HEADERS)
    b = AsyncClient(transport=transport, base_url="http://test", headers=BROWSER_HEADERS)
    authenticate(a, await open_session(redis, ids["alice"]))
    authenticate(b, await open_session(redis, ids["bob"]))
    try:
        yield {"sm": sm, "redis": redis, "queue": queue, "a": a, "b": b, **ids}
    finally:
        await a.aclose()
        await b.aclose()
        await engine.dispose()


# --- ceilings -----------------------------------------------------------------------
async def test_an_account_connects_shops_up_to_its_ceiling(world) -> None:
    service = _service()
    async with world["sm"]() as s:
        third = await service.save_from_tokens(s, world["alice"], _tokens(103), [])
        assert third.position == 2  # appended to the switcher
        with pytest.raises(ShopLimitReached) as err:
            await service.save_from_tokens(s, world["alice"], _tokens(104), [])
        assert err.value.scope == "account"
        # Reconnecting a shop the account already has is not a new shop.
        again = await service.save_from_tokens(s, world["alice"], _tokens(101), [])
        assert again.id == world["a1"]


async def test_the_app_wide_ceiling_holds_across_accounts(world) -> None:
    service = _service()
    async with world["sm"]() as s:
        await service.save_from_tokens(s, world["bob"], _tokens(202), [])  # 4 of 4 app-wide
        with pytest.raises(ShopLimitReached) as err:
            await service.save_from_tokens(s, world["alice"], _tokens(103), [])
        assert err.value.scope == "app"


async def test_a_shop_belongs_to_one_account(world) -> None:
    async with world["sm"]() as s:
        with pytest.raises(ShopTaken):
            await _service().save_from_tokens(s, world["alice"], _tokens(201), [])
        bob_shop = await s.get(EtsyConnection, world["b1"])
        assert bob_shop.tenant_id == world["bob"]  # untouched


async def test_an_admin_can_change_one_accounts_ceiling(world) -> None:
    async with world["sm"]() as s:
        alice = await s.get(Tenant, world["alice"])
        alice.max_shops = 2
        await s.commit()
        with pytest.raises(ShopLimitReached):
            await _service().save_from_tokens(s, world["alice"], _tokens(103), [])
    slots = (await world["a"].get("/api/shops")).json()["slots"]
    assert slots == {"used": 2, "limit": 2, "app_used": 3, "app_limit": 4, "can_add": False}


# --- shops API ------------------------------------------------------------------------
async def test_shops_are_listed_in_the_sellers_order_and_can_be_renamed(world) -> None:
    body = (await world["a"].get("/api/shops")).json()
    assert [shop["name"] for shop in body["shops"]] == ["Frost Tees", "Frost Mugs"]
    await world["a"].post("/api/shops/order", json={"ids": [str(world["a2"]), str(world["a1"])]})
    await world["a"].patch(f"/api/shops/{world['a2']}", json={"display_name": "Mugs"})
    body = (await world["a"].get("/api/shops")).json()
    assert [shop["name"] for shop in body["shops"]] == ["Mugs", "Frost Tees"]
    assert all("token" not in key for shop in body["shops"] for key in shop)


# --- publishing to several shops --------------------------------------------------------
def test_the_title_prefix_follows_the_shop() -> None:
    assert retarget_title(f"{PREFIX_A}, Frog Tee", PREFIX_A, "").title == "Frog Tee"
    # The prefix joins the first phrase with a space, never a comma (v6 §C).
    assert retarget_title("Frog Tee", "", "Gildan").title == "Gildan Frog Tee"
    assert retarget_title(f"{PREFIX_A} Frog Tee", PREFIX_A, "Bella").title == "Bella Frog Tee"
    # Titles written with the old comma lose it when they move shop.
    assert retarget_title(f"{PREFIX_A}, Frog Tee", PREFIX_A, "Bella").title == "Bella Frog Tee"
    # Never twice.
    assert retarget_title("Bella, Frog Tee", "", "Bella").title == "Bella Frog Tee"
    assert retarget_title("Bella Frog Tee", "", "Bella").title == "Bella Frog Tee"
    assert retarget_title("COMFORT COLORS Frog Tee, Pond Shirt", "COMFORT COLORS", "").title == (
        "Frog Tee, Pond Shirt"
    )


# Phrases of a 129-character title with the 15-character prefix (112 without it).
PHRASES = BODY.split(", ")


def test_a_longer_prefix_drops_trailing_phrases_until_the_title_fits() -> None:
    long_prefix = "Bella Canvas Unisex Jersey Tee"  # 15 characters longer than PREFIX_A
    fitted = retarget_title(f"{PREFIX_A}, {BODY}", PREFIX_A, long_prefix)
    assert len(f"{long_prefix} {BODY}") > 140
    assert len(fitted.title) <= 140
    assert fitted.title == f"{long_prefix} " + ", ".join(PHRASES[:-1])  # the last phrase went
    assert fitted.dropped == (PHRASES[-1],) and fitted.used_all is False


def test_a_shorter_prefix_keeps_every_phrase() -> None:
    fitted = retarget_title(f"{PREFIX_A}, {BODY}", PREFIX_A, "")
    assert fitted.title == BODY and fitted.used_all is True and fitted.dropped == ()


async def test_a_shop_is_not_refused_because_trimming_left_the_title_short(world) -> None:
    """Dropping a long last phrase can leave the title under 110. That is still a
    draft in that shop, not a refused shop."""
    long_tail = (
        "Retro Frog Tee, Cottagecore Shirt, Pond Life Crewneck, "
        "Handmade Vintage Frog Graphic Top For Pond And Nature Lovers"
    )
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, world["contents"][0])
        content.title = f"{PREFIX_A}, {long_tail}"
        (await s.get(ListingProfile, world["pa2"])).title_prefix = "Bella Canvas Unisex Jersey"
        await s.commit()
        assert len(content.title) <= 140
        target = await resolve_target(s, content, await s.get(EtsyConnection, world["a2"]))
    assert target.ok, target.reason
    assert target.title == "Bella Canvas Unisex Jersey Retro Frog Tee, Cottagecore Shirt, Pond Life Crewneck"
    assert len(target.title) < 110  # short, but a draft rather than a refusal


async def test_a_title_still_short_with_every_phrase_refuses_that_shop(world) -> None:
    short = "Retro Frog Tee, Cottagecore Shirt, Pond Life Crewneck, Frog Mom Gift, Nature Lover Top, Pond Gift"
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, world["contents"][0])
        content.title = f"{PREFIX_A}, {short}"  # 110+ here, under 110 without the prefix
        await s.commit()
        assert len(content.title) >= 110 > len(short)
        target = await resolve_target(s, content, await s.get(EtsyConnection, world["a2"]))
    assert not target.ok
    assert "at least 110" in target.reason


async def test_each_shop_builds_its_draft_from_its_own_profile(world) -> None:
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, world["contents"][0])
        own = await resolve_target(s, content, await s.get(EtsyConnection, world["a1"]))
        other = await resolve_target(s, content, await s.get(EtsyConnection, world["a2"]))
    # Its own shop: its own profile, and the text the seller edited.
    assert own.profile.id == world["pa1"]
    assert own.description == "Edited description for shop one."
    # The other shop: that shop's same-named profile, its prefix, its reference body.
    assert other.profile.id == world["pa2"]
    assert other.title == BODY
    assert other.description == f"{BODY}\n\nSizes for shop two."


async def test_n_shops_make_n_drafts_and_one_shop_failing_does_not_stop_the_others(world) -> None:
    async with world["sm"]() as s:
        # The second listing already has a draft in the second shop.
        s.add(
            ListingPublication(
                tenant_id=world["alice"], content_id=world["contents"][1],
                connection_id=world["a2"], etsy_listing_id=9, state="draft",
            )
        )
        await s.commit()
    targets = [{"connection_id": str(world["a1"])}, {"connection_id": str(world["a2"])}]
    res = await world["a"].post(f"/api/batches/{world['batch']}/publish", json={"targets": targets})
    assert res.status_code == 200, res.json()
    body = res.json()
    assert len(body["jobs"]) == 3  # 2 listings x 2 shops, minus the existing draft
    assert body["skipped"] == [
        {
            "content_id": str(world["contents"][1]),
            "reason": "already has a draft in this shop",
            "connection_id": str(world["a2"]),
            "shop_name": "Frost Mugs",
        }
    ]
    async with world["sm"]() as s:
        jobs = (await s.execute(select(Job))).scalars().all()
    profile_by_shop = {j.connection_id: j.payload["profile_id"] for j in jobs}
    assert profile_by_shop == {world["a1"]: str(world["pa1"]), world["a2"]: str(world["pa2"])}


async def test_a_shop_without_a_suitable_profile_cannot_be_chosen(world) -> None:
    async with world["sm"]() as s:
        await s.delete(await s.get(ListingProfile, world["pa2"]))
        await s.commit()
    targets = [{"connection_id": str(world["a1"])}, {"connection_id": str(world["a2"])}]
    preview = (
        await world["a"].post(f"/api/batches/{world['batch']}/publish/preview", json={"targets": targets})
    ).json()
    by_shop = {shop["connection_id"]: shop for shop in preview["shops"]}
    assert by_shop[str(world["a1"])]["ready"] == 2
    assert by_shop[str(world["a2"])]["ready"] == 0
    assert "no confirmed apparel profile" in by_shop[str(world["a2"])]["blocked"][0]["reason"]


# --- quota protection -------------------------------------------------------------------------
async def test_the_estimate_is_shown_before_publishing(world) -> None:
    targets = [{"connection_id": str(world["a1"])}, {"connection_id": str(world["a2"])}]
    preview = (
        await world["a"].post(f"/api/batches/{world['batch']}/publish/preview", json={"targets": targets})
    ).json()
    assert preview["drafts"] == 4 and preview["estimated_calls"] == 60 and preview["fits"] is True


async def test_a_publish_that_would_pass_the_budget_is_refused_saying_what_fits(world) -> None:
    # 1000 a day for Alice; 950 used leaves 50: one listing to two shops needs ~30.
    await world["redis"].set(f"quota:tenant:{world['alice']}:{TODAY}", 950)
    targets = [{"connection_id": str(world["a1"])}, {"connection_id": str(world["a2"])}]
    preview = (
        await world["a"].post(f"/api/batches/{world['batch']}/publish/preview", json={"targets": targets})
    ).json()
    assert preview["fits"] is False and preview["listings_that_fit"] == 1

    res = await world["a"].post(f"/api/batches/{world['batch']}/publish", json={"targets": targets})
    assert res.status_code == 409
    assert "2 shops × 2 listings ≈ 60 Etsy requests" in res.json()["detail"]
    assert "1 listing would fit" in res.json()["detail"]
    assert world["queue"].calls == []  # nothing queued


async def test_the_90_percent_pause_counts_as_the_budget(world) -> None:
    await world["redis"].set(f"quota:global:{TODAY}", 4490)  # 10 left before the pause
    res = await world["a"].post(f"/api/batches/{world['batch']}/publish")
    assert res.status_code == 409 and "only 10 can be spent today" in res.json()["detail"]


# --- isolation at shop level (B6) ---------------------------------------------------------------
async def test_another_accounts_shop_is_a_404_everywhere(world) -> None:
    a, bob_shop = world["a"], world["b1"]
    content = world["contents"][0]
    checks = [
        ("GET", f"/api/profiles?shop={bob_shop}", None),
        ("GET", f"/api/shop/listings?shop={bob_shop}", None),
        ("GET", f"/api/shop/summary?shop={bob_shop}", None),
        ("GET", f"/api/quota?shop={bob_shop}", None),
        ("POST", f"/api/shop/detect-profiles?shop={bob_shop}", None),
        ("POST", f"/api/shop/listings/77/use-as-profile?shop={bob_shop}", None),
        ("PATCH", f"/api/shops/{bob_shop}", {"display_name": "mine now"}),
        ("POST", f"/api/shops/{bob_shop}/disconnect", None),
        ("POST", "/api/profiles", {"connection_id": str(bob_shop), "name": "x", "reference_listing_id": 1}),
        ("POST", f"/api/content/{content}/publish", {"targets": [{"connection_id": str(bob_shop)}]}),
        ("POST", f"/api/batches/{world['batch']}/publish", {"targets": [{"connection_id": str(bob_shop)}]}),
        ("POST", f"/api/batches/{world['batch']}/publish/preview", {"targets": [{"connection_id": str(bob_shop)}]}),
        ("POST", f"/api/batches/{world['batch']}/publish-live", {"connection_ids": [str(bob_shop)]}),
    ]
    for method, path, body in checks:
        resp = await a.request(method, path, json=body)
        assert resp.status_code == 404, f"{method} {path} -> {resp.status_code}"
    # Bob's shop is exactly as it was.
    async with world["sm"]() as s:
        shop = await s.get(EtsyConnection, bob_shop)
        assert shop.display_name is None and shop.status is ConnectionStatus.active
    # And Bob sees only his own shop and profiles.
    assert [s["id"] for s in (await world["b"].get("/api/shops")).json()["shops"]] == [str(bob_shop)]
    assert [p["id"] for p in (await world["b"].get("/api/profiles")).json()] == [str(world["pb1"])]


async def test_one_shops_profiles_and_listings_stay_in_that_shop(world) -> None:
    only_two = (await world["a"].get(f"/api/profiles?shop={world['a2']}")).json()
    assert [p["id"] for p in only_two] == [str(world["pa2"])]
    assert only_two[0]["shop_name"] == "Frost Mugs"


# --- disconnecting one shop ---------------------------------------------------------------------
async def test_disconnecting_one_shop_leaves_the_others_and_the_sellers_work(world) -> None:
    async with world["sm"]() as s:
        s.add_all(
            [
                ListingPublication(tenant_id=world["alice"], content_id=world["contents"][0], connection_id=world["a1"], etsy_listing_id=11),
                ListingPublication(tenant_id=world["alice"], content_id=world["contents"][0], connection_id=world["a2"], etsy_listing_id=12),
            ]
        )
        await s.commit()

    res = await world["a"].post(f"/api/shops/{world['a2']}/disconnect")
    assert res.status_code == 200
    assert [shop["id"] for shop in res.json()["shops"]] == [str(world["a1"])]

    async def count(model, **where) -> int:
        async with world["sm"]() as s:
            query = select(func.count()).select_from(model)
            for key, value in where.items():
                query = query.where(getattr(model, key) == value)
            return int((await s.execute(query)).scalar_one())

    # The disconnected shop's Etsy content is gone...
    assert await count(ListingProfile, connection_id=world["a2"]) == 0
    assert await count(ShopListingCache, connection_id=world["a2"]) == 0
    assert await count(ListingPublication, connection_id=world["a2"]) == 0
    # ...the other shop's is not...
    assert await count(ListingProfile, connection_id=world["a1"]) == 1
    assert await count(ShopListingCache, connection_id=world["a1"]) == 1
    assert await count(ListingPublication, connection_id=world["a1"]) == 1
    # ...and the seller's own work stays.
    assert await count(GeneratedContent, tenant_id=world["alice"]) == 2
    assert await count(Asset, tenant_id=world["alice"]) == 2
