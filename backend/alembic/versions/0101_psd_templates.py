"""PSD templates (feature/psd-templates): ``mockup_photo.psd`` and ``psd_upload``.

``mockup_photo.psd`` holds what a photo made from a seller's Photoshop mockup needs to place a design (the smart
object's placement, the overlay layers, the mask, the notes); NULL for every existing photo, so nothing changes
for them. ``psd_upload`` tracks a PSD while it is received in chunks and converted. The downgrade drops both.

Revision ID: 0101_psd_templates
Revises: 0100_usage_ai_write
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0101_psd_templates"
down_revision: str | None = "0100_usage_ai_write"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mockup_photo", sa.Column("psd", postgresql.JSONB(), nullable=True))
    op.create_table(
        "psd_upload",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("received", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="receiving"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("choice", sa.Integer(), nullable=True),
        sa.Column("file_key", sa.Text(), nullable=True),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("mockup_template.id", ondelete="SET NULL"), nullable=True),
        sa.Column("photo_id", sa.Uuid(), sa.ForeignKey("mockup_photo.id", ondelete="SET NULL"), nullable=True),
        sa.Column("rights_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("took_seconds", sa.Float(), nullable=True),
        sa.Column("peak_mb", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_psd_upload_tenant_id", "psd_upload", ["tenant_id"])
    op.create_index("ix_psd_upload_template_id", "psd_upload", ["template_id"])
    op.create_index("ix_psd_upload_photo_id", "psd_upload", ["photo_id"])


def downgrade() -> None:
    op.drop_index("ix_psd_upload_photo_id", table_name="psd_upload")
    op.drop_index("ix_psd_upload_template_id", table_name="psd_upload")
    op.drop_index("ix_psd_upload_tenant_id", table_name="psd_upload")
    op.drop_table("psd_upload")
    op.drop_column("mockup_photo", "psd")
