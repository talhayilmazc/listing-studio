"""apparel default, batch size-chart profile, replace-images job

Revision ID: 0009_apparel_default_and_replace
Revises: 0008_profile_detection
Create Date: 2026-08-31

- Make ``apparel`` the default content_template and flip existing profiles that
  were wrongly left on ``digital_products``.
- Add ``upload_batch.size_chart_profile_id`` so size charts can come from a chosen
  profile (Task 4).
- Add the ``replace_images`` job type (update an existing listing in place, B4).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_apparel_default_and_replace"
down_revision: str | None = "0008_profile_detection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Apparel is now the default; opt in to digital_products explicitly.
    op.alter_column("listing_profile", "content_template", server_default="apparel")
    # Fix profiles that were left on the wrong template (apparel shop).
    op.execute(
        "UPDATE listing_profile SET content_template = 'apparel' "
        "WHERE content_template = 'digital_products'"
    )

    op.add_column(
        "upload_batch",
        sa.Column(
            "size_chart_profile_id",
            sa.Uuid(),
            sa.ForeignKey("listing_profile.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE job_type ADD VALUE IF NOT EXISTS 'replace_images'")


def downgrade() -> None:
    op.drop_column("upload_batch", "size_chart_profile_id")
    op.alter_column("listing_profile", "content_template", server_default="digital_products")
    # Enum value and the data backfill are intentionally left in place.
