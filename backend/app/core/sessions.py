"""Server-side sessions and auth throttling, in Redis (production-spec A2/A3).

Deliberately not JWT: a server-side session can be revoked, and the spec requires
that changing a password invalidates every session a tenant holds. That needs a
server-side record to delete.

Keys
  ``session:{token}``        -> hash of tenant_id / created_at / last_seen
  ``tenant-sessions:{id}``   -> set of that tenant's live tokens, for revoke-all
  ``login-fail:{scope}``     -> counter, for the 5-per-15-minutes lockout

Tokens are opaque 256-bit random strings. They are never logged, and only ever
travel in an HttpOnly cookie.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from redis.asyncio import Redis

SESSION_COOKIE = "session"
SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 days, rolling (A3)

# Failed-login lockout (A2): 5 attempts per 15 minutes, per email and per IP.
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60


def _session_key(token: str) -> str:
    return f"session:{token}"


def _tenant_key(tenant_id: uuid.UUID) -> str:
    return f"tenant-sessions:{tenant_id}"


def _fail_key(scope: str) -> str:
    return f"login-fail:{scope}"


@dataclass(frozen=True)
class SessionRecord:
    token: str
    tenant_id: uuid.UUID
    created_at: datetime
    last_seen: datetime


def _decode(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class SessionStore:
    """Session lifecycle. One instance per request is fine; state lives in Redis."""

    def __init__(self, redis: Redis, *, ttl_seconds: int = SESSION_TTL_SECONDS) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def create(self, tenant_id: uuid.UUID) -> str:
        """Open a session and return its token."""
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).isoformat()
        key = _session_key(token)
        await self._redis.hset(
            key,
            mapping={"tenant_id": str(tenant_id), "created_at": now, "last_seen": now},
        )
        await self._redis.expire(key, self._ttl)
        # Index by tenant so a password change can revoke every one of them.
        await self._redis.sadd(_tenant_key(tenant_id), token)
        await self._redis.expire(_tenant_key(tenant_id), self._ttl)
        return token

    async def read(self, token: str, *, touch: bool = True) -> SessionRecord | None:
        """Resolve a token, sliding its expiry (A3: rolling renewal)."""
        if not token:
            return None
        key = _session_key(token)
        data = await self._redis.hgetall(key)
        if not data:
            return None
        decoded = {_decode(k): _decode(v) for k, v in data.items()}
        try:
            tenant_id = uuid.UUID(decoded["tenant_id"])
        except (KeyError, ValueError):
            await self.destroy(token)
            return None

        now = datetime.now(timezone.utc)
        if touch:
            await self._redis.hset(key, "last_seen", now.isoformat())
            await self._redis.expire(key, self._ttl)
            await self._redis.expire(_tenant_key(tenant_id), self._ttl)

        return SessionRecord(
            token=token,
            tenant_id=tenant_id,
            created_at=_parse(decoded.get("created_at"), now),
            last_seen=now if touch else _parse(decoded.get("last_seen"), now),
        )

    async def destroy(self, token: str) -> None:
        """End one session (logout)."""
        if not token:
            return
        data = await self._redis.hgetall(_session_key(token))
        await self._redis.delete(_session_key(token))
        if data:
            decoded = {_decode(k): _decode(v) for k, v in data.items()}
            raw = decoded.get("tenant_id")
            if raw:
                try:
                    await self._redis.srem(_tenant_key(uuid.UUID(raw)), token)
                except ValueError:
                    pass

    async def destroy_all(self, tenant_id: uuid.UUID) -> int:
        """End every session this tenant holds (A3: on password change)."""
        key = _tenant_key(tenant_id)
        tokens = await self._redis.smembers(key)
        count = 0
        for raw in tokens:
            await self._redis.delete(_session_key(_decode(raw)))
            count += 1
        await self._redis.delete(key)
        return count

    # --- failed-login throttle ---------------------------------------------
    async def register_failure(self, scope: str) -> int:
        """Count one failed attempt for ``scope``; returns the running total."""
        key = _fail_key(scope)
        count = int(await self._redis.incr(key))
        if count == 1:
            await self._redis.expire(key, LOGIN_WINDOW_SECONDS)
        return count

    async def is_locked(self, scope: str) -> bool:
        raw = await self._redis.get(_fail_key(scope))
        return raw is not None and int(_decode(raw)) >= LOGIN_MAX_ATTEMPTS

    async def clear_failures(self, scope: str) -> None:
        await self._redis.delete(_fail_key(scope))

    async def retry_after(self, scope: str) -> int:
        """Seconds until ``scope`` unlocks (0 when it is not locked)."""
        ttl = await self._redis.ttl(_fail_key(scope))
        return max(0, int(ttl)) if ttl and int(ttl) > 0 else 0


def _parse(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return fallback
