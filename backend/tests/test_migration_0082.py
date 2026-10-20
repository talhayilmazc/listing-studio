"""Migration 0082: the shop setting map is built from what the profile links hold, so an
existing account's second reference listing can be filled from the first one's copy (the
worker's one-time job, tests/test_shop_setting_map.py). Links are not changed here.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)

PAYLOAD = {"shipping_profile_id": 501, "return_policy_id": 601,
           "inventory_products": [{"offerings": [{"readiness_state_id": 701}]}]}  # processing only on offerings


def test_the_map_is_built_from_the_links_and_the_latest_choice_wins(scratch) -> None:  # noqa: F811
    _alembic("0081_design_variants")
    tenant, opa, pcs, mugs = (uuid.uuid4() for _ in range(4))
    regular, second, third = (uuid.uuid4() for _ in range(3))
    now = datetime.now(timezone.utc)
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        for shop, n in ((opa, 1), (pcs, 2), (mugs, 3)):
            _insert(c, "etsy_connection", {"id": shop, "tenant_id": tenant, "etsy_user_id": n, "shop_id": n})
        for profile, ref in ((regular, 1), (second, 2), (third, 3)):
            _insert(c, "listing_profile", {"id": profile, "tenant_id": tenant, "connection_id": opa, "name": f"p{ref}",
                                           "reference_listing_id": ref})
            c.execute(sa.text("UPDATE listing_profile SET cached_payload = CAST(:p AS JSONB) WHERE id = :id"),
                      {"p": json.dumps(PAYLOAD), "id": profile})

        def link(profile, shop, ship, ret, ready, when, status="ready"):
            _insert(c, "profile_shop_link", {"id": uuid.uuid4(), "tenant_id": tenant, "profile_id": profile,
                                             "connection_id": shop, "shipping_profile_id": ship, "return_policy_id": ret,
                                             "readiness_state_id": ready, "status": status, "checked_at": when})

        # "Regular" in PCS: the copies made there. The second reference listing: "no match".
        link(regular, pcs, 9001, 9002, 9003, now)
        link(second, pcs, None, None, None, now, status="incomplete")
        # In Mugs two cards chose differently for the same shipping profile: the later wins.
        link(regular, mugs, 7001, None, None, now - timedelta(hours=2))
        link(third, mugs, 7002, None, None, now)
        link(regular, opa, None, None, None, now)  # the main shop's own link holds nothing
    _alembic("0082_shop_setting_map")
    with scratch.connect() as c:
        rows = c.execute(sa.text(
            "SELECT resource, source_connection_id, source_id, target_connection_id, target_id, how FROM shop_setting_map"
        )).all()
    assert sorted((r[0], r[2], r[3], r[4], r[5]) for r in rows) == sorted([
        ("shipping_profile", 501, pcs, 9001, "earlier choice"),
        ("return_policy", 601, pcs, 9002, "earlier choice"),
        ("readiness_state", 701, pcs, 9003, "earlier choice"),  # from the reference's offerings
        ("shipping_profile", 501, mugs, 7002, "earlier choice"),  # the most recently checked choice
    ])
    assert {r[1] for r in rows} == {opa}
    _alembic("0081_design_variants", down=True)
    with scratch.connect() as c:
        assert c.execute(sa.text("SELECT to_regclass('shop_setting_map')")).scalar() is None
        assert c.execute(sa.text("SELECT count(*) FROM profile_shop_link")).scalar() == 5  # untouched
