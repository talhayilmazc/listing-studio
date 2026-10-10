"""Occasion, Holiday and Section on the review card (pipeline/item_options.py).

* ``generated_content.item_options``: the seller's Occasion / Holiday and the
  Section per shop; NULL follows the writer and the profile.
* ``listing_profile.default_occasion`` / ``default_holiday``: the profile's
  defaults ("" = none, NULL = no default).
* ``etsy_connection.sections`` / ``sections_at``: the shop's own sections, kept
  24 hours (other Etsy content), deleted with the shop.
* ``content_version.section``: the section the draft was given.

Revision ID: 0057_item_options
Revises: 0056_size_chart_position
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0057_item_options"
down_revision: str | None = "0056_size_chart_position"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("generated_content", sa.Column("item_options", JSON))
    op.add_column("listing_profile", sa.Column("default_occasion", sa.Text()))
    op.add_column("listing_profile", sa.Column("default_holiday", sa.Text()))
    op.add_column("etsy_connection", sa.Column("sections", JSON))
    op.add_column("etsy_connection", sa.Column("sections_at", sa.DateTime(timezone=True)))
    op.add_column("content_version", sa.Column("section", sa.Text()))


def downgrade() -> None:
    op.drop_column("content_version", "section")
    op.drop_column("etsy_connection", "sections_at")
    op.drop_column("etsy_connection", "sections")
    op.drop_column("listing_profile", "default_holiday")
    op.drop_column("listing_profile", "default_occasion")
    op.drop_column("generated_content", "item_options")
