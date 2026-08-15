"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-15

Creates the step-1 data model (see docs/data-model.md). Targets PostgreSQL.
Retention for listing_snapshot (90 days) is enforced by a later cleanup job;
the schema supports it via the taken_at index.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Named enum types (created explicitly, dropped on downgrade).
tenant_status = postgresql.ENUM("active", "suspended", name="tenant_status", create_type=False)
connection_status = postgresql.ENUM(
    "active", "expired", "revoked", name="connection_status", create_type=False
)
job_type = postgresql.ENUM(
    "create_draft",
    "update_listing",
    "upload_image",
    "sync_listings",
    "update_inventory",
    name="job_type",
    create_type=False,
)
job_status = postgresql.ENUM(
    "queued", "running", "succeeded", "failed", "cancelled", name="job_status", create_type=False
)
asset_status = postgresql.ENUM(
    "uploaded", "processed", "failed", name="asset_status", create_type=False
)
upload_batch_status = postgresql.ENUM(
    "uploading",
    "processing",
    "ready",
    "applied",
    "failed",
    name="upload_batch_status",
    create_type=False,
)
compliance_severity = postgresql.ENUM(
    "blocking", "warning", "info", name="compliance_severity", create_type=False
)

_ENUMS = (
    tenant_status,
    connection_status,
    job_type,
    job_status,
    asset_status,
    upload_batch_status,
    compliance_severity,
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in _ENUMS:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "tenant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("status", tenant_status, nullable=False),
        sa.Column("daily_quota", sa.Integer(), server_default="2000", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "etsy_connection",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("etsy_user_id", sa.BigInteger(), nullable=True),
        sa.Column("shop_id", sa.BigInteger(), nullable=True),
        sa.Column("shop_name", sa.Text(), nullable=True),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("status", connection_status, nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_etsy_connection_tenant_id", "etsy_connection", ["tenant_id"])

    op.create_table(
        "job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("type", job_type, nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", job_status, nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["connection_id"], ["etsy_connection.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_tenant_status", "job", ["tenant_id", "status"])
    op.create_index("ix_job_status_scheduled", "job", ["status", "scheduled_at"])
    op.create_index("ix_job_batch", "job", ["batch_id"])

    op.create_table(
        "api_usage",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("request_count", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "usage_date"),
    )

    op.create_table(
        "listing_snapshot",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.BigInteger(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["job.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_snapshot_tenant_listing_taken",
        "listing_snapshot",
        ["tenant_id", "listing_id", "taken_at"],
    )
    op.create_index("ix_snapshot_taken_at", "listing_snapshot", ["taken_at"])

    op.create_table(
        "upload_batch",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("status", upload_batch_status, nullable=False),
        sa.Column("file_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_upload_batch_tenant_id", "upload_batch", ["tenant_id"])

    op.create_table(
        "asset",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("parsed_sku", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("processed_key", sa.Text(), nullable=True),
        sa.Column("status", asset_status, nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["upload_batch.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_batch_id", "asset", ["batch_id"])

    op.create_table(
        "generated_content",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("taxonomy_id", sa.BigInteger(), nullable=True),
        sa.Column("attributes", postgresql.JSONB(), nullable=True),
        sa.Column("model_used", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("approved", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["batch_id"], ["upload_batch.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], ["asset.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "compliance_finding",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("generated_content_id", sa.Uuid(), nullable=False),
        sa.Column("severity", compliance_severity, nullable=False),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["generated_content_id"], ["generated_content.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compliance_finding_generated_content_id",
        "compliance_finding",
        ["generated_content_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_compliance_finding_generated_content_id", table_name="compliance_finding")
    op.drop_table("compliance_finding")
    op.drop_table("generated_content")
    op.drop_index("ix_asset_batch_id", table_name="asset")
    op.drop_table("asset")
    op.drop_index("ix_upload_batch_tenant_id", table_name="upload_batch")
    op.drop_table("upload_batch")
    op.drop_index("ix_snapshot_taken_at", table_name="listing_snapshot")
    op.drop_index("ix_snapshot_tenant_listing_taken", table_name="listing_snapshot")
    op.drop_table("listing_snapshot")
    op.drop_table("api_usage")
    op.drop_index("ix_job_batch", table_name="job")
    op.drop_index("ix_job_status_scheduled", table_name="job")
    op.drop_index("ix_job_tenant_status", table_name="job")
    op.drop_table("job")
    op.drop_index("ix_etsy_connection_tenant_id", table_name="etsy_connection")
    op.drop_table("etsy_connection")
    op.drop_table("tenant")

    bind = op.get_bind()
    for enum_type in reversed(_ENUMS):
        enum_type.drop(bind, checkfirst=True)
