"""The listing counter counts at the earlier of the first AI write and the first draft (core/usage.py).

0099 filled ``usage_event`` from the drafts only, so a listing our AI wrote and nobody sent counted 0.
This fills it with the new rule: every listing with a draft or written by our AI (``source = 'ai'``,
example batches never), at the earlier of the two (``usage.BACKFILL``, word for word;
tests/test_usage.py checks). A listing counted already keeps its row and takes the earlier time; a
draft's listing whose batch is gone (its own ``pub:`` key) is not added again when a row is linked to
that draft. Nothing is deleted. The downgrade removes the rows the old rule would not have: listings
never drafted (no shop on the row); a drafted listing keeps the earlier time.

Revision ID: 0100_usage_ai_write
Revises: 0099_usage_event
"""

from __future__ import annotations

from alembic import op

revision: str = "0100_usage_ai_write"
down_revision: str | None = "0099_usage_event"
branch_labels = None
depends_on = None

BACKFILL = """
WITH d AS (
    SELECT DISTINCT ON (p.tenant_id, k.key)
           p.tenant_id, k.key, k.content_id, p.id, p.connection_id, p.created_at
    FROM listing_publication p
    CROSS JOIN LATERAL (
        SELECT COALESCE(CAST(COALESCE(p.content_id, p.former_content_id) AS TEXT), 'pub:' || CAST(p.id AS TEXT)) AS key,
               COALESCE(p.content_id, p.former_content_id) AS content_id
    ) k
    ORDER BY p.tenant_id, k.key, p.created_at, p.id
), w AS (
    SELECT c.tenant_id, CAST(c.id AS TEXT) AS key, c.id AS content_id, c.created_at
    FROM generated_content c
    JOIN upload_batch b ON b.id = c.batch_id
    WHERE c.source = 'ai' AND NOT b.is_example
)
SELECT COALESCE(d.tenant_id, w.tenant_id) AS tenant_id, COALESCE(d.key, w.key) AS key,
       COALESCE(d.content_id, w.content_id) AS content_id, d.id AS publication_id, d.connection_id,
       LEAST(d.created_at, w.created_at) AS at
FROM d FULL OUTER JOIN w ON w.tenant_id = d.tenant_id AND w.key = d.key
"""


def upgrade() -> None:
    # Counted already: the earlier time (its AI write came before its first draft).
    op.execute(
        f"UPDATE usage_event u SET at = b.at FROM ({BACKFILL}) AS b "
        "WHERE u.tenant_id = b.tenant_id AND u.listing_key = b.key AND b.at < u.at"
    )
    # Not counted yet: written by our AI and never sent, or drafted and somehow missed.
    op.execute(
        "INSERT INTO usage_event (id, tenant_id, listing_key, content_id, publication_id, connection_id, at, source) "
        "SELECT gen_random_uuid(), b.tenant_id, b.key, b.content_id, b.publication_id, b.connection_id, b.at, 'backfill' "
        f"FROM ({BACKFILL}) AS b "
        "WHERE b.publication_id IS NULL OR NOT EXISTS ("
        "    SELECT 1 FROM usage_event u WHERE u.tenant_id = b.tenant_id AND u.publication_id = b.publication_id) "
        "ON CONFLICT (tenant_id, listing_key) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM usage_event WHERE connection_id IS NULL")
