"""Etsy keeps at most 20 images on a listing (pipeline/chart_order.py::fit). The size
charts always keep their places; when photos and charts together are more than
20, the last photos in the photo order are left out: before the draft starts, the
same way in every shop and in "Replace images", and never silently."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    Asset,
    AssetStatus,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
)
from app.etsy.publisher import PublishImage, publish_content, replace_listing_images
from app.pipeline import chart_order
from app.pipeline.chart_order import END, MAX_IMAGES
from app.workers import replace as replace_worker
from app.workers.publish import draft_photos
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_grouping import _upload
from tests.test_publish_api import _add_content, _shop, ctx  # noqa: F401  (fixture)
from tests.test_publisher import CONFIG, REFERENCE, FakeEtsy, _seed
from tests.test_replace_photos import _Etsy, _Images, _png

CHARTS = [901, 902, 903]
#: Three charts, each in its own place: 2nd, 5th, and after every photo.
SLOTS = [1, 3, END]


def _photos(n: int) -> list[str]:
    return ["cover.jpg", *(f"p{i:02d}.jpg" for i in range(2, n + 1))]


# --- the rule itself -------------------------------------------------------------------------


def test_exactly_twenty_images_all_fit() -> None:
    kept, over = chart_order.fit(_photos(17), 3)
    assert (len(kept), over) == (17, [])
    assert len(chart_order.arrange(kept, CHARTS, SLOTS)) == MAX_IMAGES == 20
    # No charts: twenty photos fit too.
    assert chart_order.fit(_photos(20), 0) == (_photos(20), [])


def test_the_twenty_first_image_is_the_last_photo_and_it_is_left_out() -> None:
    kept, over = chart_order.fit(_photos(18), 3)
    assert over == ["p18.jpg"] and kept == _photos(17)
    assert chart_order.fit(_photos(21), 0)[1] == ["p21.jpg"]
    # More over the limit: still the tail of the photo order, in order.
    assert chart_order.fit(_photos(25), 3)[1] == [f"p{i}.jpg" for i in range(18, 26)]


def test_three_charts_keep_their_places_when_photos_are_left_out() -> None:
    for photos in (17, 18, 30):
        kept, _ = chart_order.fit(_photos(photos), len(CHARTS))
        order = chart_order.arrange(kept, CHARTS, SLOTS)
        assert len(order) == 20
        # 2nd, 5th and last, however many photos were uploaded.
        assert [order.index(c) + 1 for c in CHARTS] == [2, 5, 20]
        assert order[0] == "cover.jpg"
    # The cover is never the photo left out, even with the listing full of charts.
    assert chart_order.fit(_photos(4), 19) == (["cover.jpg"], ["p02.jpg", "p03.jpg", "p04.jpg"])
    assert chart_order.fit(_photos(4), 40)[0] == ["cover.jpg"]
    # A listing has a cover, so at most nineteen charts are kept.
    charts, slots = chart_order.fit_charts(list(range(25)), [END] * 25)
    assert (len(charts), len(slots)) == (19, 19)


# --- drafts ------------------------------------------------------------------------------------


async def _draft(sm: async_sessionmaker, ids: tuple, photos: list[str], fixed: list, fake: FakeEtsy):
    _, conn_id, content_id, job_id = ids
    async with sm() as s:
        result = await publish_content(
            s, job_id=job_id, content=await s.get(GeneratedContent, content_id), connection=await s.get(EtsyConnection, conn_id),
            sku="BR5475", thumbnail=PublishImage(b"t", "0.jpg", label=photos[0]),
            extra_images=[PublishImage(b"x", f"{i}.jpg", label=name) for i, name in enumerate(photos[1:], start=1)],
            fixed_image_ids=fixed, chart_slots=SLOTS[: len(fixed)], client=fake, access_token="tok", config=CONFIG,
            reference=REFERENCE, theme="x", tenant_limit=2000,
        )
    return result, [name for _, name in sorted(fake.uploaded)]


async def test_a_draft_at_exactly_twenty_sends_everything(async_sm: async_sessionmaker) -> None:
    result, sent = await _draft(async_sm, await _seed(async_sm), _photos(17), CHARTS, FakeEtsy())
    assert len(sent) == 20 and result.image_count == 20 and result.left_out == []
    assert [sent.index(c) + 1 for c in CHARTS] == [2, 5, 20]


async def test_a_draft_at_twenty_one_leaves_out_the_last_photo_and_finishes(async_sm: async_sessionmaker) -> None:
    fake = FakeEtsy()
    result, sent = await _draft(async_sm, await _seed(async_sm), _photos(18), CHARTS, fake)
    # Twenty images, not twenty-one: the draft never stops halfway on an image Etsy refuses.
    assert len(sent) == 20 and result.image_count == 20
    assert [rank for rank, _ in sorted(fake.uploaded)] == list(range(1, 21))
    # The charts are where they were put; the photo left out is the last one, by its own name.
    assert [sent.index(c) + 1 for c in CHARTS] == [2, 5, 20]
    assert result.left_out == ["p18.jpg"] and "17.jpg" not in sent and "16.jpg" in sent


async def test_every_shop_leaves_out_the_same_photos(async_sm: async_sessionmaker) -> None:
    """The profile's own shop re-uses its charts by id and another shop gets
    copies: the same twenty images in the same order, the same photo left out."""
    main_ids = await _seed(async_sm)
    main, main_sent = await _draft(async_sm, main_ids, _photos(19), CHARTS, FakeEtsy())
    async with async_sm() as s:  # a second shop: another Etsy account
        (await s.get(EtsyConnection, main_ids[1])).etsy_user_id = 901
        await s.commit()
    copies = [PublishImage(b"chart", f"size-chart-{c}.jpg") for c in CHARTS]
    other, other_sent = await _draft(async_sm, await _seed(async_sm), _photos(19), copies, FakeEtsy())
    assert main.left_out == other.left_out == ["p18.jpg", "p19.jpg"]
    assert len(main_sent) == len(other_sent) == 20
    assert [f"size-chart-{x}.jpg" if isinstance(x, int) else x for x in main_sent] == other_sent


async def test_the_worker_picks_the_photos_before_reading_a_file(async_sm: async_sessionmaker) -> None:
    """What the draft job sends is the group's photos in the seller's order, cut
    to what fits beside the charts: the files left out are never read."""
    _, _, content_id, _ = await _seed(async_sm)
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        cover = await s.get(Asset, content.asset_id)
        for rank in range(2, 19):  # eighteen photos with the cover
            s.add(Asset(batch_id=cover.batch_id, tenant_id=cover.tenant_id, original_filename=f"p{rank:02d}.png", group_key=cover.group_key,
                        storage_key=f"k{rank}", processed_key=f"k{rank}", status=AssetStatus.processed, rank=rank))
        await s.commit()
        kept, over = await draft_photos(s, cover, 3)
        assert kept[0].id == cover.id and len(kept) == 17 and [a.original_filename for a in over] == ["p18.png"]
        assert (await draft_photos(s, cover, 2))[1] == []  # 18 + 2 = 20: all of them


# --- Replace images ----------------------------------------------------------------------------


@pytest.mark.parametrize(("photos", "left"), [(17, []), (18, ["p18.jpg"])])
async def test_replace_images_at_twenty_and_at_twenty_one(async_sm: async_sessionmaker, photos: int, left: list[str]) -> None:
    ids = await _seed(async_sm)
    fake = FakeEtsy()
    async with async_sm() as s:
        result = await replace_listing_images(
            s, job_id=ids[3], listing_id=555, shop_id=900, tenant_id=ids[0], client=fake, access_token="tok",
            tenant_limit=2000, existing_listing={"listing_id": 555}, keep_image_ids=CHARTS, delete_image_ids=[700],
            new_images=[PublishImage(b"n", f"{i}.jpg", label=name) for i, name in enumerate(_photos(photos))],
            chart_slots=SLOTS,
        )
    sent = [name for _, name in sorted(fake.uploaded)]
    assert len(sent) == 20 and [sent.index(c) + 1 for c in CHARTS] == [2, 5, 20]
    assert (result.left_out, result.added, result.kept) == (left, 17, 3)


class _ThreeCharts(_Etsy):
    """The listing on Etsy: two artwork photos and the three size charts the app put there."""

    async def get_listing_images(self, listing_id: int, **_: Any) -> dict[str, Any]:
        type(self).calls.append(("get_listing_images", listing_id))
        return {"results": [{"listing_image_id": i, "rank": n, "url_fullxfull": f"https://img/{i}.jpg"}
                            for n, i in enumerate((801, 802, *CHARTS), start=1)]}


async def test_the_replace_job_says_which_photos_it_left_out(ctx, monkeypatch) -> None:  # noqa: F811
    from app.db.models import UploadBatch, UploadBatchStatus

    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    read: list[str] = []
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
        profile = (await s.execute(select(ListingProfile).where(ListingProfile.connection_id == shop))).scalars().first()
        profile.fixed_image_ids = CHARTS
        profile.size_chart_slots = [{"id": c, "slot": slot} for c, slot in zip(CHARTS, SLOTS)]
        batch = UploadBatch(tenant_id=ctx["tenant_id"], status=UploadBatchStatus.ready, file_count=19)
        s.add(batch)
        await s.flush()
        for rank in range(1, 20):  # nineteen new photos + three charts = 22
            s.add(Asset(tenant_id=ctx["tenant_id"], batch_id=batch.id, original_filename=f"new-{rank:02d}.png", group_key="G1", rank=rank,
                        status=AssetStatus.processed, storage_key=f"k/{rank:02d}", processed_key=f"k/{rank:02d}", mime_type="image/png"))
        await s.commit()
        batch_id = batch.id
    res = await ctx["client"].post(f"/api/shop/listings/777/replace-images?shop={shop}", json={"batch_id": str(batch_id), "group_key": "G1"})
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]

    class Storage:
        def __init__(self, *_: Any) -> None:
            pass

        def get(self, key: str) -> bytes:
            read.append(key)
            return _png()

    class Service:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def get_valid_access_token(self, session: Any, connection: Any) -> str:
            return "token"

    _ThreeCharts.calls = []
    monkeypatch.setattr(replace_worker, "EtsyApiClient", _ThreeCharts)
    monkeypatch.setattr(replace_worker, "LocalStorage", Storage)
    monkeypatch.setattr(replace_worker, "ConnectionService", Service)
    monkeypatch.setattr(replace_worker, "get_cipher", lambda: None)
    monkeypatch.setattr(replace_worker, "httpx", _Images)
    assert await replace_worker.run_replace_images_job({"sessionmaker": ctx["sm"], "bucket": None, "quota": None}, job_id) == "succeeded"

    uploads = sorted(c[1:] for c in _ThreeCharts.calls if c[0] == "upload")
    # Twenty images; the charts second, fifth and last, as the profile places them.
    assert [rank for rank, _ in uploads] == list(range(1, 21))
    assert [rank for rank, what in uploads if isinstance(what, int)] == [2, 5, 20]
    assert [what for _, what in uploads if isinstance(what, int)] == CHARTS
    async with ctx["sm"]() as s:
        job = await s.get(Job, uuid.UUID(job_id))
        assert job.status is JobStatus.succeeded and job.payload["left_out"] == ["new-18.png", "new-19.png"]
    # The seller is told from what the job did.
    assert (await ctx["client"].get(f"/api/jobs/{job_id}")).json()["left_out"] == ["new-18.png", "new-19.png"]


# --- before drafting: the group card and the review card ------------------------------------------


async def test_the_cards_say_what_will_be_left_out_before_any_draft(client: AsyncClient) -> None:  # noqa: F811
    batch = (await client.post("/api/batches")).json()["id"]
    names = [f"BR5229-{i:02d}.png" for i in range(1, 19)]  # eighteen photos
    for name in names:
        await _upload(client, batch, name)
    await client.post(f"/api/batches/{batch}/finalize")

    async def card() -> dict | None:
        return (await client.get(f"/api/batches/{batch}/groups")).json()[0]["image_limit"]

    assert await card() is None  # no charts yet: eighteen images
    async with client.sm() as s:  # type: ignore[attr-defined]
        assets = (await s.execute(select(Asset).where(Asset.batch_id == uuid.UUID(batch)).order_by(Asset.rank, Asset.original_filename))).scalars().all()
        conn = EtsyConnection(tenant_id=assets[0].tenant_id, etsy_user_id=1, shop_id=1)
        s.add(conn)
        await s.flush()
        profile = ListingProfile(tenant_id=assets[0].tenant_id, connection_id=conn.id, name="Tee", reference_listing_id=42,
                                 content_template="apparel", confirmed=True, fixed_image_ids=CHARTS[:2], size_chart_position="third")
        s.add(profile)
        await s.commit()
        pid = profile.id
    assert (await client.put(f"/api/batches/{batch}/groups", json={"profile_id": str(pid)})).status_code == 200

    assert await card() is None  # two charts: exactly twenty
    async with client.sm() as s:  # type: ignore[attr-defined]
        (await s.get(ListingProfile, pid)).fixed_image_ids = CHARTS
        # The listing is written: its review card says the same as the group's card.
        s.add(GeneratedContent(tenant_id=assets[0].tenant_id, batch_id=uuid.UUID(batch), asset_id=assets[0].id, title="A Title For The Listing",
                               tags=[f"tag{i}" for i in range(13)], description="Body", listing_profile_id=pid))
        await s.commit()
    # Three charts: twenty-one. The last photo in the order is the one named.
    assert await card() == {"total": 21, "limit": 20, "left_out": [names[-1]]}
    review = (await client.get(f"/api/batches/{batch}/content")).json()
    assert [c["image_limit"] for c in review] == [{"total": 21, "limit": 20, "left_out": [names[-1]]}]

    # Reordering chooses: with another photo moved to the end, that one is left out.
    async with client.sm() as s:  # type: ignore[attr-defined]
        group = (await s.execute(select(Asset.group_key).where(Asset.batch_id == uuid.UUID(batch)))).scalars().first()
    ids = [a.id for a in assets]
    order = [*ids[:4], *ids[5:], ids[4]]
    res = await client.put(f"/api/batches/{batch}/groups/order", json={"group_key": group or "", "asset_ids": [str(i) for i in order]})
    assert res.status_code == 200, res.text
    assert (await card())["left_out"] == [names[4]]
    # Removing a photo makes room: nothing is left out.
    assert (await client.delete(f"/api/assets/{ids[7]}")).status_code == 200
    assert await card() is None
    assert [c["image_limit"] for c in (await client.get(f"/api/batches/{batch}/content")).json()] == [None]
