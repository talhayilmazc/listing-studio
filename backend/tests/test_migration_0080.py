"""Migration 0080: every shop's group (``etsy_connection.group_id``) becomes a
``shop_group_member`` row, so each group keeps exactly its shops; the column goes.
Downgrade puts each shop back in one group (its first by position).

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)


def test_each_shops_group_becomes_a_membership_row_and_back(scratch) -> None:  # noqa: F811
    _alembic("0079_sales_history_from")
    tenant, a, b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    s1, s2, s3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "shop_group", {"id": a, "tenant_id": tenant, "name": "Group A", "position": 0})
        _insert(c, "shop_group", {"id": b, "tenant_id": tenant, "name": "Group B", "position": 1})
        for n, (sid, gid) in enumerate(((s1, a), (s2, b), (s3, None))):
            _insert(c, "etsy_connection", {"id": sid, "tenant_id": tenant, "etsy_user_id": 9100 + n, "status": "active",
                                           "group_id": gid})
    _alembic("0080_shop_group_members")
    with scratch.begin() as c:
        rows = set(c.execute(sa.text("SELECT group_id, connection_id FROM shop_group_member")).fetchall())
        assert rows == {(a, s1), (b, s2)}  # each group exactly its shops; a shop in no group has no row
        cols = {r[0] for r in c.execute(sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = 'etsy_connection'"))}
        assert "group_id" not in cols
        # After the upgrade a shop may be in both groups.
        c.execute(sa.text("INSERT INTO shop_group_member (group_id, connection_id) VALUES (:g, :s)"), {"g": a, "s": s2})
    _alembic("0079_sales_history_from", down=True)
    with scratch.connect() as c:
        back = dict(c.execute(sa.text("SELECT id, group_id FROM etsy_connection")).fetchall())
        assert back == {s1: a, s2: a, s3: None}  # the first of its groups by position
        assert c.execute(sa.text("SELECT to_regclass('shop_group_member')")).scalar() is None
