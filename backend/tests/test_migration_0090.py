"""Migration 0090 drops listing_publication.how_made_opened_at ("Finish in Etsy" is gone); the
profile's How it's made answers stay. Downgrade adds the column back, empty.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)


def _columns(c: sa.Connection, table: str) -> set[str]:
    return {r[0] for r in c.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table})}


def test_the_watch_column_goes_and_the_answers_stay(scratch) -> None:  # noqa: F811
    _alembic("0089_profile_merge_scrub")
    tenant, shop, profile = (uuid.uuid4() for _ in range(3))
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "etsy_connection", {"id": shop, "tenant_id": tenant, "etsy_user_id": 1, "shop_id": 1})
        _insert(c, "listing_profile", {"id": profile, "tenant_id": tenant, "connection_id": shop, "name": "Regular",
                                       "how_made_production": "altered"})
        _insert(c, "listing_publication", {"id": uuid.uuid4(), "tenant_id": tenant, "connection_id": shop,
                                           "etsy_listing_id": 77, "state": "draft"})
    _alembic("0090_drop_how_made_opened")
    with scratch.connect() as c:
        assert "how_made_opened_at" not in _columns(c, "listing_publication")
        assert {"how_made_production", "how_made_tools"} <= _columns(c, "listing_profile")
        assert c.execute(sa.text("SELECT how_made_production FROM listing_profile")).scalar_one() == "altered"
        assert c.execute(sa.text("SELECT count(*) FROM listing_publication")).scalar_one() == 1
    _alembic("0089_profile_merge_scrub", down=True)
    with scratch.connect() as c:
        assert "how_made_opened_at" in _columns(c, "listing_publication")
