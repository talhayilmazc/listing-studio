"""Indexes on every foreign key column that had none (quality pass 2026-10, review R10).

Postgres does not index a foreign key by itself. These columns are what the app filters and
joins on (``generated_content.batch_id`` on every batch and review page, ``tenant_id`` on every
account's rows) and what a cascading delete scans (deleting a batch, an account or a shop). The
models mark the same columns ``index=True``; ``tests/test_schema_matches_models.py`` keeps the two
together. Only indexes are added; no row changes.

Revision ID: 0088_fk_indexes
Revises: 0087_how_its_made
"""

from __future__ import annotations

from alembic import op

revision: str = "0088_fk_indexes"
down_revision: str | None = "0087_how_its_made"
branch_labels = None
depends_on = None

#: (table, column), each indexed as ix_<table>_<column> (SQLAlchemy's name for index=True).
COLUMNS = [
    ("ad_charge", "tenant_id"),
    ("ads_daily", "tenant_id"),
    ("asset", "mockup_design_id"),
    ("asset", "mockup_photo_id"),
    ("asset", "tenant_id"),
    ("audit_log", "target_invite_id"),
    ("compliance_finding", "tenant_id"),
    ("content_version", "connection_id"),
    ("content_version", "tenant_id"),
    ("design_distribution", "group_id"),
    ("draft_attempt", "tenant_id"),
    ("generated_content", "asset_id"),
    ("generated_content", "batch_id"),
    ("generated_content", "tenant_id"),
    ("group_plan", "batch_id"),
    ("invite_code", "created_by_tenant_id"),
    ("invite_request", "decided_by_tenant_id"),
    ("invite_request", "invite_id"),
    ("job", "connection_id"),
    ("ledger_sync", "tenant_id"),
    ("listing_group_setting", "connection_id"),
    ("listing_group_setting", "profile_id"),
    ("listing_group_setting", "size_chart_profile_id"),
    ("listing_group_setting", "tenant_id"),
    ("listing_publication", "profile_id"),
    ("listing_publication", "schedule_job_id"),
    ("listing_snapshot", "job_id"),
    ("mockup_design", "tenant_id"),
    ("mockup_design_version", "tenant_id"),
    ("mockup_output", "batch_id"),
    ("mockup_output", "photo_id"),
    ("mockup_output", "tenant_id"),
    ("mockup_run", "tenant_id"),
    ("mockup_zip", "tenant_id"),
    ("planned_slot", "job_id"),
    ("sale_line", "tenant_id"),
    ("sales_sync", "tenant_id"),
    ("statement_import", "tenant_id"),
    ("statement_listing_fee", "tenant_id"),
    ("statement_order", "tenant_id"),
    ("upload_batch", "connection_id"),
    ("upload_batch", "size_chart_profile_id"),
]


def upgrade() -> None:
    for table, column in COLUMNS:
        op.create_index(f"ix_{table}_{column}", table, [column], if_not_exists=True)


def downgrade() -> None:
    for table, column in reversed(COLUMNS):
        op.drop_index(f"ix_{table}_{column}", table_name=table, if_exists=True)
