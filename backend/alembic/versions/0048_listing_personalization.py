"""Personalization per listing (v8 §D)

``generated_content.personalization``: the listing's own setting, sent in every
shop it goes to. NULL (every existing listing) follows its profile's, as before.

Revision ID: 0048_listing_personalization
Revises: 0047_group_distribution
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_listing_personalization"
down_revision: str | None = "0047_group_distribution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generated_content",
        sa.Column("personalization", sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generated_content", "personalization")
