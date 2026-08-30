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
    error: str | None = None  # last content-generation failure reason, if any


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
    etsy_listing_id: int | None = None  # set once published as a draft
    etsy_listing_state: str | None = None  # "draft" | "active" | null
    listing_link: str | None = None  # edit URL for a draft, public URL once active
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


class AssetFailure(BaseModel):
    asset_id: uuid.UUID
    original_filename: str
    error: str


class GenerateRequest(BaseModel):
    profile_id: uuid.UUID  # required: the reference-listing profile to generate against


class GenerateResult(BaseModel):
    generated: int
    failed: int
    skipped: int
    failures: list[AssetFailure] = Field(default_factory=list)


class PublishJobOut(BaseModel):
    content_id: uuid.UUID
    job_id: uuid.UUID


class PublishSkipped(BaseModel):
    content_id: uuid.UUID
    reason: str


class BatchPublishResult(BaseModel):
    jobs: list[PublishJobOut] = Field(default_factory=list)
    skipped: list[PublishSkipped] = Field(default_factory=list)


class JobStatusOut(BaseModel):
    id: uuid.UUID
    type: str
    status: str
    error: str | None = None
    listing_id: int | None = None
    listing_url: str | None = None  # edit URL for a draft, public URL once active
    is_draft: bool = True


class ProfileCreate(BaseModel):
    name: str
    reference_listing_id: int
    content_template: str = "digital_products"
    title_replace_lines: int = 1
    fixed_image_ids: list[int] = Field(default_factory=list)


class ProfileUpdate(BaseModel):
    name: str | None = None
    content_template: str | None = None
    title_replace_lines: int | None = None
    fixed_image_ids: list[int] | None = None


class ReferenceImageOut(BaseModel):
    listing_image_id: int | None = None
    rank: int | None = None
    url: str | None = None


class ProfileOut(BaseModel):
    id: uuid.UUID
    name: str
    reference_listing_id: int
    content_template: str
    title_replace_lines: int
    fixed_image_ids: list[int] = Field(default_factory=list)
    updated_at: datetime | None = None
    is_fresh: bool = False  # cached reference payload present and <24h old
    reference_images: list[ReferenceImageOut] = Field(default_factory=list)


class ShopListingOut(BaseModel):
    listing_id: int
    title: str | None = None
    state: str | None = None
    sku: str | None = None
    shop_section_id: int | None = None
    url: str | None = None
    thumbnail_url: str | None = None


class ShopListingsOut(BaseModel):
    listings: list[ShopListingOut] = Field(default_factory=list)
    stale: bool = False  # a background refresh was triggered


class ConnectionOut(BaseModel):
    """Etsy connection status for the UI. Never includes tokens."""

    connected: bool
    status: str | None = None
    etsy_user_id: int | None = None
    shop_name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    connected_at: datetime | None = None
    expires_at: datetime | None = None


class MetaOut(BaseModel):
    support_email: str
    trademark_notice: str = Field(
        default=(
            "The term 'Etsy' is a trademark of Etsy, Inc. This application uses the "
            "Etsy API but is not endorsed or certified by Etsy, Inc."
        )
    )
