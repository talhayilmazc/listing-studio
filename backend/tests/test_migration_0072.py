"""Migration 0072: each photo has its own layout; photos of templates whose "several garments in
one photo" switch was on become "Several garments" (set by the seller); the switch's column
stays, unread; nothing else changes.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)


def test_photos_of_switched_templates_become_several_garments(scratch) -> None:  # noqa: F811
    _alembic("0071_mockup_several_garments")
    tenant, on, off = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    photos = {uuid.uuid4(): on, uuid.uuid4(): on, uuid.uuid4(): off}
    now = datetime.now(timezone.utc)
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "mockup_template", {"id": on, "tenant_id": tenant, "name": "Rows of tees", "several_garments": True})
        _insert(c, "mockup_template", {"id": off, "tenant_id": tenant, "name": "One tee", "several_garments": False})
        for pid, tid in photos.items():
            _insert(c, "mockup_photo", {"id": pid, "tenant_id": tenant, "template_id": tid, "name": "p",
                                        "rights_confirmed_at": now})
    _alembic("0072_mockup_photo_layout")
    with scratch.connect() as c:
        rows = dict(c.execute(sa.text("SELECT template_id, array_agg(layout || '/' || layout_source) FROM mockup_photo GROUP BY template_id")).fetchall())
        assert sorted(rows[on]) == ["several/seller", "several/seller"]
        assert rows[off] == ["single/auto"]
        assert c.execute(sa.text("SELECT back_without_design FROM mockup_template WHERE id = :i"), {"i": off}).scalar() == "front"
        assert c.execute(sa.text("SELECT several_garments FROM mockup_template WHERE id = :i"), {"i": on}).scalar() is True
    _alembic("0071_mockup_several_garments", down=True)
    with scratch.connect() as c:
        cols = {r[0] for r in c.execute(sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = 'mockup_photo'"))}
        assert "layout" not in cols and c.execute(sa.text("SELECT count(*) FROM mockup_photo")).scalar() == 3
