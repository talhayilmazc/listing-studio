"""Migration 0081: design variants and photo colour groups add columns only; every existing row
keeps what it had (no variant, no colour, the shirt meaning); the downgrade removes them.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)


def test_existing_designs_and_photos_keep_what_they_had(scratch) -> None:  # noqa: F811
    _alembic("0080_shop_group_members")
    tenant, template, photo, batch, design, version = (uuid.uuid4() for _ in range(6))
    now = datetime.now(timezone.utc)
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "mockup_template", {"id": template, "tenant_id": tenant, "name": "Tees"})
        _insert(c, "mockup_photo", {"id": photo, "tenant_id": tenant, "template_id": template, "name": "p", "rights_confirmed_at": now})
        _insert(c, "upload_batch", {"id": batch, "tenant_id": tenant, "status": "ready"})
        _insert(c, "mockup_design", {"id": design, "tenant_id": tenant, "batch_id": batch, "group_key": "BR100",
                                     "original_filename": "BR100-white.png", "width": 900, "height": 900})
        _insert(c, "mockup_design_version", {"id": version, "tenant_id": tenant, "design_id": design, "subgroup": "Black",
                                             "original_filename": "BR100-black.png", "width": 900, "height": 900})
    _alembic("0081_design_variants")
    with scratch.begin() as c:
        assert c.execute(sa.text("SELECT colour, colour_source FROM mockup_photo")).one() == (None, None)
        assert c.execute(sa.text("SELECT variant, fill_other FROM mockup_design")).one() == (None, False)
        assert c.execute(sa.text("SELECT subgroup, variant FROM mockup_design_version")).one() == ("Black", None)
        assert c.execute(sa.text("SELECT variant_suffix FROM tenant")).scalar() == "shirt"
        # After the upgrade a variant file has no subgroup.
        _insert(c, "mockup_design_version", {"id": uuid.uuid4(), "tenant_id": tenant, "design_id": design, "subgroup": None,
                                             "variant": "dark", "original_filename": "BR100-dark.png", "width": 900, "height": 900})
    _alembic("0080_shop_group_members", down=True)
    with scratch.connect() as c:
        assert c.execute(sa.text("SELECT subgroup FROM mockup_design_version")).scalars().all() == ["Black"]
        cols = {r[0] for r in c.execute(sa.text("SELECT column_name FROM information_schema.columns WHERE table_name = 'mockup_photo'"))}
        assert "colour" not in cols
