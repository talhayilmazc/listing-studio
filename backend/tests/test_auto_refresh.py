"""Profiles refresh themselves ahead of their limits (docs/duzeltmeler-v6.md §H)."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    Asset,
    AssetStatus,
    ConnectionStatus,
    EtsyConnection,
    GeneratedContent,
    ListingProfile,
    Tenant,
    TenantStatus,
    UploadBatch,
    UploadBatchStatus,
)
from app.etsy.errors import EtsyClientError
from app.workers import profiles as worker
from app.workers.retention import purge_expired_rows
from tests.test_workers_profiles import FakeEtsy, _patch


def _ago(hours: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


PAYLOAD = {
    "taxonomy_id": 2078,
    "images": [
        {"listing_image_id": 900, "rank": 1, "url": "old", "display_url": "old", "kind": "artwork"},
        {"listing_image_id": 901, "rank": 2, "url": "old2", "display_url": "old2", "kind": "size_chart"},
    ],
}


_next_shop = iter(range(10_000, 20_000))


async def _profile(
    sm, *, structure_h: float | None, images_h: float | None, confirmed=True, used_days_ago: float | None = 1, **extra
):
    """A tenant with its own shop (each a distinct Etsy user) and one profile,
    which wrote a listing ``used_days_ago`` (None: never used)."""
    async with sm() as s:
        tenant = Tenant(email=f"{uuid.uuid4()}@e.com", password_hash="x", daily_quota=2000)
        s.add(tenant)
        await s.flush()
        shop = next(_next_shop)
        connection = EtsyConnection(
            tenant_id=tenant.id, status=ConnectionStatus.active, etsy_user_id=shop, shop_id=shop
        )
        s.add(connection)
        await s.flush()
        p = ListingProfile(tenant_id=tenant.id, connection_id=connection.id, name="Tee", reference_listing_id=111)
        s.add(p)
        await s.flush()
        tenant_id, profile_id = tenant.id, p.id
        p.confirmed = confirmed
        p.cached_payload = dict(PAYLOAD) if structure_h is not None else None
        p.updated_at = _ago(structure_h) if structure_h is not None else None
        p.images_updated_at = _ago(images_h) if images_h is not None else None
        for k, v in extra.items():
            setattr(p, k, v)
        if used_days_ago is not None:
            batch = UploadBatch(tenant_id=tenant.id, status=UploadBatchStatus.ready, file_count=1)
            s.add(batch)
            await s.flush()
            asset = Asset(batch_id=batch.id, tenant_id=tenant.id, original_filename="a.png", storage_key="k",
                          status=AssetStatus.processed, rank=1)
            s.add(asset)
            await s.flush()
            s.add(GeneratedContent(tenant_id=tenant.id, batch_id=batch.id, asset_id=asset.id, title="t",
                                   listing_profile_id=p.id, created_at=_ago(used_days_ago * 24)))
        await s.commit()
    return tenant_id, profile_id


class _Queue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    async def __call__(self, function: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((function, args, kwargs))


def _ctx(sm, queue=None) -> dict:
    return {"sessionmaker": sm, "bucket": None, "quota": None, "enqueue": queue or _Queue()}


# --- the schedule ---------------------------------------------------------------------
def test_each_refresh_comes_before_its_limit() -> None:
    assert ListingProfile.AUTO_REFRESH_IMAGES_SECONDS < ListingProfile.DISPLAY_MAX_AGE_SECONDS
    assert ListingProfile.AUTO_REFRESH_SECONDS < ListingProfile.CACHE_MAX_AGE_SECONDS


async def test_the_cron_queues_what_is_due_and_nothing_else(async_sm: async_sessionmaker) -> None:
    _, fresh = await _profile(async_sm, structure_h=1, images_h=1)
    _, images_due = await _profile(async_sm, structure_h=6, images_h=5.5)
    _, all_due = await _profile(async_sm, structure_h=21, images_h=21)
    _, lapsed = await _profile(async_sm, structure_h=None, images_h=None)
    _, unconfirmed = await _profile(async_sm, structure_h=21, images_h=21, confirmed=False)

    queue = _Queue()
    counts = await worker.auto_refresh_profiles(_ctx(async_sm, queue))

    queued = {(f, a[0]) for f, a, _ in queue.calls}
    assert queued == {
        ("refresh_profile_images", str(images_due)),
        ("refresh_profile", str(all_due)),
        ("refresh_profile", str(lapsed)),
    }
    assert counts == {"refresh_profile": 2, "refresh_profile_images": 1}
    assert str(fresh) not in {a[0] for _, a, _ in queue.calls}
    assert str(unconfirmed) not in {a[0] for _, a, _ in queue.calls}
    # One queued job per profile, however often the cron fires.
    assert all(kw["_job_id"] == f"auto:{f}:{a[0]}" for f, a, kw in queue.calls)


async def test_only_profiles_used_in_the_last_two_weeks_stay_warm(async_sm: async_sessionmaker) -> None:
    """A profile nobody publishes with is refreshed on demand, not in the background."""
    _, recent = await _profile(async_sm, structure_h=21, images_h=21, used_days_ago=13)
    await _profile(async_sm, structure_h=21, images_h=21, used_days_ago=15)
    await _profile(async_sm, structure_h=21, images_h=21, used_days_ago=None)

    queue = _Queue()
    await worker.auto_refresh_profiles(_ctx(async_sm, queue))
    assert [(f, a[0]) for f, a, _ in queue.calls] == [("refresh_profile", str(recent))]


async def test_suspended_accounts_disconnected_shops_and_recent_failures_wait(
    async_sm: async_sessionmaker,
) -> None:
    t1, _ = await _profile(async_sm, structure_h=21, images_h=21)
    t2, _ = await _profile(async_sm, structure_h=21, images_h=21)
    await _profile(async_sm, structure_h=21, images_h=21, refresh_error="x", refresh_failed_at=_ago(1))
    _, retry = await _profile(async_sm, structure_h=21, images_h=21, refresh_error="x", refresh_failed_at=_ago(4))
    async with async_sm() as s:
        (await s.get(Tenant, t1)).status = TenantStatus.suspended
        for c in (await s.execute(EtsyConnection.__table__.select().where(EtsyConnection.tenant_id == t2))).all():
            (await s.get(EtsyConnection, c.id)).status = ConnectionStatus.revoked
        await s.commit()

    queue = _Queue()
    await worker.auto_refresh_profiles(_ctx(async_sm, queue))
    assert [(f, a[0]) for f, a, _ in queue.calls] == [("refresh_profile", str(retry))]


# --- the image-only refresh -----------------------------------------------------------
class ImagesOnly(FakeEtsy):
    def __init__(self, ids=(900, 901)) -> None:
        super().__init__()
        self.ids = ids
        self.calls: list[str] = []

    async def get_listing(self, listing_id: int, **kw: Any) -> dict[str, Any]:
        self.calls.append("listing")
        return await super().get_listing(listing_id, **kw)

    async def get_listing_images(self, listing_id: int, **_: Any) -> dict[str, Any]:
        self.calls.append("images")
        return {"results": [
            {"listing_image_id": i, "rank": n, "url_fullxfull": f"new{i}", "url_570xN": f"small{i}"}
            for n, i in enumerate(self.ids, start=1)
        ]}


async def test_the_image_refresh_renews_only_the_links_with_one_request(
    async_sm: async_sessionmaker, monkeypatch
) -> None:
    tenant_id, pid = await _profile(async_sm, structure_h=6, images_h=5.5)
    fake = ImagesOnly()
    _patch(monkeypatch, tenant_id, fake)

    assert await worker.refresh_profile_images(_ctx(async_sm), str(pid)) == "images-refreshed"
    assert fake.calls == ["images"]
    async with async_sm() as s:
        p = await s.get(ListingProfile, pid)
    stamp = p.images_updated_at if p.images_updated_at.tzinfo else p.images_updated_at.replace(tzinfo=timezone.utc)
    upd = p.updated_at if p.updated_at.tzinfo else p.updated_at.replace(tzinfo=timezone.utc)
    assert datetime.now(timezone.utc) - stamp < timedelta(minutes=1)
    assert datetime.now(timezone.utc) - upd > timedelta(hours=5)  # the structure's clock is untouched
    # New links, same classifications.
    assert [(i["listing_image_id"], i["display_url"], i["kind"]) for i in p.cached_payload["images"]] == [
        (900, "small900", "artwork"), (901, "small901", "size_chart"),
    ]


async def test_changed_images_get_the_full_refresh(async_sm: async_sessionmaker, monkeypatch) -> None:
    tenant_id, pid = await _profile(async_sm, structure_h=6, images_h=5.5)
    fake = ImagesOnly(ids=(900, 901, 902))
    _patch(monkeypatch, tenant_id, fake)
    assert await worker.refresh_profile_images(_ctx(async_sm), str(pid)) == "refreshed"
    assert "listing" in fake.calls  # the whole reference was read again


# --- failures are reported ------------------------------------------------------------
class Gone(FakeEtsy):
    async def get_listing(self, listing_id: int, **_: Any) -> dict[str, Any]:
        raise EtsyClientError(404, body="listing not found")


async def test_a_failed_refresh_is_recorded_for_the_seller_and_cleared_by_a_success(
    async_sm: async_sessionmaker, monkeypatch
) -> None:
    tenant_id, pid = await _profile(async_sm, structure_h=21, images_h=21)
    _patch(monkeypatch, tenant_id, Gone())
    assert await worker.refresh_profile(_ctx(async_sm), str(pid)) == "failed"
    async with async_sm() as s:
        p = await s.get(ListingProfile, pid)
        assert p.refresh_error == worker.REFERENCE_GONE and p.refresh_failed_at is not None
        assert "listing not found" not in p.refresh_error  # never Etsy's own text

    _patch(monkeypatch, tenant_id, FakeEtsy())
    assert await worker.refresh_profile(_ctx(async_sm), str(pid)) == "refreshed"
    async with async_sm() as s:
        p = await s.get(ListingProfile, pid)
        assert p.refresh_error is None and p.refresh_failed_at is None


async def test_a_disconnected_shop_is_reported(async_sm: async_sessionmaker, monkeypatch) -> None:
    tenant_id, pid = await _profile(async_sm, structure_h=21, images_h=21)
    async with async_sm() as s:
        p = await s.get(ListingProfile, pid)
        (await s.get(EtsyConnection, p.connection_id)).status = ConnectionStatus.revoked
        await s.commit()
    _patch(monkeypatch, tenant_id, FakeEtsy())
    assert await worker.refresh_profile(_ctx(async_sm), str(pid)) == "no-connection"
    async with async_sm() as s:
        assert (await s.get(ListingProfile, pid)).refresh_error == worker.NO_SHOP


class TokenFails:
    async def get_valid_access_token(self, session, connection) -> str:  # noqa: ANN001
        raise RuntimeError("invalid_grant for refresh token abc123")


async def test_a_lost_sign_in_asks_for_a_reconnect_without_leaking_detail(
    async_sm: async_sessionmaker, monkeypatch
) -> None:
    tenant_id, pid = await _profile(async_sm, structure_h=21, images_h=21)
    _patch(monkeypatch, tenant_id, FakeEtsy())
    monkeypatch.setattr(worker, "_connection_service", lambda settings: TokenFails())
    assert await worker.refresh_profile(_ctx(async_sm), str(pid)) == "failed"
    async with async_sm() as s:
        error = (await s.get(ListingProfile, pid)).refresh_error
    assert error == worker.ACCESS_LOST and "abc123" not in error


# --- retention still holds -------------------------------------------------------------
async def test_image_links_follow_their_own_clock_in_retention(async_sm: async_sessionmaker) -> None:
    _, renewed = await _profile(async_sm, structure_h=10, images_h=1)
    _, stale = await _profile(async_sm, structure_h=10, images_h=7)
    async with async_sm() as s:
        await purge_expired_rows(s)
    async with async_sm() as s:
        kept = (await s.get(ListingProfile, renewed)).cached_payload["images"][0]
        stripped = (await s.get(ListingProfile, stale)).cached_payload["images"][0]
    assert kept["url"] == "old"  # renewed an hour ago: within 6 hours
    assert "url" not in stripped and stripped["kind"] == "artwork"  # past 6 hours: links gone
