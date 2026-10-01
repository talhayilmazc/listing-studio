"""draft_attempt: creating a draft is resumable

Revision ID: 0034_draft_attempt
Revises: 0033_ledger_backfill
Create Date: 2026-10-02

One row per content and shop while its draft is being created: the Etsy listing
id as soon as Etsy returns it, so a retry carries on with that listing instead
of creating a second draft. Deleted when the draft is finished.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_draft_attempt"
down_revision: str | None = "0033_ledger_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "draft_attempt",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_id", sa.Uuid(), sa.ForeignKey("generated_content.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", sa.Uuid(), sa.ForeignKey("etsy_connection.id", ondelete="CASCADE"), nullable=False),
        sa.Column("etsy_listing_id", sa.BigInteger()),
        sa.Column("create_sent_at", sa.DateTime(timezone=True)),
        sa.Column("title", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("content_id", "connection_id", name="uq_draft_attempt_content_shop"),
    )
    op.create_index("ix_draft_attempt_connection_id", "draft_attempt", ["connection_id"])


def downgrade() -> None:
    op.drop_index("ix_draft_attempt_connection_id", table_name="draft_attempt")
    op.drop_table("draft_attempt")
