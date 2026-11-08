"""Onboarding: the sign-up funnel (``funnel_event``), the example designs (``upload_batch.is_example``)
and the dismissed "You're set up" note (``tenant.setup_note_dismissed_at``).

``funnel_event`` holds one row per account and step (pipeline/funnel.py): the account id, the step's
name and its time, nothing else. The upgrade fills it from what the database already shows (each query
is ``funnel.BACKFILL``'s, word for word; tests/test_onboarding.py checks): sign-up, first shop, first
batch with a photo, first listing written, first approval (kept since 0091), first draft or schedule.
What no row shows is not written. The downgrade drops the table and the two columns.

Revision ID: 0098_onboarding
Revises: 0097_listing_fix
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0098_onboarding"
down_revision: str | None = "0097_listing_fix"
branch_labels = None
depends_on = None

#: (step, the query giving each account's first time), as in pipeline/funnel.py::BACKFILL.
BACKFILL: list[tuple[str, str]] = [
    ("signed_up", 'SELECT id, created_at FROM tenant'),
    ("shop_connected", 'SELECT tenant_id, min(connected_at) FROM etsy_connection GROUP BY tenant_id'),
    ("first_upload", 'SELECT b.tenant_id, min(b.created_at) FROM upload_batch b WHERE NOT b.is_example AND EXISTS (SELECT 1 FROM asset a WHERE a.batch_id = b.id) GROUP BY b.tenant_id'),
    ("first_listing_ready", 'SELECT c.tenant_id, min(c.created_at) FROM generated_content c JOIN upload_batch b ON b.id = c.batch_id WHERE NOT b.is_example GROUP BY c.tenant_id'),
    ("first_approved", 'SELECT c.tenant_id, min(c.approved_at) FROM generated_content c JOIN upload_batch b ON b.id = c.batch_id WHERE NOT b.is_example AND c.approved_at IS NOT NULL GROUP BY c.tenant_id'),
    ("first_sent", 'SELECT tenant_id, min(at) FROM ( SELECT tenant_id, created_at AS at FROM listing_publication UNION ALL SELECT tenant_id, created_at AS at FROM group_plan) sent GROUP BY tenant_id'),
]


def upgrade() -> None:
    op.add_column("tenant", sa.Column("setup_note_dismissed_at", sa.DateTime(timezone=True)))
    op.add_column("upload_batch", sa.Column("is_example", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_table(
        "funnel_event",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_funnel_event_tenant_name"),
    )
    op.create_index("ix_funnel_event_tenant_id", "funnel_event", ["tenant_id"])
    op.create_index("ix_funnel_event_at", "funnel_event", ["at"])
    for name, query in BACKFILL:
        op.execute(
            sa.text(
                "INSERT INTO funnel_event (id, tenant_id, name, at) "
                f"SELECT gen_random_uuid(), first.tid, :name, first.at FROM ({query}) AS first(tid, at) "
                "WHERE first.at IS NOT NULL ON CONFLICT (tenant_id, name) DO NOTHING"
            ).bindparams(name=name)
        )


def downgrade() -> None:
    op.drop_index("ix_funnel_event_at", table_name="funnel_event")
    op.drop_index("ix_funnel_event_tenant_id", table_name="funnel_event")
    op.drop_table("funnel_event")
    op.drop_column("upload_batch", "is_example")
    op.drop_column("tenant", "setup_note_dismissed_at")
