"""The grouping board: move photos between groups and Unsorted, a new group from a
selection, merge two groups, edit a group's SKU. Per account; a written listing
stays with its group and says when its cover changed."""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import (
    Asset,
    GeneratedContent,
    ListingGroupSetting,
    Tenant,
    UploadBatch,
    UploadBatchStatus,
)
from app.pipeline import grouping
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_grouping import _upload


async def _batch(client: AsyncClient, names: list[str]) -> tuple[str, dict[str, str]]:  # noqa: F811
    batch = (await client.post("/api/batches")).json()["id"]
    ids = {}
    for name in names:
        ids[name] = (await _upload(client, batch, name))["id"]
    await client.post(f"/api/batches/{batch}/finalize")
    return batch, ids


def _groups(detail: dict) -> dict[str, list[str]]:
    out: dict[str, list[tuple[int, str]]] = {}
    for a in detail["assets"]:
        out.setdefault(a["group_key"] or "", []).append((a["rank"], a["original_filename"]))
    return {k: [n for _, n in sorted(v)] for k, v in sorted(out.items())}


async def _write(client: AsyncClient, asset_id: str) -> uuid.UUID:  # noqa: F811
    async with client.sm() as s:  # type: ignore[attr-defined]
        asset = await s.get(Asset, uuid.UUID(asset_id))
        c = GeneratedContent(tenant_id=asset.tenant_id, batch_id=asset.batch_id, asset_id=asset.id,
                             written_from_asset_id=asset.id, title="t", tags=[], description="d")
        s.add(c)
        await s.commit()
        return c.id


async def _content(client: AsyncClient, cid: uuid.UUID) -> GeneratedContent:  # noqa: F811
    async with client.sm() as s:  # type: ignore[attr-defined]
        return await s.get(GeneratedContent, cid)


NAMES = ["AB1234.png", "AB1234-back.png", "BR5229-1.png", "BR5229 copy.png", "IMG_4411.jpg", "IMG_4412.jpg"]


async def test_photos_move_between_groups_and_unsorted(client: AsyncClient) -> None:  # noqa: F811
    batch, ids = await _batch(client, NAMES)
    # From Unsorted into a group: they join its end and take its SKU.
    res = await client.post(f"/api/batches/{batch}/board/move",
                            json={"asset_ids": [ids["IMG_4412.jpg"], ids["IMG_4411.jpg"]], "to": "BR5229"})
    assert res.status_code == 200, res.text
    groups = _groups(res.json())
    assert groups == {
        "AB1234": ["AB1234-back.png", "AB1234.png"],
        "BR5229": ["BR5229 copy.png", "BR5229-1.png", "IMG_4411.jpg", "IMG_4412.jpg"],
    }
    assert {a["parsed_sku"] for a in res.json()["assets"] if a["group_key"] == "BR5229"} == {"BR5229"}
    # Back to Unsorted: no SKU there.
    res = await client.post(f"/api/batches/{batch}/board/move", json={"asset_ids": [ids["IMG_4411.jpg"]], "to": grouping.UNSORTED})
    moved = next(a for a in res.json()["assets"] if a["id"] == ids["IMG_4411.jpg"])
    assert (moved["group_key"], moved["parsed_sku"]) == (grouping.UNSORTED, None)
    # A group emptied without a listing simply goes.
    res = await client.post(f"/api/batches/{batch}/board/move",
                            json={"asset_ids": [ids["AB1234.png"], ids["AB1234-back.png"]], "to": "BR5229"})
    assert "AB1234" not in _groups(res.json())
    # Unknown target group.
    assert (await client.post(f"/api/batches/{batch}/board/move", json={"asset_ids": [ids["IMG_4412.jpg"]], "to": "NOPE1"})).status_code == 404


