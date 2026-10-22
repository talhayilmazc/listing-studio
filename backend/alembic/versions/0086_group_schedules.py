"""Schedules per shop group, and pausing them (B8: the Scheduled page).

``shop_group.schedule`` = ``{"per_shop_per_day", "window_start", "window_end",
"spacing_minutes", "stagger_minutes", "paused"}``: the group's schedule settings (minutes
between listings in a shop, listings a day, the daily window) and whether its shops'
go-lives are paused. ``tenant.schedules_paused``: every go-live of the account paused.
NULL / false: as before (nothing paused; no group settings). Existing schedules, plans and
their times are untouched.

Revision ID: 0086_group_schedules
Revises: 0085_profile_shop_settings
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0086_group_schedules"
down_revision: str | None = "0085_profile_shop_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shop_group", sa.Column("schedule", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True))
    op.add_column("tenant", sa.Column("schedules_paused", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("tenant", "schedules_paused")
    op.drop_column("shop_group", "schedule")
