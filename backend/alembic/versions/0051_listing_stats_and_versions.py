"""Listing views/favourites per day and published content versions (Part D).

Revision ID: 0051_listing_stats_and_versions
Revises: 0050_short_title_default
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051_listing_stats_and_versions"
down_revision: str | None = "0050_short_title_default"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "listing_stat_daily",
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("listing_id", sa.BigInteger(), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("views_total", sa.Integer()),
        sa.Column("favorites_total", sa.Integer()),
        sa.Column("views", sa.Integer()),
        sa.Column("favorites", sa.Integer()),
    )
    op.create_index("ix_listing_stat_daily_tenant_day", "listing_stat_daily", ["tenant_id", "day"])
    op.create_table(
        "content_version",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False),
        sa.Column("publication_id", sa.Uuid(), sa.ForeignKey("listing_publication.id", ondelete="CASCADE"), nullable=False),
        sa.Column("etsy_listing_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("tags", JSONB),
        sa.Column("description", sa.Text()),
        sa.Column("attributes", JSONB),
        sa.Column("title_style", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("active_from", sa.DateTime(timezone=True)),
        sa.Column("active_to", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_content_version_publication", "content_version", ["publication_id", "active_from"])


def downgrade() -> None:
    op.drop_index("ix_content_version_publication", table_name="content_version")
    op.drop_table("content_version")
    op.drop_index("ix_listing_stat_daily_tenant_day", table_name="listing_stat_daily")
    op.drop_table("listing_stat_daily")
