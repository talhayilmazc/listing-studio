"""Where a listing's size charts sit among its photos.

* ``listing_profile.size_chart_position``: "after_cover" | "third" | "last";
  "last" (what drafts always did) for every existing profile.
* ``listing_group_setting.chart_slots``: a group's own placement, dragged by the
  seller (one slot per chart); NULL follows the profile.

Revision ID: 0056_size_chart_position
Revises: 0055_written_from
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0056_size_chart_position"
down_revision: str | None = "0055_written_from"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "listing_profile",
        sa.Column("size_chart_position", sa.Text(), nullable=False, server_default="last"),
    )
    op.add_column(
        "listing_group_setting",
        sa.Column("chart_slots", sa.JSON().with_variant(postgresql.JSONB(), "postgresql")),
    )


def downgrade() -> None:
    op.drop_column("listing_group_setting", "chart_slots")
    op.drop_column("listing_profile", "size_chart_position")
