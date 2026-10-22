"""The database the migrations build is the database the models describe (quality pass 2026-10).

Real Alembic upgrades a scratch Postgres database to head; Alembic's own comparison then lists
every table, column and index the models have and the database lacks, or the other way round.
A model change without its migration (or a migration without its model change) fails here
instead of in production. Also: every foreign key column is indexed (review R10), and the
whole chain downgrades to 0063 and back up again cleanly.

Skipped where no Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.db.base import Base
import app.db.models  # noqa: F401  (every table on Base.metadata)
from tests.test_migration_0068 import _alembic, scratch  # noqa: F401  (fixture)

#: Kinds of difference checked: what exists, not how a type or default is spelled.
CHECKED = {"add_table", "remove_table", "add_column", "remove_column", "add_index", "remove_index"}


def _diffs(engine: sa.Engine) -> list[str]:
    with engine.connect() as c:
        ctx = MigrationContext.configure(c, opts={"compare_type": False, "compare_server_default": False})
        out = []
        for d in compare_metadata(ctx, Base.metadata):
            items = d if isinstance(d, list) else [d]
            for item in items:
                kind = item[0]
                if kind not in CHECKED:
                    continue
                obj = item[1] if kind != "add_column" and kind != "remove_column" else item[3]
                where = item[2] if kind in ("add_column", "remove_column") else ""
                name = getattr(obj, "name", obj)
                if kind in ("remove_table",) and name == "alembic_version":
                    continue
                out.append(f"{kind} {where}.{name}".replace(" .", " "))
        return sorted(out)


def test_head_schema_matches_the_models(scratch) -> None:  # noqa: F811
    _alembic("head")
    diffs = _diffs(scratch)
    assert diffs == [], "; ".join(diffs)


def test_every_foreign_key_column_is_indexed(scratch) -> None:  # noqa: F811
    _alembic("head")
    with scratch.connect() as c:
        missing = c.execute(sa.text(
            "SELECT c.conrelid::regclass || '.' || a.attname FROM pg_constraint c "
            "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
            "WHERE c.contype = 'f' AND NOT EXISTS (SELECT 1 FROM pg_index i "
            "WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1]) ORDER BY 1")).scalars().all()
    assert missing == []


def test_migrations_0063_to_head_go_down_and_up_again(scratch) -> None:  # noqa: F811
    _alembic("0063_allowance_design")
    _alembic("head")
    _alembic("0063_allowance_design", down=True)
    _alembic("head")
    diffs = _diffs(scratch)
    assert diffs == [], "; ".join(diffs)
