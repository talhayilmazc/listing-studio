"""Replace images, photos only: the images change, the title, tags and description
do not; no AI is called, so nothing counts as a listing generated. The full
option still rewrites the title and tags and counts one."""

from __future__ import annotations

import io
import uuid
from typing import Any

from PIL import Image
from sqlalchemy import select, update

from app.core import allowance
from app.db.models import AiCall, AllowanceUse, Asset, AssetStatus, Job, JobStatus, JobType, ListingProfile, Tenant, UploadBatch, UploadBatchStatus
from app.etsy.publisher import PublishImage, replace_listing_images
from app.workers import replace as worker
from tests.test_publish_api import _add_content, _shop, ctx  # noqa: F401  (fixture)
from tests.test_publisher import FakeEtsy as PublisherFake
from tests.test_publisher import _seed as publisher_seed


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (600, 600), (200, 60, 60)).save(buf, "PNG")
    return buf.getvalue()


async def test_without_new_text_the_listings_title_tags_and_description_are_never_sent(async_sm) -> None:
    tenant_id, _conn_id, _content_id, job_id = await publisher_seed(async_sm)
    fake = PublisherFake()
    async with async_sm() as s:
        result = await replace_listing_images(
            s, job_id=job_id, listing_id=999, shop_id=900, tenant_id=tenant_id, client=fake, access_token="tok",
            tenant_limit=4500, existing_listing={"listing_id": 999, "title": "Kept Title", "state": "active"},
            keep_image_ids=[900], delete_image_ids=[801], new_images=[PublishImage(b"a", "a.jpg"), PublishImage(b"b", "b.jpg")],
        )
    assert (result.deleted, result.added, result.kept) == (1, 2, 1)
    assert fake.deleted == [801] and fake.uploaded == [(1, "a.jpg"), (2, "b.jpg"), (3, 900)]
    assert fake.last_update is None  # no update at all: the copy on Etsy is untouched


async def _batch(ctx) -> uuid.UUID:  # noqa: F811
    async with ctx["sm"]() as s:
        batch = UploadBatch(tenant_id=ctx["tenant_id"], status=UploadBatchStatus.ready, file_count=2)
        s.add(batch)
        await s.flush()
        for rank, name in enumerate(("front.png", "back.png"), start=1):
            s.add(Asset(tenant_id=ctx["tenant_id"], batch_id=batch.id, original_filename=name, group_key="G1", rank=rank,
                        status=AssetStatus.processed, storage_key=f"k/{name}", processed_key=f"k/{name}", mime_type="image/png"))
        await s.commit()
        return batch.id


async def _allow(ctx, amount: int) -> None:  # noqa: F811
    async with ctx["sm"]() as s:
        await s.execute(update(Tenant).where(Tenant.id == ctx["tenant_id"]).values(allowance_amount=amount, allowance_period="monthly"))
        await s.commit()


async def test_photos_only_is_the_default_and_is_not_limited_by_the_listing_allowance(ctx) -> None:  # noqa: F811
    batch = await _batch(ctx)
    await _allow(ctx, 0)  # nothing left to generate
    res = await ctx["client"].post("/api/shop/listings/12345/replace-images", json={"batch_id": str(batch), "group_key": "G1"})
    assert res.status_code == 200, res.text
    async with ctx["sm"]() as s:
        job = (await s.execute(select(Job).where(Job.type == JobType.replace_images))).scalars().one()
        assert job.payload["mode"] == "photos"
    mine = (await ctx["client"].get("/api/account/allowance")).json()
    assert (mine["used"], mine["pending"]) == (0, 0)  # a queued photos-only job generates nothing

    # The full option writes a new title and tags: one listing generated, so the allowance is asked.
    full = await ctx["client"].post("/api/shop/listings/12345/replace-images", json={"batch_id": str(batch), "group_key": "G1", "mode": "full"})
    assert full.status_code == 429 and "generated your 0 listings" in full.json()["detail"]
    await _allow(ctx, 5)
    full = await ctx["client"].post("/api/shop/listings/12345/replace-images", json={"batch_id": str(batch), "group_key": "G1", "mode": "full"})
    assert full.status_code == 200
    mine = (await ctx["client"].get("/api/account/allowance")).json()
    assert (mine["used"], mine["pending"]) == (1, 1)  # held as used until it has written
    assert (await ctx["client"].post("/api/shop/listings/12345/replace-images", json={"batch_id": str(batch), "mode": "titles"})).status_code == 422


