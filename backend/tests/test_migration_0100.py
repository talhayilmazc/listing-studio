"""Migration 0100: the listing counter counts at the earlier of the first AI write and the first draft.

A database at 0099 counted drafts only. After 0100: an AI-written listing never sent counts once, at its
write; a drafted AI-written listing keeps its row and takes the earlier time; a hand-written listing
counts only through its drafts; examples never; a draft whose listing is gone stays its own key. The
downgrade removes only the rows the old rule would not have (never drafted).

Real Alembic against a scratch Postgres database (synthetic rows only); skipped where no Postgres server
is reachable, like tests/test_migration_0068.py whose helpers it uses.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from tests.test_migration_0068 import _alembic, _insert, scratch  # noqa: F401  (fixture)


def test_ai_written_listings_count_at_the_earlier_event(scratch) -> None:  # noqa: F811
    _alembic("0098_onboarding")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    tenant, batch, example, shop = (uuid.uuid4() for _ in range(4))
    ai_unsent, ai_drafted, hand_drafted, hand_unsent, example_ai = (uuid.uuid4() for _ in range(5))
    drafted_pub, hand_pub, orphan_pub = (uuid.uuid4() for _ in range(3))
    with scratch.begin() as c:
        _insert(c, "tenant", {"id": tenant})
        _insert(c, "upload_batch", {"id": batch, "tenant_id": tenant, "is_example": False})
        _insert(c, "upload_batch", {"id": example, "tenant_id": tenant, "is_example": True})
        _insert(c, "etsy_connection", {"id": shop, "tenant_id": tenant, "etsy_user_id": 1, "shop_id": 1,
                                       "position": 0, "status": "active"})
        for cid, b, source, days in ((ai_unsent, batch, "ai", 5), (ai_drafted, batch, "ai", 5), (hand_drafted, batch, "manual", 6),
                                     (hand_unsent, batch, "manual", 6), (example_ai, example, "ai", 5)):
            asset = uuid.uuid4()
            _insert(c, "asset", {"id": asset, "tenant_id": tenant, "batch_id": b, "original_filename": "a.png"})
            _insert(c, "generated_content", {"id": cid, "tenant_id": tenant, "batch_id": b, "asset_id": asset,
                                             "title": "T", "tags": [], "source": source, "created_at": now - timedelta(days=days)})
        for pid, cid, n, days in ((drafted_pub, ai_drafted, 1, 3), (hand_pub, hand_drafted, 2, 2), (orphan_pub, None, 3, 4)):
            _insert(c, "listing_publication", {"id": pid, "tenant_id": tenant, "content_id": cid, "connection_id": shop,
                                               "etsy_listing_id": n, "state": "draft", "created_at": now - timedelta(days=days)})
    _alembic("0099_usage_event")
    _alembic("0100_usage_ai_write")

    def rows() -> dict[str, tuple]:
        with scratch.connect() as c:
            return {r.listing_key: (r.at, r.publication_id) for r in c.execute(
                sa.text("SELECT listing_key, at, publication_id FROM usage_event"))}

    after = rows()
    assert set(after) == {str(ai_unsent), str(ai_drafted), str(hand_drafted), f"pub:{orphan_pub}"}
    assert after[str(ai_unsent)] == (now - timedelta(days=5), None)  # written, never sent: counted at the write
    assert after[str(ai_drafted)] == (now - timedelta(days=5), drafted_pub)  # the earlier time, its draft kept
    assert after[str(hand_drafted)] == (now - timedelta(days=2), hand_pub)  # hand-written: at its first draft
    _alembic("0099_usage_event", down=True)
    assert set(rows()) == {str(ai_drafted), str(hand_drafted), f"pub:{orphan_pub}"}  # never drafted: gone
    _alembic("0100_usage_ai_write")
    assert rows() == after  # again: the same, nothing doubled
