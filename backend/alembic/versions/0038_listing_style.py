"""listing_profile: listing style (current / search guidance) and title length bounds

Revision ID: 0038_listing_style
Revises: 0037_batch_name
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_listing_style"
down_revision: str | None = "0037_batch_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "listing_profile", sa.Column("listing_style", sa.Text(), nullable=False, server_default="classic")
    )
    op.add_column("listing_profile", sa.Column("title_min_length", sa.Integer()))
    op.add_column("listing_profile", sa.Column("title_max_length", sa.Integer()))


def downgrade() -> None:
    op.drop_column("listing_profile", "title_max_length")
    op.drop_column("listing_profile", "title_min_length")
    op.drop_column("listing_profile", "listing_style")
