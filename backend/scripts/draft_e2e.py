"""The app with the backend tests' fake Etsy answering from memory: the draft browser test's entry point.

    python scripts/draft_e2e.py setup                 schema, one synthetic account, two connected shops
    arq scripts.draft_e2e.WorkerSettings              the worker
    uvicorn scripts.draft_e2e:app                     the API

Only inside docker-compose.draft-e2e.yml (frontend/e2e/manualDraft.spec.ts): it refuses to load unless
ETSY_CALLS_BLOCKED is on and the database is that stack's own. Importing it:

* gives every ``httpx.AsyncClient`` in the process an in-memory transport: Etsy's API is answered by
  ``tests/browser_etsy.py::BrowserEtsy`` (the backend tests' fake, with what the profile form reads), and any
  other address is refused, so no request of any kind leaves the container;
* lets through the guard only the API client's own requests ("GET /path"), which that transport answers; a
  token exchange, a token refresh and an image fetch stay blocked, as on a development machine.

Every ``createDraftListing`` the fake is sent is kept in the stack's own Redis, and the API answers it at
``GET /api/__etsy/sent`` (this stack only), so the browser test can compare the request with the profile.
Synthetic: one made-up account and two shops, gone with the stack (no volumes).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.core.config import get_settings  # noqa: E402

_settings = get_settings()
if (not _settings.etsy_calls_blocked or _settings.app_env == "production"
        or not _settings.database_url.endswith("@postgres:5432/listyro_draft_e2e")):
    raise SystemExit("draft e2e: only in its own stack, with ETSY_CALLS_BLOCKED on: refusing to start")

EMAIL = "draft-e2e@example.com"
PASSWORD = "manual-draft-e2e-password"
SHOP_ID = 900
SHOP_NAME = "Pine Ridge Tees"
#: (Etsy user, Etsy shop id, name): made up.
SHOPS = [(4242, SHOP_ID, SHOP_NAME), (4343, 901, "Harbor Thread")]
SENT_KEY = "draft-e2e:sent"
VIDEOS_KEY = "draft-e2e:videos"

from app.etsy import guard  # noqa: E402

_real_check = guard.check


def _check(what: str = "") -> None:
    if what.split(" ", 1)[0] in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return  # the API client's own request: it goes to the transport below
    _real_check(what)


guard.check = _check

from tests.browser_etsy import BrowserEtsy  # noqa: E402


async def _keep(record: dict[str, Any]) -> None:
    from redis.asyncio import Redis

    redis = Redis.from_url(_settings.redis_url)
    try:
        await redis.rpush(SENT_KEY, json.dumps(record))
    finally:
        await redis.aclose()


async def _keep_video(record: dict[str, Any]) -> None:
    from redis.asyncio import Redis

    redis = Redis.from_url(_settings.redis_url)
    try:
        await redis.rpush(VIDEOS_KEY, json.dumps(record))
    finally:
        await redis.aclose()


etsy = BrowserEtsy(on_sent=_keep, on_video=_keep_video)


async def _handle(request: httpx.Request) -> httpx.Response:
    if "/v3/application" in request.url.path:
        return await etsy.handle(request)
    return httpx.Response(599, json={"error": "the draft e2e stack answers only Etsy's API, from memory"})


_transport = httpx.MockTransport(_handle)
_real_init = httpx.AsyncClient.__init__


def _init(self: httpx.AsyncClient, *args: Any, **kwargs: Any) -> None:
    kwargs["transport"] = _transport
    kwargs.pop("mounts", None)
    _real_init(self, *args, **kwargs)


httpx.AsyncClient.__init__ = _init  # type: ignore[method-assign]

from app.main import app as _api  # noqa: E402
from app.workers.settings import WorkerSettings as _Worker  # noqa: E402


async def app(scope: Any, receive: Any, send: Any) -> None:
    """The API, and one more address: what the fake Etsy was sent."""
    if scope.get("type") == "http" and scope.get("path") in ("/api/__etsy/sent", "/api/__etsy/videos") and scope.get("method") == "GET":
        from redis.asyncio import Redis

        videos = scope["path"].endswith("/videos")  # every uploadListingVideo the fake was sent (feature/listing-video)
        redis = Redis.from_url(_settings.redis_url)
        try:
            rows = [json.loads(r) for r in await redis.lrange(VIDEOS_KEY if videos else SENT_KEY, 0, -1)]
        finally:
            await redis.aclose()
        body = json.dumps({"videos" if videos else "sent": rows}).encode()
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json"), (b"cache-control", b"private, no-store")]})
        await send({"type": "http.response.body", "body": body})
        return
    await _api(scope, receive, send)


# arq reads a settings class's own attributes only, not inherited ones.
WorkerSettings = type("WorkerSettings", (), {k: v for k, v in vars(_Worker).items() if not k.startswith("__")})


async def _setup() -> None:
    from sqlalchemy import select

    from app.core.crypto import get_cipher
    from app.core.passwords import hash_password
    from app.core.terms import TERMS_VERSION
    from app.db.base import Base
    from app.db.models import EtsyConnection, Tenant
    from app.db.session import get_engine, get_sessionmaker
    from app.etsy.connection import ConnectionService
    from app.etsy.oauth import TokenResponse

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with get_sessionmaker()() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.email == EMAIL))).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(email=EMAIL, password_hash=hash_password(PASSWORD), terms_version=TERMS_VERSION,
                            terms_accepted_at=datetime.now(timezone.utc), time_zone="America/Chicago")
            s.add(tenant)
            await s.flush()
        shops = (await s.execute(select(EtsyConnection).where(EtsyConnection.tenant_id == tenant.id))).scalars().all()
        if not shops:
            service = ConnectionService(get_cipher(), client_id=_settings.etsy_client_id, token_url=_settings.etsy_oauth_token_url)
            # Two shops, as a seller with several has: the batch page then shows each shop's own rows and counts.
            # Tokens that outlive the stack: nothing here refreshes one (a refresh stays blocked).
            for position, (user, shop_id, name) in enumerate(SHOPS):
                shop = await service.save_from_tokens(
                    s, tenant.id, TokenResponse(access_token=f"{user}.draft-e2e-access", refresh_token=f"{user}.draft-e2e-refresh",
                                                expires_in=30 * 86400),
                    ["listings_r", "listings_w", "shops_r", "shops_w", "transactions_r"])
                shop.shop_id, shop.shop_name, shop.position = shop_id, name, position
        await s.commit()
    await get_engine().dispose()


if __name__ == "__main__":
    if sys.argv[1:] != ["setup"]:
        raise SystemExit("usage: python scripts/draft_e2e.py setup")
    asyncio.run(_setup())
    print("draft e2e: schema, one synthetic account and its two shops ready")
