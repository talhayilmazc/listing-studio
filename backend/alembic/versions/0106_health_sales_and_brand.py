"""Listing health: reading sales from the page, and brand names the seller keeps (fix/health-sales-and-brand).

``sales_sync.queued_at``: the seller asked for the shop's sales from Listing health; the account's shops are
counted and read one after another. NULL for every existing row (nothing is queued). ``brand_kept``: "Keep it, I
know the risk" per shop, Etsy listing id and brand word. The downgrade drops both.

Revision ID: 0106_health_sales_and_brand
Revises: 0105_mockup_render_keys
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0106_health_sales_and_brand"
down_revision: str | None = "0105_mockup_render_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sales_sync", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "brand_kept",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("connection_id", "listing_id", "brand", name="uq_brand_kept_shop_listing_brand"),
    )
    op.create_index("ix_brand_kept_tenant_id", "brand_kept", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_brand_kept_tenant_id", table_name="brand_kept")
    op.drop_table("brand_kept")
    op.drop_column("sales_sync", "queued_at")
