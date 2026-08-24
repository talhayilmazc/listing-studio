"""add asset.error

Revision ID: 0003_asset_error
Revises: 0002_asset_rank
Create Date: 2026-08-23

Stores the last content-generation failure reason per asset so the UI can show
why generation failed. Safe text only (never tokens or secrets).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_asset_error"
down_revision: str | None = "0002_asset_rank"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("asset", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("asset", "error")
