"""ETSY_CALLS_BLOCKED: a development process sends nothing to Etsy.

A local database can hold real shops that production already serves. With the
switch on, every outbound call is refused before any network I/O: API requests,
the token exchange and the token refresh (which would replace the stored
refresh token), image fetches, and sending the browser to Etsy to connect.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fakeredis import FakeAsyncRedis

from app.core.config import Settings, set_settings_override
from app.core.crypto import TokenCipher
from app.db.models import ConnectionStatus, EtsyConnection, Tenant
from app.etsy import guard, oauth
from app.etsy.api import EtsyApiClient
from app.etsy.connection import ConnectionService
from app.etsy.rate_limiter import DailyQuota
from tests.test_publish_api import ctx  # noqa: F401  (fixture)

APP = Path(__file__).resolve().parents[1] / "app"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def blocked(test_settings: Settings) -> None:
    set_settings_override(test_settings.model_copy(update={"etsy_calls_blocked": True}))


class Wire:
    """A transport that counts what would have left for Etsy."""

    def __init__(self) -> None:
        self.sent: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        return httpx.Response(200, json={"access_token": "1.new", "refresh_token": "1.newrefresh", "expires_in": 3600, "results": []})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def test_it_is_off_unless_asked_for() -> None:
    assert Settings(_env_file=None, etsy_calls_blocked=False).etsy_calls_blocked is False
    assert Settings.model_fields["etsy_calls_blocked"].default is False  # production is unchanged
    assert guard.blocked() is False
    guard.check("anything")  # does nothing


async def test_no_api_request_leaves_and_no_quota_is_spent(blocked) -> None:
    wire, redis = Wire(), FakeAsyncRedis()
    quota = DailyQuota(redis, global_daily_limit=5000)
    client = EtsyApiClient(client_id="k", shared_secret="s", http_client=wire.client(), quota=quota)
    calls = [
        lambda: client.get_listing(1, access_token="t", tenant_id="a", tenant_limit=100),
        lambda: client.get_listing_images(1, access_token="t"),
        lambda: client.update_listing(9, 1, updates={"title": "x"}, access_token="t"),
        lambda: client.delete_listing_image(9, 1, 2, access_token="t"),
        lambda: client.upload_listing_image(9, 1, rank=1, image_bytes=b"x", filename="a.jpg", access_token="t"),
        lambda: client.get_shop_transactions(9, limit=1, offset=0, access_token="t"),
    ]
    for call in calls:
        with pytest.raises(guard.EtsyCallsBlocked) as refused:
            await call()
        assert "nothing was sent to Etsy" in str(refused.value)
    assert wire.sent == []
    assert await quota.global_usage() == 0  # refused before the quota and the bucket


def test_every_api_request_goes_through_the_one_guarded_method() -> None:
    source = (APP / "etsy" / "api.py").read_text(encoding="utf-8")
    assert source.count("self._http.") == 1  # the single send, inside _request
    body = source[source.index("async def _request("):]
    assert body.index("guard.check(") < body.index("self._http.")


async def test_the_token_endpoint_is_never_called(blocked) -> None:
    wire = Wire()
    async with wire.client() as http:
        with pytest.raises(guard.EtsyCallsBlocked):
            await oauth.refresh_tokens(http, token_url="https://etsy.test/token", client_id="k", refresh_token="1.secret")
        with pytest.raises(guard.EtsyCallsBlocked):
            await oauth.exchange_code(http, token_url="https://etsy.test/token", client_id="k", redirect_uri="http://x", code="c", verifier="v")
    assert wire.sent == []
    source = (APP / "etsy" / "oauth.py").read_text(encoding="utf-8")
    assert source.count("await client.post(") == 2 and source.count("guard.check(") == 2  # both guarded, and there is no third


async def test_an_expired_token_is_not_refreshed_and_the_stored_tokens_stay_as_they_are(async_sm, blocked) -> None:
    cipher, wire = TokenCipher(Fernet.generate_key()), Wire()
    async with async_sm() as s:
        tenant = Tenant(email="seller@example.com", password_hash="x")
        s.add(tenant)
        await s.flush()
        connection = EtsyConnection(
            tenant_id=tenant.id, status=ConnectionStatus.active, etsy_user_id=1, shop_id=1,
            access_token_enc=cipher.encrypt("1.old"), refresh_token_enc=cipher.encrypt("1.oldrefresh"),
            token_expires_at=datetime.now(timezone.utc) - timedelta(days=7),
        )
        s.add(connection)
        await s.commit()
        before = (connection.access_token_enc, connection.refresh_token_enc, connection.token_expires_at.replace(tzinfo=None))

        service = ConnectionService(cipher, client_id="k", token_url="https://etsy.test/token", client_factory=wire.client)
        with pytest.raises(guard.EtsyCallsBlocked):
            await service.get_valid_access_token(s, connection)
        await s.refresh(connection)
        assert (connection.access_token_enc, connection.refresh_token_enc, connection.token_expires_at.replace(tzinfo=None)) == before
        assert connection.status is ConnectionStatus.active  # nothing is disconnected by a refused call
        assert cipher.decrypt(connection.refresh_token_enc) == "1.oldrefresh"
    assert wire.sent == []

    # With the switch off the same call does refresh: the guard is the only thing in the way.
    set_settings_override(Settings(_env_file=None, etsy_calls_blocked=False))
    async with async_sm() as s:
        connection = (await s.get(EtsyConnection, connection.id))
        assert await service.get_valid_access_token(s, connection) == "1.new"
    assert len(wire.sent) == 1


async def test_a_shop_cannot_be_connected_the_browser_is_not_sent_to_etsy(ctx, test_settings) -> None:  # noqa: F811
    set_settings_override(test_settings.model_copy(update={"etsy_calls_blocked": True, "etsy_client_id": "keystring"}))
    for path in ("/api/auth/etsy/start", "/api/auth/etsy/callback?state=s&code=c"):
        res = await ctx["client"].get(path, follow_redirects=False)
        assert res.status_code in (302, 303, 307), res.text
        where = res.headers["location"]
        assert "etsy.com" not in where and "/connect?status=etsy_error" in where and "blocked" in where


#: Every file that opens an HTTP client, and why it is safe with the switch on.
#: A new file here means a new way out: guard it, then add it.
HTTP_CLIENTS = {
    "api/deps.py": "the token client's factory; used only by oauth.exchange_code (guarded)",
    "cli.py": "hands the client to EtsyApiClient (guarded)",
    "core/alerts.py": "the operator's own alert webhook, not Etsy",
    "etsy/connection.py": "the default factory for oauth.refresh_tokens (guarded)",
    "ops/draft_failures.py": "draft-failures --check-etsy: hands the client to EtsyApiClient, reads only (guarded)",
    "ops/publish_report.py": "publish-report --check-etsy: hands the client to EtsyApiClient, one getListing per listing (guarded)",
    "workers/etsy_spec.py": "the weekly read of Etsy's public OpenAPI document (no token, no quota); guard.check first",
    "ops/raw_listing.py": "etsy-raw-listing: hands the client to EtsyApiClient (cli._client_for_check), three GETs (guarded)",
    "pipeline/fetch_models.py": "build time only (docker/Dockerfile): the bundled mockup analysis models, not Etsy; nothing at runtime imports it",
    "workers/ledger.py": "EtsyApiClient (guarded)",
    "workers/links.py": "EtsyApiClient (guarded)",
    "workers/listing_stats.py": "EtsyApiClient (guarded)",
    "workers/profiles.py": "EtsyApiClient, and reference images (both guarded)",
    "workers/publish.py": "EtsyApiClient, and size chart images (both guarded)",
    "workers/replace.py": "EtsyApiClient, and the listing's images (both guarded)",
    "workers/sales.py": "EtsyApiClient (guarded)",
    "workers/sections.py": "EtsyApiClient (guarded)",
    "workers/skus.py": "EtsyApiClient (guarded)",
}


def test_no_other_code_opens_an_http_client() -> None:
    found = set()
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        clients = r"httpx\.AsyncClient\(|httpx\.Client\(|return httpx\.AsyncClient|or httpx\.AsyncClient"
        other_libraries = r"^\s*(import|from) (requests|aiohttp|urllib\.request|http\.client)\b"
        if re.search(f"{clients}|{other_libraries}", text, flags=re.M):
            found.add(path.relative_to(APP).as_posix())
    assert found == set(HTTP_CLIENTS), f"new or removed HTTP client: {sorted(found ^ set(HTTP_CLIENTS))}"
    # The model download runs only when the image is built: no app module imports it.
    for path in APP.rglob("*.py"):
        if path.name != "fetch_models.py":
            assert not re.search(r"^\s*(from|import) .*fetch_models", path.read_text(encoding="utf-8"), re.M), path
    from app.pipeline import fetch_models, garments, mockup_vision
    assert (mockup_vision.SEGMENTER, mockup_vision.LANDMARKER) == (fetch_models.SEGMENTER, fetch_models.LANDMARKER)
    assert garments.SALIENCY == fetch_models.SALIENCY
    # The image fetches (not API calls, but Etsy's image host) are refused as well.
    for name in ("workers/profiles.py", "workers/publish.py", "workers/replace.py"):
        text = (APP / name).read_text(encoding="utf-8")
        fetch = text.index("img_http.get(")
        assert "guard.check(" in text[max(0, fetch - 600): fetch], name


def test_local_development_runs_with_it_on_and_production_does_not() -> None:
    dev, prod = ROOT / "docker-compose.yml", ROOT / "docker-compose.prod.yml"
    if not dev.exists():
        pytest.skip("the compose files are not here (the dev container mounts only backend/)")
    text = dev.read_text(encoding="utf-8")
    for service in ("api", "worker"):
        block = text[text.index(f"\n  {service}:"):]
        block = block[: block.index("\n  ", block.index("depends_on:")) if "depends_on:" in block else len(block)]
        assert 'ETSY_CALLS_BLOCKED: "true"' in block, service
        assert 'UPLOAD_RETENTION_APPLY: "false"' in block, service
    assert "ETSY_CALLS_BLOCKED" not in prod.read_text(encoding="utf-8")
