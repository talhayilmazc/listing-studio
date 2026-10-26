"""Migration 0092 adds who published a draft (the seller in Shop Manager), when the app last read
its state on Etsy, and the listing a draft deleted on Etsy was made from. No row changes; the
downgrade drops the three columns and nothing else.

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no
Postgres server is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)
from tests.test_migration_0090 import _columns

NEW = {"published_by", "etsy_checked_at", "former_content_id"}


def test_the_three_columns_are_added_and_existing_drafts_stay(scratch) -> None:  # noqa: F811
    _alembic("0091_approval_record")
    tenant, shop, pub = (uuid.uuid4() for _ in range(3))
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "etsy_connection", {"id": shop, "tenant_id": tenant})
        _insert(c, "listing_publication", {"id": pub, "tenant_id": tenant, "connection_id": shop,
                                           "etsy_listing_id": 70001, "state": "draft"})
    _alembic("0092_etsy_reconcile")
    with scratch.connect() as c:
        assert NEW <= _columns(c, "listing_publication")
        row = c.execute(sa.text("SELECT state, published_by, etsy_checked_at, former_content_id FROM listing_publication")).one()
        assert tuple(row) == ("draft", None, None, None)
    _alembic("0091_approval_record", down=True)
    with scratch.connect() as c:
        assert not NEW & _columns(c, "listing_publication")
        assert c.execute(sa.text("SELECT state FROM listing_publication")).scalar_one() == "draft"
