"""What needs the seller (api/attention.py): Overview's "Needs your attention" block and the
sidebar badges come from one count; each item links to where it is dealt with; another account
sees none of it."""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import (
    Asset,
    ListingProfile,
    AssetStatus,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    MockupPhoto,
    MockupTemplate,
    ProfileShopLink,
    UploadBatch,
    UploadBatchStatus,
)
from tests.test_multi_shop import world  # noqa: F401  (fixture)

NOW = datetime.now(timezone.utc)


async def test_counts_and_links_and_only_the_sellers_own(world) -> None:  # noqa: F811
    alice = world["alice"]
    async with world["sm"]() as s:
        t = MockupTemplate(tenant_id=alice, name="Tees")
        s.add(t)
        await s.flush()
        for name, confirmed, source in (("a", None, "auto"), ("b", None, "uncertain"), ("c", NOW, "auto")):
            s.add(MockupPhoto(tenant_id=alice, template_id=t.id, name=name, status="ready", rights_confirmed_at=NOW,
                              confirmed_at=confirmed, colour="light", colour_source=source))
        s.add(ProfileShopLink(tenant_id=alice, profile_id=world["pa1"], connection_id=world["a2"], status="incomplete"))
        b = UploadBatch(tenant_id=alice, status=UploadBatchStatus.ready, file_count=1, connection_id=world["a1"])
        s.add(b)
        await s.flush()
        a = Asset(tenant_id=alice, batch_id=b.id, original_filename="x.png", storage_key="k", status=AssetStatus.processed,
                  rank=1, group_key="X")
        s.add(a)
        await s.flush()
        c = GeneratedContent(tenant_id=alice, batch_id=b.id, asset_id=a.id, title="t", tags=["t"] * 13, description="d",
                             approved=True)
        s.add(c)
        await s.flush()
        s.add(Job(tenant_id=alice, connection_id=world["a1"], type=JobType.create_draft, status=JobStatus.failed,
                  payload={"content_id": str(c.id)}, batch_id=b.id, finished_at=NOW, last_error="Etsy refused it"))
        await s.commit()
    # The world's two listings are approved; mark one unapproved to have something to approve.
    async with world["sm"]() as s:
        first = await s.get(GeneratedContent, world["contents"][0])
        first.approved = False
        await s.commit()

    data = (await world["a"].get("/api/attention")).json()
    items = {i["key"]: (i["count"], i["href"], i["nav"]) for i in data["items"]}
    # Listed on Home, but no badge: checking mockup photos matters only when making mockups.
    assert items["photos"] == (2, "/templates", "")  # not confirmed yet
    assert items["colours"] == (1, "/templates", "")  # light or dark uncertain
    # A link still being prepared is not broken (setup is automatic): neither listed nor a badge.
    assert "setup" not in items and "profiles" not in items and "refused" not in items
    assert items["approve"] == (1, "/review", "/review")
    assert items["failed"] == (1, "/batches?status=failed", "/review")
    assert data["nav"] == {"/review": 2}  # Settings: nothing broken, so no badge
    assert data["total"] == sum(i["count"] for i in data["items"])
    assert all(i["count"] > 0 for i in data["items"])  # nothing to do: not listed
    # Bob: none of Alice's.
    theirs = (await world["b"].get("/api/attention")).json()
    assert theirs["items"] == [] and theirs["total"] == 0


async def test_settings_counts_only_what_is_broken(world) -> None:  # noqa: F811
    """Part C: the Settings badge is a profile that can no longer write, or a shop setting Etsy refused."""
    from app.workers.profiles import ACCESS_LOST, REFERENCE_GONE, TRANSIENT

    alice = world["alice"]
    async with world["sm"]() as s:
        p1 = await s.get(ListingProfile, world["pa1"])
        p1.refresh_error = TRANSIENT  # Etsy could not be reached: it tries again by itself
        await s.commit()
    data = (await world["a"].get("/api/attention")).json()
    assert "/settings" not in data["nav"]
    for error, expected in ((REFERENCE_GONE, 1), (ACCESS_LOST, 1)):
        async with world["sm"]() as s:
            (await s.get(ListingProfile, world["pa1"])).refresh_error = error
            await s.commit()
        data = (await world["a"].get("/api/attention")).json()
        assert data["nav"]["/settings"] == expected, error
        item = next(i for i in data["items"] if i["key"] == "profiles")
        assert item["href"] == "/profiles" and "can no longer write" in item["label"]
    async with world["sm"]() as s:
        (await s.get(ListingProfile, world["pa1"])).refresh_error = None
        s.add(ProfileShopLink(tenant_id=alice, profile_id=world["pa1"], connection_id=world["a2"], status="error"))
        await s.commit()
    data = (await world["a"].get("/api/attention")).json()
    assert data["nav"]["/settings"] == 1 and next(i for i in data["items"] if i["key"] == "refused")["count"] == 1
    theirs = (await world["b"].get("/api/attention")).json()
    assert "/settings" not in theirs["nav"]
