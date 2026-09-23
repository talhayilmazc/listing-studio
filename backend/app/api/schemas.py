"""API request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AssetOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    parsed_sku: str | None
    group_key: str | None = None  # folder-derived listing group (D1)
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
    size_chart_profile_id: uuid.UUID | None = None  # profile whose size charts to append


class SizeChartProfileUpdate(BaseModel):
    profile_id: uuid.UUID | None = None  # null clears it (use each content's own profile)


class GroupOut(BaseModel):
    group_key: str
    sku: str | None = None
    image_count: int
    has_content: bool
    profile_id: uuid.UUID | None = None
    size_chart_profile_id: uuid.UUID | None = None
    manual: bool = False


class GroupAssign(BaseModel):
    # group_key null/absent => apply to every group that the seller hasn't set manually.
    group_key: str | None = None
    profile_id: uuid.UUID | None = None
    size_chart_profile_id: uuid.UUID | None = None


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


class QuotaDay(BaseModel):
    """One day of this tenant's Etsy API usage."""

    date: str  # YYYY-MM-DD (UTC), matching the quota reset boundary
    count: int


class QuotaOut(BaseModel):
    tenant_used: int
    tenant_limit: int
    tenant_remaining: int
    global_used: int
    global_limit: int
    global_remaining: int
    usage_date: str
    # Additive: the last 7 days of this tenant's usage, oldest first, for the
    # dashboard sparkline. Existing fields and their meanings are unchanged.
    history: list[QuotaDay] = Field(default_factory=list)


class AssetFailure(BaseModel):
    asset_id: uuid.UUID
    original_filename: str
    error: str


class GenerateRequest(BaseModel):
    # Batch-level default profile; each group may override it via its group setting
    # (v4 §E). None => every group must have its own assigned profile.
    profile_id: uuid.UUID | None = None
    group_key: str | None = None  # limit to one folder group; None = all groups (D3)


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
    content_template: str = "apparel"
    fixed_image_ids: list[int] = Field(default_factory=list)


class ProfileUpdate(BaseModel):
    name: str | None = None
    content_template: str | None = None
    fixed_image_ids: list[int] | None = None
    confirmed: bool | None = None
    title_prefix: str | None = None


class ReferenceImageOut(BaseModel):
    listing_image_id: int | None = None
    rank: int | None = None
    url: str | None = None
    kind: str | None = None  # "size_chart" | "artwork" | null (unclassified)
    is_fixed: bool = False  # currently included on every draft (B3)


class ProfileOut(BaseModel):
    id: uuid.UUID
    name: str
    reference_listing_id: int
    content_template: str
    source: str = "manual"  # "manual" | "detected"
    confirmed: bool = True
    title_prefix: str = ""  # prepended to every generated title (e.g. "COMFORT COLORS")
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
    # Additive: Etsy's own timestamp for when the listing entered its current
    # state (unix seconds) - for an active listing, when it went live.
    state_timestamp: int | None = None


class ShopListingsOut(BaseModel):
    listings: list[ShopListingOut] = Field(default_factory=list)
    stale: bool = False  # a background refresh was triggered


class ShopSummaryOut(BaseModel):
    """Counts derived from the cached shop listings.

    Read-only: unlike ``/shop/listings`` this never enqueues a refresh, so it is
    safe to call from a component present on every page.
    """

    total: int = 0
    active: int = 0
    draft: int = 0
    published_this_month: int = 0
    published_last_month: int = 0
    fetched_at: datetime | None = None
    stale: bool = False  # cache is older than its 6h window (no refresh triggered)


class ReplaceImagesRequest(BaseModel):
    batch_id: uuid.UUID  # the uploaded batch of new product photos


class ReplaceImagesOut(BaseModel):
    listing_id: int
    job_id: uuid.UUID


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
    # Operator facts for the legal pages; see Settings.operator_*.
    operator_name: str = ""
    operator_location: str = ""
    governing_law: str = ""
    dispute_venue: str = ""
    trademark_notice: str = Field(
        default=(
            "The term 'Etsy' is a trademark of Etsy, Inc. This application uses the "
            "Etsy API but is not endorsed or certified by Etsy, Inc."
        )
    )
