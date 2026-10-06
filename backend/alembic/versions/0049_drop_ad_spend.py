"""Drop ad_spend: the old per-listing Etsy Ads import (v7 §C1)

Nothing has written it since the shop-level Ads import replaced it, migration
0043 deleted its rows, and nothing reads it any more. Etsy's Ads report is a
daily total for the whole shop (``ads_daily``) and is never given to listings.
The downgrade recreates the empty table as it was.

Revision ID: 0049_drop_ad_spend
Revises: 0048_listing_personalization
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_drop_ad_spend"
down_revision: str | None = "0048_listing_personalization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    left = op.get_bind().execute(sa.text("SELECT count(*) FROM ad_spend")).scalar()
    print(f"  ad_spend: {left} row(s) dropped with the table")
    op.drop_table("ad_spend")


def downgrade() -> None:
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
        sa.Column("ad_views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ad_spend_tenant_period", "ad_spend", ["tenant_id", "period_end"])
    op.create_index("ix_ad_spend_connection_id", "ad_spend", ["connection_id"])
    op.create_index("ix_ad_spend_upload_id", "ad_spend", ["upload_id"])
