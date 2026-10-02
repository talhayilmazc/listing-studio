"""invite_request: requests from the public "Request an invite" form

Revision ID: 0039_invite_request
Revises: 0038_listing_style
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_invite_request"
down_revision: str | None = "0038_listing_style"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invite_request",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("shop", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by_tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="SET NULL")),
        sa.Column("invite_id", sa.Uuid(), sa.ForeignKey("invite_code.id", ondelete="SET NULL")),
    )
    op.create_index("ix_invite_request_status", "invite_request", ["status", "created_at"])
    op.create_index("ix_invite_request_email", "invite_request", ["email"])


def downgrade() -> None:
    op.drop_table("invite_request")
