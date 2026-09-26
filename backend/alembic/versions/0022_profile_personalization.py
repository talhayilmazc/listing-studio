"""listing_profile.personalization: the seller's personalization override (v7 §D4)

Revision ID: 0022_profile_personalization
Revises: 0021_tenant_trademark_filter
Create Date: 2026-09-26

NULL copies the reference listing's personalization question (read on refresh);
{"enabled": false} turns it off; otherwise the question to put on new drafts.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_profile_personalization"
down_revision: str | None = "0021_tenant_trademark_filter"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("listing_profile", sa.Column("personalization", postgresql.JSONB()))


def downgrade() -> None:
    op.drop_column("listing_profile", "personalization")
