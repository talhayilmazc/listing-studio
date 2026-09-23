"""admin role, invite binding and revocation, audit log

Revision ID: 0014_admin
Revises: 0013_asset_byte_size
Create Date: 2026-09-23

An operator role on tenant (granted only by the CLI), invites that can be bound
to one email address and revoked before use, and an audit log of every admin
action. The audit log stores ids, never email addresses.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_admin"
down_revision: str | None = "0013_asset_byte_size"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenant",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.add_column("invite_code", sa.Column("bound_email", sa.Text(), nullable=True))
    op.add_column(
        "invite_code", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "invite_code",
        sa.Column(
            "created_by_tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenant.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "actor_tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenant.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "target_tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenant.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_invite_id",
            sa.Uuid(),
            sa.ForeignKey("invite_code.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "details",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index("ix_audit_log_actor_tenant_id", "audit_log", ["actor_tenant_id"])
    op.create_index("ix_audit_log_target_tenant_id", "audit_log", ["target_tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_target_tenant_id", table_name="audit_log")
    op.drop_index("ix_audit_log_actor_tenant_id", table_name="audit_log")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_column("invite_code", "created_by_tenant_id")
    op.drop_column("invite_code", "revoked_at")
    op.drop_column("invite_code", "bound_email")
    op.drop_column("tenant", "is_admin")
