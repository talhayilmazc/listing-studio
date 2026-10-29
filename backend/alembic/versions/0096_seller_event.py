"""``seller_event``: what a seller met on a page, without personal data (api/events.py).

Each send summary's result (counts per reason a listing won't go) and each frontend error or
failed action (page path, action, short message with e-mail addresses removed). Kept 30 days;
deleted with the account. Read by ``python -m app.cli seller-events``.

Revision ID: 0096_seller_event
Revises: 0095_batch_soft_delete
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0096_seller_event"
down_revision: str | None = "0095_batch_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "seller_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("page", sa.Text()),
        sa.Column("action", sa.Text()),
        sa.Column("message", sa.Text()),
        sa.Column("counts", postgresql.JSONB()),
    )
    op.create_index("ix_seller_event_tenant_id", "seller_event", ["tenant_id"])
    op.create_index("ix_seller_event_at", "seller_event", ["at"])
    op.create_index("ix_seller_event_tenant_at", "seller_event", ["tenant_id", "at"])


def downgrade() -> None:
    op.drop_table("seller_event")
