"""Which design an allowance unit was for, so a rewrite can be told from a first write.

``allowance_use.design``: the design a listing's text was written for (its batch
and group, or the Etsy listing when the batch is gone). Rewriting the same design
is free for its first three rewrites in the allowance period: those are recorded
with ``kind = 'rewrite'`` and are not counted; the fourth and later are
``'generation'`` like a first write (core/allowance.py).

Rows from before this have no design; they stay as they are and still count.
Nothing is deleted or rewritten by this migration.

Revision ID: 0063_allowance_design
Revises: 0062_commercial_readiness
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0063_allowance_design"
down_revision: str | None = "0062_commercial_readiness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("allowance_use", sa.Column("design", sa.Text(), nullable=True))
    op.create_index("ix_allowance_use_design", "allowance_use", ["tenant_id", "design"])


def downgrade() -> None:
    op.drop_index("ix_allowance_use_design", table_name="allowance_use")
    op.drop_column("allowance_use", "design")
