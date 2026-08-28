"""add listing state

Revision ID: 0005_listing_state
Revises: 0004_publish_fields
Create Date: 2026-08-28

Records whether a published listing is still a DRAFT or has been made ACTIVE by
the seller, so the UI can link a draft to Shop Manager (which is editable) rather
than the public URL (which 404s for drafts). Null == never published.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_listing_state"
down_revision: str | None = "0004_publish_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generated_content", sa.Column("etsy_listing_state", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("generated_content", "etsy_listing_state")
