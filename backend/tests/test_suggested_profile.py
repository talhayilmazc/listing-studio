"""No profile wall (fix/onboarding-followups, Part E): a suggested profile from the shop's newest active listing.

A new seller's first shop read makes one unconfirmed profile from the newest active listing (cache only,
nothing sent to Etsy); Home's checklist asks "Confirm your listing settings" with one button. A shop with
no active listings gets no suggestion and one sentence why. A seller may change it before confirming.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import AuditLog, ListingProfile, Tenant
from app.pipeline import onboarding, suggested_profile
from app.workers import profiles as worker
from tests.test_publish_api import ctx  # noqa: F401  (fixture)
from tests.test_workers_profiles import FakeEtsy, _patch, _seed, _shop_of


class ShopWithListings(FakeEtsy):
    """Three active listings made at different times, and a newer draft (a draft is not a listing for sale)."""

    writes: list[str] = []

    async def get_listings_by_shop(self, shop_id: int, *, state: str, **_: Any) -> dict[str, Any]:
        self.listings_calls.append(state)
        if state == "active":
            return {"results": [
                {"listing_id": 11, "title": "Retro Frog Tee", "state": "active", "taxonomy_id": 482, "original_creation_timestamp": 1_700_000_000},
                {"listing_id": 12, "title": "Retro Cat Tee", "state": "active", "taxonomy_id": 482, "original_creation_timestamp": 1_760_000_000},
                {"listing_id": 13, "title": "Mug", "state": "active", "taxonomy_id": 1000, "original_creation_timestamp": 1_720_000_000},
            ]}
        if state == "draft":
            return {"results": [{"listing_id": 14, "title": "Newest draft", "state": "draft", "original_creation_timestamp": 1_790_000_000}]}
        return {"results": []}

    def __getattr__(self, name: str):  # any write would be a bug: nothing changes on Etsy
        if name.startswith(("create_", "update_", "delete_")):
            raise AssertionError(f"Etsy write {name} during a suggestion")
        raise AttributeError(name)


class EmptyShop(FakeEtsy):
    async def get_listings_by_shop(self, shop_id: int, *, state: str, **_: Any) -> dict[str, Any]:
        self.listings_calls.append(state)
        return {"count": 0, "results": []}


async def _sync(sm, monkeypatch, fake) -> tuple[Any, list]:  # noqa: ANN001
    tenant_id, _ = await _seed(sm, with_profile=False)
    _patch(monkeypatch, tenant_id, fake)
    queued: list = []

    async def enqueue(function: str, *args: Any, **_: Any) -> None:
        queued.append((function, args))

    ctx = {"sessionmaker": sm, "bucket": None, "quota": None, "enqueue": enqueue}
    await worker.sync_shop_listings(ctx, str(await _shop_of(sm, tenant_id)))
    return tenant_id, queued


async def _profiles(sm, tenant_id) -> list[ListingProfile]:  # noqa: ANN001
    async with sm() as s:
        return list((await s.execute(select(ListingProfile).where(ListingProfile.tenant_id == tenant_id))).scalars())


async def _steps(sm, tenant_id) -> dict[str, onboarding.Step]:  # noqa: ANN001
    async with sm() as s:
        found = await onboarding.checklist(s, await s.get(Tenant, tenant_id))
    return {step.key: step for step in found.steps}


async def test_a_shop_with_listings_gets_one_suggestion_from_its_newest_active_listing(async_sm: async_sessionmaker, monkeypatch) -> None:
    tenant_id, queued = await _sync(async_sm, monkeypatch, ShopWithListings())
    [profile] = await _profiles(async_sm, tenant_id)
    assert (profile.reference_listing_id, profile.source, profile.confirmed) == (12, "suggested", False)
    assert profile.name == "Retro Tee"  # from the titles of the same kind
    assert ("refresh_profile", (str(profile.id),)) in queued  # its reference is read like any profile's
    async with async_sm() as s:
        assert await s.scalar(select(AuditLog.action).where(AuditLog.target_tenant_id == tenant_id)) == "profile.suggested"

    steps = await _steps(async_sm, tenant_id)
    assert [k for k in steps] == ["shop", "profile", "upload", "approve", "send"]
    step = steps["profile"]
    assert (step.title, step.done, step.confirm_profile_id) == ("Confirm your listing settings", False, profile.id)
    assert "Nothing changes on Etsy" in step.body
    assert step.second_href == f"/profiles?profile={profile.id}"

    # Read again: no second suggestion.
    tenant_again = await _profiles(async_sm, tenant_id)
    async with async_sm() as s:
        from app.db.models import EtsyConnection

        conn = (await s.execute(select(EtsyConnection).where(EtsyConnection.tenant_id == tenant_id))).scalar_one()
        assert await suggested_profile.suggest(s, conn) is None
    assert len(tenant_again) == 1


async def test_a_refresh_sets_the_suggestions_template_from_its_category(async_sm: async_sessionmaker, monkeypatch) -> None:
    fake = ShopWithListings()
    tenant_id, _ = await _sync(async_sm, monkeypatch, fake)
    [profile] = await _profiles(async_sm, tenant_id)
    async with async_sm() as s:
        p = await s.get(ListingProfile, profile.id)
        p.content_template = "digital_products"
        await s.commit()

    async def get_listing(listing_id: int, **_: Any) -> dict[str, Any]:
        return {**await FakeEtsy.get_listing(fake, listing_id), "taxonomy_id": 482, "shop_id": 900}

    fake.get_listing = get_listing  # type: ignore[method-assign]
    await worker.refresh_profile({"sessionmaker": async_sm, "bucket": None, "quota": None}, str(profile.id))
    [after] = await _profiles(async_sm, tenant_id)
    assert after.content_template == "apparel"  # 482 is under Clothing


async def test_a_shop_with_no_active_listings_gets_no_suggestion_and_one_sentence(async_sm: async_sessionmaker, monkeypatch) -> None:
    tenant_id, queued = await _sync(async_sm, monkeypatch, EmptyShop())
    assert await _profiles(async_sm, tenant_id) == []
    assert not [q for q in queued if q[0] == "refresh_profile"]
    step = (await _steps(async_sm, tenant_id))["profile"]
    assert (step.done, step.confirm_profile_id, step.href) == (False, None, "/profiles")
    assert step.body == suggested_profile.NO_LISTINGS


async def test_a_seller_who_deleted_a_profile_is_not_given_one_back(async_sm: async_sessionmaker, monkeypatch) -> None:
    tenant_id, _ = await _seed(async_sm, with_profile=False)
    async with async_sm() as s:
        s.add(AuditLog(actor_tenant_id=tenant_id, action="profile.deleted", target_tenant_id=tenant_id, details={}))
        await s.commit()
        assert await suggested_profile.wanted(s, tenant_id) is False


async def test_the_seller_edits_the_suggestion_then_confirms_and_the_edits_stay(ctx) -> None:  # noqa: F811
    """Through the API, as the seller does it: Home's step, Profiles' edit, then the step's one button."""
    async with ctx["sm"]() as s:
        from app.db.models import EtsyConnection

        conn = (await s.execute(select(EtsyConnection).where(EtsyConnection.tenant_id == ctx["tenant_id"]))).scalar_one()
        profile = ListingProfile(tenant_id=ctx["tenant_id"], connection_id=conn.id, name="Retro Tee",
                                 reference_listing_id=12, source="suggested", confirmed=False)
        s.add(profile)
        await s.commit()
        pid = profile.id
    home = (await ctx["client"].get("/api/onboarding")).json()
    step = next(x for x in home["steps"] if x["key"] == "profile")
    assert step["confirm_profile_id"] == str(pid) and step["done"] is False

    edited = await ctx["client"].patch(f"/api/profiles/{pid}", json={"name": "Frog shirts", "title_prefix": "Frog"})
    assert edited.status_code == 200 and edited.json()["confirmed"] is False  # editing alone uses nothing
    assert (await ctx["client"].post(f"/api/profiles/{pid}/confirm")).status_code == 200

    async with ctx["sm"]() as s:
        p = await s.get(ListingProfile, pid)
        assert (p.name, p.title_prefix, p.confirmed, p.source) == ("Frog shirts", "Frog", True, "suggested")
    home = (await ctx["client"].get("/api/onboarding")).json()
    step = next(x for x in home["steps"] if x["key"] == "profile")
    assert step["done"] is True and step["confirm_profile_id"] is None
    assert ctx["enqueuer"].calls == []  # confirming sends nothing anywhere


async def test_another_accounts_suggestion_cannot_be_confirmed(ctx) -> None:  # noqa: F811
    async with ctx["sm"]() as s:
        other = Tenant(email="other@example.test", password_hash="!")
        s.add(other)
        await s.flush()
        from app.db.models import ConnectionStatus, EtsyConnection

        conn = EtsyConnection(tenant_id=other.id, status=ConnectionStatus.active, etsy_user_id=901, shop_id=901)
        s.add(conn)
        await s.flush()
        p = ListingProfile(tenant_id=other.id, connection_id=conn.id, name="X", reference_listing_id=5,
                           source="suggested", confirmed=False)
        s.add(p)
        await s.commit()
        pid = p.id
    assert (await ctx["client"].post(f"/api/profiles/{pid}/confirm")).status_code == 404
