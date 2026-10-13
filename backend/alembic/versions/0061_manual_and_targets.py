"""Listings the seller writes themselves, and the shops chosen for each listing.

``generated_content.source`` / ``content_version.source``: "ai" (the app wrote the
title and tags) or "manual" (the seller did: no vision or content call was made,
so the version's ``vision_model`` and ``content_model`` are NULL because there
was none, not because it is unknown). Every existing row is "ai".

``generated_content.target_shops``: the shops ticked for the listing on the
review page (connection ids as text). NULL = the shop it was written for, which
is what every existing listing keeps. Nothing on Etsy changes.

Revision ID: 0061_manual_and_targets
Revises: 0060_profile_chart_slots
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0061_manual_and_targets"
down_revision: str | None = "0060_profile_chart_slots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generated_content", sa.Column("source", sa.Text(), nullable=False, server_default="ai"))
    op.add_column("generated_content", sa.Column("target_shops", postgresql.JSONB(), nullable=True))
    op.add_column("content_version", sa.Column("source", sa.Text(), nullable=False, server_default="ai"))


def downgrade() -> None:
    op.drop_column("content_version", "source")
    op.drop_column("generated_content", "target_shops")
    op.drop_column("generated_content", "source")
