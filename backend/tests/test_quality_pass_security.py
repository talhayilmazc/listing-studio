"""Quality pass 2026-10 (docs/qa-2026-10.md, section 7): small security and robustness fixes.

* LocalStorage refuses any key that could leave the storage root (empty, absolute, "..", a
  backslash) as a missing file, and ``exists`` says False for it.
* A stored file that is missing answers 404 in plain words, never a 500.
* The bootstrap admin endpoints answer 404 to a caller without the token whatever body it sends
  (a 422 would show they exist).
"""

from __future__ import annotations

import pytest
from fakeredis import FakeAsyncRedis
from httpx import ASGITransport, AsyncClient

from app.api import deps
from app.main import create_app
from app.pipeline.storage import LocalStorage
from tests.auth_support import BROWSER_HEADERS


@pytest.mark.parametrize("key", ["", "/etc/passwd", "../secret", "a/../../b", "a\\..\\b", "tenant/../../x"])
def test_storage_refuses_keys_outside_its_root(tmp_path, key: str) -> None:
    storage = LocalStorage(tmp_path / "root")
    (tmp_path / "secret").write_bytes(b"x")
    with pytest.raises(FileNotFoundError):
        storage.get(key)
    with pytest.raises(FileNotFoundError):
        storage.put(key, b"y")
    assert storage.exists(key) is False
    assert (tmp_path / "secret").read_bytes() == b"x"


def test_storage_still_reads_and_writes_its_own_keys(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    storage.put("tenant/batch/processed/a.jpg", b"img")
    assert storage.get("tenant/batch/processed/a.jpg") == b"img"
    assert storage.exists("tenant/batch/processed/a.jpg")


async def test_a_missing_file_is_404_in_plain_words() -> None:
    app = create_app()

    @app.get("/api/_missing_file_probe")
    async def probe() -> None:
        LocalStorage("/nonexistent-root").get("tenant/x.jpg")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=BROWSER_HEADERS) as c:
        res = await c.get("/api/_missing_file_probe")
    assert res.status_code == 404
    assert res.json() == {"detail": "This file is no longer stored."}
    assert "nonexistent" not in res.text


@pytest.mark.parametrize("path,body", [
    ("/api/account/admin/reset-password", {"email": "not-an-email"}),
    ("/api/account/admin/reset-password", {}),
    ("/api/account/admin/invites", {"expires_in_days": "soon"}),
])
async def test_bootstrap_endpoints_are_404_without_the_token_whatever_the_body(path: str, body: dict) -> None:
    app = create_app()
    redis = FakeAsyncRedis()
    app.dependency_overrides[deps.get_redis] = lambda: redis
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=BROWSER_HEADERS) as c:
        res = await c.post(path, json=body)
    assert res.status_code == 404
