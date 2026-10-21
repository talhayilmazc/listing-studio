"""Migration 0083: a listing's shop comes from its profile (B3). A written listing whose old
default shop (its group's, else its batch's) differs from its profile's main shop names that
shop explicitly, so it goes where it went before; the others keep following the default.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)


def test_a_listing_keeps_the_shop_it_went_to_and_the_rest_follow_their_profile(scratch) -> None:  # noqa: F811
    _alembic("0082_shop_setting_map")
    tenant, opa, pcs = (uuid.uuid4() for _ in range(3))
    profile, batch, other_batch = (uuid.uuid4() for _ in range(3))
    moved, same, chosen, by_group = (uuid.uuid4() for _ in range(4))
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        for shop, n in ((opa, 1), (pcs, 2)):
            _insert(c, "etsy_connection", {"id": shop, "tenant_id": tenant, "etsy_user_id": n, "shop_id": n})
        _insert(c, "listing_profile", {"id": profile, "tenant_id": tenant, "connection_id": opa, "name": "Regular"})
        _insert(c, "upload_batch", {"id": batch, "tenant_id": tenant, "connection_id": pcs})  # the batch was "for PCS"
        _insert(c, "upload_batch", {"id": other_batch, "tenant_id": tenant, "connection_id": opa})
        _insert(c, "listing_group_setting", {"id": uuid.uuid4(), "tenant_id": tenant, "batch_id": other_batch,
                                             "group_key": "G", "connection_id": pcs})  # a group set to PCS
        for cid, b, key, targets in ((moved, batch, "A", None), (chosen, batch, "B", [str(opa)]),
                                     (same, other_batch, "C", None), (by_group, other_batch, "G", None)):
            aid = uuid.uuid4()
            _insert(c, "asset", {"id": aid, "tenant_id": tenant, "batch_id": b, "group_key": key, "original_filename": f"{key}.png"})
            _insert(c, "generated_content", {"id": cid, "tenant_id": tenant, "batch_id": b, "asset_id": aid,
                                             "listing_profile_id": profile})
            if targets is not None:
                c.execute(sa.text("UPDATE generated_content SET target_shops = CAST(:t AS JSONB) WHERE id = :id"),
                          {"t": json.dumps(targets), "id": cid})

    def targets() -> dict:
        with scratch.connect() as c:
            return {r[0]: r[1] for r in c.execute(sa.text("SELECT id, target_shops FROM generated_content"))}

    _alembic("0083_listing_shop_from_profile")
    after = targets()
    assert after[moved] == [str(pcs)]  # its batch was PCS's: it still goes to PCS
    assert after[by_group] == [str(pcs)]  # its group was set to PCS
    assert after[same] is None  # its old default was its profile's main shop: follows the default
    assert after[chosen] == [str(opa)]  # the seller's own choice is untouched

    _alembic("0082_shop_setting_map", down=True)
    back = targets()
    assert back[moved] is None and back[by_group] is None and back[chosen] == [str(opa)] and back[same] is None
