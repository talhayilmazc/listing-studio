"""Each size chart's own place, saved on the profile.

``listing_profile.size_chart_slots``: the charts in the order they appear, each
with the number of photos before it (-1 = after every photo); NULL = the
profile's preset (``size_chart_position``: after cover / 3rd / last), which is
what every existing profile keeps. Nothing on Etsy changes: it is read when a
draft is made and by "Replace images".

Revision ID: 0060_profile_chart_slots
Revises: 0059_version_models
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0060_profile_chart_slots"
down_revision: str | None = "0059_version_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("listing_profile", sa.Column("size_chart_slots", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("listing_profile", "size_chart_slots")
