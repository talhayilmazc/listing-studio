"""A profile made in one shop, drafted in another: only that shop's own ids reach Etsy.

The production fault: content written with a profile from teetimecustomapparel (TTC)
was drafted into BlueCustomTeeShirt (BCT). The draft was created, then every inventory
update failed: ``Could not find readiness_state_id='1402915464979' associated with
shop '54783739'``. Etsy keeps a processing profile (readiness state) on every
offering, and the inventory sent to BCT carried TTC's, copied from the reference
listing's own inventory.

* Drafted in BCT, every offering carries BCT's processing profile, and so does the
  listing; no TTC id (shipping, return, processing, image, offering, product) is sent.
  In TTC itself each offering keeps its own.
* A processing profile BCT does not have stops the draft before any Etsy call, in
  the worker and in the publisher, saying "Processing profile is not set up in BCT".
* A draft whose inventory update failed is finished on the next try with the same
  listing id: never a second listing ("Finish these drafts").
* Every call carries the token of the shop it is for.
* The seller reads Etsy's refusal in plain words; the operator's check is read only.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import event, select

from app.db.models import (
    DraftAttempt,
    EtsyConnection,
    GeneratedContent,
    Job,
    JobStatus,
    JobType,
    ListingProfile,
    ListingPublication,
    ProfileShopLink,
)
from app.etsy.errors import EtsyClientError, explain
from app.etsy.publisher import PublishImage, ShopNotSetUp, publish_content
from app.pipeline import links as L
from app.pipeline.reference import PAYLOAD_VERSION
from app.workers import publish as publish_worker
from app.workers import recovery
from tests.test_multi_shop import world  # noqa: F401  (fixture)
from tests.test_publish_recovery import Shop
from tests.test_publisher import CONFIG

TTC_READINESS = 1402915464979  # the id Etsy refused in BCT
BCT_READINESS = 3003
REFUSAL = json.dumps({"error": f"Could not find readiness_state_id='{TTC_READINESS}' associated with shop '54783739'"})

PAYLOAD: dict[str, Any] = {
    "payload_version": PAYLOAD_VERSION,
    "taxonomy_id": 482,
    "price": 24.0,
    "who_made": "i_did",
    "when_made": "made_to_order",
    "shipping_profile_id": 910001,
    "return_policy_id": 910002,
    "readiness_state_id": TTC_READINESS,
    "production_partner_ids": [],
    "price_on_property": [200],
    "description": "Reference title\n\nSizes and care.",
    "images": [{"listing_image_id": 910901, "rank": 1, "url": "https://img.example/901.jpg"}],
    "attributes": [],
    "inventory_products": [
        {
            "product_id": 910071, "sku": "TTC-S",
            "property_values": [{"property_id": 200, "property_name": "Size", "value_ids": [1], "values": ["S"]}],
            "offerings": [{"offering_id": 910081, "price": {"amount": 2400, "divisor": 100}, "quantity": 9,
                           "is_enabled": True, "readiness_state_id": TTC_READINESS}],
        },
        {
            "product_id": 910072, "sku": "TTC-M",
            "property_values": [{"property_id": 200, "property_name": "Size", "value_ids": [2], "values": ["M"]}],
            "offerings": [{"offering_id": 910082, "price": {"amount": 2600, "divisor": 100}, "quantity": 9,
                           "is_enabled": True, "readiness_state_id": TTC_READINESS}],
        },
    ],
}


class Recording(Shop):
    """Etsy, remembering every call with the token it came with, and what was sent."""

    def __init__(self, *, refuse_inventory: int = 0) -> None:
        super().__init__()
        self.tokens: list[tuple[str, str | None]] = []
        self.sent: list[Any] = []
        self.refuse_inventory = refuse_inventory
        self.inventories: list[dict[str, Any]] = []

    def __getattribute__(self, name: str) -> Any:
        attr = super().__getattribute__(name)
        if name.startswith("_") or not callable(attr) or name in ("tokens", "sent"):
            return attr
        tokens, sent = super().__getattribute__("tokens"), super().__getattribute__("sent")

        async def record(*args: Any, **kwargs: Any) -> Any:
            tokens.append((name, kwargs.get("access_token")))
            sent.append({k: v for k, v in kwargs.items() if k not in ("image_bytes",)})
            return await attr(*args, **kwargs)

        import inspect

        return record if inspect.iscoroutinefunction(attr) else attr

    async def update_listing_inventory(self, listing_id: int, *, inventory: dict[str, Any], **kw: Any):
        self.inventories.append(inventory)
        if self.refuse_inventory:
            self.refuse_inventory -= 1
            raise EtsyClientError(404, body=REFUSAL, path=f"/v3/application/listings/{listing_id}/inventory", method="PUT")
        return await super().update_listing_inventory(listing_id, inventory=inventory, **kw)


async def _setup(world, *, bct_readiness: int | None = BCT_READINESS) -> dict[str, Any]:  # noqa: F811
    """Alice's two shops as TTC (the profile's main shop) and BCT (linked)."""
    async with world["sm"]() as s:
        ttc, bct = await s.get(EtsyConnection, world["a1"]), await s.get(EtsyConnection, world["a2"])
        ttc.shop_name, bct.shop_name, bct.shop_id = "teetimecustomapparel", "BlueCustomTeeShirt", 54783739
        profile = await s.get(ListingProfile, world["pa1"])
        profile.cached_payload = dict(PAYLOAD)
        profile.updated_at = datetime.now(timezone.utc)
        s.add(ProfileShopLink(tenant_id=world["alice"], profile_id=profile.id, connection_id=ttc.id, status="ready"))
        link = ProfileShopLink(tenant_id=world["alice"], profile_id=profile.id, connection_id=bct.id,
                               shipping_profile_id=2001, return_policy_id=2002, readiness_state_id=bct_readiness,
                               production_partner_ids=[], status="ready" if bct_readiness else "incomplete",
                               notes=None if bct_readiness else {"readiness_state": L.NOT_FOUND})
        s.add(link)
        await s.commit()
        return {"link": link, "ttc": ttc.id, "bct": bct.id, "content": world["contents"][0]}


async def _draft(world, fake: Recording, shop_id: uuid.UUID, reference: dict[str, Any], token: str):  # noqa: F811
    async with world["sm"]() as s:
        job = Job(tenant_id=world["alice"], connection_id=shop_id, type=JobType.create_draft,  # bookkeeping only
                  payload={"content_id": str(world["contents"][0])}, status=JobStatus.succeeded)
        s.add(job)
        await s.flush()
        return await publish_content(
            s, job_id=job.id, content=await s.get(GeneratedContent, world["contents"][0]),
            connection=await s.get(EtsyConnection, shop_id), sku="BR5475", thumbnail=PublishImage(b"t", "t.jpg"),
            client=fake, access_token=token, config=CONFIG, reference=reference, tenant_limit=2000,
        )


def _all_ids(sent: list[Any]) -> str:
    return json.dumps(sent, default=str)


async def test_a_ttc_profile_drafted_in_bct_sends_bcts_processing_profile_on_every_offering(world) -> None:  # noqa: F811
    ids = await _setup(world)
    reference = L.shop_reference(PAYLOAD, ids["link"])
    # Every shop-scoped id of TTC's reference is BCT's own, or gone.
    assert (reference["shipping_profile_id"], reference["return_policy_id"], reference["readiness_state_id"]) == (2001, 2002, BCT_READINESS)
    assert all(o["readiness_state_id"] == BCT_READINESS for p in reference["inventory_products"] for o in p["offerings"])
    assert not any("product_id" in p or any("offering_id" in o for o in p["offerings"]) for p in reference["inventory_products"])
    assert "images" not in reference and reference["shop_missing"] == []

    fake = Recording()
    await _draft(world, fake, ids["bct"], reference, token="bct-token")
    assert fake.last_listing["readiness_state_id"] == BCT_READINESS and fake.last_listing["shipping_profile_id"] == 2001
    [inventory] = fake.inventories
    assert [o["readiness_state_id"] for p in inventory["products"] for o in p["offerings"]] == [BCT_READINESS, BCT_READINESS]
    # No TTC id went to BCT in any call: not the processing profile, shipping, return, image, product or offering.
    sent = _all_ids(fake.sent)
    for ttc_id in (TTC_READINESS, 910001, 910002, 910901, 910071, 910072, 910081, 910082):
        assert f"{ttc_id}" not in sent, ttc_id
    # Every call carried BCT's own token.
    assert fake.tokens and {t for _, t in fake.tokens} == {"bct-token"}, fake.tokens

    # In TTC itself (the profile's main shop) each offering keeps its own processing profile.
    async with world["sm"]() as s:
        await s.execute(ListingPublication.__table__.delete())
        await s.commit()
    own = Recording()
    await _draft(world, own, ids["ttc"], L.shop_reference(PAYLOAD, None), token="ttc-token")
    assert [o["readiness_state_id"] for p in own.inventories[0]["products"] for o in p["offerings"]] == [TTC_READINESS] * 2
    assert {t for _, t in own.tokens} == {"ttc-token"}


async def test_a_processing_profile_set_only_on_offerings_is_mapped_too(world) -> None:  # noqa: F811
    ids = await _setup(world)
    only_offerings = {**PAYLOAD, "readiness_state_id": None}
    assert L.wanted(only_offerings, L.READINESS) == TTC_READINESS
    reference = L.shop_reference(only_offerings, ids["link"])
    assert reference["readiness_state_id"] == BCT_READINESS
    assert {o["readiness_state_id"] for p in reference["inventory_products"] for o in p["offerings"]} == {BCT_READINESS}


async def test_a_missing_processing_profile_blocks_before_any_etsy_call(world, monkeypatch) -> None:  # noqa: F811
    ids = await _setup(world, bct_readiness=None)
    reference = L.shop_reference(PAYLOAD, ids["link"])
    assert reference["shop_missing"] == [L.READINESS]
    fake = Recording()
    with pytest.raises(ShopNotSetUp, match="Processing profile is not set up in BlueCustomTeeShirt"):
        await _draft(world, fake, ids["bct"], reference, token="bct-token")
    assert fake.tokens == []  # nothing was sent to Etsy

    # The worker stops it too, before reading a token or making a client.
    def no_client(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("an Etsy client was made")

    async def no_token(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("a token was read")

    async def no_gate(ctx, session, job, function):  # noqa: ANN001
        return None

    monkeypatch.setattr(publish_worker, "EtsyApiClient", no_client)
    monkeypatch.setattr(publish_worker.ConnectionService, "get_valid_access_token", no_token)
    monkeypatch.setattr(publish_worker, "start_job", no_gate)
    monkeypatch.setattr(publish_worker, "get_cipher", lambda: None)
    async with world["sm"]() as s:
        job = Job(tenant_id=world["alice"], connection_id=ids["bct"], type=JobType.create_draft,
                  payload={"content_id": str(ids["content"])}, status=JobStatus.running)
        s.add(job)
        await s.commit()
        job_id = job.id
    class Pool:
        def __init__(self) -> None:
            self.jobs: list[tuple] = []

        async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> None:
            self.jobs.append((function, args))

    pool = Pool()
    # A link the old rules left at "no match": set up again by itself (its processing profile is
    # matched or copied), and the draft waits for it, without a try spent and with nothing sent.
    assert await publish_worker._run_publish_job({"sessionmaker": world["sm"], "redis": pool}, str(job_id)) == "deferred"
    async with world["sm"]() as s:
        job = await s.get(Job, job_id)
        assert (job.status, job.paused_reason) == (JobStatus.queued, recovery.WAIT_SETUP)
        assert (await s.execute(select(DraftAttempt))).scalars().all() == []  # no draft was started
        link = await s.get(ProfileShopLink, ids["link"].id)
        assert link.status == "checking"
        # Etsy refused the copy: the draft for BCT fails with that one line, before any Etsy call.
        link.status, link.notes = "incomplete", {"readiness_state": 'Etsy refused the copy of "Ready": "Nope". Create it '
                                                                   "in BlueCustomTeeShirt's Shop Manager, then press Refresh."}
        refused = Job(tenant_id=world["alice"], connection_id=ids["bct"], type=JobType.create_draft,
                      payload={"content_id": str(ids["content"])}, status=JobStatus.running)
        s.add(refused)
        await s.commit()
        refused_id = refused.id
    assert ("link_profile", (str(world["pa1"]),)) in pool.jobs
    assert await publish_worker._run_publish_job({"sessionmaker": world["sm"], "redis": pool}, str(refused_id)) == "failed"
    async with world["sm"]() as s:
        assert (await s.get(Job, refused_id)).last_error.startswith(
            "BlueCustomTeeShirt: Processing profile: Etsy refused the copy")

    # On the review page the refusal is one line for the shop, not a block, and the cell can still be ticked.
    preview = (await world["a"].post(f"/api/batches/{world['batch']}/publish/preview",
                                     json={"pairs": [{"content_id": str(ids["content"]), "connection_id": str(ids["bct"])}]})).json()
    column = next(c for c in preview["columns"] if c["connection_id"] == str(ids["bct"]))
    need = next(n for n in column["needs"] if n["kind"] == "settings")
    assert need["message"].startswith("BlueCustomTeeShirt: Processing profile: Etsy refused") and not need["blocking"]


async def test_a_retry_after_a_failed_inventory_update_finishes_the_same_listing(world) -> None:  # noqa: F811
    ids = await _setup(world)
    reference = L.shop_reference(PAYLOAD, ids["link"])
    fake = Recording(refuse_inventory=1)  # the first try fails as production did
    with pytest.raises(EtsyClientError):
        await _draft(world, fake, ids["bct"], reference, token="bct-token")
    async with world["sm"]() as s:
        [attempt] = (await s.execute(select(DraftAttempt))).scalars().all()
        listing_id = attempt.etsy_listing_id
        assert listing_id is not None
        # The job fails with Etsy's reason in plain words, naming the shop.
        job = Job(tenant_id=world["alice"], connection_id=ids["bct"], type=JobType.create_draft,
                  payload={"content_id": str(ids["content"])}, status=JobStatus.running)
        s.add(job)
        await s.commit()
        refused = EtsyClientError(404, body=REFUSAL, path=f"/v3/application/listings/{listing_id}/inventory", method="PUT")
        assert await recovery.after_failure({}, s, job.id, refused, "run_publish_job") == "failed"
        await s.refresh(job)
        assert job.last_error.startswith(
            "BlueCustomTeeShirt does not have the processing profile this listing used; set it up in BlueCustomTeeShirt")

    # Listed for the seller as a half-made draft, with what went wrong.
    listed = (await world["a"].get("/api/drafts/unfinished")).json()
    assert [(d["etsy_listing_id"], d["shop_name"], d["step"]) for d in listed] == [
        (listing_id, "BlueCustomTeeShirt", "setting sizes, prices and SKU")]
    assert "does not have the processing profile" in listed[0]["error"] and listed[0]["blocked"] is None
    assert (await world["b"].get("/api/drafts/unfinished")).json() == []  # another account sees none
    assert (await world["b"].post("/api/drafts/unfinished/finish", json={"attempt_ids": [listed[0]["attempt_id"]]})).json()["started"] == 0

    # "Finish these drafts" queues the same draft job; it carries on with the listing.
    res = await world["a"].post("/api/drafts/unfinished/finish", json={"attempt_ids": [listed[0]["attempt_id"]]})
    assert res.json() == {"started": 1, "skipped": []}
    assert world["queue"].calls[-1][0] == "run_publish_job"
    creates_before = fake.calls.count("create")
    result = await _draft(world, fake, ids["bct"], reference, token="bct-token")
    assert result.listing_id == listing_id and fake.calls.count("create") == creates_before == 1  # never a second listing
    assert [o["readiness_state_id"] for p in fake.inventories[-1]["products"] for o in p["offerings"]] == [BCT_READINESS] * 2
    assert {t for _, t in fake.tokens} == {"bct-token"}
    async with world["sm"]() as s:
        [publication] = (await s.execute(select(ListingPublication))).scalars().all()
        assert publication.etsy_listing_id == listing_id and publication.connection_id == ids["bct"]
        assert (await s.execute(select(DraftAttempt))).scalars().all() == []
    assert (await world["a"].get("/api/drafts/unfinished")).json() == []


async def test_a_half_made_draft_in_a_shop_still_missing_its_processing_profile_is_not_started(world) -> None:  # noqa: F811
    ids = await _setup(world, bct_readiness=None)
    async with world["sm"]() as s:
        s.add(DraftAttempt(tenant_id=world["alice"], content_id=ids["content"], connection_id=ids["bct"],
                           etsy_listing_id=4242, title="T"))
        await s.commit()
    [item] = (await world["a"].get("/api/drafts/unfinished")).json()
    assert item["blocked"] == "Processing profile is not set up in BlueCustomTeeShirt" and item["setup"]
    res = (await world["a"].post("/api/drafts/unfinished/finish", json={})).json()
    assert res["started"] == 0 and res["skipped"][0]["reason"].startswith("Processing profile is not set up")


def test_etsys_refusal_of_another_shops_id_is_said_in_plain_words() -> None:
    refused = EtsyClientError(404, body=REFUSAL, path="/v3/application/listings/9/inventory", method="PUT")
    assert explain(refused, "BlueCustomTeeShirt") == (
        "BlueCustomTeeShirt does not have the processing profile this listing used; "
        "set it up in BlueCustomTeeShirt, then finish the draft (Etsy refused it while setting sizes, prices and SKU)")
    shipping = EtsyClientError(400, body='{"error": "Could not find shipping_profile_id=12 associated with shop 5"}',
                               path="/v3/application/shops/5/listings", method="POST")
    assert explain(shipping, "BCT").startswith("BCT does not have the shipping profile")
    # A listing that is really gone still says so.
    gone = EtsyClientError(404, body='{"error": "Listing not found"}', path="/v3/application/listings/9", method="GET")
    assert "could not find the listing" in explain(gone)


async def test_the_operators_check_reads_only_and_says_what_each_shop_has(world) -> None:  # noqa: F811
    from app.ops.profile_shop_check import report

    ids = await _setup(world, bct_readiness=None)
    async with world["sm"]() as s:
        s.add(DraftAttempt(tenant_id=world["alice"], content_id=ids["content"], connection_id=ids["bct"],
                           etsy_listing_id=4242, title="Frog tee"))
        await s.commit()
    statements: list[str] = []
    engine = world["sm"].kw["bind"].sync_engine

    def seen(conn, cursor, statement, *args):  # noqa: ANN001
        statements.append(statement.strip().split()[0].upper())

    event.listen(engine, "before_cursor_execute", seen)
    try:
        out = await report(world["sm"], "alice@example.com")
    finally:
        event.remove(engine, "before_cursor_execute", seen)
    assert set(statements) <= {"SELECT"}, set(statements)  # only SELECTs
    assert "BlueCustomTeeShirt (Etsy shop 54783739, active): token" in out
    assert "processing profile: missing" in out and "processing profile: 2002" not in out
    assert f"processing profile on the inventory's offerings: {TTC_READINESS}" in out
    assert "listing 4242 in BlueCustomTeeShirt" in out and "cannot be finished yet: Processing profile is not set up" in out
    assert ".abc" not in out  # no token, ever
