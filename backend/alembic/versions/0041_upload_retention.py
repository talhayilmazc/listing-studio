"""asset: when its files were removed by upload retention, and the kept cover thumbnail

Revision ID: 0041_upload_retention
Revises: 0040_ai_call
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_upload_retention"
down_revision: str | None = "0040_ai_call"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("asset", sa.Column("files_removed_at", sa.DateTime(timezone=True)))
    op.add_column("asset", sa.Column("thumbnail_key", sa.Text()))
    # The daily sweep looks only at images whose files are still there.
    op.create_index(
        "ix_asset_files_kept", "asset", ["batch_id"], postgresql_where=sa.text("files_removed_at IS NULL")
    )


def downgrade() -> None:
    op.drop_index("ix_asset_files_kept", table_name="asset")
    op.drop_column("asset", "thumbnail_key")
    op.drop_column("asset", "files_removed_at")
