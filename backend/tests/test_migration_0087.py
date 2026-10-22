"""Migration 0087 adds How it's made: two columns on a profile (the answers) and one on a
listing's publication (opened from "Finish in Etsy"). Existing rows start empty: no profile has
answers and nothing was opened. Downgrade removes the columns.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)


def test_how_made_columns_start_empty_and_go_on_downgrade(scratch) -> None:  # noqa: F811
    _alembic("0086_group_schedules")
    tenant, shop, profile = (uuid.uuid4() for _ in range(3))
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "etsy_connection", {"id": shop, "tenant_id": tenant, "etsy_user_id": 1, "shop_id": 1})
        _insert(c, "listing_profile", {"id": profile, "tenant_id": tenant, "connection_id": shop, "name": "Regular"})
        _insert(c, "listing_publication", {"id": uuid.uuid4(), "tenant_id": tenant, "connection_id": shop,
                                           "etsy_listing_id": 77, "state": "draft"})
    _alembic("0087_how_its_made")
    with scratch.connect() as c:
        assert c.execute(sa.text("SELECT how_made_production, how_made_tools FROM listing_profile")).one() == (None, None)
        assert c.execute(sa.text("SELECT how_made_opened_at FROM listing_publication")).scalar_one() is None
    _alembic("0086_group_schedules", down=True)
    with scratch.connect() as c:
        columns = {r[0] for r in c.execute(sa.text(
            "SELECT column_name FROM information_schema.columns WHERE table_name IN ('listing_profile', 'listing_publication')"))}
    assert not {"how_made_production", "how_made_tools", "how_made_opened_at"} & columns
