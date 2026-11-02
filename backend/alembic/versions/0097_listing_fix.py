"""``listing_fix``: a fix to a live listing, before and after, kept for Undo (api/listing_health.py).

Holds the listing's previous title, tags and attributes: Etsy content, so it is on a clock. A preview nobody
approved goes after 6 hours (the age limit for listing content), a fix after its 7 days of Undo, and all of a
shop's go at once when it disconnects. Adds a table and nothing else.

Revision ID: 0097_listing_fix
Revises: 0096_seller_event
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0097_listing_fix"
down_revision: str | None = "0096_seller_event"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "listing_fix",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=False),
        sa.Column("kinds", postgresql.JSONB(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("after", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("design", sa.Text()),
        sa.Column("allowance_kind", sa.Text()),
        sa.Column("approval_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("written_at", sa.DateTime(timezone=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("undo_until", sa.DateTime(timezone=True)),
        sa.Column("undone_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_listing_fix_tenant_id", "listing_fix", ["tenant_id"])
    op.create_index("ix_listing_fix_connection_id", "listing_fix", ["connection_id"])
    op.create_index("ix_listing_fix_tenant_listing", "listing_fix", ["tenant_id", "listing_id"])
    op.create_index("ix_listing_fix_created_at", "listing_fix", ["created_at"])


def downgrade() -> None:
    op.drop_table("listing_fix")
