"""Where a listing's size charts sit among its photos (pipeline/chart_order.py): the
profile's position by default, a group's own drag when it has one, the same place
in every shop, kept by "Replace images", and checked on the draft's read-back."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    Asset,
    EtsyConnection,
    GeneratedContent,
    ListingGroupSetting,
    ListingProfile,
)
from app.etsy.publisher import PublishImage, publish_content, replace_listing_images
from app.pipeline import chart_order
from app.pipeline.chart_order import END
from app.workers.publish import chart_plan
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_grouping import _upload
from tests.test_publisher import CONFIG, REFERENCE, FakeEtsy, _seed

# --- the order itself ------------------------------------------------------------------


def test_the_profile_positions() -> None:
    photos, charts = ["cover", "p2", "p3", "p4"], ["chart"]
    assert chart_order.arrange(photos, charts, chart_order.default_slots("after_cover", 1)) == ["cover", "chart", "p2", "p3", "p4"]
    assert chart_order.arrange(photos, charts, chart_order.default_slots("third", 1)) == ["cover", "p2", "chart", "p3", "p4"]
    assert chart_order.arrange(photos, charts, chart_order.default_slots("last", 1)) == ["cover", "p2", "p3", "p4", "chart"]
    # Today's behaviour is the default: after every photo.
    assert chart_order.default_slots(None, 2) == [END, END]
    # Two charts stay together, in their own order.
    assert chart_order.arrange(photos, ["c1", "c2"], [1, 1]) == ["cover", "c1", "c2", "p2", "p3", "p4"]


def test_a_dragged_order_and_its_slots() -> None:
    order = ["cover", "p2", "chart", "p3"]
    slots = chart_order.slots_from_order(order, ["chart"])
    assert slots == [2] and chart_order.arrange(["cover", "p2", "p3"], ["chart"], slots) == order
    # Dragged to the end: it stays last when photos are added.
    assert chart_order.slots_from_order(["cover", "p2", "chart"], ["chart"]) == [END]
    assert chart_order.arrange(["cover", "p2", "p3"], ["chart"], [END])[-1] == "chart"
    # Never before the cover; a slot past the photos is the end.
    assert chart_order.slots_from_order(["chart", "cover", "p2"], ["chart"]) == [1]
    assert chart_order.arrange(["cover"], ["chart"], [2]) == ["cover", "chart"]
    assert chart_order.positions(3, [1]) == [2] and chart_order.positions(3, [END]) == [4]
    with pytest.raises(ValueError):
        chart_order.check_position("first")


# --- drafts --------------------------------------------------------------------------------


async def _profile(sm: async_sessionmaker, content_id: uuid.UUID, conn_id: uuid.UUID, position: str) -> uuid.UUID:
    async with sm() as s:
        content = await s.get(GeneratedContent, content_id)
        profile = ListingProfile(tenant_id=content.tenant_id, connection_id=conn_id, name="Tee", reference_listing_id=1,
                                 content_template="apparel", confirmed=True, fixed_image_ids=[901],
                                 size_chart_position=position)
        s.add(profile)
        await s.flush()
        content.listing_profile_id = profile.id
        await s.commit()
        return profile.id


async def _draft(sm: async_sessionmaker, ids: tuple, fixed: list, fake: FakeEtsy) -> list:
    """Make the draft as the worker does: its chart plan, then the images."""
    _, conn_id, content_id, job_id = ids
    async with sm() as s:
        content = await s.get(GeneratedContent, content_id)
        asset = await s.get(Asset, content.asset_id)
        profile = await s.get(ListingProfile, content.listing_profile_id)
        chart_profile, own = await chart_plan(s, content.tenant_id, content, asset, profile)
        slots = chart_order.clean_slots(own, len(fixed), chart_profile.size_chart_position)
        await publish_content(
            s, job_id=job_id, content=content, connection=await s.get(EtsyConnection, conn_id), sku="BR5475",
            thumbnail=PublishImage(b"t", "cover.jpg"),
            extra_images=[PublishImage(b"b", "back.jpg"), PublishImage(b"d", "detail.jpg")],
            fixed_image_ids=fixed, chart_slots=slots, client=fake, access_token="tok", config=CONFIG,
            reference=REFERENCE, theme="x", tenant_limit=2000,
        )
    return [name for _, name in sorted(fake.uploaded)]


async def test_drafts_put_the_charts_where_the_profile_says(async_sm: async_sessionmaker) -> None:
    ids = await _seed(async_sm)
    await _profile(async_sm, ids[2], ids[1], "after_cover")
    assert await _draft(async_sm, ids, [901], FakeEtsy()) == ["cover.jpg", 901, "back.jpg", "detail.jpg"]


async def test_a_groups_own_drag_overrides_the_profile(async_sm: async_sessionmaker) -> None:
    ids = await _seed(async_sm)
    await _profile(async_sm, ids[2], ids[1], "after_cover")
    async with async_sm() as s:
        content = await s.get(GeneratedContent, ids[2])
        asset = await s.get(Asset, content.asset_id)
        s.add(ListingGroupSetting(tenant_id=content.tenant_id, batch_id=content.batch_id,
                                  group_key=asset.group_key or "", chart_slots=[2]))
        await s.commit()
    assert await _draft(async_sm, ids, [901], FakeEtsy()) == ["cover.jpg", "back.jpg", 901, "detail.jpg"]


async def test_every_shop_gets_the_charts_at_the_same_place(async_sm: async_sessionmaker) -> None:
    # The main shop re-uses its chart by id; another shop gets a copy uploaded.
    # Either way it is third.
    main_ids = await _seed(async_sm)
    await _profile(async_sm, main_ids[2], main_ids[1], "third")
    main = await _draft(async_sm, main_ids, [901], FakeEtsy())
    async with async_sm() as s:  # a second shop: another Etsy account
        (await s.get(EtsyConnection, main_ids[1])).etsy_user_id = 901
        await s.commit()
    other_ids = await _seed(async_sm)
    await _profile(async_sm, other_ids[2], other_ids[1], "third")
    other = await _draft(async_sm, other_ids, [PublishImage(b"chart", "size-chart-901.jpg")], FakeEtsy())
    assert main.index(901) == other.index("size-chart-901.jpg") == 2


async def test_the_read_back_catches_charts_out_of_place(async_sm: async_sessionmaker) -> None:
    ids = await _seed(async_sm)
    await _profile(async_sm, ids[2], ids[1], "after_cover")

    class Shuffled(FakeEtsy):
        async def get_listing_images(self, listing_id: int, **_: Any) -> dict[str, Any]:
            order = list(getattr(self, "listing_images", []))
            order.append(order.pop(1))  # Etsy kept the chart at the end
            return {"results": [{"listing_image_id": i, "rank": n} for n, i in enumerate(order, start=1)]}

    with pytest.raises(ValueError, match="did not save in the order set"):
        await _draft(async_sm, ids, [901], Shuffled())


# --- Replace images ----------------------------------------------------------------------------


async def test_replace_images_keeps_the_charts_place(async_sm: async_sessionmaker) -> None:
    ids = await _seed(async_sm)
    fake = FakeEtsy()
    async with async_sm() as s:
        await replace_listing_images(
            s, job_id=ids[3], listing_id=555, shop_id=900, tenant_id=ids[0], client=fake, access_token="tok",
            tenant_limit=2000, existing_listing={"listing_id": 555}, keep_image_ids=[901], delete_image_ids=[700],
            new_images=[PublishImage(b"n1", "new-cover.jpg"), PublishImage(b"n2", "new-2.jpg"), PublishImage(b"n3", "new-3.jpg")],
            chart_slots=[1],
        )
    assert [name for _, name in sorted(fake.uploaded)] == ["new-cover.jpg", 901, "new-2.jpg", "new-3.jpg"]
    assert fake.deleted == [700]


async def test_replace_images_reads_the_groups_place(async_sm: async_sessionmaker) -> None:
    from app.db.models import Job, JobType
    from app.workers.replace import _chart_slots

    ids = await _seed(async_sm)
    await _profile(async_sm, ids[2], ids[1], "third")
    async with async_sm() as s:
        content = await s.get(GeneratedContent, ids[2])
        asset = await s.get(Asset, content.asset_id)
        job = Job(tenant_id=content.tenant_id, connection_id=ids[1], type=JobType.replace_images,
                  payload={"content_id": str(content.id), "group_key": asset.group_key or ""}, batch_id=content.batch_id)
        s.add(job)
        await s.commit()
        assert await _chart_slots(s, job, content.batch_id, 1) == [2]  # the profile's "third"
        s.add(ListingGroupSetting(tenant_id=content.tenant_id, batch_id=content.batch_id,
                                  group_key=asset.group_key or "", chart_slots=[END]))
        await s.commit()
        assert await _chart_slots(s, job, content.batch_id, 1) == [END]  # the group's drag


# --- the page: the setting, the tiles, saving a drag ---------------------------------------------


async def test_profile_setting_and_group_drag_through_the_api(client: AsyncClient) -> None:  # noqa: F811
    from sqlalchemy import select

    from app.api import deps
    from app.db.models import Tenant

    class _Queue:
        async def enqueue(self, *args: Any, **kwargs: Any) -> None:
            return None

    client.app.dependency_overrides[deps.get_enqueuer] = lambda: _Queue()  # type: ignore[attr-defined]
    batch = (await client.post("/api/batches")).json()["id"]
    for name in ("BR5229-1.png", "BR5229-2.png", "BR5229-3.png"):
        await _upload(client, batch, name)
    await client.post(f"/api/batches/{batch}/finalize")
    async with client.sm() as s:  # type: ignore[attr-defined]
        tenant = (await s.execute(select(Tenant).order_by(Tenant.created_at.desc()))).scalars().first()
        b = (await s.execute(select(Asset).where(Asset.batch_id == uuid.UUID(batch)))).scalars().first()
        conn = EtsyConnection(tenant_id=b.tenant_id, etsy_user_id=1, shop_id=1)
        s.add(conn)
        await s.flush()
        profile = ListingProfile(tenant_id=b.tenant_id, connection_id=conn.id, name="Tee", reference_listing_id=42,
                                 content_template="apparel", confirmed=True, fixed_image_ids=[901, 902])
        s.add(profile)
        await s.commit()
        profile_id = profile.id
        assert tenant is not None
    assert (await client.put(f"/api/batches/{batch}/groups", json={"profile_id": str(profile_id)})).status_code == 200
    group = (await client.get(f"/api/batches/{batch}/groups")).json()[0]
    assert [c["listing_image_id"] for c in group["size_charts"]] == [901, 902]
    assert (group["chart_slots"], group["chart_position"], group["chart_slots_custom"]) == ([END, END], "last", False)
    assert group["chart_listing_id"] == 42  # the back link to the listing on Etsy
    # The profile's setting.
    res = await client.patch(f"/api/profiles/{profile_id}", json={"size_chart_position": "after_cover"})
    assert res.status_code == 200 and res.json()["size_chart_position"] == "after_cover"
    assert (await client.patch(f"/api/profiles/{profile_id}", json={"size_chart_position": "first"})).status_code == 422
    assert (await client.get(f"/api/batches/{batch}/groups")).json()[0]["chart_slots"] == [1, 1]
    # A drag in the group's strip, saved; then back to the profile's.
    res = await client.put(f"/api/batches/{batch}/groups/chart-slots", json={"group_key": "BR5229", "slots": [2, END]})
    assert res.status_code == 200 and (res.json()[0]["chart_slots"], res.json()[0]["chart_slots_custom"]) == ([2, END], True)
    assert (await client.put(f"/api/batches/{batch}/groups/chart-slots", json={"group_key": "BR5229", "slots": [0]})).status_code == 422
    res = await client.put(f"/api/batches/{batch}/groups/chart-slots", json={"group_key": "BR5229", "slots": None})
    assert res.json()[0]["chart_slots"] == [1, 1] and res.json()[0]["chart_slots_custom"] is False
    assert (await client.put(f"/api/batches/{batch}/groups/chart-slots", json={"group_key": "NOPE", "slots": [1]})).status_code == 404


async def test_another_account_cannot_move_the_charts(client: AsyncClient) -> None:  # noqa: F811
    from app.db.models import Tenant, UploadBatch, UploadBatchStatus

    async with client.sm() as s:  # type: ignore[attr-defined]
        other = Tenant(email=f"{uuid.uuid4()}@e.com", password_hash="x")
        s.add(other)
        await s.flush()
        theirs = UploadBatch(tenant_id=other.id, status=UploadBatchStatus.ready, file_count=0)
        s.add(theirs)
        await s.commit()
    res = await client.put(f"/api/batches/{theirs.id}/groups/chart-slots", json={"group_key": "", "slots": [1]})
    assert res.status_code == 404
