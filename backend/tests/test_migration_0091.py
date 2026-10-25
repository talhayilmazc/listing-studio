"""Migration 0091 adds when a listing was approved and by which path; listings approved before
keep their approval and say "not recorded". Downgrade drops the two columns, nothing else.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)
from tests.test_migration_0090 import _columns


def test_approval_time_and_path_are_added_and_old_approvals_stay(scratch) -> None:  # noqa: F811
    _alembic("0090_drop_how_made_opened")
    tenant, batch, asset, content = (uuid.uuid4() for _ in range(4))
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "upload_batch", {"id": batch, "tenant_id": tenant})
        _insert(c, "asset", {"id": asset, "tenant_id": tenant, "batch_id": batch, "original_filename": "a.png"})
        _insert(c, "generated_content", {"id": content, "tenant_id": tenant, "batch_id": batch, "asset_id": asset,
                                         "approved": True})
    _alembic("0091_approval_record")
    with scratch.connect() as c:
        assert {"approved_at", "approved_via"} <= _columns(c, "generated_content")
        row = c.execute(sa.text("SELECT approved, approved_at, approved_via FROM generated_content")).one()
        assert tuple(row) == (True, None, None)
    _alembic("0090_drop_how_made_opened", down=True)
    with scratch.connect() as c:
        assert not {"approved_at", "approved_via"} & _columns(c, "generated_content")
        assert c.execute(sa.text("SELECT approved FROM generated_content")).scalar_one() is True
