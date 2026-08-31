"""profile auto-detection fields

Revision ID: 0008_profile_detection
Revises: 0007_asset_group_key
Create Date: 2026-08-30

Section B refinements: profiles can be auto-detected from the seller's own shop
(``source`` + ``confirmed``), and the description title block is replaced up to the
first blank line rather than by a fixed line count (``title_replace_lines`` dropped).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_profile_detection"
down_revision: str | None = "0007_asset_group_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "listing_profile",
        sa.Column("source", sa.Text(), nullable=False, server_default="manual"),
    )
    op.add_column(
        "listing_profile",
        sa.Column(
            "confirmed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.drop_column("listing_profile", "title_replace_lines")


def downgrade() -> None:
    op.add_column(
        "listing_profile",
        sa.Column("title_replace_lines", sa.Integer(), nullable=False, server_default="1"),
    )
    op.drop_column("listing_profile", "confirmed")
    op.drop_column("listing_profile", "source")
