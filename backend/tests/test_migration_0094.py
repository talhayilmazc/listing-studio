"""Migration 0094 fills a listing's empty title and tags from the text typed for one of its
shops (production, 2026-10-28: a hand-written listing's text saved as one shop's own showed
as empty everywhere else). Never overwrites; the shop it is on Etsy in first wins; each shop
keeps its own copy; downgrade empties only what was filled.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)


def test_empty_listing_text_is_filled_from_its_shop_and_nothing_is_overwritten(scratch) -> None:  # noqa: F811
    _alembic("0093_listing_shop_overrides")
    tenant, batch, a_shop, b_shop = (uuid.uuid4() for _ in range(4))
    assets = [uuid.uuid4() for _ in range(3)]
    empty, written, drafted_in_b = (uuid.uuid4() for _ in range(3))
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "upload_batch", {"id": batch, "tenant_id": tenant})
        for i, shop in enumerate((a_shop, b_shop)):
            _insert(c, "etsy_connection", {"id": shop, "tenant_id": tenant, "etsy_user_id": i + 1, "shop_id": i + 1,
                                           "position": i, "status": "active"})
        for asset in assets:
            _insert(c, "asset", {"id": asset, "tenant_id": tenant, "batch_id": batch, "original_filename": "a.png"})
        texts = {str(a_shop): {"title": "Typed for A", "tags": ["a one", "a two"]},
                 str(b_shop): {"title": "Typed for B", "tags": ["b one"]}}
        for cid, asset, title, tags in ((empty, assets[0], None, []), (written, assets[1], "Its own", ["own"]),
                                        (drafted_in_b, assets[2], "", [])):
            _insert(c, "generated_content", {"id": cid, "tenant_id": tenant, "batch_id": batch, "asset_id": asset,
                                             "title": title, "tags": tags, "source": "manual",
                                             "shop_overrides": json.dumps(texts)})
        _insert(c, "listing_publication", {"id": uuid.uuid4(), "tenant_id": tenant, "content_id": drafted_in_b,
                                           "connection_id": b_shop, "etsy_listing_id": 9, "state": "draft"})
    _alembic("0094_listing_text_from_shop")
    with scratch.connect() as c:
        rows = {r.id: (r.title, list(r.tags or []), r.shop_overrides) for r in c.execute(
            sa.text("SELECT id, title, tags, shop_overrides FROM generated_content"))}
        log = c.execute(sa.text("SELECT details FROM audit_log WHERE action = 'migration.listing_text_filled'")).scalar_one()
    assert rows[empty][:2] == ("Typed for A", ["a one", "a two"])  # first shop in the seller's order
    assert rows[written][:2] == ("Its own", ["own"])  # never overwritten
    assert rows[drafted_in_b][:2] == ("Typed for B", ["b one"])  # the shop it is on Etsy in wins
    assert rows[empty][2][str(b_shop)]["title"] == "Typed for B"  # each shop keeps its own copy
    assert sorted(i["content_id"] for i in log["filled"]) == sorted([str(empty), str(drafted_in_b)])
    assert "Typed" not in json.dumps(log)  # ids and field names only
    _alembic("0093_listing_shop_overrides", down=True)
    with scratch.connect() as c:
        rows = {r.id: (r.title, list(r.tags or [])) for r in c.execute(sa.text("SELECT id, title, tags FROM generated_content"))}
    assert rows[empty] == (None, []) and rows[drafted_in_b] == (None, []) and rows[written] == ("Its own", ["own"])
