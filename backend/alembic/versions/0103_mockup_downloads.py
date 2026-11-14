"""Mockup downloads (feature/mockups-visible): the seller's download options, a ZIP's selection, options and
progress.

``tenant.mockup_download`` keeps the seller's last choice (format, size, quality, names, folders); NULL = the
defaults, which is every account today. ``mockup_zip`` gains ``output_ids`` (mockups chosen one by one; NULL =
every mockup of its designs, as every existing ZIP), ``options`` (NULL = the defaults) and ``total``/``done``
(progress; 0 for existing rows). The downgrade drops them.

Revision ID: 0103_mockup_downloads
Revises: 0102_asset_uploaded_at
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0103_mockup_downloads"
down_revision: str | None = "0102_asset_uploaded_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("mockup_download", sa.JSON(), nullable=True))
    op.add_column("mockup_zip", sa.Column("output_ids", postgresql.JSONB(), nullable=True))
    op.add_column("mockup_zip", sa.Column("options", sa.JSON(), nullable=True))
    op.add_column("mockup_zip", sa.Column("total", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("mockup_zip", sa.Column("done", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for column in ("done", "total", "options", "output_ids"):
        op.drop_column("mockup_zip", column)
    op.drop_column("tenant", "mockup_download")
