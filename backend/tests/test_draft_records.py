"""Half-made drafts as one record per design and shop (pipeline/unfinished.py,
pipeline/design_match.py, ops/draft_failures.py, api/unfinished.py, the "Finish draft"
guard in etsy/publisher.py).

* the listing id is found wherever it was kept: the draft attempt, the job's create
  snapshot (also after the batch was deleted), the path in Etsy's kept answer; else it
  says "never stored";
* a draft redone in another batch is replaced, matched by the group's name or the SKU,
  and the matching key is said;
* ``--check-etsy`` only reads (getListing, getListingsByShop), counts its calls, goes
  through the rate-limited client, links a draft found near a failure and lists the
  drafts nothing knows;
* the summary per action; duplicates merged;
* "Finish draft" never makes a second listing: a known listing is linked and carried on
  with, an existing one refuses with its id;
* another account sees and starts nothing.

Synthetic rows only; Etsy is faked; nothing is sent anywhere.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select

from app.db.models import (
    Asset,
    AssetStatus,
    DraftAttempt,
    DraftDismissal,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingPublication,
    ListingSnapshot,
    UploadBatch,
    UploadBatchStatus,
)
from app.etsy.errors import EtsyClientError
from app.ops import draft_failures
from app.pipeline import design_match, unfinished
from tests.test_multi_shop import BODY, PREFIX_A, world  # noqa: F401  (fixture)

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
INVENTORY_400 = "Etsy refused it while setting sizes, prices and SKU (it answered 400) without saying why"


def _title(tag: str) -> str:
    return f"{PREFIX_A}, {BODY} {tag}"


async def _batch(world, groups: list[tuple[str, str | None]], tenant: str = "alice") -> tuple[uuid.UUID, list[uuid.UUID]]:  # noqa: ANN001, F811
    """A batch with one design (listing group) per entry: (group key, cover SKU)."""
    async with world["sm"]() as s:
        tenant_id = world[tenant]
        profile = world["pa1"] if tenant == "alice" else world["pb1"]
        batch = UploadBatch(tenant_id=tenant_id, status=UploadBatchStatus.ready, file_count=len(groups))
        s.add(batch)
        await s.flush()
        ids = []
        for n, (group, sku) in enumerate(groups):
            asset = Asset(tenant_id=tenant_id, batch_id=batch.id, original_filename=f"{group}.png", storage_key=f"k-{group}-{n}",
                          status=AssetStatus.processed, rank=1, group_key=group, parsed_sku=sku)
            s.add(asset)
            await s.flush()
            content = GeneratedContent(tenant_id=tenant_id, batch_id=batch.id, asset_id=asset.id, listing_profile_id=profile,
                                       title=_title(group), tags=["shirt", *[f"tag{i}" for i in range(12)]],
                                       description="d", approved=True)
            s.add(content)
            await s.flush()
            ids.append(content.id)
        await s.commit()
        return batch.id, ids


async def _failed(world, content_id, shop, at: datetime, *, error: str = INVENTORY_400, snapshot: int | None = None,  # noqa: ANN001, F811
                  attempt: int | None = None, record: dict | None = None, title: str | None = None) -> uuid.UUID:
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, content_id)
        job = Job(tenant_id=content.tenant_id, connection_id=shop, type=JobType.create_draft, status=JobStatus.failed,
                  payload={"content_id": str(content_id), **({"etsy_error": record} if record else {})},
                  batch_id=content.batch_id, last_error=error, created_at=at, finished_at=at + timedelta(minutes=1))
        s.add(job)
        await s.flush()
        if snapshot is not None:
            s.add(ListingSnapshot(tenant_id=content.tenant_id, listing_id=snapshot, job_id=job.id, taken_at=at,
                                  payload={"operation": "create_draft", "submitted": {"title": title or content.title},
                                           "created": {"listing_id": snapshot}}))
        if attempt is not None:
            s.add(DraftAttempt(tenant_id=content.tenant_id, content_id=content_id, connection_id=shop,
                               etsy_listing_id=attempt, title=title or content.title, created_at=at))
        await s.commit()
        return job.id


async def _published(world, content_id, shop, listing: int, at: datetime, *, sku: str | None = None) -> None:  # noqa: ANN001, F811
    async with world["sm"]() as s:
        content = await s.get(GeneratedContent, content_id)
        s.add(ListingPublication(tenant_id=content.tenant_id, content_id=content_id, connection_id=shop,
                                 etsy_listing_id=listing, title=content.title, sku=sku, created_at=at))
        await s.commit()


async def _delete_batch(world, batch_id) -> None:  # noqa: ANN001, F811
    async with world["sm"]() as s:
        await s.delete(await s.get(UploadBatch, batch_id))  # contents and draft attempts go with it; jobs stay
        await s.commit()


async def _records(world, tenant: str = "alice") -> dict[Any, unfinished.DraftRecord]:  # noqa: ANN001, F811
    async with world["sm"]() as s:
        found = await unfinished.records(s, world[tenant])
    return {(r.etsy_listing_id or r.record_id): r for r in found}


# --- the listing id, wherever it was kept ---------------------------------------------------------


async def test_the_listing_id_is_found_wherever_it_was_kept_and_never_guessed(world) -> None:  # noqa: F811
    _, (c100, c101, c102, c103, c104) = await _batch(world, [("CDM100", None), ("CDM101", None), ("CDM102", None),
                                                             ("CDM103", None), ("CDM104", None)])
    shop = world["a1"]
    await _failed(world, c100, shop, T0, attempt=4596800100)
    await _failed(world, c101, shop, T0, snapshot=4596800101)  # its draft attempt is gone (30 days, say)
    await _failed(world, c102, shop, T0, record={"status": 400, "step": "setting sizes, prices and SKU", "method": "PUT",
                                                 "path": "/application/listings/4596800102/inventory", "message": "x", "sent": None})
    await _failed(world, c103, shop, T0)  # failed after create, but nothing kept the id
    await _failed(world, c104, shop, T0, error="Processing profile is not set up in Frost Tees; press Set up")
    # A design whose batch was deleted later: only its job and create snapshot are left.
    gone_batch, (c200,) = await _batch(world, [("CDM200", None)])
    await _failed(world, c200, shop, T0 + timedelta(days=5), snapshot=4596864790, attempt=4596864790)
    await _delete_batch(world, gone_batch)

    found = await _records(world)
    assert found[4596800100].id_source == "draft attempt"
    assert found[4596800101].id_source == "create snapshot"
    assert found[4596800102].id_source == "Etsy's answer"
    gone = found[4596864790]
    assert gone.id_source == "create snapshot" and gone.content_gone and gone.category == unfinished.BATCH_DELETED
    assert gone.batch_id == gone_batch  # the design is still named by its batch
    rest = [r for r in found.values() if r.etsy_listing_id is None]
    by_step = {r.step: r for r in rest}
    assert by_step["setting sizes, prices and SKU"].category == unfinished.UNCHECKED
    assert by_step[None].category == unfinished.NOT_ON_ETSY
    for r in found.values():
        if r.etsy_listing_id in (4596800100, 4596800101, 4596800102):
            assert r.category == unfinished.FINISH

    out = await draft_failures.report(world["sm"], "alice@example.com")
    assert "listing 4596864790 (id from create snapshot)" in out and "its batch was deleted" in out
    assert "listing 4596800102 (id from Etsy's answer)" in out
    assert "listing id: never stored" in out and "not created on Etsy (it stopped before the create request)" in out
    # The seller's list: every draft that exists on Etsy, the deleted batch's under "delete".
    listed = {i["etsy_listing_id"]: i for i in (await world["a"].get("/api/drafts/unfinished")).json()}
    assert set(listed) >= {4596800100, 4596800101, 4596800102, 4596864790}
    assert listed[4596864790]["section"] == "delete" and listed[4596864790]["can_dismiss"] is True
    assert listed[4596800101]["section"] == "finish" and listed[4596800101]["can_finish"] is True


# --- replaced across batches ------------------------------------------------------------------------


async def test_a_draft_redone_in_another_batch_is_replaced_by_group_or_sku_and_says_which(world) -> None:  # noqa: F811
    shop = world["a1"]
    _, (by_group, by_sku, other) = await _batch(world, [("CDM289", None), ("FROG-7", "BR5229"), ("CDM346", None)])
    await _failed(world, by_group, shop, T0, attempt=4500000289)
    await _failed(world, by_sku, shop, T0, attempt=4500000007)
    await _failed(world, other, shop, T0, attempt=4500000346)
    # Three days later, a new batch: the same groups' names (one with another file name, the same SKU).
    _, (again_group, again_sku) = await _batch(world, [("CDM289", None), ("FROG-7b", "br5229")])
    await _published(world, again_group, shop, 4600000289, T0 + timedelta(days=3))
    await _published(world, again_sku, shop, 4600000007, T0 + timedelta(days=3), sku="BR5229")

    found = await _records(world)
    assert found[4500000289].replaced_by == 4600000289 and found[4500000289].replaced_key == "group CDM289"
    assert found[4500000007].replaced_by == 4600000007 and found[4500000007].replaced_key == "SKU BR5229"
    assert found[4500000346].replaced_by is None and found[4500000346].category == unfinished.FINISH
    assert {found[i].category for i in (4500000289, 4500000007)} == {unfinished.REPLACED}
    out = await draft_failures.report(world["sm"], "alice@example.com")
    assert "superseded: yes, by listing 4600000289 (matched by group CDM289)" in out
    assert "superseded: yes, by listing 4600000007 (matched by SKU BR5229)" in out


def test_the_first_key_both_have_decides() -> None:
    batch = uuid.uuid4()
    a = design_match.keys(content=uuid.uuid4(), batch=batch, group="d0", title="Same Title")
    b = design_match.keys(content=uuid.uuid4(), batch=batch, group="d1", title="Same Title")
    assert design_match.same(a, b) is None  # two designs of one batch, one generic title: two designs
    c = design_match.keys(batch=uuid.uuid4(), group="CDM289", title="Another title")
    assert str(design_match.same(design_match.keys(batch=batch, group="cdm289"), c)) == "group CDM289"
    # SKUs differ: no match, even though the titles agree.
    assert design_match.same(design_match.keys(skus=["A1"], title="t"), design_match.keys(skus=["B2"], title="t")) is None
    # A folder named like a word with a count is not compared across batches.
    assert design_match.keys(group="design1").group is None and design_match.keys(group="New folder").group is None
    assert str(design_match.same(design_match.keys(title="Frog  Tee"), design_match.keys(title="frog tee"))) == "title"


async def test_duplicates_of_one_design_and_shop_are_one_record_and_an_older_draft_goes(world) -> None:  # noqa: F811
    shop = world["a2"]
    _, (old,) = await _batch(world, [("CDM375", None)])
    _, (new,) = await _batch(world, [("CDM375", None)])
    await _failed(world, old, shop, T0, attempt=4500000375)
    await _failed(world, old, shop, T0 + timedelta(hours=1), error="client_error:400")  # its retry
    await _failed(world, new, shop, T0 + timedelta(days=3), attempt=4500000376)
    found = await _records(world)
    assert found[4500000375].replaced_by == 4500000376 and "another draft of the same design" in found[4500000375].replaced_key
    assert found[4500000375].category == unfinished.REPLACED
    assert found[4500000376].replaced_by is None
    assert len([r for r in found.values() if r.connection_id == shop]) == 2  # the retry is not a third


# --- the summary ------------------------------------------------------------------------------------


async def test_the_summary_counts_each_action_with_its_listing_ids(world) -> None:  # noqa: F811
    shop = world["a1"]
    _, (c1, c2, c3, c4) = await _batch(world, [("CDM401", None), ("CDM402", None), ("CDM403", None), ("CDM404", None)])
    await _failed(world, c1, shop, T0, attempt=4700000001)                      # finish now
    await _failed(world, c2, shop, T0, attempt=4700000002,
                  error="Etsy refused it while setting the personalization question: x")
    async with world["sm"]() as s:                                             # the question breaks Etsy's rules now
        (await s.get(GeneratedContent, c2)).personalization = {"enabled": True, "question_text": "- Name"}
        await s.commit()
    await _failed(world, c3, shop, T0, attempt=4700000003)
    _, (c3b,) = await _batch(world, [("CDM403", None)])
    await _published(world, c3b, shop, 4700000033, T0 + timedelta(days=1))     # replaced
    await _failed(world, c4, shop, T0, snapshot=4700000004)
    await _published(world, c4, shop, 4700000004, T0 + timedelta(hours=2))     # finished since
    out = await draft_failures.report(world["sm"], "alice@example.com")
    head = out.split("\n\n")[1]
    assert "Finish now: 1 — Frost Tees 4700000001" in head
    assert "Fix personalization, then finish: 1 — Frost Tees 4700000002" in head
    assert "Replaced: delete on Etsy: 1 — Frost Tees 4700000003" in head
    assert "Nothing to do: 1 — Frost Tees 4700000004" in head
    assert "start with a letter or a number" in out
    listed = {i["etsy_listing_id"]: i for i in (await world["a"].get("/api/drafts/unfinished")).json()}
    assert listed[4700000002]["section"] == "fix" and listed[4700000002]["can_finish"] is False
    assert 4700000004 not in listed  # nothing to do: not on the seller's list


# --- --check-etsy: read only, counted, rate limited -------------------------------------------------


class ReadOnlyEtsy:
    """Only the two reads exist: any other call would raise AttributeError."""

    def __init__(self, listings: dict[int, dict], drafts: list[dict]) -> None:
        self.listings, self.drafts, self.calls = listings, drafts, []

    async def get_listing(self, listing_id: int, **kw: Any) -> dict:
        assert kw["access_token"] == "tok"
        self.calls.append(("get_listing", listing_id))
        if listing_id not in self.listings:
            raise EtsyClientError(404, body='{"error": "not found"}')
        return self.listings[listing_id]

    async def get_listings_by_shop(self, shop_id: int, *, state: str, limit: int, offset: int = 0, **kw: Any) -> dict:
        assert state == "draft" and limit == 100
        self.calls.append(("get_listings_by_shop", offset))
        return {"results": self.drafts[offset:offset + limit]}


class Tokens:
    async def get_valid_access_token(self, session, shop) -> str:  # noqa: ANN001
        return "tok"


async def _keyed(world, shop) -> None:  # noqa: ANN001, F811
    """The shop has an (encrypted, here made-up) Etsy key stored, as a connected shop does."""
    async with world["sm"]() as s:
        (await s.get(EtsyConnection, shop)).access_token_enc = b"made-up-ciphertext"
        await s.commit()


async def test_check_etsy_reads_only_counts_its_calls_and_links_a_draft_found_near_a_failure(world) -> None:  # noqa: F811
    shop = world["a1"]
    _, (known, gone, lost, stray) = await _batch(world, [("CDM501", None), ("CDM502", None), ("CDM503", None), ("CDM504", None)])
    await _failed(world, known, shop, T0, attempt=4800000001)
    await _failed(world, gone, shop, T0, attempt=4800000002)
    await _failed(world, lost, shop, T0)  # failed at the inventory with no id kept
    await _keyed(world, shop)
    ts = int((T0 + timedelta(minutes=2)).timestamp())
    fake = ReadOnlyEtsy(
        listings={4800000001: {"listing_id": 4800000001, "state": "draft"}},
        drafts=[
            {"listing_id": 4800000003, "title": _title("CDM503"), "skus": [], "state": "draft", "created_timestamp": ts},
            {"listing_id": 4800000009, "title": "Something else", "skus": ["ZZ9"], "state": "draft", "created_timestamp": ts},
            {"listing_id": 4800000001, "title": _title("CDM501"), "state": "draft", "created_timestamp": ts},  # known
        ],
    )
    made: list = []

    def factory(shop_id, http):  # noqa: ANN001, ANN202
        made.append(shop_id)
        return fake

    out = await draft_failures.report(world["sm"], "alice@example.com", check_etsy=True, client_factory=factory,
                                      token_service=Tokens())
    assert made == [shop]
    assert ("get_listing", 4800000001) in fake.calls and ("get_listing", 4800000002) in fake.calls
    assert [c for c in fake.calls if c[0] == "get_listings_by_shop"] == [("get_listings_by_shop", 0)]  # one page was enough
    assert f"Etsy calls made: {len(fake.calls)} (read only" in out
    assert "on Etsy: draft" in out and "on Etsy: gone" in out
    assert "listing 4800000003 (id from found on Etsy (matched by title))" in out
    assert "On Etsy, not linked: 1 — Frost Tees 4800000009" in out and "SKU ZZ9" in out
    assert "4800000001 · created" not in out  # a draft the app knows is never "not linked"
    async with world["sm"]() as s:  # nothing was written: the found draft is reported, not linked in the database
        assert await s.scalar(select(DraftAttempt).where(DraftAttempt.etsy_listing_id == 4800000003)) is None
    del stray


def test_the_checks_client_goes_through_the_rate_limiter_and_the_daily_budget(test_settings) -> None:  # noqa: ANN001
    from app.cli import _client_for_check

    client = _client_for_check(uuid.uuid4(), http=None)
    assert client._bucket is not None and client._quota is not None and client._upkeep is True


async def test_check_etsy_stops_at_the_daily_budget_and_reports_it(world) -> None:  # noqa: F811
    from app.etsy.api import RateLimitExceeded

    _, (c,) = await _batch(world, [("CDM601", None)])
    await _failed(world, c, world["a1"], T0, attempt=4900000001)
    await _keyed(world, world["a1"])

    class Spent:
        async def get_listing(self, *_a, **_k):
            raise RateLimitExceeded("budget")

    out = await draft_failures.report(world["sm"], "alice@example.com", check_etsy=True,
                                      client_factory=lambda *_: Spent(), token_service=Tokens())
    assert "daily Etsy budget is used up" in out


async def test_check_etsy_skips_a_shop_without_a_stored_key_and_says_so(world) -> None:  # noqa: F811
    _, (c,) = await _batch(world, [("CDM602", None)])
    await _failed(world, c, world["a1"], T0, attempt=4900000002)
    calls: list = []
    out = await draft_failures.report(world["sm"], "alice@example.com", check_etsy=True,
                                      client_factory=lambda *a: calls.append(a), token_service=Tokens())
    assert calls == [] and "no Etsy key is stored for this shop" in out and "Etsy calls made: 0" in out


# --- "Finish draft" never makes a second listing ---------------------------------------------------


async def test_finishing_a_draft_known_only_from_its_snapshot_links_that_listing_first(world) -> None:  # noqa: F811
    _, (c,) = await _batch(world, [("CDM701", None)])
    await _failed(world, c, world["a1"], T0, snapshot=4596846505)
    [item] = [i for i in (await world["a"].get("/api/drafts/unfinished")).json() if i["etsy_listing_id"] == 4596846505]
    assert item["attempt_id"] is None and item["record_id"].startswith("job-")
    assert (await world["b"].post("/api/drafts/unfinished/finish", json={"record_ids": [item["record_id"]]})).json()["started"] == 0
    res = (await world["a"].post("/api/drafts/unfinished/finish", json={"record_ids": [item["record_id"]]})).json()
    assert res["started"] == 1
    async with world["sm"]() as s:
        attempt = await s.scalar(select(DraftAttempt).where(DraftAttempt.content_id == c))
        assert attempt.etsy_listing_id == 4596846505  # the job carries on with it
        job = (await s.execute(select(Job).where(Job.status == JobStatus.queued))).scalars().one()
        assert job.payload["finish"] is True


class GuardEtsy:
    """The publisher's fake, with the reads the guard makes."""

    def __new__(cls, *, drafts=(), live=(), **kw):  # noqa: ANN001, ANN204
        from tests.test_publisher import FakeEtsy

        class _Fake(FakeEtsy):
            async def get_listings_by_shop(self, shop_id, *, state, limit, offset=0, **_):  # noqa: ANN001
                self.calls.append(f"drafts:{offset}")
                return {"results": list(drafts)[offset:offset + limit]}

            async def find_active_listings_by_shop(self, shop_id, *, keywords, limit=25, **_):  # noqa: ANN001
                self.calls.append(f"live:{keywords}")
                return {"results": list(live)}

        return _Fake(**kw)


