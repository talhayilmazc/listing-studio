""""Regenerate" replaces a group's content, within limits; profiles not in use
refresh on demand (follow-up to docs/duzeltmeler-v6.md §H)."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from sqlalchemy import select

from app.api import batches as batches_api
from app.api import deps
from app.db.models import Asset, GeneratedContent, ListingProfile
from app.pipeline.llm import AnthropicLLMClient
from app.pipeline.storage import LocalStorage
from tests.support import VALID_TITLE, FakeMessages, fake_response
from tests.test_publish_api import _add_content, ctx  # noqa: F401  (fixture)

VISION = {
    "theme": "mountain sunrise",
    "embedded_text": "",
    "style": "flat vector",
    "colors": ["orange"],
    "target_audience": "hikers",
    "product_type_hints": ["poster"],
}
NEW_TITLE = ("Sunrise Mountain Printable Wall Art Digital Download Poster " * 3)[:130]


def _content_reply(title: str = NEW_TITLE) -> dict:
    return {"title": title, "tags": [f"new{i}" for i in range(13)], "description": "New."}


@pytest.fixture()
def llm(ctx, monkeypatch, tmp_path, test_settings):  # noqa: F811
    """A provider that answers from a queue, and storage holding the group's image."""
    test_settings.llm_api_key = "test-key"
    storage = LocalStorage(tmp_path)
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "orange").save(buf, "PNG")
    storage.put("p/1.png", buf.getvalue(), "image/png")
    ctx["app"].dependency_overrides[deps.get_storage] = lambda: storage
    messages = FakeMessages([])
    monkeypatch.setattr(
        batches_api,
        "client_for",
        lambda settings, role: AnthropicLLMClient(
            api_key="test-key", model="claude-haiku-4-5-20251001", messages_client=messages
        ),
    )
    return messages


async def _group(ctx, *, approved=False, listing_id=None, tokens=(500, 100)):  # noqa: F811
    """One listing group with content; returns (batch id, old content id, profile id)."""
    content_id = await _add_content(ctx["sm"], ctx["tenant_id"], approved=approved, listing_id=listing_id)
    async with ctx["sm"]() as s:
        content = await s.get(GeneratedContent, content_id)
        content.input_tokens, content.output_tokens = tokens
        asset = await s.get(Asset, content.asset_id)
        asset.processed_key, asset.group_key, asset.mime_type = "p/1.png", "G1", "image/png"
        await s.commit()
        return content.batch_id, content_id, content.listing_profile_id


async def _generate(ctx, batch, profile, **flags):  # noqa: F811
    body = {"profile_id": str(profile), "group_key": "G1", **flags}
    return (await ctx["client"].post(f"/api/batches/{batch}/generate", json=body)).json()


async def _contents(ctx, batch):  # noqa: F811
    async with ctx["sm"]() as s:
        rows = await s.execute(select(GeneratedContent).where(GeneratedContent.batch_id == batch))
        return list(rows.scalars())


# --- Regenerate -------------------------------------------------------------------------
async def test_generating_for_all_still_skips_a_group_with_content(ctx, llm) -> None:  # noqa: F811
    batch, old, profile = await _group(ctx)
    res = await _generate(ctx, batch, profile)
    assert (res["generated"], res["skipped"], res["skipped_groups"]) == (0, 1, [])
    assert llm.calls == []  # no LLM call spent
    assert [c.id for c in await _contents(ctx, batch)] == [old]


async def test_regenerate_replaces_the_content_and_keeps_its_cost(ctx, llm) -> None:  # noqa: F811
    batch, old, profile = await _group(ctx)
    llm._responses += [fake_response(VISION), fake_response(_content_reply(), input_tokens=100, output_tokens=50)]
    res = await _generate(ctx, batch, profile, replace=True)
    assert res["generated"] == 1, res

    [new] = await _contents(ctx, batch)
    assert new.id != old and new.title == NEW_TITLE and new.approved is False
    # The earlier generation's tokens stay counted for this listing and batch.
    assert (new.input_tokens, new.output_tokens) == (600, 150)


async def test_approved_content_is_replaced_only_when_confirmed(ctx, llm) -> None:  # noqa: F811
    batch, old, profile = await _group(ctx, approved=True)
    res = await _generate(ctx, batch, profile, replace=True)
    assert res["generated"] == 0
    assert res["skipped_groups"] == [{"group_key": "G1", "reason": "its content is approved; confirm to replace it"}]
    assert llm.calls == [] and [c.id for c in await _contents(ctx, batch)] == [old]

    llm._responses += [fake_response(VISION), fake_response(_content_reply())]
    res = await _generate(ctx, batch, profile, replace=True, replace_approved=True)
    assert res["generated"] == 1
    [new] = await _contents(ctx, batch)
    assert new.id != old and new.approved is False  # the new text needs approving again


