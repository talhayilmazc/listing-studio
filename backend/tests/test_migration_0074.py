"""Migration 0074: designs fill the print area. Photos still at the old default size (0.9)
move to 1.0, any other size the seller chose stays; design files get a trims column.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)


def test_photos_at_the_old_default_fill_and_chosen_sizes_stay(scratch) -> None:  # noqa: F811
    _alembic("0073_mockup_subgroups")
    tenant, template = uuid.uuid4(), uuid.uuid4()
    default, chosen = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "mockup_template", {"id": template, "tenant_id": tenant, "name": "Tees"})
        for pid, size in ((default, 0.9), (chosen, 0.6)):
            _insert(c, "mockup_photo", {"id": pid, "tenant_id": tenant, "template_id": template, "name": "p",
                                        "rights_confirmed_at": now, "size": size})
    _alembic("0074_mockup_fill")
    with scratch.connect() as c:
        sizes = dict(c.execute(sa.text("SELECT id, size FROM mockup_photo")).fetchall())
        assert sizes[default] == 1.0 and abs(sizes[chosen] - 0.6) < 1e-9
        cols = {r[0] for r in c.execute(sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = 'mockup_design'"))}
        assert "trims" in cols
    _alembic("0073_mockup_subgroups", down=True)
    with scratch.connect() as c:
        cols = {r[0] for r in c.execute(sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = 'mockup_design'"))}
        assert "trims" not in cols and c.execute(sa.text("SELECT count(*) FROM mockup_photo")).scalar() == 2
