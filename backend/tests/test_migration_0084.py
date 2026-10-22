"""Migration 0084: the old review matrix's ticks become "Where and when" choices, exactly.

A batch whose listings all had the same ticks gets them as its own choice; otherwise each
listing with ticks keeps them as its exception; listings without ticks keep following the
default. Downgrade writes every listing's shops back as ticks.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)


def test_ticks_carry_over_as_the_batch_choice_or_each_listings_own(scratch) -> None:  # noqa: F811
    _alembic("0083_listing_shop_from_profile")
    tenant, opa, pcs = (uuid.uuid4() for _ in range(3))
    same_batch, mixed_batch = uuid.uuid4(), uuid.uuid4()
    s1, s2, m1, m2 = (uuid.uuid4() for _ in range(4))
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        for shop, n in ((opa, 1), (pcs, 2)):
            _insert(c, "etsy_connection", {"id": shop, "tenant_id": tenant, "etsy_user_id": n, "shop_id": n})
        for b in (same_batch, mixed_batch):
            _insert(c, "upload_batch", {"id": b, "tenant_id": tenant})
        rows = ((s1, same_batch, [str(opa), str(pcs)]), (s2, same_batch, [str(pcs), str(opa)]),
                (m1, mixed_batch, [str(pcs)]), (m2, mixed_batch, None))
        for cid, b, ticks in rows:
            aid = uuid.uuid4()
            _insert(c, "asset", {"id": aid, "tenant_id": tenant, "batch_id": b, "original_filename": "x.png"})
            _insert(c, "generated_content", {"id": cid, "tenant_id": tenant, "batch_id": b, "asset_id": aid})
            if ticks is not None:
                c.execute(sa.text("UPDATE generated_content SET target_shops = CAST(:t AS JSONB) WHERE id = :id"),
                          {"t": json.dumps(ticks), "id": cid})

    _alembic("0084_send_to")
    with scratch.connect() as c:
        batches = {r[0]: r[1] for r in c.execute(sa.text("SELECT id, send_to FROM upload_batch"))}
        contents = {r[0]: (r[1], r[2]) for r in c.execute(sa.text("SELECT id, send_to, target_shops FROM generated_content"))}
    # Same ticks on every listing: the batch's choice; the listings follow it.
    assert sorted(batches[same_batch]["connection_ids"]) == sorted([str(opa), str(pcs)])
    assert contents[s1] == (None, None) and contents[s2] == (None, None)
    # Different ticks: the listing keeps its own; the untouched one follows the default.
    assert batches[mixed_batch] is None
    assert contents[m1] == ({"group_ids": [], "connection_ids": [str(pcs)]}, None)
    assert contents[m2] == (None, None)

    _alembic("0083_listing_shop_from_profile", down=True)
    with scratch.connect() as c:
        back = {r[0]: r[1] for r in c.execute(sa.text("SELECT id, target_shops FROM generated_content"))}
    assert sorted(back[s1]) == sorted([str(opa), str(pcs)]) and sorted(back[s2]) == sorted([str(opa), str(pcs)])
    assert back[m1] == [str(pcs)] and back[m2] is None