async def test_a_new_group_from_a_selection(client: AsyncClient) -> None:  # noqa: F811
    batch, ids = await _batch(client, NAMES)
    res = await client.post(f"/api/batches/{batch}/board/move",
                            json={"asset_ids": [ids["IMG_4411.jpg"], ids["IMG_4412.jpg"]], "to": " cc7001 ", "new_group": True})
    assert res.status_code == 200, res.text
    groups = _groups(res.json())
    assert groups["CC7001"] == ["IMG_4411.jpg", "IMG_4412.jpg"] and grouping.UNSORTED not in groups
    assert {a["parsed_sku"] for a in res.json()["assets"] if a["group_key"] == "CC7001"} == {"CC7001"}
    # Its name must be new, and not the tray's.
    again = await client.post(f"/api/batches/{batch}/board/move", json={"asset_ids": [ids["AB1234.png"]], "to": "CC7001", "new_group": True})
    assert again.status_code == 409
    tray = await client.post(f"/api/batches/{batch}/board/move", json={"asset_ids": [ids["AB1234.png"]], "to": "~unsorted", "new_group": True})
    assert tray.status_code == 422
    # Unsorted photos no longer block writing.
    assert (await client.get(f"/api/batches/{batch}/summary")).json()["unsorted"] == 0


async def test_merging_two_groups(client: AsyncClient) -> None:  # noqa: F811
    batch, ids = await _batch(client, NAMES)
    async with client.sm() as s:  # type: ignore[attr-defined]
        tenant_id = (await s.get(Asset, uuid.UUID(ids["AB1234.png"]))).tenant_id
        s.add(ListingGroupSetting(tenant_id=tenant_id, batch_id=uuid.UUID(batch), group_key="AB1234", manual=True))
        await s.commit()
    written = await _write(client, ids["AB1234-back.png"])  # AB1234's cover
    res = await client.post(f"/api/batches/{batch}/board/merge", json={"from_key": "AB1234", "into_key": "BR5229"})
    assert res.status_code == 200, res.text
    groups = _groups(res.json())
    assert groups["BR5229"] == ["BR5229 copy.png", "BR5229-1.png", "AB1234-back.png", "AB1234.png"]
    assert "AB1234" not in groups
    # The listing written for AB1234 moved with its photos, onto the merged group's
    # cover, which it was not written from; the group's settings came along.
    content = await _content(client, written)
    assert content.asset_id == uuid.UUID(ids["BR5229 copy.png"])
    assert content.written_from_asset_id == uuid.UUID(ids["AB1234-back.png"])
    async with client.sm() as s:  # type: ignore[attr-defined]
        keys = (await s.execute(select(ListingGroupSetting.group_key).where(ListingGroupSetting.batch_id == uuid.UUID(batch)))).scalars().all()
    assert keys == ["BR5229"]
    # Merging into the tray or into itself is not a merge.
    assert (await client.post(f"/api/batches/{batch}/board/merge", json={"from_key": "BR5229", "into_key": "~unsorted"})).status_code == 422
    assert (await client.post(f"/api/batches/{batch}/board/merge", json={"from_key": "BR5229", "into_key": "BR5229"})).status_code == 422


async def test_two_written_listings_are_never_merged(client: AsyncClient) -> None:  # noqa: F811
    batch, ids = await _batch(client, NAMES)
    await _write(client, ids["AB1234-back.png"])
    await _write(client, ids["BR5229 copy.png"])
    res = await client.post(f"/api/batches/{batch}/board/merge", json={"from_key": "AB1234", "into_key": "BR5229"})
    assert res.status_code == 409
    assert "Both AB1234 and BR5229" in res.json()["detail"]
    detail = (await client.get(f"/api/batches/{batch}")).json()
    assert _groups(detail)["AB1234"] == ["AB1234-back.png", "AB1234.png"]  # nothing changed


async def test_a_written_group_keeps_a_photo_and_its_listing_follows_the_cover(client: AsyncClient) -> None:  # noqa: F811
    batch, ids = await _batch(client, NAMES)
    written = await _write(client, ids["AB1234-back.png"])
    # Its cover leaves: the listing stays with the group, on its new cover, and says
    # it was written from the old one.
    res = await client.post(f"/api/batches/{batch}/board/move", json={"asset_ids": [ids["AB1234-back.png"]], "to": "BR5229"})
    assert res.status_code == 200
    content = await _content(client, written)
    assert content.asset_id == uuid.UUID(ids["AB1234.png"])
    assert content.written_from_asset_id == uuid.UUID(ids["AB1234-back.png"])
    listed = (await client.get(f"/api/batches/{batch}/content")).json()
    assert listed[0]["written_from_asset_id"] == ids["AB1234-back.png"] != listed[0]["asset_id"]
    # Its last photo cannot leave.
    res = await client.post(f"/api/batches/{batch}/board/move", json={"asset_ids": [ids["AB1234.png"]], "to": grouping.UNSORTED})
    assert res.status_code == 409
    assert "AB1234 has a listing written" in res.json()["detail"]
    # Photos joining a written group go after its cover: its listing stays where it is.
    res = await client.post(f"/api/batches/{batch}/board/move", json={"asset_ids": [ids["IMG_4411.jpg"]], "to": "AB1234"})
    assert _groups(res.json())["AB1234"] == ["AB1234.png", "IMG_4411.jpg"]
    assert (await _content(client, written)).asset_id == uuid.UUID(ids["AB1234.png"])


