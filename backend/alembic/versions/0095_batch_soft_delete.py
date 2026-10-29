"""Deleting a batch hides it for 7 days with "Restore", then removes it for good.

``upload_batch.deleted_at``: set when the seller deletes the batch (only Listyro's records;
nothing on Etsy changes); NULL for every existing batch. pipeline/batch_delete.py removes a
batch deleted more than ``UploadBatch.RESTORE_DAYS`` days ago exactly as deleting did before.

Revision ID: 0095_batch_soft_delete
Revises: 0094_listing_text_from_shop
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0095_batch_soft_delete"
down_revision: str | None = "0094_listing_text_from_shop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("upload_batch", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_upload_batch_deleted_at", "upload_batch", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_upload_batch_deleted_at", table_name="upload_batch")
    op.drop_column("upload_batch", "deleted_at")
