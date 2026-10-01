"""ledger_sync: the backfill of 13 months of history

Revision ID: 0033_ledger_backfill
Revises: 0032_ledger
Create Date: 2026-10-01

Where the backwards read of the ledger stands: its target (the 13-month edge),
how far back the totals reach, and the slice and offset being read, so it
resumes exactly. Counts and times only.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_ledger_backfill"
down_revision: str | None = "0032_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ledger_sync", sa.Column("backfill_state", sa.Text(), nullable=False, server_default="none"))
    op.add_column("ledger_sync", sa.Column("backfill_target", sa.BigInteger()))
    op.add_column("ledger_sync", sa.Column("covered_from", sa.BigInteger()))
    op.add_column("ledger_sync", sa.Column("slice_start", sa.BigInteger()))
    op.add_column("ledger_sync", sa.Column("slice_offset", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ledger_sync", sa.Column("backfill_requests", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("ledger_sync", sa.Column("backfill_note", sa.Text()))


def downgrade() -> None:
    for name in ("backfill_note", "backfill_requests", "slice_offset", "slice_start", "covered_from",
                 "backfill_target", "backfill_state"):
        op.drop_column("ledger_sync", name)
