"""tenant.trademark_filter_seller: the seller's own trademark filter (v7 §A4)

Revision ID: 0029_seller_trademark_filter
Revises: 0028_sales_sync
Create Date: 2026-09-27

The seller turns the filter on or off in Settings (on by default; off needs
their explicit acceptance of the risk, which is audited). tenant.trademark_filter
stays as the admin override and wins while set.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_seller_trademark_filter"
down_revision: str | None = "0028_sales_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column("trademark_filter_seller", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("tenant", sa.Column("trademark_filter_changed_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("tenant", "trademark_filter_changed_at")
    op.drop_column("tenant", "trademark_filter_seller")
