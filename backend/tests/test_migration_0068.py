"""Migration 0068: colour sets become templates of many photos, nothing the seller made is
lost, and only photos whose print area the seller had set by hand count as confirmed.

Runs the real Alembic migrations against a scratch Postgres database (created and
dropped here, beside the development database; synthetic rows only). Skipped where no
Postgres server is reachable (the rest of the suite runs on SQLite).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.core.config import get_settings, set_settings_override

SCRATCH = "listyro_migration_0068_test"
BACKEND = Path(__file__).resolve().parents[1]


def _urls() -> tuple[str, str] | None:
    url = sa.engine.make_url(get_settings().database_url.replace("+asyncpg", "+psycopg"))
    if url.get_backend_name() != "postgresql":
        return None
    return url.render_as_string(hide_password=False), url.set(database=SCRATCH).render_as_string(hide_password=False)


@pytest.fixture()
def scratch():
    urls = _urls()
    if urls is None:
        pytest.skip("no Postgres configured")
    admin_url, scratch_url = urls
    try:
        admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 2})
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH}"'))
            c.execute(sa.text(f'CREATE DATABASE "{SCRATCH}"'))
    except Exception as exc:  # no server here
        pytest.skip(f"Postgres not reachable: {type(exc).__name__}")
    settings = get_settings()
    set_settings_override(settings.model_copy(update={"database_url": scratch_url.replace("+psycopg", "+asyncpg")}))
    engine = sa.create_engine(scratch_url)
    try:
        yield engine
    finally:
        engine.dispose()
        set_settings_override(settings)
        with admin.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH}"'))
        admin.dispose()


def _alembic(target: str, down: bool = False) -> None:
    from alembic import command
    from alembic.config import Config

    # No ini file: alembic/env.py would load its logging config from it, which switches off
    # every logger already made and leaves later tests that read the log with nothing.
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    (command.downgrade if down else command.upgrade)(cfg, target)


def _insert(c: sa.Connection, table: str, values: dict) -> None:
    """A row with ``values``; every other required column gets a plain value of its type."""
    cols = c.execute(sa.text(
        "SELECT column_name, data_type, udt_name FROM information_schema.columns WHERE table_name = :t "
        "AND is_nullable = 'NO' AND column_default IS NULL"
    ), {"t": table}).fetchall()
    row = dict(values)
    for name, kind, udt in cols:
        if name in row:
            continue
        if kind == "USER-DEFINED":  # an enum: its first value
            row[name] = c.execute(sa.text(
                "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = :u ORDER BY e.enumsortorder LIMIT 1"), {"u": udt}).scalar()
            continue
        row[name] = {"uuid": uuid.uuid4(), "integer": 0, "bigint": 0, "boolean": False, "text": f"x-{uuid.uuid4().hex[:8]}",
                     "jsonb": "{}", "timestamp with time zone": datetime.now(timezone.utc),
                     "double precision": 0.0}.get(kind, "x")
    names = ", ".join(row)
    params = ", ".join(f"CAST(:{k} AS JSONB)" if isinstance(v, str) and k in ("print_area", "garments", "analysis", "template_ids", "settings") else f":{k}"
                       for k, v in row.items())
    c.execute(sa.text(f"INSERT INTO {table} ({names}) VALUES ({params})"), row)


def test_sets_become_templates_loose_photos_get_their_own_and_only_hand_set_ones_are_confirmed(scratch) -> None:
    _alembic("0067_mockup_garments")
    tenant, colour_set = uuid.uuid4(), uuid.uuid4()
    white, black, loose, failed, run, batch, design, output = (uuid.uuid4() for _ in range(8))
    area = [[0.3, 0.2], [0.7, 0.2], [0.7, 0.6], [0.3, 0.6]]
    found = [[0.32, 0.22], [0.68, 0.22], [0.68, 0.58], [0.32, 0.58]]
    now = datetime.now(timezone.utc)
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "mockup_template_set", {"id": colour_set, "tenant_id": tenant, "name": "Comfort Colors", "position": 1})

        def photo(pid, name, position, set_id, source, status="ready"):
            _insert(c, "mockup_template", {
                "id": pid, "tenant_id": tenant, "name": name, "position": position, "set_id": set_id,
                "status": status, "print_area": json.dumps(area) if status == "ready" else None,
                "print_area_source": source, "analysis": json.dumps({"detected_area": found}),
                "rights_confirmed_at": now, "size": 0.9, "fold_strength": 0.5, "occlusion": True,
            })

        photo(white, "White", 2, colour_set, "seller")
        photo(black, "Black", 3, colour_set, "default")
        photo(loose, "Navy tee", 1, None, "default")
        photo(failed, "Broken", 4, None, "default", status="failed")
        _insert(c, "upload_batch", {"id": batch, "tenant_id": tenant})
        _insert(c, "mockup_design", {"id": design, "tenant_id": tenant, "batch_id": batch, "group_key": "BR1",
                                     "original_filename": "BR1.png", "width": 10, "height": 10})
        _insert(c, "mockup_run", {"id": run, "tenant_id": tenant, "batch_id": batch,
                                  "template_ids": json.dumps([str(white), str(loose)])})
        _insert(c, "mockup_output", {"id": output, "tenant_id": tenant, "run_id": run, "batch_id": batch,
                                     "design_id": design, "template_id": white, "template_name": "White",
                                     "position": 0, "filename": "BR1_White.jpg", "width": 10, "height": 10, "byte_size": 1})

    _alembic("0068_mockup_template_photos")
    with scratch.connect() as c:
        templates = {r.id: r for r in c.execute(sa.text("SELECT * FROM mockup_template ORDER BY position")).fetchall()}
        assert len(templates) == 3  # the set, and one for each photo that was in no set
        names = [t.name for t in templates.values()]
        assert names == ["Navy tee", "Comfort Colors", "Broken"]  # in the photos' order
        photos = {r.id: r for r in c.execute(sa.text("SELECT * FROM mockup_photo")).fetchall()}
        assert photos[white].template_id == photos[black].template_id == colour_set
        assert templates[photos[loose].template_id].name == "Navy tee"
        # Set by hand: confirmed. Still as found: waits for the seller's check. Failed: neither.
        assert photos[white].confirmed_at is not None
        assert photos[black].confirmed_at is None and photos[loose].confirmed_at is None
        assert photos[failed].confirmed_at is None and photos[failed].garments is None
        # Every ready photo keeps its areas in the list, with the suggestion.
        assert photos[black].garments == [{"print_area": area, "detected_area": found, "source": "default", "confidence": None}]
        run_row = c.execute(sa.text("SELECT template_ids, photo_ids, skipped FROM mockup_run")).one()
        assert run_row.photo_ids == [str(white), str(loose)]
        assert run_row.template_ids == [str(colour_set), str(photos[loose].template_id)] and run_row.skipped == 0
        out = c.execute(sa.text("SELECT photo_id, photo_name FROM mockup_output")).one()
        assert out.photo_id == white and out.photo_name is None
        # Deleting a template deletes its photos.
        fk = c.execute(sa.text(
            "SELECT confdeltype FROM pg_constraint WHERE conname = 'fk_mockup_photo_template_id'")).scalar()
        assert fk == "c"

    _alembic("0067_mockup_garments", down=True)
    with scratch.connect() as c:
        sets = c.execute(sa.text("SELECT id, name FROM mockup_template_set")).fetchall()
        assert [(s.id, s.name) for s in sets] == [(colour_set, "Comfort Colors")]
        rows = {r.id: r for r in c.execute(sa.text("SELECT id, set_id, garments FROM mockup_template")).fetchall()}
        assert rows[white].set_id == rows[black].set_id == colour_set
        assert rows[loose].set_id is None and rows[failed].set_id is None
        assert rows[black].garments is None
        assert c.execute(sa.text("SELECT template_ids FROM mockup_run")).scalar() == [str(white), str(loose)]
        assert c.execute(sa.text("SELECT template_id FROM mockup_output")).scalar() == white
