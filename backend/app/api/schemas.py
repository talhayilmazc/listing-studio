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
    # The shop this content was written for (its profile's shop).
    connection_id: uuid.UUID | None = None
    # One draft per shop it was sent to (v5 §E).
    publications: list["PublicationOut"] = Field(default_factory=list)
    # asset context for the review screen
    original_filename: str
    parsed_sku: str | None
    rank: int | None


class ManualStepOut(BaseModel):
    """A setting the seller must make on the draft in Shop Manager (not in the API)."""

    key: str
    label: str
    detail: str
    done: bool = False  # the seller ticked it for this draft


class ManualStepTick(BaseModel):
    done: bool


class ManualStepsDone(BaseModel):
    updated_drafts: int


class PublicationOut(BaseModel):
    connection_id: uuid.UUID
    shop_name: str | None = None
    etsy_listing_id: int
    state: str  # "draft" | "active"
    listing_link: str  # edit URL for a draft, public URL once active
    # For a draft: what still has to be set in Shop Manager before publishing.
    manual_steps: list[ManualStepOut] = Field(default_factory=list)


class ContentUpdate(BaseModel):
    title: str | None = None
    tags: list[str] | None = None
    description: str | None = None


class ApproveUpdate(BaseModel):
    approved: bool


class ApproveSkipped(BaseModel):
    content_id: uuid.UUID
    original_filename: str
    reason: str


class ApproveAllResult(BaseModel):
    approved: int = 0  # newly approved now
    already_approved: int = 0
    skipped: list[ApproveSkipped] = Field(default_factory=list)  # failed validation


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


class PauseOut(BaseModel):
    """Why this seller's Etsy work is waiting, in words, and until when."""

    reason: str  # global_quota | tenant_quota
    message: str
    resumes_at: datetime


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
    # App-wide count at which new jobs pause (production-spec C).
    global_pause_at: int = 0
    # Set while new work is paused for this seller, with the reason.
    pause: PauseOut | None = None
    # With ?shop=: that shop's share of today's requests. Display only; the
    # limits are per account and app-wide.
    shop_used: int | None = None


class AssetFailure(BaseModel):
    asset_id: uuid.UUID
    original_filename: str
    error: str


class ArchiveFailure(BaseModel):
    filename: str
    error: str


class ArchiveResult(BaseModel):
    """What one uploaded ZIP became (v6 §F)."""

    assets: list[AssetOut] = []
    #: Files that are not an accepted image (by content), or were encrypted.
    skipped_unsupported: int = 0
    #: Paths that tried to leave the archive (zip slip), and links.
    skipped_unsafe: int = 0
    #: Archives inside the archive; never opened.
    skipped_nested: int = 0
    #: Images the upload rules refused (too large, unreadable...), with why.
    failed: list[ArchiveFailure] = []


class GroupOrder(BaseModel):
    """A listing group's images in the order the seller chose (v6 §E): the first
    is the cover. ``group_key`` "" is the files at the root of the upload."""

    group_key: str = ""
    asset_ids: list[uuid.UUID]


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
    connection_id: uuid.UUID | None = None
    shop_name: str | None = None


class PublishSkipped(BaseModel):
    content_id: uuid.UUID
    reason: str
    connection_id: uuid.UUID | None = None
    shop_name: str | None = None


class BatchPublishResult(BaseModel):
    jobs: list[PublishJobOut] = Field(default_factory=list)
    skipped: list[PublishSkipped] = Field(default_factory=list)


class PublishTarget(BaseModel):
    connection_id: uuid.UUID
    # Which of that shop's profiles builds its draft; omitted = chosen for you
    # (the content's own profile in its own shop, else a same-named one, else the
    # only one of the same kind).
    profile_id: uuid.UUID | None = None


class PublishRequest(BaseModel):
    # Omitted = each listing goes to the shop it was written for.
    targets: list[PublishTarget] | None = Field(default=None, max_length=50)
    # Limit to these listings (a single card); omitted = every approved one.
    content_ids: list[uuid.UUID] | None = None


class LiveRequest(BaseModel):
    # Omitted = every shop that has a draft of it.
    connection_ids: list[uuid.UUID] | None = None
    content_ids: list[uuid.UUID] | None = None


class ShopTargetOut(BaseModel):
    connection_id: uuid.UUID
    shop_name: str | None
    # Listings this shop can take, and why the others cannot (first reason each).
    ready: int
    blocked: list[PublishSkipped] = Field(default_factory=list)
    profiles: list["ProfileChoiceOut"] = Field(default_factory=list)


class ProfileChoiceOut(BaseModel):
    id: uuid.UUID
    name: str
    content_template: str
    is_fresh: bool


class PublishPreviewOut(BaseModel):
    """What a publish would do, before it is confirmed (v5 §E quota protection)."""

    shops: list[ShopTargetOut]
    drafts: int  # drafts that would be created
    estimated_calls: int  # ~15 Etsy requests per draft
    calls_per_draft: int
    budget_remaining: int  # what may still be spent today (account and app-wide)
    fits: bool
    listings_that_fit: int  # per the selected shops, if it does not all fit
    message: str | None = None


class JobStatusOut(BaseModel):
    id: uuid.UUID
    type: str
    status: str
    error: str | None = None
    listing_id: int | None = None
    listing_url: str | None = None  # edit URL for a draft, public URL once active
    is_draft: bool = True
    connection_id: uuid.UUID | None = None
    shop_name: str | None = None
    # For a finished draft: what still has to be set in Shop Manager before publishing.
    manual_steps: list[ManualStepOut] = Field(default_factory=list)
    # A queued job waiting for the daily reset says so, rather than timing out.
    pause: PauseOut | None = None


class ProfileCreate(BaseModel):
    connection_id: uuid.UUID  # the shop whose listing is the reference
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
    connection_id: uuid.UUID
    shop_name: str | None = None
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
    # True once the image links pass the 6h display limit: ids, ranks and
    # size-chart classifications are still returned, the links are not.
    reference_images_expired: bool = False


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


class ShopOut(BaseModel):
    """One connected shop. Never includes tokens."""

    id: uuid.UUID  # the connection id: what every ?shop= and target refers to
    name: str  # display name, else the Etsy shop name, else a placeholder
    shop_name: str | None = None  # the name on Etsy, once known
    display_name: str | None = None
    shop_id: int | None = None
    position: int
    connected_at: datetime


class ShopSlotsOut(BaseModel):
    used: int
    limit: int
    app_used: int
    app_limit: int
    can_add: bool


class ShopsOut(BaseModel):
    shops: list[ShopOut]
    slots: ShopSlotsOut


class ShopUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)


class ShopOrder(BaseModel):
    ids: list[uuid.UUID] = Field(max_length=100)


class MetaOut(BaseModel):
    support_email: str
    # Operator facts for the legal pages; see Settings.operator_*.
    operator_name: str = ""
    operator_location: str = ""
    governing_law: str = ""
    dispute_venue: str = ""
    # Whether error reports go to Sentry; the Privacy Policy names it only if so.
    error_tracking: bool = False
    trademark_notice: str = Field(
        default=(
            "The term 'Etsy' is a trademark of Etsy, Inc. This application uses the "
            "Etsy API but is not endorsed or certified by Etsy, Inc."
        )
    )
