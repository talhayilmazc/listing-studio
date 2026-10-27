"""Migration 0093 adds a listing's own profile and SKU per shop (``shop_overrides``, "Publish in
another shop"). Every existing listing gets NULL: it follows its own profile and SKU in every
shop, as before. The downgrade drops the column and nothing else.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)
from tests.test_migration_0090 import _columns


def test_the_column_is_added_and_existing_listings_follow_their_own(scratch) -> None:  # noqa: F811
    _alembic("0092_etsy_reconcile")
    tenant, batch, asset, content = (uuid.uuid4() for _ in range(4))
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "upload_batch", {"id": batch, "tenant_id": tenant})
        _insert(c, "asset", {"id": asset, "tenant_id": tenant, "batch_id": batch, "original_filename": "a.png"})
        _insert(c, "generated_content", {"id": content, "tenant_id": tenant, "batch_id": batch, "asset_id": asset,
                                         "approved": True})
    _alembic("0093_listing_shop_overrides")
    with scratch.connect() as c:
        assert "shop_overrides" in _columns(c, "generated_content")
        row = c.execute(sa.text("SELECT approved, shop_overrides FROM generated_content")).one()
        assert tuple(row) == (True, None)
    _alembic("0092_etsy_reconcile", down=True)
    with scratch.connect() as c:
        assert "shop_overrides" not in _columns(c, "generated_content")
        assert c.execute(sa.text("SELECT approved FROM generated_content")).scalar_one() is True
