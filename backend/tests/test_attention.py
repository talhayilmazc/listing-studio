"""What needs the seller (api/attention.py): Overview's "Needs your attention" block and the
sidebar badges come from one count; each item links to where it is dealt with; another account
sees none of it."""

from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import (
    Asset,
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
    assert items["photos"] == (2, "/templates", "/templates")  # not confirmed yet
    assert items["colours"] == (1, "/templates", "/templates")  # light or dark uncertain
    assert items["setup"] == (1, "/profiles", "/profiles")
    assert items["approve"] == (1, "/batches?status=in_review", "/batches")
    assert items["failed"] == (1, "/batches?status=failed", "/batches")
    assert data["nav"]["/templates"] == 3 and data["nav"]["/batches"] == 2
    assert data["total"] == sum(i["count"] for i in data["items"])
    assert all(i["count"] > 0 for i in data["items"])  # nothing to do: not listed
    # Bob: none of Alice's.
    theirs = (await world["b"].get("/api/attention")).json()
    assert theirs["items"] == [] and theirs["total"] == 0
