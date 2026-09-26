"""ad_spend.ad_views: views from the seller's Etsy Ads report (v7 §C4)

Revision ID: 0026_ad_views
Revises: 0025_sales_synced_at
Create Date: 2026-09-26

A listing with no sale stays "New" until it has had a fair chance: 45 days
live, or 30 once it has had ad spend or ad views. The Open API has no view
counts, so they come from the report the seller uploads.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_ad_views"
down_revision: str | None = "0025_sales_synced_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ad_spend", sa.Column("ad_views", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("ad_spend", "ad_views")
