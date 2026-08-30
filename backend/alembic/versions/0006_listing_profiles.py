"""reference-listing profiles

Revision ID: 0006_listing_profiles
Revises: 0005_listing_state
Create Date: 2026-08-28

Section B: the app copies listing metadata from the seller's own existing listings
instead of inventing it. Adds ``listing_profile`` (the copied reference config),
``shop_listing_cache`` (the dashboard's cached own-shop listings, 6h Member Content),
``generated_content.listing_profile_id``, and the new job types.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_listing_profiles"
down_revision: str | None = "0005_listing_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_JOB_TYPES = ("refresh_profile", "sync_shop_listings", "publish_live")


def upgrade() -> None:
    op.create_table(
        "listing_profile",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("reference_listing_id", sa.BigInteger(), nullable=False),
        sa.Column("cached_payload", postgresql.JSONB(), nullable=True),
        sa.Column("fixed_image_ids", postgresql.JSONB(), nullable=True),
        sa.Column("title_replace_lines", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "content_template", sa.Text(), nullable=False, server_default="digital_products"
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_listing_profile_tenant", "listing_profile", ["tenant_id"])

    op.create_table(
        "shop_listing_cache",
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenant.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("listing_id", sa.BigInteger(), primary_key=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.add_column(
        "generated_content",
        sa.Column(
            "listing_profile_id",
            sa.Uuid(),
            sa.ForeignKey("listing_profile.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_generated_content_listing_profile_id",
        "generated_content",
        ["listing_profile_id"],
    )

    # New job_type enum values (PG native enum). ADD VALUE must run outside the
    # migration's transaction; a no-op on SQLite (which has no enum type).
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for value in _NEW_JOB_TYPES:
                op.execute(f"ALTER TYPE job_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    op.drop_index("ix_generated_content_listing_profile_id", "generated_content")
    op.drop_column("generated_content", "listing_profile_id")
    op.drop_table("shop_listing_cache")
    op.drop_index("ix_listing_profile_tenant", "listing_profile")
    op.drop_table("listing_profile")
    # Enum values are intentionally left in place: PostgreSQL cannot drop a single
    # enum value without recreating the type.
