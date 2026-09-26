"""sales_daily, ad_spend and tenant.cost_settings: analytics and profit (v7 §C)

Revision ID: 0024_sales_and_costs
Revises: 0023_own_patterns
Create Date: 2026-09-26

sales_daily holds one derived row per listing per day (units, orders, revenue)
from the seller's own sales: no raw transactions and nothing about buyers.
ad_spend holds the seller's uploaded Etsy Ads CSV, per listing and period. Both
are kept 13 months and deleted with the shop. cost_settings holds the seller's
own fee rates and costs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_sales_and_costs"
down_revision: str | None = "0023_own_patterns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column("cost_settings", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_table(
        "sales_daily",
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("listing_id", sa.BigInteger(), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.Text()),
    )
    op.create_index("ix_sales_daily_tenant_day", "sales_daily", ["tenant_id", "day"])
    op.create_table(
        "ad_spend",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False),
        sa.Column("upload_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("spend_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ad_orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ad_revenue_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ad_spend_tenant_period", "ad_spend", ["tenant_id", "period_end"])
    op.create_index("ix_ad_spend_connection_id", "ad_spend", ["connection_id"])
    op.create_index("ix_ad_spend_upload_id", "ad_spend", ["upload_id"])


def downgrade() -> None:
    op.drop_table("ad_spend")
    op.drop_index("ix_sales_daily_tenant_day", table_name="sales_daily")
    op.drop_table("sales_daily")
    op.drop_column("tenant", "cost_settings")
