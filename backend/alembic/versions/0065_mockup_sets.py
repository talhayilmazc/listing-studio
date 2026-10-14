"""Mockup sets, templates prepared in the worker, run outputs and ZIP downloads.

* ``mockup_template_set``: templates that belong together (one garment in several
  colours), with their own name.
* ``mockup_template``: ``set_id``; ``status`` ("preparing" while the worker finds the
  outline, then "ready" or "failed" with ``error``) and ``source_key`` (the uploaded
  photo, resized, until it is prepared). The photo's own fields may be empty while
  a template is preparing. Existing templates are "ready".
* ``mockup_output``: each mockup a run made, named "<SKU>_<template name>.jpg"; it
  becomes a listing photo only when the seller chooses "Use for listings".
* ``mockup_zip``: a ZIP of a run's mockups to download, deleted 24 hours after it
  is ready.

Nothing existing is deleted. Mockups made before this (as listing photos) stay.

Revision ID: 0065_mockup_sets
Revises: 0064_mockups
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0065_mockup_sets"
down_revision: str | None = "0064_mockups"
branch_labels = None
depends_on = None

JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
NULLABLE_AFTER = ("image_key", "mask_key", "width", "height", "mask_source", "print_area")


def upgrade() -> None:
    op.create_table(
        "mockup_template_set",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mockup_template_set_tenant_id", "mockup_template_set", ["tenant_id"])
    with op.batch_alter_table("mockup_template") as table:
        table.add_column(sa.Column("set_id", sa.Uuid(), nullable=True))
        table.add_column(sa.Column("status", sa.Text(), nullable=False, server_default="ready"))
        table.add_column(sa.Column("error", sa.Text(), nullable=True))
        table.add_column(sa.Column("source_key", sa.Text(), nullable=True))
        table.create_foreign_key(
            "fk_mockup_template_set_id", "mockup_template_set", ["set_id"], ["id"], ondelete="SET NULL"
        )
        for column in NULLABLE_AFTER:
            table.alter_column(column, nullable=True)
    op.create_index("ix_mockup_template_set_id", "mockup_template", ["set_id"])
    op.create_table(
        "mockup_output",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("mockup_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("upload_batch.id", ondelete="CASCADE"), nullable=False),
        sa.Column("design_id", sa.Uuid(), sa.ForeignKey("mockup_design.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id", sa.Uuid(), sa.ForeignKey("mockup_template.id", ondelete="SET NULL"), nullable=True),
        sa.Column("template_name", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("files_removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mockup_output_run_id", "mockup_output", ["run_id"])
    op.create_index("ix_mockup_output_design_id", "mockup_output", ["design_id"])
    op.create_table(
        "mockup_zip",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("mockup_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("layout", sa.Text(), nullable=False, server_default="folders"),
        sa.Column("design_ids", JSONB, nullable=True),
        sa.Column("key", sa.Text(), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("files", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mockup_zip_run_id", "mockup_zip", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_mockup_zip_run_id", table_name="mockup_zip")
    op.drop_table("mockup_zip")
    op.drop_index("ix_mockup_output_design_id", table_name="mockup_output")
    op.drop_index("ix_mockup_output_run_id", table_name="mockup_output")
    op.drop_table("mockup_output")
    op.drop_index("ix_mockup_template_set_id", table_name="mockup_template")
    # Templates that never finished preparing have none of the photo's fields:
    # they cannot exist under the older schema.
    op.execute("DELETE FROM mockup_template WHERE status <> 'ready' OR image_key IS NULL")
    with op.batch_alter_table("mockup_template") as table:
        for column in NULLABLE_AFTER:
            table.alter_column(column, nullable=False)
        table.drop_constraint("fk_mockup_template_set_id", type_="foreignkey")
        table.drop_column("source_key")
        table.drop_column("error")
        table.drop_column("status")
        table.drop_column("set_id")
    op.drop_index("ix_mockup_template_set_tenant_id", table_name="mockup_template_set")
    op.drop_table("mockup_template_set")
