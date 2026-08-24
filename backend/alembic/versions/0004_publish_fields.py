"""add publish fields

Revision ID: 0004_publish_fields
Revises: 0003_asset_error
Create Date: 2026-08-23

Adds the columns the draft-publish flow needs: the app's reusable shop section id
on ``etsy_connection`` and the created draft listing id on ``generated_content``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_publish_fields"
down_revision: str | None = "0003_asset_error"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("etsy_connection", sa.Column("section_id", sa.BigInteger(), nullable=True))
    op.add_column("generated_content", sa.Column("etsy_listing_id", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("generated_content", "etsy_listing_id")
    op.drop_column("etsy_connection", "section_id")
