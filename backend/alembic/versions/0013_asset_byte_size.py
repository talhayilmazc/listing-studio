"""asset byte size

Revision ID: 0013_asset_byte_size
Revises: 0012_auth
Create Date: 2026-09-23

Records each upload's size so a batch can be held to a total ceiling
(production-spec D). Nullable: rows from before this carry no size and count as
zero toward the ceiling.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_asset_byte_size"
down_revision: str | None = "0012_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("asset", sa.Column("byte_size", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("asset", "byte_size")
