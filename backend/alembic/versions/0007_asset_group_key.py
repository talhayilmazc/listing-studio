"""asset folder group key

Revision ID: 0007_asset_group_key
Revises: 0006_listing_profiles
Create Date: 2026-08-29

Section D: one uploaded folder = one listing. ``asset.group_key`` records which
folder an asset came from so its images are grouped into a single listing and the
SKU can be parsed from the folder name.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_asset_group_key"
down_revision: str | None = "0006_listing_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("asset", sa.Column("group_key", sa.Text(), nullable=True))
    op.create_index("ix_asset_group_key", "asset", ["group_key"])


def downgrade() -> None:
    op.drop_index("ix_asset_group_key", "asset")
    op.drop_column("asset", "group_key")
