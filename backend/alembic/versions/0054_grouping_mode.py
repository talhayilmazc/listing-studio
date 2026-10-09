"""How a batch's photos become listings: the seller's choice, and each photo's folder.

* ``upload_batch.grouping_mode``: "folder" | "sku" | "one" (pipeline/grouping.py);
  NULL: folders as folders, loose files by SKU.
* ``asset.upload_folder``: the folder a file was uploaded in, so the batch can be
  grouped again another way before anything is written. Existing rows: their
  group key, which was their folder.

Revision ID: 0054_grouping_mode
Revises: 0053_storage_fix
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0054_grouping_mode"
down_revision: str | None = "0053_storage_fix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("upload_batch", sa.Column("grouping_mode", sa.Text()))
    op.add_column("asset", sa.Column("upload_folder", sa.Text()))
    asset = sa.table("asset", sa.column("upload_folder", sa.Text()), sa.column("group_key", sa.Text()))
    op.execute(asset.update().values(upload_folder=asset.c.group_key))


def downgrade() -> None:
    op.drop_column("asset", "upload_folder")
    op.drop_column("upload_batch", "grouping_mode")