class _Etsy:
    """The listing on Etsy: an artwork photo, a second one, and a size chart the app put there."""

    calls: list[tuple] = []

    def __init__(self, **_: Any) -> None:
        pass

    async def get_listing(self, listing_id: int, **_: Any) -> dict[str, Any]:
        type(self).calls.append(("get_listing", listing_id))
        return {"listing_id": listing_id, "title": "The Title On Etsy", "description": "The Title On Etsy\n\nBody", "taxonomy_id": 1, "state": "active"}

    async def get_listing_images(self, listing_id: int, **_: Any) -> dict[str, Any]:
        type(self).calls.append(("get_listing_images", listing_id))
        return {"results": [
            {"listing_image_id": 801, "rank": 1, "url_fullxfull": "https://img/801.jpg"},
            {"listing_image_id": 802, "rank": 2, "url_fullxfull": "https://img/802.jpg"},
            {"listing_image_id": 9001, "rank": 3, "url_fullxfull": "https://img/9001.jpg"},
        ]}

    async def get_seller_taxonomy_nodes(self, **_: Any) -> dict[str, Any]:
        type(self).calls.append(("taxonomy",))
        return {"results": []}

    async def delete_listing_image(self, shop_id: int, listing_id: int, image_id: int, **_: Any) -> None:
        type(self).calls.append(("delete", image_id))

    async def upload_listing_image(self, shop_id: int, listing_id: int, *, rank: int, filename: str | None = None,
                                   listing_image_id: int | None = None, **_: Any) -> dict[str, Any]:
        type(self).calls.append(("upload", rank, listing_image_id or filename))
        return {"listing_image_id": 1}

    async def update_listing(self, *args: Any, **kwargs: Any) -> None:
        type(self).calls.append(("update_listing", kwargs.get("updates")))


class _Images:
    """The existing images cannot be fetched: nothing here looks at them."""

    Timeout = staticmethod(lambda *a, **k: None)

    class AsyncClient:
        def __init__(self, **_: Any) -> None:
            pass

        async def __aenter__(self) -> "_Images.AsyncClient":
            return self

        async def __aexit__(self, *_: Any) -> None:
            return None

        async def get(self, url: str) -> None:
            raise RuntimeError("no network in tests")


async def test_a_photos_only_job_changes_the_images_and_calls_no_ai(ctx, monkeypatch) -> None:  # noqa: F811
    batch = await _batch(ctx)
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
        profile = (await s.execute(select(ListingProfile).where(ListingProfile.connection_id == shop))).scalars().first()
        profile.fixed_image_ids = [9001]  # the size chart this app copies onto its drafts
        await s.commit()
    res = await ctx["client"].post(f"/api/shop/listings/777/replace-images?shop={shop}", json={"batch_id": str(batch), "group_key": "G1"})
    assert res.status_code == 200, res.text
    async with ctx["sm"]() as s:
        job = (await s.execute(select(Job).where(Job.type == JobType.replace_images))).scalars().one()
        assert job.payload["mode"] == "photos" and job.payload["chart_ids"] == [9001] and job.payload["content_id"] == str(content_id)

    class Storage:
        def __init__(self, *_: Any) -> None:
            pass

        def get(self, key: str) -> bytes:
            return _png()

    class Service:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def get_valid_access_token(self, session: Any, connection: Any) -> str:
            return "token"

    def no_ai(*_: Any, **__: Any) -> None:
        raise AssertionError("photos only must not build an AI client")

    _Etsy.calls = []
    monkeypatch.setattr(worker, "EtsyApiClient", _Etsy)
    monkeypatch.setattr(worker, "LocalStorage", Storage)
    monkeypatch.setattr(worker, "ConnectionService", Service)
    monkeypatch.setattr(worker, "get_cipher", lambda: None)
    monkeypatch.setattr(worker, "httpx", _Images)
    monkeypatch.setattr(worker, "client_for", no_ai)
    monkeypatch.setattr(worker, "AnthropicVisionAnalyzer", no_ai)
    monkeypatch.setattr(worker, "AnthropicContentGenerator", no_ai)

    before = (await ctx["client"].get(f"/api/batches/{(await _content_batch(ctx, content_id))}/content")).json()
    result = await worker.run_replace_images_job({"sessionmaker": ctx["sm"], "bucket": None, "quota": None}, str(job.id))
    assert result == "succeeded", result

    calls = _Etsy.calls
    # The two old photos go; the size chart, known by its id, stays and moves after the new ones.
    assert [c for c in calls if c[0] == "delete"] == [("delete", 801), ("delete", 802)]
    uploads = [c for c in calls if c[0] == "upload"]
    assert [(u[1], u[2] if isinstance(u[2], int) else u[2].split("-")[-1].split(".")[-1]) for u in uploads] == [(1, "jpg"), (2, "jpg"), (3, 9001)]
    # Nothing about the listing's text is sent to Etsy.
    assert not any(c[0] in ("update_listing", "taxonomy") for c in calls)

    async with ctx["sm"]() as s:
        done = await s.get(Job, job.id)
        assert done.status is JobStatus.succeeded
        # No AI call, no listing generated.
        assert (await s.execute(select(AiCall))).scalars().all() == []
        assert (await s.execute(select(AllowanceUse))).scalars().all() == []
    after = (await ctx["client"].get(f"/api/batches/{(await _content_batch(ctx, content_id))}/content")).json()
    assert [(c["title"], c["tags"], c["description"]) for c in after] == [(c["title"], c["tags"], c["description"]) for c in before]
    mine = (await ctx["client"].get("/api/account/allowance")).json()
    assert mine["used"] == 0 and mine["label"] == allowance.LABEL


