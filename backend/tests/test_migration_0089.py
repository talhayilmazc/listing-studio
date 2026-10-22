"""Migration 0089: profile_merge keeps no Etsy content, loses orphans, and goes with its profile.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)


def test_etsy_content_is_removed_orphans_go_and_rows_follow_their_profile(scratch) -> None:  # noqa: F811
    _alembic("0088_fk_indexes")
    tenant, shop, kept, gone_profile = (uuid.uuid4() for _ in range(4))
    merged = {"id": str(uuid.uuid4()), "name": "Regular", "cached_payload": {"description": "Etsy text"},
              "refresh_error": "Etsy said no", "title_prefix": "CC"}
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "etsy_connection", {"id": shop, "tenant_id": tenant, "etsy_user_id": 1, "shop_id": 1})
        _insert(c, "listing_profile", {"id": kept, "tenant_id": tenant, "connection_id": shop, "name": "Regular"})
        for keep in (kept, gone_profile):
            c.execute(sa.text("INSERT INTO profile_merge (id, kept_profile_id, merged, repointed, link_id) "
                              "VALUES (:id, :keep, :merged, '{}', :link)"),
                      {"id": uuid.uuid4(), "keep": keep, "merged": json.dumps(merged), "link": uuid.uuid4()})
    _alembic("0089_profile_merge_scrub")
    with scratch.connect() as c:
        rows = c.execute(sa.text("SELECT kept_profile_id, merged FROM profile_merge")).all()
    assert [r[0] for r in rows] == [kept]  # the orphan went
    stored = json.loads(rows[0][1])
    assert "cached_payload" not in stored and "refresh_error" not in stored
    assert stored["title_prefix"] == "CC" and stored["name"] == "Regular"  # the downgrade's data stays
    with scratch.begin() as c:
        c.execute(sa.text("DELETE FROM listing_profile WHERE id = :id"), {"id": kept})
    with scratch.connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM profile_merge")).scalar_one() == 0
    _alembic("0088_fk_indexes", down=True)
