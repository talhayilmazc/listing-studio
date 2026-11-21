"""Profiles made and edited by hand (feature/manual-profiles).

``listing_profile.manual``: the profile has no reference listing: its settings are the seller's own, typed into
the profile form (false for every existing row). ``own_values``: for a manual profile, every value of the form;
for a profile that follows a reference listing, only the fields the seller changed ("your value": a Refresh
never overwrites them). ``default_tags``: tags every listing written with the profile starts with. All NULL for
existing rows: an existing profile is exactly as it was. Additive; the downgrade drops the three columns.

(Numbered 0108: 0107 is taken by a branch that is not merged yet; both follow 0106.)

Revision ID: 0108_manual_profiles
Revises: 0106_health_sales_and_brand
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0108_manual_profiles"
down_revision: str | None = "0106_health_sales_and_brand"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("listing_profile", sa.Column("manual", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("listing_profile", sa.Column("own_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("listing_profile", sa.Column("default_tags", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("listing_profile", "default_tags")
    op.drop_column("listing_profile", "own_values")
    op.drop_column("listing_profile", "manual")
