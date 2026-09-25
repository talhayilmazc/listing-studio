"""tenant.trademark_filter: the trademark filter per account (v7 §A4)

Revision ID: 0021_tenant_trademark_filter
Revises: 0020_asset_cover_crop
Create Date: 2026-09-26

NULL follows TRADEMARK_FILTER; an admin can turn it on or off for one account.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_tenant_trademark_filter"
down_revision: str | None = "0020_asset_cover_crop"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("trademark_filter", sa.Boolean()))


def downgrade() -> None:
    op.drop_column("tenant", "trademark_filter")
