"""When a listing was approved, and by which path (the publish report; 2026-10-25).

``generated_content.approved_at`` and ``approved_via``: "card" (the listing's own approve
toggle) or "approve_all" (the review page's "Approve all"). Written from now on; listings
approved before stay empty and the report says "not recorded". Taking approval away clears
both. Nothing else changes; the downgrade drops the two columns.

Revision ID: 0091_approval_record
Revises: 0090_drop_how_made_opened
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0091_approval_record"
down_revision: str | None = "0090_drop_how_made_opened"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generated_content", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("generated_content", sa.Column("approved_via", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("generated_content", "approved_via")
    op.drop_column("generated_content", "approved_at")
