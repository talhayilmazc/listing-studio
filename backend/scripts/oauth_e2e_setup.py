"""Setup for the shop-connection browser test stack (docker-compose.oauth-e2e.yml). Never elsewhere.

1. Refuses to go on unless every Etsy URL points at the stub (etsy-stub.test, oauth-stub) and the
   database is the stack's own: ETSY_CALLS_BLOCKED is off in that stack only because its "Etsy" is
   the stub, so a misconfigured run must not reach Etsy or another database.
2. Creates the schema and two synthetic accounts (a@example.com, b@example.com; password
   "connect-a-shop-e2e"), terms accepted, time zone set.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings  # noqa: E402

STUB_HOSTS = {"etsy-stub.test", "oauth-stub"}
PASSWORD = "connect-a-shop-e2e"


def _check() -> None:
    s = get_settings()
    for name in ("etsy_oauth_authorize_url", "etsy_oauth_token_url"):
        host = urlparse(getattr(s, name)).hostname
        if host not in STUB_HOSTS:
            raise SystemExit(f"oauth e2e: {name} points at {host}, not the stub: refusing to start")
    if not s.database_url.endswith("@postgres:5432/listyro_oauth_e2e") or s.app_env == "production":
        raise SystemExit("oauth e2e: not the stack's own database: refusing to start")


async def _seed() -> None:
    from sqlalchemy import select

    from app.core.passwords import hash_password
    from app.core.terms import TERMS_VERSION
    from app.db.base import Base
    from app.db.models import Tenant
    from app.db.session import get_engine, get_sessionmaker

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with get_sessionmaker()() as s:
        for email in ("a@example.com", "b@example.com"):
            if (await s.execute(select(Tenant).where(Tenant.email == email))).scalar_one_or_none() is None:
                s.add(Tenant(email=email, password_hash=hash_password(PASSWORD), terms_version=TERMS_VERSION,
                             terms_accepted_at=datetime.now(timezone.utc), time_zone="Europe/Istanbul"))
        await s.commit()
    await get_engine().dispose()


if __name__ == "__main__":
    _check()
    asyncio.run(_seed())
    print("oauth e2e: schema and two synthetic accounts ready")
