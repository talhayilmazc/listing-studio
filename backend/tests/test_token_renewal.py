"""Etsy tokens in long and concurrent jobs (quality pass 2026-10, review R5 and R6).

* R6: a job reads its access token once; Etsy may refuse it partway through (it expired during a
  long job, or another job renewed it). The client then renews once and sends the request again
  (Etsy did not act on a 401), and the job's later requests use the new token. Without a token to
  renew, the 401 stands.
* R5: one refresh per shop at a time: a refresh that finds the token already renewed by another
  job uses it, and never spends the refresh token a second time.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.crypto import TokenCipher
from app.db.models import ConnectionStatus, EtsyConnection, Tenant
from app.etsy.api import EtsyApiClient
from app.etsy.connection import ConnectionService, TokenUnavailable
from app.etsy.errors import EtsyClientError


def _client(handler, renew=None) -> tuple[EtsyApiClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return EtsyApiClient(client_id="k", shared_secret="s", http_client=http, renew=renew), http


async def test_a_401_mid_job_renews_once_resends_and_keeps_the_new_token() -> None:
    sent: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        token = req.headers["authorization"]
        sent.append((req.method, token))
        return httpx.Response(401, json={"error": "expired"}) if token == "Bearer old" else httpx.Response(200, json={"listing_id": 7})

    renewals: list[str] = []

    async def renew(stale: str) -> str:
        renewals.append(stale)
        return "new"

    client, http = _client(handler, renew)
    async with http:
        assert (await client.create_draft_listing(55, listing={"title": "x"}, access_token="old"))["listing_id"] == 7
        # The job still passes the token it read at the start; the client uses the renewed one.
        await client.create_draft_listing(55, listing={"title": "y"}, access_token="old")
    assert renewals == ["old"]
    assert sent == [("POST", "Bearer old"), ("POST", "Bearer new"), ("POST", "Bearer new")]


async def test_without_a_token_to_renew_the_401_stands() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_token"})

    async def renew(stale: str) -> str:
        raise TokenUnavailable("the shop is not connected")

    client, http = _client(handler, renew)
    async with http:
        with pytest.raises(EtsyClientError):
            await client.get_listing(1, access_token="old")


async def test_a_second_401_after_renewing_is_not_renewed_again() -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.headers["authorization"])
        return httpx.Response(401, json={"error": "invalid_token"})

    async def renew(stale: str) -> str:
        return "new"

    client, http = _client(handler, renew)
    async with http:
        with pytest.raises(EtsyClientError):
            await client.get_listing(1, access_token="old")
    assert calls == ["Bearer old", "Bearer new"]


async def _shop(sm: async_sessionmaker, cipher: TokenCipher, *, access: str, expires_in: timedelta) -> uuid.UUID:
    async with sm() as s:
        tenant = Tenant(email=f"{uuid.uuid4()}@example.com", password_hash="x")
        s.add(tenant)
        await s.flush()
        shop = EtsyConnection(tenant_id=tenant.id, status=ConnectionStatus.active, etsy_user_id=1, shop_id=1,
                              access_token_enc=cipher.encrypt(access), refresh_token_enc=cipher.encrypt("1.ref"),
                              token_expires_at=datetime.now(timezone.utc) + expires_in)
        s.add(shop)
        await s.commit()
        return shop.id


def _service(cipher: TokenCipher, refreshes: list) -> ConnectionService:
    def factory() -> httpx.AsyncClient:
        def handler(req: httpx.Request) -> httpx.Response:
            refreshes.append(req)
            return httpx.Response(200, json={"access_token": "1.fresh", "refresh_token": "1.ref2", "expires_in": 3600})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ConnectionService(cipher, client_id="k", token_url="https://t", client_factory=factory)


async def test_renew_uses_a_token_another_job_renewed_meanwhile(async_sm: async_sessionmaker) -> None:
    cipher = TokenCipher(Fernet.generate_key())
    refreshes: list = []
    shop_id = await _shop(async_sm, cipher, access="1.renewed-by-another-job", expires_in=timedelta(hours=1))
    async with async_sm() as s:
        shop = await s.get(EtsyConnection, shop_id)
        assert await _service(cipher, refreshes).renew(s, shop, "1.stale") == "1.renewed-by-another-job"
    assert refreshes == []  # the refresh token was not spent again


async def test_renew_refreshes_when_the_refused_token_is_still_the_stored_one(async_sm: async_sessionmaker) -> None:
    cipher = TokenCipher(Fernet.generate_key())
    refreshes: list = []
    shop_id = await _shop(async_sm, cipher, access="1.stale", expires_in=timedelta(hours=1))
    async with async_sm() as s:
        shop = await s.get(EtsyConnection, shop_id)
        assert await _service(cipher, refreshes).renew(s, shop, "1.stale") == "1.fresh"
        assert cipher.decrypt(shop.refresh_token_enc) == "1.ref2"
    assert len(refreshes) == 1


async def test_an_expiring_token_refreshed_by_another_job_meanwhile_is_reused(async_sm: async_sessionmaker) -> None:
    """Two jobs see the token near expiry; the second, after the lock, finds the first's renewal."""
    cipher = TokenCipher(Fernet.generate_key())
    refreshes: list = []
    shop_id = await _shop(async_sm, cipher, access="1.near-expiry", expires_in=timedelta(seconds=10))
    service = _service(cipher, refreshes)
    async with async_sm() as first, async_sm() as second:
        a = await first.get(EtsyConnection, shop_id)
        b = await second.get(EtsyConnection, shop_id)  # read before the first job refreshed
        assert await service.get_valid_access_token(first, a) == "1.fresh"
        assert await service.get_valid_access_token(second, b) == "1.fresh"
    assert len(refreshes) == 1


async def test_a_disconnected_shop_has_no_token_to_renew(async_sm: async_sessionmaker) -> None:
    cipher = TokenCipher(Fernet.generate_key())
    shop_id = await _shop(async_sm, cipher, access="1.stale", expires_in=timedelta(hours=1))
    async with async_sm() as s:
        shop = await s.get(EtsyConnection, shop_id)
        shop.status, shop.refresh_token_enc = ConnectionStatus.revoked, None
        await s.commit()
        with pytest.raises(TokenUnavailable):
            await _service(cipher, []).renew(s, shop, "1.stale")
