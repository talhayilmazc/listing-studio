"""invite codes and forced password change

Revision ID: 0012_auth
Revises: 0011_profile_title_prefix
Create Date: 2026-09-20

Invite-only registration (production-spec A1) and the flag that forces a tenant
to replace an admin-issued temporary password before using the API (A4).

Invite codes are stored hashed, never in clear text: the database is the least
trustworthy place to keep a credential that grants account creation.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_auth"
down_revision: str | None = "0011_profile_title_prefix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "invite_code",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "used_by_tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenant.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_invite_code_used_by_tenant_id", "invite_code", ["used_by_tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_invite_code_used_by_tenant_id", table_name="invite_code")
    op.drop_table("invite_code")
    op.drop_column("tenant", "must_change_password")
