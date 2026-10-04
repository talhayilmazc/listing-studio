"""ai_usage_daily: what the AI provider's work cost us, per account, day and model (admin-only)

Revision ID: 0040_ai_usage_daily
Revises: 0039_invite_request
Create Date: 2026-10-04
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_ai_usage_daily"
down_revision: str | None = "0039_invite_request"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table = op.create_table(
        "ai_usage_daily",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # SET NULL: what an account cost us is still our cost after it is deleted.
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="SET NULL")),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("listings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.UniqueConstraint("tenant_id", "day", "model", name="uq_ai_usage_daily"),
    )
    op.create_index("ix_ai_usage_daily_day", "ai_usage_daily", ["day"])

    # What is still known of the past: the listings that exist today carry the
    # tokens that wrote them. Calls behind deleted or failed listings are gone.
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT tenant_id, CAST(created_at AS DATE) AS day, COALESCE(model_used, 'unknown') AS model, "
            "COUNT(*) AS listings, COALESCE(SUM(input_tokens), 0) AS input_tokens, "
            "COALESCE(SUM(output_tokens), 0) AS output_tokens "
            "FROM generated_content GROUP BY tenant_id, CAST(created_at AS DATE), COALESCE(model_used, 'unknown')"
        )
    ).mappings().all()
    if rows:
        op.bulk_insert(
            table,
            [
                {
                    "id": uuid.uuid4(), "tenant_id": r["tenant_id"], "day": r["day"], "model": r["model"],
                    "calls": int(r["listings"]), "listings": int(r["listings"]),
                    "input_tokens": int(r["input_tokens"]), "output_tokens": int(r["output_tokens"]),
                    "cache_write_tokens": 0, "cache_read_tokens": 0,
                }
                for r in rows
            ],
        )


    # Failure reasons kept from before this change could quote the provider
    # (model names, token limits, account errors). Sellers can read them, so
    # they are replaced with the neutral sentence the app writes from now on.
    bind.execute(
        sa.text(
            "UPDATE asset SET error = :neutral WHERE error IS NOT NULL AND ("
            "error LIKE '%Error code:%' OR error LIKE '%LLMError%' OR error LIKE '%anthropic%' "
            "OR error LIKE '%claude%' OR error LIKE '%token%' OR error LIKE '%APIStatusError%' "
            "OR error LIKE '%BadRequestError%' OR error LIKE '%RateLimitError%' OR error LIKE '%APIConnectionError%')"
        ),
        {"neutral": "This listing could not be written this time. Nothing is wrong with your design: try again."},
    )


def downgrade() -> None:
    op.drop_table("ai_usage_daily")
