"""API request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AssetOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    parsed_sku: str | None
    rank: int | None
    status: str
    mime_type: str | None
    width: int | None
    height: int | None
    has_content: bool = False


class BatchSummary(BaseModel):
    id: uuid.UUID
    status: str
    file_count: int
    created_at: datetime
    asset_count: int
    processed_count: int
    approved_count: int


class BatchDetail(BatchSummary):
    assets: list[AssetOut]


class ContentOut(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    title: str | None
    tags: list[str]
    description: str | None
    approved: bool
    model_used: str | None
    input_tokens: int | None
    output_tokens: int | None
    # asset context for the review screen
    original_filename: str
    parsed_sku: str | None
    rank: int | None


class ContentUpdate(BaseModel):
    title: str | None = None
    tags: list[str] | None = None
    description: str | None = None


class ApproveUpdate(BaseModel):
    approved: bool


class ValidationInfo(BaseModel):
    valid: bool
    errors: list[str]


class ContentUpdateResult(BaseModel):
    content: ContentOut
    validation: ValidationInfo


class ListingCost(BaseModel):
    content_id: uuid.UUID
    asset_id: uuid.UUID
    model_used: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: str  # Decimal serialized as string to avoid float drift


class BatchCostOut(BaseModel):
    listing_count: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: str
    listings: list[ListingCost]


class QuotaOut(BaseModel):
    tenant_used: int
    tenant_limit: int
    tenant_remaining: int
    global_used: int
    global_limit: int
    global_remaining: int
    usage_date: str


class GenerateResult(BaseModel):
    generated: int
    failed: int
    skipped: int


class MetaOut(BaseModel):
    support_email: str
    trademark_notice: str = Field(
        default=(
            "The term 'Etsy' is a trademark of Etsy, Inc. This application uses the "
            "Etsy API but is not endorsed or certified by Etsy, Inc."
        )
    )