async def test_a_group_whose_draft_is_on_etsy_is_never_touched(ctx, llm) -> None:  # noqa: F811
    batch, old, profile = await _group(ctx, approved=True, listing_id=777)
    res = await _generate(ctx, batch, profile, replace=True, replace_approved=True)
    assert res["generated"] == 0
    [skip] = res["skipped_groups"]
    assert "already on Etsy" in skip["reason"] and "Replace images" in skip["reason"]
    assert llm.calls == [] and [c.id for c in await _contents(ctx, batch)] == [old]


async def test_a_failed_regenerate_keeps_the_old_content(ctx, llm) -> None:  # noqa: F811
    batch, old, profile = await _group(ctx)
    bad = _content_reply(title="too short")
    llm._responses += [fake_response(VISION), fake_response(bad), fake_response(bad)]
    res = await _generate(ctx, batch, profile, replace=True)
    assert res["failed"] == 1
    assert [c.id for c in await _contents(ctx, batch)] == [old]


# --- profiles not in use refresh on demand ----------------------------------------------
async def _stale(ctx, profile, hours: float) -> None:  # noqa: F811
    async with ctx["sm"]() as s:
        p = await s.get(ListingProfile, profile)
        p.updated_at = p.images_updated_at = datetime.now(timezone.utc) - timedelta(hours=hours)
        await s.commit()


async def test_generating_with_a_stale_profile_refreshes_it_now(ctx, llm) -> None:  # noqa: F811
    batch, _, profile = await _group(ctx)
    await _stale(ctx, profile, 30)
    res = await _generate(ctx, batch, profile, replace=True)
    assert "being refreshed" in res["failures"][0]["error"]
    assert ("refresh_profile", (str(profile),)) in ctx["enqueuer"].calls
    assert llm.calls == []


async def test_choosing_a_profile_for_a_batch_refreshes_it_if_due(ctx) -> None:  # noqa: F811
    batch, _, profile = await _group(ctx)
    await _stale(ctx, profile, 6)  # images past 5 hours, structure fine
    resp = await ctx["client"].put(f"/api/batches/{batch}/groups", json={"group_key": "G1", "profile_id": str(profile)})
    assert resp.status_code == 200
    assert ctx["enqueuer"].calls == [("refresh_profile_images", (str(profile),))]


async def test_the_profiles_page_refreshes_what_it_shows(ctx) -> None:  # noqa: F811
    _, _, profile = await _group(ctx)
    await _stale(ctx, profile, 6)
    # Other pages list profiles too; only the Profiles page asks for a refresh.
    await ctx["client"].get("/api/profiles")
    assert ctx["enqueuer"].calls == []
    [row] = (await ctx["client"].get("/api/profiles?refresh=1")).json()
    assert row["refreshing"] is True and row["in_use"] is True  # it wrote a listing just now
    assert ctx["enqueuer"].calls == [("refresh_profile_images", (str(profile),))]


# --- a group already on Etsy: Replace images, scoped to that group ------------------------
async def test_replace_images_for_a_group_on_etsy_keeps_to_that_group(ctx) -> None:  # noqa: F811
    from app.db.models import Job, JobType

    batch, content_id, profile = await _group(ctx, approved=True, listing_id=777)
    async with ctx["sm"]() as s:
        (await s.get(ListingProfile, profile)).title_prefix = "Comfort Colors®"
        await s.commit()
        shop = (await s.get(GeneratedContent, content_id)).listing_profile_id
        shop = (await s.get(ListingProfile, shop)).connection_id
    resp = await ctx["client"].post(
        f"/api/shop/listings/777/replace-images?shop={shop}",
        json={"batch_id": str(batch), "group_key": "G1"},
    )
    assert resp.status_code == 200, resp.text
    async with ctx["sm"]() as s:
        job = (await s.execute(select(Job).where(Job.type == JobType.replace_images))).scalars().one()
    assert job.payload == {
        "listing_id": 777,
        "batch_id": str(batch),
        "group_key": "G1",
        "content_id": str(content_id),
        "title_prefix": "Comfort Colors®",
    }
