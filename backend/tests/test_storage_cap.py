"""Per-account storage cap: 5 GB by default, an admin's own number per account;
an upload past it is refused before anything is stored, saying how much is used
and what frees space."""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select

from app.core import storage_cap
from app.db.models import Asset, AuditLog, Tenant
from tests.test_admin import world  # noqa: F401  (fixture)
from tests.test_api import client  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    storage_cap.forget()
    yield
    storage_cap.forget()


def _png(size=(300, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


async def _upload(client: AsyncClient, batch_id: str, name: str = "a.png"):  # noqa: F811
    return await client.post(f"/api/batches/{batch_id}/assets", files={"file": (name, io.BytesIO(_png()), "image/png")})


async def test_the_default_is_5_gb_and_an_upload_past_the_cap_is_refused(client: AsyncClient, tmp_path) -> None:  # noqa: F811
    async with client.sm() as s:  # type: ignore[attr-defined]
        tenant = await s.get(Tenant, client.tenant_id)  # type: ignore[attr-defined]
        assert storage_cap.limit(tenant) == 5 * storage_cap.GB
        tenant.storage_cap_bytes = 4000  # bytes: room for one small processed image, not two
        await s.commit()
    batch = (await client.post("/api/batches")).json()["id"]
    first = await _upload(client, batch)
    assert first.status_code == 201, first.text
    storage_cap.forget()  # measure again: the first image is on disk now
    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    second = await _upload(client, batch, "b.png")
    assert second.status_code == 413
    detail = second.json()["detail"]
    assert detail.startswith("Your stored images use ") and "of your 0 MB limit" in detail
    assert "3 days after it has a draft in every shop" in detail and "delete batches you no longer need" in detail
    assert {p for p in tmp_path.rglob("*") if p.is_file()} == before  # nothing was stored
    async with client.sm() as s:  # type: ignore[attr-defined]
        assert len((await s.execute(select(Asset))).scalars().all()) == 1


async def test_the_cap_counts_what_this_process_stores_before_the_next_measure(client: AsyncClient) -> None:  # noqa: F811
    async with client.sm() as s:  # type: ignore[attr-defined]
        (await s.get(Tenant, client.tenant_id)).storage_cap_bytes = 4000  # type: ignore[attr-defined]
        await s.commit()
    batch = (await client.post("/api/batches")).json()["id"]
    assert (await _upload(client, batch)).status_code == 201
    assert (await _upload(client, batch, "b.png")).status_code == 413  # no re-measure needed


async def test_an_admin_sets_an_accounts_cap_or_puts_it_back_on_the_default(world) -> None:  # noqa: F811
    a, b, bob = world["a"], world["b"], world["bob"]
    url = f"/api/admin/users/{bob.tenant_id}/storage-cap"
    assert (await b.put(url, json={"gb": 50})).status_code == 404
    assert (await a.put(url, json={"gb": 0})).status_code == 422
    out = (await a.put(url, json={"gb": 12.5})).json()
    assert out["storage_cap_bytes"] == int(12.5 * storage_cap.GB) and out["storage_cap_custom"] is True
    out = (await a.put(url, json={"gb": None})).json()
    assert out["storage_cap_bytes"] == 5 * storage_cap.GB and out["storage_cap_custom"] is False
    async with world["sm"]() as s:
        changes = (await s.execute(select(AuditLog).where(AuditLog.action == "user.storage_cap_changed"))).scalars().all()
    assert [(c.details["previous"], c.details["new"]) for c in changes] == [(None, int(12.5 * storage_cap.GB)), (int(12.5 * storage_cap.GB), None)]