async def _finish(async_sm, fake, *, sku="BR5475"):  # noqa: ANN001, ANN202
    from app.etsy.publisher import PublishImage, publish_content
    from tests.test_publisher import CONFIG, REFERENCE, _seed

    _, conn_id, content_id, job_id = await _seed(async_sm)
    async with async_sm() as s:
        try:
            return await publish_content(
                s, job_id=job_id, content=await s.get(GeneratedContent, content_id),
                connection=await s.get(EtsyConnection, conn_id), sku=sku, thumbnail=PublishImage(b"t", "t.jpg"),
                extra_images=[], client=fake, access_token="tok", config=CONFIG, reference=REFERENCE, theme="",
                tenant_limit=2000, finishing=True,
            ), conn_id, content_id
        except Exception as exc:  # noqa: BLE001 - the test looks at what was refused
            return exc, conn_id, content_id


async def test_finishing_carries_on_with_a_draft_of_the_design_found_on_etsy_never_a_new_one(async_sm) -> None:
    from tests.test_publisher import REFERENCE, VALID_TITLE

    fake = GuardEtsy(
        drafts=[{"listing_id": 777, "title": VALID_TITLE, "skus": [], "state": "draft"}],
        readback_override={"taxonomy_id": REFERENCE["taxonomy_id"], "who_made": REFERENCE["who_made"],
                           "when_made": REFERENCE["when_made"], "state": "draft", "title": VALID_TITLE,
                           "production_partners": [{"production_partner_id": 7, "partner_name": "Print Co"}]},
    )
    result, conn_id, content_id = await _finish(async_sm, fake)
    # The guard found the shop's draft of this design and the draft carried on with it.
    assert fake.calls[:3] == ["shop", "drafts:0", "get_listing"] and "create" not in fake.calls
    assert getattr(result, "listing_id", None) == 777, result


