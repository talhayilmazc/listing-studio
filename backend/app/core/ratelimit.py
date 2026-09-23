"""Request ceilings and client-IP resolution (production-spec D).

Two tiers, both fixed windows in Redis keyed by client IP:

* **auth** — login, register and the admin endpoints: tight, because these are
  the endpoints worth guessing against. It sits on top of the login lockout
  (5 failures per email and per IP), not instead of it.
* **general** — everything else: a generous ceiling that no real page load
  approaches (a batches page with thumbnails is ~150 requests) but that stops a
  runaway script.

The limiter fails *open*: if Redis is unreachable the request proceeds. Refusing
every request because the counter store hiccuped would turn a Redis blip into a
full outage, and the auth lockout still applies wherever Redis is healthy.
"""

from __future__ import annotations

import logging
import time

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.api.deps import get_redis
from app.core.config import get_settings

logger = logging.getLogger(__name__)

AUTH_PATHS = ("/api/account/login", "/api/account/register")
AUTH_PREFIXES = ("/api/account/admin/",)
EXEMPT_PATHS = ("/health",)


def client_ip(request: Request) -> str:
    """The caller's IP, from the configured trusted header or the socket peer.

    ``X-Forwarded-For`` is deliberately *not* read: its first entry is whatever
    the client sent, so trusting it would let anyone mint a fresh IP per request
    and walk straight past the lockout. Behind Cloudflare, ``cf-connecting-ip``
    is overwritten at the edge and can be trusted — set CLIENT_IP_HEADER to it.
    """
    header = get_settings().client_ip_header.strip().lower()
    if header:
        value = request.headers.get(header, "").strip()
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _tier(path: str) -> str | None:
    if path in EXEMPT_PATHS:
        return None
    if path in AUTH_PATHS or path.startswith(AUTH_PREFIXES):
        return "auth"
    return "general"


async def rate_limit(request: Request, redis: Redis = Depends(get_redis)) -> None:
    """Global dependency: 429 once a client exceeds its tier's window."""
    tier = _tier(request.url.path)
    if tier is None:
        return

    settings = get_settings()
    if tier == "auth":
        limit, window = settings.auth_rate_limit_requests, settings.auth_rate_limit_window_seconds
    else:
        limit, window = settings.rate_limit_requests, settings.rate_limit_window_seconds

    now = int(time.time())
    bucket = now - now % window
    key = f"rl:{tier}:{client_ip(request)}:{bucket}"
    try:
        count = int(await redis.incr(key))
        if count == 1:
            await redis.expire(key, window)
    except (RedisError, OSError):
        logger.warning("rate limiter unavailable; allowing request")
        return

    if count > limit:
        raise HTTPException(
            status_code=429,
            detail="too many requests; slow down",
            headers={"Retry-After": str(max(1, bucket + window - now))},
        )
