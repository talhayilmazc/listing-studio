"""Uploads are prepared by the worker (fix/perf-and-memory): ``asset.uploaded_at``.

The upload request now stores the file as it came and returns; the worker makes the processed copy. A photo
still waiting long after it arrived is queued again, so the row says when it arrived, with a partial index on
the waiting ones. NULL for every existing photo (all of them were prepared in the request). The downgrade drops
both.

Revision ID: 0102_asset_uploaded_at
Revises: 0101_psd_templates
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0102_asset_uploaded_at"
down_revision: str | None = "0101_psd_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("asset", sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_asset_waiting", "asset", ["uploaded_at"], postgresql_where=sa.text("status = 'uploaded'"))


def downgrade() -> None:
    op.drop_index("ix_asset_waiting", table_name="asset")
    op.drop_column("asset", "uploaded_at")
