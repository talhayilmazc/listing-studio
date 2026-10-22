"""Migrations 0085 and 0086 add columns only: per-shop profile settings, a shop's daily limit,
a group's schedule and pause, and "Pause all". Existing rows keep their values: nothing is
paused, no group has settings, no shop a limit. Downgrade removes the columns.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)


def test_new_columns_start_empty_and_go_on_downgrade(scratch) -> None:  # noqa: F811
    _alembic("0084_send_to")
    tenant, shop, group, profile = (uuid.uuid4() for _ in range(4))
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "etsy_connection", {"id": shop, "tenant_id": tenant, "etsy_user_id": 1, "shop_id": 1})
        _insert(c, "shop_group", {"id": group, "tenant_id": tenant, "name": "A"})
        _insert(c, "listing_profile", {"id": profile, "tenant_id": tenant, "connection_id": shop, "name": "Regular"})
    _alembic("0086_group_schedules")
    with scratch.connect() as c:
        assert c.execute(sa.text("SELECT schedules_paused FROM tenant")).scalar_one() is False
        assert c.execute(sa.text("SELECT schedule FROM shop_group")).scalar_one() is None
        assert c.execute(sa.text("SELECT daily_limit FROM etsy_connection")).scalar_one() is None
        assert c.execute(sa.text("SELECT shop_settings FROM listing_profile")).scalar_one() is None
    _alembic("0084_send_to", down=True)
    with scratch.connect() as c:
        columns = {r[0] for r in c.execute(sa.text(
            "SELECT column_name FROM information_schema.columns WHERE table_name IN ('tenant', 'shop_group', 'etsy_connection', 'listing_profile')"))}
    assert not {"schedules_paused", "schedule", "daily_limit", "shop_settings"} & columns
