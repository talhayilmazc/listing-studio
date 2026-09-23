"""job.paused_reason: why a queued job is waiting for the daily reset

Revision ID: 0015_job_paused_reason
Revises: 0014_admin
Create Date: 2026-09-23

New jobs pause at 90% of the app-wide Etsy budget, or when the tenant's own
allowance cannot cover them (production-spec C). The reason is kept on the job so
the seller is told why their work stopped instead of watching it time out.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_job_paused_reason"
down_revision: str | None = "0014_admin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("job", sa.Column("paused_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("job", "paused_reason")