async def test_finishing_refuses_when_the_design_is_live_and_names_the_listing(async_sm) -> None:
    from app.etsy.publisher import FinishRefused

    fake = GuardEtsy(live=[{"listing_id": 4600009999, "title": "Other words", "skus": ["BR5475"], "state": "active"}])
    result, _, _ = await _finish(async_sm, fake)
    assert isinstance(result, FinishRefused) and "4600009999" in str(result) and "SKU BR5475" in str(result)
    assert "create" not in fake.calls


async def test_finishing_refuses_when_the_app_already_drafted_this_design_in_the_shop(async_sm) -> None:
    from app.etsy.publisher import FinishRefused
    from tests.test_publisher import _seed

    fake = GuardEtsy()
    # The app's finished draft of the same design (another listing text, the same SKU) in this shop.
    tenant_id, conn_id, content_id, _ = await _seed(async_sm)
    async with async_sm() as s:
        content = await s.get(GeneratedContent, content_id)
        s.add(ListingPublication(tenant_id=tenant_id, content_id=None, connection_id=conn_id, etsy_listing_id=4600001234,
                                 title="An older text", sku="BR5475"))
        await s.commit()
        from app.etsy.publisher import PublishImage, publish_content
        from tests.test_publisher import CONFIG, REFERENCE

        with pytest.raises(FinishRefused, match="already has listing 4600001234 for this design"):
            await publish_content(
                s, job_id=uuid.uuid4(), content=content, connection=await s.get(EtsyConnection, conn_id), sku="BR5475",
                thumbnail=PublishImage(b"t", "t.jpg"), extra_images=[], client=fake, access_token="tok", config=CONFIG,
                reference=REFERENCE, theme="", tenant_limit=2000, finishing=True,
            )
    assert "create" not in fake.calls


