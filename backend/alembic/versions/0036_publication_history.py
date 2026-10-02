"""listing_publication outlives its batch: content_id SET NULL, own title/sku/published_at

Revision ID: 0036_publication_history
Revises: 0035_group_shop
Create Date: 2026-10-03

Deleting a batch cascaded through generated_content to listing_publication, so
the record that a listing had been created and published went with the uploads.
The foreign key now sets content_id to NULL instead, and the row carries the
title, SKU and publish time itself. Existing rows are filled from their content.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_publication_history"
down_revision: str | None = "0035_group_shop"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK = "listing_publication_content_id_fkey"


def upgrade() -> None:
    op.add_column("listing_publication", sa.Column("title", sa.Text()))
    op.add_column("listing_publication", sa.Column("sku", sa.Text()))
    op.add_column("listing_publication", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE listing_publication p SET title = c.title, sku = a.parsed_sku "
        "FROM generated_content c JOIN asset a ON a.id = c.asset_id WHERE c.id = p.content_id"
    )
    # The best record there is of when an existing live listing was published.
    op.execute("UPDATE listing_publication SET published_at = updated_at WHERE state = 'active'")
    op.drop_constraint(FK, "listing_publication", type_="foreignkey")
    op.alter_column("listing_publication", "content_id", existing_type=sa.Uuid(), nullable=True)
    op.create_foreign_key(FK, "listing_publication", "generated_content", ["content_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint(FK, "listing_publication", type_="foreignkey")
    op.execute("DELETE FROM listing_publication WHERE content_id IS NULL")
    op.alter_column("listing_publication", "content_id", existing_type=sa.Uuid(), nullable=False)
    op.create_foreign_key(FK, "listing_publication", "generated_content", ["content_id"], ["id"], ondelete="CASCADE")
    op.drop_column("listing_publication", "published_at")
    op.drop_column("listing_publication", "sku")
    op.drop_column("listing_publication", "title")
