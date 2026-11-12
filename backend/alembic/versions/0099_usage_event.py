"""The listing counter: ``usage_event`` (fix/usage-and-admin-publishing, Part 1; core/usage.py).

One row per listing counted against the allowance, unique per (account, listing), written in the same
transaction as the listing's first successful Etsy draft. The upgrade fills it from the publication
records (``usage.BACKFILL``, word for word; tests/test_usage.py checks): each listing with a draft,
counted at its first draft's time. Nothing else changes; ``USAGE_COUNTER`` (default ``shadow``) decides
whether screens read it. The downgrade drops the table.

Revision ID: 0099_usage_event
Revises: 0098_onboarding
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0099_usage_event"
down_revision: str | None = "0098_onboarding"
branch_labels = None
depends_on = None

BACKFILL = """
SELECT DISTINCT ON (p.tenant_id, k.key)
       p.tenant_id, k.key, k.content_id, p.id, p.connection_id, p.created_at
FROM listing_publication p
CROSS JOIN LATERAL (
    SELECT COALESCE(CAST(COALESCE(p.content_id, p.former_content_id) AS TEXT), 'pub:' || CAST(p.id AS TEXT)) AS key,
           COALESCE(p.content_id, p.former_content_id) AS content_id
) k
ORDER BY p.tenant_id, k.key, p.created_at, p.id
"""


def upgrade() -> None:
    op.create_table(
        "usage_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("listing_key", sa.Text(), nullable=False),
        sa.Column("content_id", sa.Uuid(), nullable=True),
        sa.Column("publication_id", sa.Uuid(), nullable=True),
        sa.Column("connection_id", sa.Uuid(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="draft"),
        sa.UniqueConstraint("tenant_id", "listing_key", name="uq_usage_event_listing"),
    )
    op.create_index("ix_usage_event_tenant_at", "usage_event", ["tenant_id", "at"])
    op.execute(
        "INSERT INTO usage_event (id, tenant_id, listing_key, content_id, publication_id, connection_id, at, source) "
        "SELECT gen_random_uuid(), b.tenant_id, b.key, b.content_id, b.id, b.connection_id, b.created_at, 'backfill' "
        f"FROM ({BACKFILL}) AS b"
    )


def downgrade() -> None:
    op.drop_index("ix_usage_event_tenant_at", table_name="usage_event")
    op.drop_table("usage_event")
