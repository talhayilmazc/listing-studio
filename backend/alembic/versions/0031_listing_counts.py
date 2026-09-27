"""etsy_connection.listing_counts: how many listings a shop has in each state

Revision ID: 0031_listing_counts
Revises: 0030_allowance
Create Date: 2026-09-27

The shop sync now reads every listing state (active, draft, inactive, sold
out, expired) and up to 5,000 per state; it was 1,000 active plus drafts. The
counts Etsy reports are kept so the app can say what the shop holds.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0031_listing_counts"
down_revision: str | None = "0030_allowance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("etsy_connection", sa.Column("listing_counts", postgresql.JSONB()))
    op.add_column("etsy_connection", sa.Column("listing_counts_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("etsy_connection", "listing_counts_at")
    op.drop_column("etsy_connection", "listing_counts")
