"""tenant.time_zone: schedules are entered and shown in the seller's own zone

Revision ID: 0027_tenant_time_zone
Revises: 0026_ad_views
Create Date: 2026-09-26

An IANA name ("America/Chicago"), detected from the browser on first sign-in
and editable in Settings. A schedule's wall-clock time is converted to UTC once,
on save, with that zone's rules for the chosen date.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_tenant_time_zone"
down_revision: str | None = "0026_ad_views"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tenant", sa.Column("time_zone", sa.Text()))


def downgrade() -> None:
    op.drop_column("tenant", "time_zone")
