"""per-group profile selection

Revision ID: 0010_group_settings
Revises: 0009_apparel_default_and_replace
Create Date: 2026-09-01

Section E (v4): one upload can mix product types, so the metadata profile and the
size-chart profile are chosen per listing group, not just per batch.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_group_settings"
down_revision: str | None = "0009_apparel_default_and_replace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "listing_group_setting",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "batch_id",
            sa.Uuid(),
            sa.ForeignKey("upload_batch.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("group_key", sa.Text(), nullable=False),
        sa.Column(
            "profile_id", sa.Uuid(), sa.ForeignKey("listing_profile.id", ondelete="SET NULL")
        ),
        sa.Column(
            "size_chart_profile_id",
            sa.Uuid(),
            sa.ForeignKey("listing_profile.id", ondelete="SET NULL"),
        ),
        sa.Column("manual", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_group_setting_batch", "listing_group_setting", ["batch_id"])
    op.create_index(
        "ix_group_setting_batch_group",
        "listing_group_setting",
        ["batch_id", "group_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_group_setting_batch_group", "listing_group_setting")
    op.drop_index("ix_group_setting_batch", "listing_group_setting")
    op.drop_table("listing_group_setting")
