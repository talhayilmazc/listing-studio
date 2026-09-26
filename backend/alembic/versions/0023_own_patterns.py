"""tenant.features and listing_group_setting.pattern_listing_id (v7 §B)

Revision ID: 0023_own_patterns
Revises: 0022_profile_personalization
Create Date: 2026-09-26

An admin turns features on per account ({"own_patterns": true}). A listing
group can follow the title and tag pattern of one of the seller's OWN listings;
only its id is stored.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_own_patterns"
down_revision: str | None = "0022_profile_personalization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column("features", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column("listing_group_setting", sa.Column("pattern_listing_id", sa.BigInteger()))


def downgrade() -> None:
    op.drop_column("listing_group_setting", "pattern_listing_id")
    op.drop_column("tenant", "features")
