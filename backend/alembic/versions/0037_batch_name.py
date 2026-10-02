"""upload_batch.name: a batch can be named

Revision ID: 0037_batch_name
Revises: 0036_publication_history
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_batch_name"
down_revision: str | None = "0036_publication_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("upload_batch", sa.Column("name", sa.Text()))


def downgrade() -> None:
    op.drop_column("upload_batch", "name")