async def _content_batch(ctx, content_id: uuid.UUID) -> uuid.UUID:  # noqa: F811
    from app.db.models import GeneratedContent

    async with ctx["sm"]() as s:
        return (await s.get(GeneratedContent, content_id)).batch_id


async def _run_full(ctx, monkeypatch, *, listing_style: str, template: str) -> tuple[dict, list]:  # noqa: F811
    """Run a "full" replace on draft 777, made with a profile of the given style."""
    from types import SimpleNamespace

    from app.core.config import get_settings
    from app.db.models import ContentVersion, ListingPublication
    from app.pipeline import versions

    batch = await _batch(ctx)
    await _add_content(ctx["sm"], ctx["tenant_id"], listing_id=777)
    async with ctx["sm"]() as s:
        shop = await _shop(s, ctx["tenant_id"])
        profile = (await s.execute(select(ListingProfile).where(ListingProfile.connection_id == shop))).scalars().first()
        profile.listing_style, profile.content_template = listing_style, template
        publication = (await s.execute(select(ListingPublication).where(ListingPublication.etsy_listing_id == 777))).scalar_one()
        versions.drafted(s, publication, None, title="Old", tags=["old"], description="Old", attributes={"Holiday": "Christmas"})
        await s.commit()
    await _allow(ctx, 5)
    res = await ctx["client"].post(f"/api/shop/listings/777/replace-images?shop={shop}",
                                   json={"batch_id": str(batch), "group_key": "G1", "mode": "full"})
    assert res.status_code == 200, res.text
    async with ctx["sm"]() as s:
        job = (await s.execute(select(Job).where(Job.type == JobType.replace_images))).scalars().one()
        assert job.payload["profile_id"] == str(profile.id)

    made: dict = {}

    class Generator:
        def __init__(self, client: Any, **kwargs: Any) -> None:
            made.update(kwargs)

        async def generate(self, analysis: Any, sku: Any = None) -> Any:
            listing = SimpleNamespace(title="Retro Frog Shirt, Pond Humor", tags=[f"t{i}" for i in range(13)],
                                      opening="A retro frog shirt for pond lovers.")
            return SimpleNamespace(listing=listing)

    class Analyzer:
        def __init__(self, *_: Any) -> None:
            pass

        async def analyze(self, *_: Any) -> Any:
            return SimpleNamespace(analysis=None)

    class Classifier:
        def __init__(self, *_: Any) -> None:
            pass

        async def classify(self, *_: Any) -> str:
            return "artwork"

    class Storage:
        def __init__(self, *_: Any) -> None:
            pass

        def get(self, key: str) -> bytes:
            return _png()

    class Service:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        async def get_valid_access_token(self, session: Any, connection: Any) -> str:
            return "token"

    settings = get_settings().model_copy(update={"llm_api_key": "test"})
    _Etsy.calls = []
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "EtsyApiClient", _Etsy)
    monkeypatch.setattr(worker, "LocalStorage", Storage)
    monkeypatch.setattr(worker, "ConnectionService", Service)
    monkeypatch.setattr(worker, "get_cipher", lambda: None)
    monkeypatch.setattr(worker, "httpx", _Images)
    monkeypatch.setattr(worker, "client_for", lambda *_: object())
    monkeypatch.setattr(worker, "AnthropicVisionAnalyzer", Analyzer)
    monkeypatch.setattr(worker, "AnthropicContentGenerator", Generator)
    monkeypatch.setattr(worker, "AnthropicImageKindClassifier", Classifier)
    result = await worker.run_replace_images_job({"sessionmaker": ctx["sm"], "bucket": None, "quota": None}, str(job.id))
    assert result == "succeeded", result
    async with ctx["sm"]() as s:
        rows = (await s.execute(select(ContentVersion).order_by(ContentVersion.created_at))).scalars().all()
    return made, rows


async def test_full_replace_writes_in_the_profiles_short_style(ctx, monkeypatch) -> None:  # noqa: F811
    made, rows = await _run_full(ctx, monkeypatch, listing_style="search", template="apparel")
    assert made["template"].name == "content/apparel_search" and made["title_rules"].readable
    assert made["title_rules"].max_words == 15 and "attribute_choices" in made
    sent = next(c[1] for c in _Etsy.calls if c[0] == "update_listing")
    # The new opening above the listing's own body; the old title block is gone.
    assert sent["description"] == "A retro frog shirt for pond lovers.\n\nBody"
    assert [(r.reason, r.title_style) for r in rows] == [("generated", "long"), ("replaced", "short")]
    assert rows[1].attributes == {"Holiday": "Christmas"} and rows[0].active_to is not None


async def test_full_replace_writes_in_the_profiles_long_style(ctx, monkeypatch) -> None:  # noqa: F811
    made, rows = await _run_full(ctx, monkeypatch, listing_style="classic", template="apparel")
    assert made["template"].name == "content/apparel" and "title_rules" not in made
    sent = next(c[1] for c in _Etsy.calls if c[0] == "update_listing")
    assert sent["description"].startswith("Retro Frog Shirt, Pond Humor")
    assert [(r.reason, r.title_style) for r in rows][-1] == ("replaced", "long")