async def test_finishing_a_draft_published_by_hand_refuses_instead_of_making_another(async_sm) -> None:
    from app.etsy.publisher import FinishRefused
    from tests.test_publisher import _seed

    fake = GuardEtsy(readback_override={"state": "active"})
    tenant_id, conn_id, content_id, job_id = await _seed(async_sm)
    async with async_sm() as s:
        s.add(DraftAttempt(tenant_id=tenant_id, content_id=content_id, connection_id=conn_id, etsy_listing_id=4596864790))
        await s.commit()
        from app.etsy.publisher import PublishImage, publish_content
        from tests.test_publisher import CONFIG, REFERENCE

        with pytest.raises(FinishRefused, match="4596864790 is already active"):
            await publish_content(
                s, job_id=job_id, content=await s.get(GeneratedContent, content_id), connection=await s.get(EtsyConnection, conn_id),
                sku="BR5475", thumbnail=PublishImage(b"t", "t.jpg"), extra_images=[], client=fake, access_token="tok",
                config=CONFIG, reference=REFERENCE, theme="", tenant_limit=2000, finishing=True,
            )
    assert "create" not in fake.calls


# --- dismissing, and another account -----------------------------------------------------------------


async def test_a_dismissed_draft_stays_off_the_list_even_from_its_snapshot_and_nothing_goes_to_etsy(world) -> None:  # noqa: F811
    gone_batch, (c,) = await _batch(world, [("CDM801", None)])
    await _failed(world, c, world["a2"], T0, snapshot=4596846491, attempt=4596846491)
    await _delete_batch(world, gone_batch)
    [item] = [i for i in (await world["a"].get("/api/drafts/unfinished")).json() if i["etsy_listing_id"] == 4596846491]
    assert item["category"] == unfinished.BATCH_DELETED
    assert (await world["b"].post(f"/api/drafts/unfinished/{item['record_id']}/dismiss")).status_code == 404
    assert (await world["a"].post(f"/api/drafts/unfinished/{item['record_id']}/dismiss")).status_code == 204
    assert all(i["etsy_listing_id"] != 4596846491 for i in (await world["a"].get("/api/drafts/unfinished")).json())
    async with world["sm"]() as s:
        assert await s.scalar(select(DraftDismissal.etsy_listing_id)) == 4596846491
        assert await s.scalar(select(ListingSnapshot.listing_id)) == 4596846491  # the app's own records are kept
    assert world["queue"].calls == []


async def test_another_account_sees_reads_and_starts_nothing(world) -> None:  # noqa: F811
    _, (c,) = await _batch(world, [("CDM901", None)])
    await _failed(world, c, world["a1"], T0, attempt=4999000001)
    _, (theirs,) = await _batch(world, [("CDM901", None)], tenant="bob")
    await _published(world, theirs, world["b1"], 4999000002, T0 + timedelta(days=1))  # bob's: never replaces alice's
    found = await _records(world)
    assert found[4999000001].replaced_by is None
    assert (await world["b"].get("/api/drafts/unfinished")).json() == []
    assert (await world["b"].post("/api/drafts/unfinished/finish", json={})).json()["started"] == 0
    bob = await draft_failures.report(world["sm"], "bob@example.com")
    assert "4999000001" not in bob and "None." in bob
    calls: list = []
    await draft_failures.report(world["sm"], "bob@example.com", check_etsy=True,
                                client_factory=lambda *a: calls.append(a), token_service=Tokens())
    assert calls == []  # no record of bob's: no shop of alice's is read