async def test_editing_a_groups_sku(client: AsyncClient) -> None:  # noqa: F811
    batch, ids = await _batch(client, NAMES)
    res = await client.put(f"/api/batches/{batch}/board/sku", json={"group_key": "BR5229", "sku": " BR5230 "})  # trimmed; case as typed
    assert res.status_code == 200, res.text
    groups = _groups(res.json())
    # A group named after its SKU takes the new one as its name.
    assert "BR5230" in groups and "BR5229" not in groups
    assert {a["parsed_sku"] for a in res.json()["assets"] if a["group_key"] == "BR5230"} == {"BR5230"}
    # Taking another group's SKU keeps the name (no silent merge).
    res = await client.put(f"/api/batches/{batch}/board/sku", json={"group_key": "BR5230", "sku": "AB1234"})
    assert set(_groups(res.json())) >= {"AB1234", "BR5230"}
    assert (await client.put(f"/api/batches/{batch}/board/sku", json={"group_key": "~unsorted", "sku": "X1"})).status_code == 422


async def test_board_operations_on_another_account_are_404(client: AsyncClient) -> None:  # noqa: F811
    batch, ids = await _batch(client, NAMES)
    async with client.sm() as s:  # type: ignore[attr-defined]
        other = Tenant(email=f"{uuid.uuid4()}@e.com", password_hash="x")
        s.add(other)
        await s.flush()
        theirs = UploadBatch(tenant_id=other.id, status=UploadBatchStatus.ready, file_count=0)
        s.add(theirs)
        await s.flush()
        their_asset = Asset(batch_id=theirs.id, tenant_id=other.id, original_filename="ZZ9999.png", group_key="ZZ9999",
                            parsed_sku="ZZ9999", storage_key="x/y.png")
        s.add(their_asset)
        await s.commit()
    for call in (
        client.post(f"/api/batches/{theirs.id}/board/move", json={"asset_ids": [str(their_asset.id)], "to": "~unsorted"}),
        client.post(f"/api/batches/{theirs.id}/board/merge", json={"from_key": "ZZ9999", "into_key": "A"}),
        client.put(f"/api/batches/{theirs.id}/board/sku", json={"group_key": "ZZ9999", "sku": "A1"}),
        # Their photo, through my batch.
        client.post(f"/api/batches/{batch}/board/move", json={"asset_ids": [str(their_asset.id)], "to": "BR5229"}),
    ):
        assert (await call).status_code == 404
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert (await s.get(Asset, their_asset.id)).group_key == "ZZ9999"


async def test_three_hundred_photos_move_in_one_request(client: AsyncClient) -> None:  # noqa: F811
    batch = (await client.post("/api/batches")).json()["id"]
    async with client.sm() as s:  # type: ignore[attr-defined]
        b = await s.get(UploadBatch, uuid.UUID(batch))
        rows = [Asset(batch_id=b.id, tenant_id=b.tenant_id, original_filename=f"IMG_{n:04}.jpg", group_key=grouping.UNSORTED,
                      storage_key=f"k/{n}.jpg", rank=n) for n in range(320)]
        s.add_all(rows)
        await s.commit()
        ids = [str(a.id) for a in rows]
    res = await client.post(f"/api/batches/{batch}/board/move", json={"asset_ids": ids, "to": "BIG100", "new_group": True})
    assert res.status_code == 200
    assert len(_groups(res.json())["BIG100"]) == 320
