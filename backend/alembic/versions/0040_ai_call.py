"""ai_call: every call to the AI provider, one row each (admin-only cost metering)

Revision ID: 0040_ai_call
Revises: 0039_invite_request
Create Date: 2026-10-04
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "0040_ai_call"
down_revision: str | None = "0039_invite_request"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEUTRAL = "This listing could not be written this time. Nothing is wrong with your design: try again."
# USD per million tokens (input, output) for what was already written, as priced when this was made.
PRICES = {"claude-haiku-4-5": ("1.00", "5.00"), "claude-sonnet-5": ("2.00", "10.00")}


def upgrade() -> None:
    table = op.create_table(
        "ai_call",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),  # the UTC day, as the provider's console groups
        # SET NULL: what an account cost us is still our cost after it is deleted.
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenant.id", ondelete="SET NULL")),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_write_1h_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(14, 8)),
        sa.Column("listings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer()),
    )
    op.create_index("ix_ai_call_day", "ai_call", ["day"])
    op.create_index("ix_ai_call_tenant_day", "ai_call", ["tenant_id", "day"])

    # What is still known of the past: each listing that exists today carries the
    # tokens of the text calls that wrote it. Its design analysis, and every call
    # behind a deleted or failed listing, were never kept. Marked "backfill".
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT tenant_id, created_at, model_used, input_tokens, output_tokens FROM generated_content "
            "WHERE COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) > 0"
        )
    ).mappings().all()
    backfill = []
    for r in rows:
        at = r["created_at"]
        if isinstance(at, str):
            at = datetime.fromisoformat(at)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        model = r["model_used"] or "unknown"
        price = next((p for name, p in PRICES.items() if model.startswith(name)), None)
        tokens_in, tokens_out = int(r["input_tokens"] or 0), int(r["output_tokens"] or 0)
        cost = None
        if price:
            cost = (tokens_in * Decimal(price[0]) + tokens_out * Decimal(price[1])) / Decimal(1_000_000)
        backfill.append(
            {
                "id": uuid.uuid4(), "at": at, "day": at.astimezone(timezone.utc).date(), "tenant_id": r["tenant_id"],
                "purpose": "backfill", "model": model, "ok": True, "error": None,
                "input_tokens": tokens_in, "output_tokens": tokens_out, "cache_write_tokens": 0,
                "cache_write_1h_tokens": 0, "cache_read_tokens": 0, "cost_usd": cost, "listings": 1, "duration_ms": None,
            }
        )
    if backfill:
        op.bulk_insert(table, backfill)

    # Failure reasons kept from before could quote the provider (model names,
    # token limits, account errors). Sellers can read them, so they are replaced
    # with the neutral sentence the app writes from now on.
    bind.execute(
        sa.text(
            "UPDATE asset SET error = :neutral WHERE error IS NOT NULL AND ("
            "error LIKE '%Error code:%' OR error LIKE '%LLMError%' OR error LIKE '%anthropic%' "
            "OR error LIKE '%claude%' OR error LIKE '%token%' OR error LIKE '%APIStatusError%' "
            "OR error LIKE '%BadRequestError%' OR error LIKE '%RateLimitError%' OR error LIKE '%APIConnectionError%')"
        ),
        {"neutral": NEUTRAL},
    )


def downgrade() -> None:
    op.drop_table("ai_call")
