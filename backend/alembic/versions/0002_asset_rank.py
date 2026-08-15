"""add asset.rank

Revision ID: 0002_asset_rank
Revises: 0001_initial
Create Date: 2026-08-15

Adds a nullable 1-based display order to ``asset`` (image rank within a batch),
introduced with the step-5 image pipeline.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_asset_rank"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("asset", sa.Column("rank", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("asset", "rank")
