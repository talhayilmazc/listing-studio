"""tenant: the Etsy ceiling becomes an override; every account follows the default

Each account stored its own daily number (``daily_quota``), copied from a
setting when it registered, so accounts drifted apart and "the default" did not
exist. The column becomes ``etsy_ceiling_override``: NULL means the account
follows the default (ACCOUNT_DAILY_CEILING, 4,500), a number means an admin set
it. Every existing fixed value is reset to follow the default, and each reset
is written to the audit log with the value it replaced, so the accounts that
changed can be listed (and a number put back from Admin > Users if one was
deliberate).

Revision ID: 0042_etsy_ceiling_default
Revises: 0041_upload_retention
Create Date: 2026-10-05
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_etsy_ceiling_default"
down_revision: str | None = "0041_upload_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "tenant", "daily_quota", new_column_name="etsy_ceiling_override",
        existing_type=sa.Integer(), nullable=True, server_default=None,
    )
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, email, etsy_ceiling_override FROM tenant WHERE etsy_ceiling_override IS NOT NULL ORDER BY created_at")).all()
    for tenant_id, email, previous in rows:
        bind.execute(
            sa.text(
                "INSERT INTO audit_log (id, action, target_tenant_id, details) "
                "VALUES (:id, 'user.quota_changed', :target, CAST(:details AS jsonb))"
            ),
            {"id": uuid.uuid4(), "target": tenant_id,
             "details": json.dumps({"previous": previous, "new": None, "by": "migration 0042: every account follows the default"})},
        )
        print(f"  etsy ceiling: {email} was {previous}, now follows the default")
    bind.execute(sa.text("UPDATE tenant SET etsy_ceiling_override = NULL"))
    print(f"  etsy ceiling: {len(rows)} account(s) reset to follow the default")


def downgrade() -> None:
    # The old fixed values are in the audit log; the column goes back to a number.
    op.execute("UPDATE tenant SET etsy_ceiling_override = 4500 WHERE etsy_ceiling_override IS NULL")
    op.alter_column(
        "tenant", "etsy_ceiling_override", new_column_name="daily_quota",
        existing_type=sa.Integer(), nullable=False, server_default="2000",
    )
