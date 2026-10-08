"""python -m app.cli listing-stats-check: one read-only request, counts only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.cli import listing_stats_check
from app.db.models import ConnectionStatus, EtsyConnection, ListingPublication, Tenant


class Etsy:
    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = results
        self.requests: list[str] = []

    async def get_listings_by_listing_ids(self, ids: list[int], **_: Any) -> dict[str, Any]:
        self.requests.append(f"batch:{ids}")
        return {"results": self.results}

    async def get_listings_by_shop(self, shop_id: int, **kw: Any) -> dict[str, Any]:
        self.requests.append(f"shop:{shop_id}:{kw.get('state')}")
        return {"results": self.results}


class Tokens:
    async def get_valid_access_token(self, session: Any, connection: Any) -> str:
        return "token"


async def _shop(sm: async_sessionmaker, *, published: int = 0, shops: int = 1) -> tuple[str, list[uuid.UUID]]:
    email = f"{uuid.uuid4()}@e.com"
    async with sm() as s:
        tenant = Tenant(email=email, password_hash="x")
        s.add(tenant)
        await s.flush()
        ids = []
        for n in range(shops):
            c = EtsyConnection(tenant_id=tenant.id, status=ConnectionStatus.active, etsy_user_id=900 + n, shop_id=900 + n,
                               shop_name=f"Shop {n}")
            s.add(c)
            await s.flush()
            ids.append(c.id)
        for lid in range(published):
            s.add(ListingPublication(tenant_id=tenant.id, connection_id=ids[0], etsy_listing_id=lid + 1, state="active",
                                     published_at=datetime.now(UTC), title="Secret Title"))
        await s.commit()
    return email, ids


async def test_it_reads_the_published_listings_once_and_prints_counts_only(async_sm: async_sessionmaker) -> None:
    email, _ = await _shop(async_sm, published=3)
    etsy = Etsy([{"listing_id": 1, "title": "Secret Title", "views": 10, "num_favorers": 2},
                 {"listing_id": 2, "title": "Secret Title", "views": None, "num_favorers": 0},
                 {"listing_id": 3, "title": "Secret Title"}])
    out = await listing_stats_check(async_sm, email, client_factory=lambda *_: etsy, token_service=Tokens())
    assert len(etsy.requests) == 1 and etsy.requests[0].startswith("batch:")
    assert "Etsy requests made: 1" in out and "listings returned: 3" in out
    assert "views: present in 2 of 3, a number in 1, missing in 1" in out
    assert "num_favorers: present in 2 of 3, a number in 2, missing in 1" in out
    assert "Secret" not in out and "token" not in out


async def test_a_shop_with_nothing_published_reads_its_active_listings(async_sm: async_sessionmaker) -> None:
    email, ids = await _shop(async_sm)
    etsy = Etsy([{"listing_id": 7, "title": "Other"}])
    out = await listing_stats_check(async_sm, "900", client_factory=lambda *_: etsy, token_service=Tokens())
    assert etsy.requests == ["shop:900:active"]
    assert "views: present in 0 of 1" in out and "store them as unknown, never as zero" in out
    assert "Other" not in out
    out = await listing_stats_check(async_sm, str(ids[0]), client_factory=lambda *_: Etsy([]), token_service=Tokens())
    assert "listings returned: 0" in out


async def test_nothing_is_requested_when_the_shop_is_ambiguous_or_unknown(async_sm: async_sessionmaker) -> None:
    email, _ = await _shop(async_sm, shops=2)
    etsy = Etsy([])
    out = await listing_stats_check(async_sm, email, client_factory=lambda *_: etsy, token_service=Tokens())
    assert "has 2 connected shops" in out and "no request made" in out
    assert "no connected shop" in await listing_stats_check(async_sm, "nobody@e.com", client_factory=lambda *_: etsy,
                                                            token_service=Tokens())
    assert etsy.requests == []
