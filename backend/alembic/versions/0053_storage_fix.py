"""Storage fix: originals are no longer kept; per-account storage cap.

* ``asset.storage_key`` becomes nullable: an upload stores only its processed
  copy from now on, and ``python -m app.cli storage-originals --apply`` deletes
  the originals already stored and clears their keys.
* ``tenant.storage_cap_bytes``: an admin's per-account cap; NULL follows the
  default (``STORAGE_CAP_GB``, 5 GB).

Downgrade: the column goes back to NOT NULL only where every row has a key; a
key that was cleared is filled with where the original was (the file is gone).

Revision ID: 0053_storage_fix
Revises: 0052_api_usage_categories
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0053_storage_fix"
down_revision: str | None = "0052_api_usage_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("asset") as batch:
        batch.alter_column("storage_key", existing_type=sa.Text(), nullable=True)
    op.add_column("tenant", sa.Column("storage_cap_bytes", sa.BigInteger()))


def downgrade() -> None:
    op.drop_column("tenant", "storage_cap_bytes")
    asset = sa.table("asset", sa.column("storage_key", sa.Text()), sa.column("tenant_id", sa.Uuid()),
                     sa.column("batch_id", sa.Uuid()), sa.column("id", sa.Uuid()))
    op.execute(
        asset.update().where(asset.c.storage_key.is_(None)).values(
            storage_key=sa.func.concat(sa.cast(asset.c.tenant_id, sa.Text()), "/", sa.cast(asset.c.batch_id, sa.Text()),
                                       "/original/", sa.cast(asset.c.id, sa.Text()))
        )
    )
    with op.batch_alter_table("asset") as batch:
        batch.alter_column("storage_key", existing_type=sa.Text(), nullable=False)
