"""Migration 0073: every existing template gets one default subgroup named after it and its photos
belong to it; designs and their versions gain their subgroup columns; nothing is deleted, and the
downgrade removes only what 0073 added.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)


def test_each_template_gets_a_default_subgroup_holding_its_photos(scratch) -> None:  # noqa: F811
    _alembic("0072_mockup_photo_layout")
    tenant, cc, bella = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    photos = {uuid.uuid4(): cc, uuid.uuid4(): cc, uuid.uuid4(): bella}
    now = datetime.now(timezone.utc)
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "mockup_template", {"id": cc, "tenant_id": tenant, "name": "Comfort Colors"})
        _insert(c, "mockup_template", {"id": bella, "tenant_id": tenant, "name": "Bella"})
        for pid, tid in photos.items():
            _insert(c, "mockup_photo", {"id": pid, "tenant_id": tenant, "template_id": tid, "name": "p",
                                        "rights_confirmed_at": now})
    _alembic("0073_mockup_subgroups")
    with scratch.connect() as c:
        groups = {r[0]: r[1:] for r in c.execute(sa.text(
            "SELECT template_id, id, name, use_on, tenant_id FROM mockup_subgroup")).fetchall()}
        assert set(groups) == {cc, bella}  # exactly one each
        assert groups[cc][1:] == ("Comfort Colors", "any", tenant) and groups[bella][1] == "Bella"
        for pid, tid in photos.items():
            assert c.execute(sa.text("SELECT subgroup_id FROM mockup_photo WHERE id = :i"), {"i": pid}).scalar() == groups[tid][0]
        cols = {r[0] for r in c.execute(sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = 'mockup_design'"))}
        assert {"subgroup", "pair_base", "ink"} <= cols
    _alembic("0072_mockup_photo_layout", down=True)
    with scratch.connect() as c:
        cols = {r[0] for r in c.execute(sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = 'mockup_photo'"))}
        assert "subgroup_id" not in cols and c.execute(sa.text("SELECT count(*) FROM mockup_photo")).scalar() == 3
        assert c.execute(sa.text("SELECT to_regclass('mockup_subgroup')")).scalar() is None
