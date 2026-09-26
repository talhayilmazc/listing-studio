"""etsy_connection.sales_synced_at: when the shop's sales totals were last read (v7 §C5)

Revision ID: 0025_sales_synced_at
Revises: 0024_sales_and_costs
Create Date: 2026-09-26

Shown on the Analytics page ("sales read 2 hours ago"), and how the page knows
an on-demand read has finished.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_sales_synced_at"
down_revision: str | None = "0024_sales_and_costs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("etsy_connection", sa.Column("sales_synced_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("etsy_connection", "sales_synced_at")
