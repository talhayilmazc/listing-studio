"""The editable SKU (pipeline/skus.py, api/skus.py, workers/skus.py): Etsy's rules,
the per-size pattern kept, a duplicate in the same shop warned about (not
refused), drafts on Etsy updated only on confirmation and in every shop that has
one, every change recorded, and another account 404."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.api import deps
from app.db.models import (
    Asset,
    AuditLog,
    ContentVersion,
    EtsyConnection,
    GeneratedContent,
    ListingPublication,
    ShopListingCache,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.etsy.publisher import PublishImage, publish_content
from app.pipeline import skus
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_grouping import _upload
from tests.test_publisher import CONFIG, REFERENCE, FakeEtsy, _seed

# --- the rules -----------------------------------------------------------------------------------


def test_etsys_rules() -> None:
    assert skus.validate("  BR5229-S ") == "BR5229-S"  # trimmed
    assert skus.validate("x" * 32) == "x" * 32
    with pytest.raises(skus.SkuInvalid, match="at most 32"):
        skus.validate("x" * 33)
    for bad in ("BR$5229", "BR^1", "BR`1", "BR\t1"):
        with pytest.raises(skus.SkuInvalid, match="does not accept"):
            skus.validate(bad)
    with pytest.raises(skus.SkuInvalid, match="Enter a SKU"):
        skus.validate("   ")


def test_a_per_size_pattern_is_kept_with_the_new_base() -> None:
    assert skus.pattern_base(["BR5229-S", "BR5229-M", "BR5229-XL"]) == "BR5229"
    assert skus.pattern_base(["BR5229-XL", "BR5229-XXL"]) == "BR5229"
    assert skus.variant_skus(["BR5229-S", "BR5229-M"], "CC7001") == ["CC7001-S", "CC7001-M"]
    # No pattern: the base on every product.
    assert skus.pattern_base(["BR5229", "BR5229"]) is None
    assert skus.pattern_base(["A1", "B2"]) is None
    assert skus.variant_skus(["BR5229", "BR5229"], "CC7001") == ["CC7001", "CC7001"]
    assert skus.variant_skus([None, None], "CC7001") == ["CC7001", "CC7001"]
    products = [{"sku": "OLD-S"}, {"sku": "OLD-M"}]
    assert skus.product_skus(products, "BR5475") == {0: "BR5475-S", 1: "BR5475-M"}
    assert skus.product_skus([{"sku": ""}, {"sku": ""}], "BR5475") == {0: "BR5475", 1: "BR5475"}


# --- drafts ------------------------------------------------------------------------------------


async def _draft(sm, ids: tuple, shop: FakeEtsy, reference: dict[str, Any], sku: str = "BR5475"):  # noqa: ANN001
    _, conn_id, content_id, job_id = ids
    async with sm() as s:
        return await publish_content(
            s, job_id=job_id, content=await s.get(GeneratedContent, content_id), connection=await s.get(EtsyConnection, conn_id),
            sku=sku, thumbnail=PublishImage(b"t", "t.jpg"), client=shop, access_token="tok", config=CONFIG,
            reference=reference, theme="x", tenant_limit=2000,
        )


async def test_the_draft_writes_the_sku_on_every_product_and_reads_it_back(async_sm) -> None:  # noqa: ANN001
    ids = await _seed(async_sm)
    flat = {**REFERENCE, "inventory_products": [{**p, "sku": ""} for p in REFERENCE["inventory_products"]]}
    shop = FakeEtsy()
    await _draft(async_sm, ids, shop, flat)
    assert [p["sku"] for p in shop.inventory["products"]] == ["BR5475", "BR5475"]
    async with async_sm() as s:
        assert (await s.execute(select(ContentVersion))).scalars().one().sku == "BR5475"


async def test_the_read_back_catches_a_sku_etsy_did_not_keep(async_sm) -> None:  # noqa: ANN001
    class Changed(FakeEtsy):
        async def get_listing_inventory(self, listing_id: int, **_: Any) -> dict[str, Any]:
            return {"products": [{**p, "sku": p["sku"].lower()} for p in self.inventory["products"]]}

    with pytest.raises(ValueError, match="SKU did not save as sent"):
        await _draft(async_sm, await _seed(async_sm), Changed(), REFERENCE)


async def test_a_sku_etsy_would_refuse_never_goes(async_sm) -> None:  # noqa: ANN001
    with pytest.raises(ValueError, match="cannot go to Etsy"):
        await _draft(async_sm, await _seed(async_sm), FakeEtsy(), REFERENCE, sku="BR$5475")


# --- the batch page and the review card -----------------------------------------------------------


class _Queue:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def enqueue(self, function: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((function, args))


async def _world(client: AsyncClient) -> dict[str, Any]:  # noqa: F811
    queue = _Queue()
    client.app.dependency_overrides[deps.get_enqueuer] = lambda: queue  # type: ignore[attr-defined]
    batch = (await client.post("/api/batches")).json()["id"]
    for name in ("BR5229-1.png", "BR5229-2.png", "AB1234-1.png"):
        await _upload(client, batch, name)
    await client.post(f"/api/batches/{batch}/finalize")
    async with client.sm() as s:  # type: ignore[attr-defined]
        tid = client.tenant_id  # type: ignore[attr-defined]
        a = EtsyConnection(tenant_id=tid, etsy_user_id=11, shop_id=11, shop_name="Shop A")
        b = EtsyConnection(tenant_id=tid, etsy_user_id=12, shop_id=12, shop_name="Shop B")
        s.add_all([a, b])
        await s.flush()
        (await s.get(UploadBatch, uuid.UUID(batch))).connection_id = a.id
        cover = (await s.execute(select(Asset).where(Asset.batch_id == uuid.UUID(batch), Asset.group_key == "BR5229")
                                 .order_by(Asset.rank))).scalars().first()
        content = GeneratedContent(tenant_id=tid, batch_id=uuid.UUID(batch), asset_id=cover.id, title="t", tags=[], description="d")
        s.add(content)
        await s.flush()
        pubs = [ListingPublication(tenant_id=tid, content_id=content.id, connection_id=shop.id, etsy_listing_id=lid,
                                   state="draft", sku="BR5229") for shop, lid in ((a, 5001), (b, 5002))]
        # Another listing in Shop A already uses CC7001.
        s.add_all([*pubs, ShopListingCache(tenant_id=tid, connection_id=a.id, listing_id=777,
                                           payload={"listing_id": 777, "skus": ["CC7001"]})])
        await s.commit()
        return {"batch": batch, "content": content.id, "a": a.id, "b": b.id, "pubs": [p.id for p in pubs], "queue": queue}


async def test_editing_on_the_group_card_validates_and_warns_about_duplicates(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    url = f"/api/batches/{w['batch']}/groups/sku"
    assert (await client.put(url, json={"group_key": "AB1234", "sku": "x" * 33})).status_code == 422
    assert (await client.put(url, json={"group_key": "AB1234", "sku": "AB$1"})).status_code == 422
    res = (await client.put(url, json={"group_key": "AB1234", "sku": " CC7001 "})).json()
    assert (res["sku"], res["group_key"]) == ("CC7001", "CC7001")  # trimmed; the group named after its SKU follows
    assert res["warnings"] and "Shop A already has CC7001 on listing 777" in res["warnings"][0]
    # Two groups of the batch with one SKU: warned too, never refused.
    res = (await client.put(f"/api/batches/{w['batch']}/groups/sku", json={"group_key": "BR5229", "sku": "CC7001"})).json()
    assert any("group CC7001 of this batch" in x for x in res["warnings"])
    async with client.sm() as s:  # type: ignore[attr-defined]
        rows = (await s.execute(select(AuditLog).where(AuditLog.action == "listing.sku_changed"))).scalars().all()
    assert len(rows) == 2 and rows[0].details["sku_to"] == "CC7001"


async def test_after_drafts_exist_nothing_goes_to_etsy_until_the_seller_presses_update(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    res = (await client.put(f"/api/content/{w['content']}/sku", json={"sku": "CC7002"})).json()
    assert {e["shop_name"]: e["requests"] for e in res["etsy"]} == {"Shop A": 3, "Shop B": 3}
    assert not w["queue"].calls  # nothing sent by editing
    plan = (await client.post(f"/api/content/{w['content']}/sku/etsy", json={})).json()
    assert (plan["queued"], plan["requests"]) == (False, 6) and not w["queue"].calls
    done = (await client.post(f"/api/content/{w['content']}/sku/etsy", json={"confirm": True})).json()
    assert done["queued"] is True
    jobs = [c for c in w["queue"].calls if c[0] == "update_sku_on_etsy"]
    assert sorted(json.loads(c[1][0])["publication_id"] for c in jobs) == sorted(str(p) for p in w["pubs"])
    assert {json.loads(c[1][0])["sku"] for c in jobs} == {"CC7002"}
    # One shop only, when asked.
    w["queue"].calls.clear()
    await client.post(f"/api/content/{w['content']}/sku/etsy", json={"confirm": True, "connection_ids": [str(w["b"])]})
    assert [json.loads(c[1][0])["publication_id"] for c in w["queue"].calls] == [str(w["pubs"][1])]


async def test_set_sku_for_selected_with_a_suffix(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    res = (await client.post(f"/api/batches/{w['batch']}/sku", json={"content_ids": [str(w["content"])], "suffix": "-CC"})).json()
    assert res["updated"] == 1 and res["results"][0]["sku"] == "BR5229-CC"
    long = (await client.post(f"/api/batches/{w['batch']}/sku", json={"content_ids": [str(w["content"])], "suffix": "x" * 30})).json()
    assert long["updated"] == 0 and "at most 32" in long["skipped"][0]["reason"]
    assert (await client.post(f"/api/batches/{w['batch']}/sku", json={"content_ids": [str(w["content"])]})).status_code == 422


async def test_another_account_gets_404(client: AsyncClient) -> None:  # noqa: F811
    w = await _world(client)
    async with client.sm() as s:  # type: ignore[attr-defined]
        other = Tenant(email=f"{uuid.uuid4()}@e.com", password_hash="x")
        s.add(other)
        await s.flush()
        batch = UploadBatch(tenant_id=other.id, status=UploadBatchStatus.ready, file_count=1)
        s.add(batch)
        await s.flush()
        asset = Asset(batch_id=batch.id, tenant_id=other.id, original_filename="ZZ1.png", group_key="ZZ1", parsed_sku="ZZ1", storage_key="k")
        s.add(asset)
        await s.flush()
        theirs = GeneratedContent(tenant_id=other.id, batch_id=batch.id, asset_id=asset.id, title="t", tags=[])
        s.add(theirs)
        await s.commit()
    assert (await client.put(f"/api/batches/{batch.id}/groups/sku", json={"group_key": "ZZ1", "sku": "MINE1"})).status_code == 404
    assert (await client.put(f"/api/content/{theirs.id}/sku", json={"sku": "MINE1"})).status_code == 404
    assert (await client.post(f"/api/content/{theirs.id}/sku/etsy", json={"confirm": True})).status_code == 404
    assert (await client.post(f"/api/batches/{batch.id}/sku", json={"content_ids": [str(theirs.id)], "suffix": "-X"})).status_code == 404
    res = (await client.post(f"/api/batches/{w['batch']}/sku", json={"content_ids": [str(theirs.id)], "suffix": "-X"})).json()
    assert res["updated"] == 0
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.get(Asset, asset.id)).parsed_sku == "ZZ1"


# --- the update on Etsy -------------------------------------------------------------------------


async def test_updating_on_etsy_changes_only_the_sku_keeps_the_pattern_and_records_it(client: AsyncClient, monkeypatch) -> None:  # noqa: F811
    import app.workers.skus as jobs

    w = await _world(client)
    inventory = {
        "products": [
            {"sku": "BR5229-S", "property_values": [{"property_id": 200, "property_name": "Size", "value_ids": [1], "values": ["S"]}],
             "offerings": [{"price": {"amount": 2599, "divisor": 100}, "quantity": 7, "is_enabled": True, "readiness_state_id": 9}]},
            {"sku": "BR5229-M", "property_values": [{"property_id": 200, "property_name": "Size", "value_ids": [2], "values": ["M"]}],
             "offerings": [{"price": {"amount": 2799, "divisor": 100}, "quantity": 3, "is_enabled": True, "readiness_state_id": 9}]},
        ],
        "price_on_property": [200], "quantity_on_property": [], "sku_on_property": [200],
    }
    written: list[dict] = []

    class Etsy:
        async def get_listing_inventory(self, listing_id: int, **_: Any) -> dict:
            return written[-1] if written else inventory

        async def update_listing_inventory(self, listing_id: int, *, inventory: dict, **_: Any) -> dict:
            written.append(inventory)
            return {}

    class Service:
        async def get_valid_access_token(self, session, connection):  # noqa: ANN001
            return "tok"

    async def shop_id(session, client, connection, kw):  # noqa: ANN001
        return connection.shop_id

    async def gated(ctx, function, arg, tenant_id, body):  # noqa: ANN001
        return await body()

    monkeypatch.setattr(jobs, "_build_client", lambda *a, **k: Etsy())
    monkeypatch.setattr(jobs, "_connection_service", lambda settings: Service())
    monkeypatch.setattr(jobs, "_resolve_shop_id", shop_id)
    monkeypatch.setattr(jobs, "_run_gated", gated)
    ctx = {"sessionmaker": client.sm}  # type: ignore[attr-defined]
    assert await jobs.update_sku_on_etsy(ctx, jobs.update_spec(w["pubs"][0], "CC7001")) == "sku:2"
    sent = written[0]
    assert [p["sku"] for p in sent["products"]] == ["CC7001-S", "CC7001-M"]
    assert [p["offerings"][0]["price"] for p in sent["products"]] == [25.99, 27.99]
    assert [p["offerings"][0]["quantity"] for p in sent["products"]] == [7, 3]
    assert sent["sku_on_property"] == [200] and sent["price_on_property"] == [200]
    async with client.sm() as s:  # type: ignore[attr-defined]
        pub = await s.get(ListingPublication, w["pubs"][0])
        version = (await s.execute(select(ContentVersion).where(ContentVersion.publication_id == pub.id))).scalars().one()
        audit = (await s.execute(select(AuditLog).where(AuditLog.action == "listing.sku_changed"))).scalars().one()
    assert pub.sku == "CC7001" and (version.sku, version.reason) == ("CC7001", "sku_edited")
    assert audit.details["on_etsy"] is True and audit.details["sku_from"] == "BR5229"

    # Etsy keeps something else: said, and the record is not changed.
    class Lossy(Etsy):
        async def get_listing_inventory(self, listing_id: int, **_: Any) -> dict:
            return inventory

    monkeypatch.setattr(jobs, "_build_client", lambda *a, **k: Lossy())
    with pytest.raises(ValueError, match="did not keep the SKU"):
        await jobs.update_sku_on_etsy(ctx, jobs.update_spec(w["pubs"][1], "CC7001"))
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.get(ListingPublication, w["pubs"][1])).sku == "BR5229"
