"""A flat upload is grouped by the SKU in each file's name; what has no SKU goes
to Unsorted, never into a guessed group. Folders work as before."""

from __future__ import annotations

import io
import uuid

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select

from app.db.models import Asset
from app.pipeline import grouping
from app.pipeline.sku import SkuParser
from tests.test_api import client  # noqa: F401  (fixture)
from tests.test_archive import _zip


def _png(color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize("name", ["BR5229-1.png", "br5229_2.jpg", "BR5229 (3).jpg", "BR5229 copy.png",
                                  "BR5229-front.png", "BR5229_back.jpg", "BR5229_mockup.png", "BR5229-back copy (2).png"])
def test_copies_views_and_numbered_shots_of_one_design_share_its_sku(name: str) -> None:
    assert grouping.sku_of(name) == "BR5229"


@pytest.mark.parametrize("name", ["IMG_4411.jpg", "DSC01234.JPG", "PXL_20261001_123456.jpg", "Screenshot 2026-10-01 at 10.22.11.png",
                                  "mockup.png", "front.png", "WhatsApp Image 2026-10-01.jpeg", "untitled.png"])
def test_names_with_no_sku_are_not_guessed(name: str) -> None:
    assert grouping.sku_of(name) is None


def test_a_sku_is_read_at_a_word_boundary() -> None:
    assert grouping.sku_of("SUNSET123.png") == "SUNSET123"  # not "NSET123"
    assert grouping.sku_of("tasarim_BR5475.png") == "BR5475"


def test_folders_keep_their_group_and_loose_files_go_by_sku() -> None:
    parser = SkuParser()
    assert grouping.place("Designs/BR6001", "a.png", parser) == ("Designs/BR6001", "BR6001")
    assert grouping.place(None, "BR5229 copy.png", parser) == ("BR5229", "BR5229")
    assert grouping.place("", "IMG_4411.jpg", parser) == (grouping.UNSORTED, None)
    # A folder called like the tray is still a folder.
    assert grouping.place(grouping.UNSORTED, "x.png", parser)[0] != grouping.UNSORTED


async def _upload(client: AsyncClient, batch_id: str, name: str, group: str | None = None):  # noqa: F811
    data = {"group_key": group} if group is not None else None
    res = await client.post(f"/api/batches/{batch_id}/assets", files={"file": (name, io.BytesIO(_png()), "image/png")}, data=data)
    assert res.status_code == 201, res.text
    return res.json()


async def test_a_flat_upload_is_grouped_by_sku_with_unsorted_left_out(client: AsyncClient) -> None:  # noqa: F811
    batch = (await client.post("/api/batches")).json()["id"]
    for name in ("BR5229-1.png", "br5229_2.jpg", "BR5229 (3).jpg", "BR5229 copy.png", "AB1234.png", "AB1234-back.png", "IMG_4411.jpg"):
        await _upload(client, batch, name)
    summary = (await client.post(f"/api/batches/{batch}/finalize")).json()
    assert (summary["groups"], summary["sku_groups"], summary["unsorted"]) == (2, 2, 1)
    assert summary["grouping"] == "Found 2 groups by SKU, 1 photo unsorted"
    async with client.sm() as s:  # type: ignore[attr-defined]
        rows = (await s.execute(select(Asset).where(Asset.batch_id == uuid.UUID(batch)))).scalars().all()
    by_group: dict[str, list[str]] = {}
    for a in sorted(rows, key=lambda a: (a.group_key, a.rank)):
        by_group.setdefault(a.group_key, []).append(a.original_filename)
    # Alphabetical within a group, the first is the cover (as with folders).
    assert by_group == {
        "AB1234": ["AB1234-back.png", "AB1234.png"],
        "BR5229": ["BR5229 (3).jpg", "BR5229 copy.png", "BR5229-1.png", "br5229_2.jpg"],
        grouping.UNSORTED: ["IMG_4411.jpg"],
    }
    # The Unsorted tray is not a listing group.
    groups = (await client.get(f"/api/batches/{batch}/groups")).json()
    assert sorted(g["group_key"] for g in groups) == ["AB1234", "BR5229"]


async def test_folders_and_loose_files_in_one_upload(client: AsyncClient) -> None:  # noqa: F811
    batch = (await client.post("/api/batches")).json()["id"]
    await _upload(client, batch, "front.png", "Summer/BR7001")
    await _upload(client, batch, "back.png", "Summer/BR7001")
    await _upload(client, batch, "BR8002-1.png")
    await _upload(client, batch, "BR8002-2.png")
    await _upload(client, batch, "notes-photo.png")
    summary = (await client.post(f"/api/batches/{batch}/finalize")).json()
    assert summary["grouping"] == "Found 1 group by SKU, 1 from folders, 1 photo unsorted"
    detail = (await client.get(f"/api/batches/{batch}")).json()
    keys = sorted({a["group_key"] for a in detail["assets"]})
    assert keys == ["BR8002", "Summer/BR7001", grouping.UNSORTED]


async def test_a_zip_with_loose_files_is_grouped_by_sku_too(client: AsyncClient) -> None:  # noqa: F811
    batch = (await client.post("/api/batches")).json()["id"]
    data = _zip({"BR5229-1.png": _png(), "BR5229 copy.png": _png("blue"), "IMG_4411.jpg": _png(), "BR6001/a.png": _png()})
    res = await client.post(f"/api/batches/{batch}/archive", files={"file": ("d.zip", io.BytesIO(data), "application/zip")})
    assert res.status_code == 201, res.text
    keys = sorted((a["group_key"], a["original_filename"]) for a in res.json()["assets"])
    assert keys == [("BR5229", "BR5229 copy.png"), ("BR5229", "BR5229-1.png"), ("BR6001", "a.png"), (grouping.UNSORTED, "IMG_4411.jpg")]


async def test_writing_does_not_start_while_photos_are_unsorted(client: AsyncClient, test_settings) -> None:  # noqa: F811
    from app.core.config import set_settings_override

    set_settings_override(test_settings.model_copy(update={"llm_api_key": "test-key"}))
    batch = (await client.post("/api/batches")).json()["id"]
    await _upload(client, batch, "IMG_4411.jpg")
    await _upload(client, batch, "IMG_4412.jpg")
    refused = await client.post(f"/api/batches/{batch}/generate", json={})
    assert refused.status_code == 409
    assert refused.json()["detail"] == ('2 photos are still unsorted. Move them into a group, or choose "Ignore unsorted" '
                                        "to write the groups without them.")
    one = await client.post(f"/api/batches/{batch}/generate", json={"group_key": "BR1"})
    assert one.status_code == 409  # one group at a time is refused the same way
    alone = await client.post(f"/api/batches/{batch}/generate", json={"group_key": grouping.UNSORTED, "ignore_unsorted": True})
    assert alone.status_code == 409
    # Ignoring them: nothing else to write here, and nothing is written for Unsorted.
    ok = await client.post(f"/api/batches/{batch}/generate", json={"ignore_unsorted": True})
    assert ok.status_code == 200, ok.text and ok.json()["generated"] == 0


async def test_the_batch_name_does_not_count_the_unsorted_tray(client: AsyncClient) -> None:  # noqa: F811
    batch = (await client.post("/api/batches")).json()["id"]
    await _upload(client, batch, "BR5229-1.png")
    await _upload(client, batch, "IMG_4411.jpg")
    assert (await client.post(f"/api/batches/{batch}/finalize")).json()["name"] == "BR5229"


# --- The seller's choice: one listing per folder, by SKU, or all one listing ---------
import json  # noqa: E402
from pathlib import Path  # noqa: E402

CASES = json.loads((Path(__file__).parent / "fixtures" / "grouping_cases.json").read_text())


@pytest.mark.parametrize("name, expected", CASES["sku_of"])
def test_the_shared_cases_read_the_same_sku(name: str, expected: str | None) -> None:
    assert grouping.sku_of(name) == expected


@pytest.mark.parametrize("folder, name, mode, key, sku", CASES["place"])
def test_the_shared_cases_land_in_the_same_group(folder, name, mode, key, sku) -> None:  # noqa: ANN001
    assert grouping.place(folder, name, SkuParser(), mode) == (key, sku)


async def _upload_as(client: AsyncClient, batch_id: str, name: str, mode: str, group: str | None = None):  # noqa: F811
    data = {"grouping": mode, **({"group_key": group} if group else {})}
    res = await client.post(f"/api/batches/{batch_id}/assets", files={"file": (name, io.BytesIO(_png()), "image/png")}, data=data)
    assert res.status_code == 201, res.text
    return res.json()


async def test_one_listing_per_folder_never_splits_a_folder(client: AsyncClient) -> None:  # noqa: F811
    batch = (await client.post("/api/batches")).json()["id"]
    for name in ("BR5229-1.png", "AB1234.png", "IMG_4411.jpg"):
        await _upload_as(client, batch, name, "folder", "Mixed")
    await _upload_as(client, batch, "CD7788.png", "folder")
    summary = (await client.post(f"/api/batches/{batch}/finalize")).json()
    assert summary["grouping_mode"] == "folder" and summary["groups"] == 2 and summary["unsorted"] == 0
    assert summary["grouping"] == "2 listings (one per folder)"


async def test_all_one_listing(client: AsyncClient) -> None:  # noqa: F811
    batch = (await client.post("/api/batches")).json()["id"]
    for name in ("front.png", "back.png", "IMG_4411.jpg"):
        await _upload_as(client, batch, name, "one")
    summary = (await client.post(f"/api/batches/{batch}/finalize")).json()
    assert (summary["groups"], summary["unsorted"], summary["grouping"]) == (1, 0, "All the photos are one listing")


async def test_switching_regroups_until_something_is_written(client: AsyncClient) -> None:  # noqa: F811
    from app.db.models import GeneratedContent

    batch = (await client.post("/api/batches")).json()["id"]
    await _upload_as(client, batch, "BR5229-1.png", "folder", "Drop")
    await _upload_as(client, batch, "BR5229 copy.png", "folder", "Drop")
    await _upload_as(client, batch, "AB1234.png", "folder", "Drop")
    await _upload_as(client, batch, "IMG_4411.jpg", "folder", "Drop")
    assert (await client.post(f"/api/batches/{batch}/finalize")).json()["groups"] == 1
    by_sku = (await client.post(f"/api/batches/{batch}/grouping", json={"mode": "sku"})).json()
    assert (by_sku["groups"], by_sku["unsorted"], by_sku["grouping_mode"]) == (2, 1, "sku")
    detail = (await client.get(f"/api/batches/{batch}")).json()
    ranks = sorted((a["group_key"], a["rank"], a["original_filename"]) for a in detail["assets"])
    assert ranks[:2] == [("AB1234", 1, "AB1234.png"), ("BR5229", 1, "BR5229 copy.png")]
    back = (await client.post(f"/api/batches/{batch}/grouping", json={"mode": "folder"})).json()
    assert (back["groups"], back["unsorted"]) == (1, 0)
    assert (await client.post(f"/api/batches/{batch}/grouping", json={"mode": "rows"})).status_code == 422
    # Once a listing is written, its photos are its own.
    async with client.sm() as s:  # type: ignore[attr-defined]
        asset = (await s.execute(select(Asset).where(Asset.batch_id == uuid.UUID(batch)))).scalars().first()
        s.add(GeneratedContent(tenant_id=asset.tenant_id, batch_id=asset.batch_id, asset_id=asset.id, title="t", tags=[], description="d"))
        await s.commit()
    refused = await client.post(f"/api/batches/{batch}/grouping", json={"mode": "sku"})
    assert refused.status_code == 409
    assert (await client.get(f"/api/batches/{batch}/summary")).json()["regroupable"] is False


async def test_another_account_cannot_regroup(client: AsyncClient) -> None:  # noqa: F811
    from app.db.models import Tenant, UploadBatch, UploadBatchStatus

    async with client.sm() as s:  # type: ignore[attr-defined]
        other = Tenant(email=f"{uuid.uuid4()}@e.com", password_hash="x")
        s.add(other)
        await s.flush()
        theirs = UploadBatch(tenant_id=other.id, status=UploadBatchStatus.ready, file_count=0)
        s.add(theirs)
        await s.commit()
    assert (await client.post(f"/api/batches/{theirs.id}/grouping", json={"mode": "sku"})).status_code == 404
