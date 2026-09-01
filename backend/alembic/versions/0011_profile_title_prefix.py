"""per-profile title prefix

Revision ID: 0011_profile_title_prefix
Revises: 0010_group_settings
Create Date: 2026-09-02

A fixed title prefix per profile (e.g. "COMFORT COLORS"), auto-filled from the
reference title's leading words and prepended to every generated title.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_profile_title_prefix"
down_revision: str | None = "0010_group_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("listing_profile", sa.Column("title_prefix", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("listing_profile", "title_prefix")
