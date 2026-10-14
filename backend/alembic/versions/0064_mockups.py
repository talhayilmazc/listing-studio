"""Mockups: blank garment templates, the designs printed on them, and each run.

* ``mockup_template``: a seller's blank garment photo (their own upload, kept
  until they delete it or their account), its garment mask, print area (four
  corners as shares of the photo), size and fold strength, and when the seller
  confirmed they may use the photo.
* ``mockup_design``: a design uploaded to a batch to be printed on templates;
  one listing group per design. Removed with the batch.
* ``mockup_run``: one "make the mockups" for a batch, with its progress.
* ``asset.mockup_design_id`` / ``asset.mockup_template_id``: set on a photo the
  app made, so it is known which design and template it came from.

No Etsy content is stored here. Nothing existing is changed or deleted.

Revision ID: 0064_mockups
Revises: 0063_allowance_design
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0064_mockups"
down_revision: str | None = "0063_allowance_design"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "mockup_template",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("image_key", sa.Text(), nullable=False),
        sa.Column("mask_key", sa.Text(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("mask_source", sa.Text(), nullable=False),
        sa.Column("print_area", JSONB, nullable=False),
        sa.Column("print_area_source", sa.Text(), nullable=False, server_default="default"),
        sa.Column("size", sa.Float(), nullable=False, server_default="0.9"),
        sa.Column("fold_strength", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("rights_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mockup_template_tenant_id", "mockup_template", ["tenant_id"])
    op.create_table(
        "mockup_design",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("upload_batch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("group_key", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("parsed_sku", sa.Text(), nullable=True),
        sa.Column("design_key", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("has_alpha", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("files_removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mockup_design_batch_id", "mockup_design", ["batch_id"])
    op.create_table(
        "mockup_run",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("upload_batch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_ids", JSONB, nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mockup_run_batch_id", "mockup_run", ["batch_id"])
    op.add_column(
        "asset",
        sa.Column("mockup_design_id", sa.Uuid(), sa.ForeignKey("mockup_design.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "asset",
        sa.Column("mockup_template_id", sa.Uuid(), sa.ForeignKey("mockup_template.id", ondelete="SET NULL"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("asset", "mockup_template_id")
    op.drop_column("asset", "mockup_design_id")
    op.drop_index("ix_mockup_run_batch_id", table_name="mockup_run")
    op.drop_table("mockup_run")
    op.drop_index("ix_mockup_design_batch_id", table_name="mockup_design")
    op.drop_table("mockup_design")
    op.drop_index("ix_mockup_template_tenant_id", table_name="mockup_template")
    op.drop_table("mockup_template")
