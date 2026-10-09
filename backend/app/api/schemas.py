"""API request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CoverCrop(BaseModel):
    """A square on the processed image, in its pixels: where the cover is cut."""

    x: float = Field(ge=0)
    y: float = Field(ge=0)
    size: float = Field(gt=0)
    #: The processed image's size when the crop was chosen (set by the server).
    width: int | None = None
    height: int | None = None


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
    #: The seller's square crop for when this image is the cover, or None (auto).
    cover_crop: CoverCrop | None = None
    #: The image's files were deleted by upload retention (a limited time after
    #: its listing was published). ``has_thumbnail``: a small cover was kept and
    #: is what the image endpoint now serves; without it there is nothing to show.
    files_removed: bool = False
    has_thumbnail: bool = False


class BatchSummary(BaseModel):
    id: uuid.UUID
    status: str
    file_count: int
    created_at: datetime
    asset_count: int
    processed_count: int
    approved_count: int
    size_chart_profile_id: uuid.UUID | None = None  # profile whose size charts to append
    # What the batch is called: the seller's name for it, else one derived from
    # its contents ("BR5229 + 4 more"). ``named`` says which.
    name: str = ""
    named: bool = False
    # The shop this batch is for (the seller's choice), and every shop its groups
    # or drafts are in: shown wherever the batch is.
    connection_id: uuid.UUID | None = None
    shop_name: str | None = None
    shop_names: list[str] = Field(default_factory=list)
    # How its images were grouped (pipeline/grouping.py): listing groups, those
    # found by the SKU in file names and from folders, and photos left Unsorted.
    groups: int = 0
    sku_groups: int = 0
    folder_groups: int = 0
    unsorted: int = 0
    #: "Found 42 groups by SKU, 3 photos unsorted".
    grouping: str = ""


class BatchCreate(BaseModel):
    # The shop the upload is for. Omitted with one shop connected: that shop.
    connection_id: uuid.UUID | None = None


class BatchRename(BaseModel):
    # Empty or null goes back to the name derived from the batch's contents.
    name: str | None = Field(default=None, max_length=200)


class BatchShop(BaseModel):
    connection_id: uuid.UUID


class SizeChartProfileUpdate(BaseModel):
    profile_id: uuid.UUID | None = None  # null clears it (use each content's own profile)


class GroupOut(BaseModel):
    group_key: str
    sku: str | None = None
    image_count: int
    has_content: bool
    # The shop the group's listing is for; its profile and size charts are that shop's.
    connection_id: uuid.UUID | None = None
    shop_name: str | None = None
    profile_id: uuid.UUID | None = None
    size_chart_profile_id: uuid.UUID | None = None
    manual: bool = False
    # One of the seller's own listings this group's listing is modelled on (v7 §B).
    pattern_listing_id: int | None = None


class GroupAssign(BaseModel):
    """Set a group's shop, profile and size charts. Only the fields sent are
    changed (null clears one).

    ``group_key``: that group, and the groups after it that the seller has not
    set themselves take the same (defaults carry forward). ``group_keys``:
    exactly those groups (bulk-apply to a selection). Neither: every group the
    seller has not set themselves.
    """

    group_key: str | None = None
    group_keys: list[str] | None = Field(default=None, max_length=500)
    connection_id: uuid.UUID | None = None
    profile_id: uuid.UUID | None = None
    size_chart_profile_id: uuid.UUID | None = None


class PatternAssign(BaseModel):
    """Model one group's listing on one of the seller's own listings (v7 §B); null clears it."""

    group_key: str
    pattern_listing_id: int | None = None


class PatternListingOut(BaseModel):
    listing_id: int
    title: str | None = None
    tags: list[str] = Field(default_factory=list)
    state: str | None = None
    thumbnail_url: str | None = None
    #: The listing on Etsy (every listing shown links back to it).
    url: str
    #: Units sold in the last 90 days, once sales are read (C).
    units_90d: int | None = None


class BatchDetail(BatchSummary):
    assets: list[AssetOut]


class ContentOut(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    title: str | None
    tags: list[str]
    description: str | None
    approved: bool
    # Which model wrote it and what it cost are ours, not the seller's: they are
    # not in any response a seller can receive (core/ai_meter.py).
    # The shop this content was written for (its profile's shop).
    connection_id: uuid.UUID | None = None
    # One draft per shop it was sent to (v5 §E).
    publications: list["PublicationOut"] = Field(default_factory=list)
    # asset context for the review screen
    original_filename: str
    parsed_sku: str | None
    rank: int | None
    # The title length this listing's profile asks for (the counter follows it),
    # how it was written, and the category attributes chosen from Etsy's lists.
    title_min_length: int = 110
    title_max_length: int = 140
    listing_style: str = "classic"
    attributes: dict[str, str] = Field(default_factory=dict)
    # What the compliance scanner found (trademarks, character artwork), shown
    # on the card before anything is sent to Etsy.
    findings: list["FindingOut"] = Field(default_factory=list)
    # What its drafts get for personalization in every shop (v8 §D), and whether
    # it is the listing's own ("listing"), its profile's ("profile") or unknown yet.
    personalization: "PersonalizationOut | None" = None
    personalization_source: str = "unknown"
    # Creating a draft or going live that did not finish: why, and whether it is
    # waiting to run again by itself. Shown on the card with "Try again".
    work: list["WorkOut"] = Field(default_factory=list)


class WorkOut(BaseModel):
    """The latest draft or go-live job of a listing in one shop, while it has not succeeded."""

    kind: str  # "draft" | "publish"
    connection_id: uuid.UUID
    shop_name: str | None = None
    job_id: uuid.UUID
    status: str  # "failed" | "queued" | "running"
    #: Why it failed, in words the seller can act on (workers/guards.py).
    error: str | None = None
    #: Set while it waits: for the daily reset, or to run again by itself in a moment.
    pause: "PauseOut | None" = None


class FindingOut(BaseModel):
    rule: str
    severity: str  # "blocking" | "warning" | "info"
    detail: str | None = None


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
    # When the seller scheduled it to go live (v6 §G), and where that stands.
    scheduled_for: datetime | None = None
    schedule_status: str | None = None
    schedule_note: str | None = None


class AllowanceOut(BaseModel):
    """ "Listings generated": designs written in the period (core/allowance.py).
    Drafts and publishing do not count. Not the Etsy request ceiling."""

    label: str = "Listings generated"
    amount: int
    period: str  # "daily" | "weekly" | "monthly"
    custom: bool  # set for this seller (amount and period); else the system default
    used: int
    generations: int
    pending: int  # Replace images queued, their listing not yet written: counted as used
    remaining: int
    period_start: datetime
    resets_at: datetime
    resets_label: str  # "Thu, Oct 1, 12:00 AM CDT", in the seller's zone
    time_zone: str


class ScheduleItem(BaseModel):
    content_id: uuid.UUID
    connection_id: uuid.UUID
    #: When to go live as a wall-clock time in the account's time zone
    #: ("2026-09-28T17:00"); converted to UTC once, on save.
    local_time: str | None = Field(default=None, max_length=32)
    #: Or the exact instant, with its zone (API clients).
    run_at: datetime | None = None


class ScheduleRequest(BaseModel):
    items: list[ScheduleItem] = Field(max_length=1000)
    #: False (bulk scheduling): leave a draft that already has a time as it is.
    replace: bool = True


class ScheduleOut(BaseModel):
    content_id: uuid.UUID
    connection_id: uuid.UUID
    shop_name: str | None = None
    title: str | None = None
    asset_id: uuid.UUID
    batch_id: uuid.UUID
    batch_name: str | None = None
    etsy_listing_id: int
    listing_link: str
    scheduled_for: datetime
    #: "scheduled" | "publishing" | "waiting" | "published" | "failed" | "not_published"
    status: str
    note: str | None = None
    #: The account's time zone the time is shown in (IANA name), and its
    #: abbreviation at that instant ("CDT").
    time_zone: str | None = None
    zone_abbreviation: str | None = None
    #: While waiting for the daily budget: when it goes out instead (UTC).
    resumes_at: datetime | None = None


class ScheduleSkipped(BaseModel):
    content_id: uuid.UUID
    connection_id: uuid.UUID
    reason: str


class ScheduleResult(BaseModel):
    scheduled: list[ScheduleOut] = []
    skipped: list[ScheduleSkipped] = []


class ContentUpdate(BaseModel):
    title: str | None = None
    tags: list[str] | None = None
    description: str | None = None
    #: This listing's personalization (v8 §D); sending null follows the profile again.
    personalization: dict[str, Any] | None = None


class BulkPersonalization(BaseModel):
    """"Set for all": one setting for the batch's listings (or the ones named); null follows each profile."""

    personalization: dict[str, Any] | None
    content_ids: list[uuid.UUID] | None = None


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


class QuotaDay(BaseModel):
    """One day of this tenant's Etsy API usage."""

    date: str  # YYYY-MM-DD (UTC), matching the quota reset boundary
    count: int


class PauseOut(BaseModel):
    """Why this seller's Etsy work is waiting, in words, and until when."""

    # global_quota | tenant_quota (the daily reset), or
    # etsy_rate_limit | etsy_unavailable | interrupted (runs again by itself shortly)
    reason: str
    message: str
    resumes_at: datetime


class EtsyCeilingOut(BaseModel):
    """ "Etsy requests today": the account's own ceiling (core/limits.py). The
    same object wherever it appears: sidebar, batch page, review page, admin."""

    label: str = "Etsy requests today"
    limit: int
    used: int
    remaining: int
    follows_default: bool  # no number of its own: the default applies
    default: int
    # Requests today keeping the account's shops and profiles current: counted
    # against the app's budget only, never in ``used``.
    upkeep: int = 0
    resets_at: datetime  # 00:00 UTC
    resets_label: str  # that instant as a time in the seller's zone: "7:00 PM CDT"


class QuotaOut(BaseModel):
    """What a seller sees of Etsy requests: their own ceiling. The app's shared
    budget is not here (admin only); when it stops their work, ``pause`` says so."""

    ceiling: EtsyCeilingOut
    usage_date: str
    # The last 7 days of this account's own requests, oldest first.
    history: list[QuotaDay] = Field(default_factory=list)
    # Set while new work is paused for this seller, with the reason.
    pause: PauseOut | None = None
    # Set while writing new listings is paused on our side (core/llm_status.py).
    generation_pause: str | None = None
    # With ?shop=: that shop's part of ``ceiling.used``. Display only; the
    # ceiling is for the account's shops together.
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


class ImageDeleteResult(BaseModel):
    """What deleting one image of a listing group did."""

    batch: "BatchDetail"
    group_key: str
    #: It was the group's last image: the group, and the listing written for it, are gone.
    group_removed: bool = False
    #: It was the cover: the next image is the cover now, and the saved crop was dropped.
    cover_changed: bool = False
    #: Drafts or live listings made from this group. They keep the photo on Etsy.
    listings_on_etsy: int = 0


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
    #: Write the groups and leave the photos in Unsorted as they are. Without it,
    #: writing does not start while any photo is unsorted.
    ignore_unsorted: bool = False
    #: "Regenerate": write new content for groups that already have some. The old
    #: content is removed only once the new one is saved.
    replace: bool = False
    #: The seller confirmed replacing content they had already approved.
    replace_approved: bool = False


class GroupSkipped(BaseModel):
    group_key: str
    reason: str


class GenerateResult(BaseModel):
    generated: int
    failed: int
    skipped: int
    failures: list[AssetFailure] = Field(default_factory=list)
    #: Groups a regenerate left alone on purpose, and why.
    skipped_groups: list[GroupSkipped] = Field(default_factory=list)
    #: Set when writing is paused on our side (the AI provider is refusing our
    #: account): nothing was attempted or failed, and no allowance was used.
    paused: str | None = None


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


class BatchActionRequest(BaseModel):
    """Create drafts or publish across several batches at once, from the Batches page."""

    batch_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)
    #: "drafts": create drafts of approved listings; "publish": make existing
    #: drafts of approved listings live.
    action: Literal["drafts", "publish"]


class BatchDeleteRequest(BaseModel):
    batch_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class BatchDeleteResult(BaseModel):
    """What deleting batches removed, and what it left alone on Etsy (v7 §E3)."""

    deleted: int = 0
    files_removed: int = 0
    #: Drafts or live listings made from these batches: left on Etsy, untouched.
    listings_left_on_etsy: int = 0
    #: Queued jobs of these batches (drafts, go-lives, schedules) that were cancelled.
    jobs_cancelled: int = 0


class BatchActionItem(BaseModel):
    batch_id: uuid.UUID
    batch_name: str | None = None
    content_id: uuid.UUID
    original_filename: str
    title: str | None = None
    shop_name: str | None = None
    #: Why it is skipped; None for what will be acted on.
    reason: str | None = None


class BatchActionPreview(BaseModel):
    action: str
    act: list[BatchActionItem] = Field(default_factory=list)
    skipped: list[BatchActionItem] = Field(default_factory=list)
    #: Drafts only: the day's budget check, as the review page shows it.
    estimated_calls: int = 0
    fits: bool = True
    message: str | None = None


class PublishTarget(BaseModel):
    connection_id: uuid.UUID
    # Which of that shop's profiles builds its draft; omitted = chosen for you
    # (the content's own profile in its own shop, else a same-named one, else the
    # only one of the same kind).
    profile_id: uuid.UUID | None = None


class PublishPair(BaseModel):
    """One cell of the matrix: this listing, as a draft in this shop."""

    content_id: uuid.UUID
    connection_id: uuid.UUID
    profile_id: uuid.UUID | None = None


class PublishRequest(BaseModel):
    # Omitted = each listing goes to the shop it was written for.
    targets: list[PublishTarget] | None = Field(default=None, max_length=50)
    # Limit to these listings (a single card); omitted = every approved one.
    content_ids: list[uuid.UUID] | None = None
    # Exactly these listing-and-shop combinations (the matrix's ticked cells).
    # With it, ``targets`` only says which profile each shop uses.
    pairs: list[PublishPair] | None = Field(default=None, max_length=2000)


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


class MatrixCellOut(BaseModel):
    connection_id: uuid.UUID
    # "available" (a draft can be created), "draft" / "live" (it already has one
    # there), or "unavailable" with the reason.
    state: str
    reason: str | None = None
    # The profile the draft would be built from.
    profile_id: uuid.UUID | None = None
    profile_name: str | None = None
    # Whether this request would create it.
    chosen: bool = False
    # Unavailable only because the profile is not set up in this shop yet: the
    # review page offers "Set up <profile> in <shop>…" (v8 §C).
    setup: bool = False


class MatrixRowOut(BaseModel):
    content_id: uuid.UUID
    title: str | None = None
    original_filename: str
    group_key: str | None = None
    approved: bool
    # The shop the listing was written for.
    own_connection_id: uuid.UUID | None = None
    cells: list[MatrixCellOut] = Field(default_factory=list)


class MatrixColumnOut(BaseModel):
    connection_id: uuid.UUID
    shop_name: str | None
    profiles: list["ProfileChoiceOut"] = Field(default_factory=list)
    drafts: int = 0  # drafts this request would create in this shop
    estimated_calls: int = 0


class PublishPreviewOut(BaseModel):
    """What a publish would do, before it is confirmed (v5 §E quota protection)."""

    shops: list[ShopTargetOut]
    # Every listing against every connected shop: what would be created where,
    # what is there already, and what cannot go there and why (Priority 2).
    columns: list[MatrixColumnOut] = Field(default_factory=list)
    rows: list[MatrixRowOut] = Field(default_factory=list)
    drafts: int  # drafts that would be created
    estimated_calls: int  # ~15 Etsy requests per draft
    calls_per_draft: int
    # What these drafts may still spend today: the account's remaining requests,
    # or less when the app's shared budget is the smaller (``limited_by`` "app").
    budget_remaining: int
    limited_by: str = "account"
    # The account's ceiling, identical to what the sidebar and batch page show.
    ceiling: EtsyCeilingOut | None = None
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
    #: "classic" or "search" (Etsy's current search guidance); the seller's choice.
    listing_style: Literal["classic", "search"] | None = None
    #: The search style's title bounds. Sending null resets one to the default.
    title_min_length: int | None = Field(default=None, ge=20, le=140)
    title_max_length: int | None = Field(default=None, ge=20, le=140)
    #: The seller's personalization override (v7 §D4). Sending null resets it to
    #: the reference's question; leaving it out changes nothing.
    personalization: dict[str, Any] | None = None


class PersonalizationOut(BaseModel):
    enabled: bool
    question_text: str | None = None
    instructions: str | None = None
    required: bool = False
    max_allowed_characters: int | None = None


class ReferenceImageOut(BaseModel):
    listing_image_id: int | None = None
    rank: int | None = None
    url: str | None = None
    kind: str | None = None  # "size_chart" | "artwork" | null (unclassified)
    is_fixed: bool = False  # currently included on every draft (B3)


class LinkMissingOut(BaseModel):
    resource: str  # "shipping_profile" | "return_policy" | "readiness_state" | "production_partners"
    label: str
    reason: str


class ProfileLinkOut(BaseModel):
    """The profile in one shop (v8 §C): usable there, or what is missing and why."""

    connection_id: uuid.UUID
    shop_name: str | None = None
    main: bool = False  # the shop its reference listing is in
    ready: bool = False
    status: str = "ready"  # "ready" | "checking" | "incomplete" | "error"
    reason: str | None = None
    missing: list[LinkMissingOut] = Field(default_factory=list)
    checked_at: datetime | None = None


class ProfileOut(BaseModel):
    id: uuid.UUID
    #: The profile's main shop: its reference listing is there (v8 §C).
    connection_id: uuid.UUID
    shop_name: str | None = None
    name: str
    #: None after the main shop changed, until a reference is chosen there.
    reference_listing_id: int | None = None
    #: Every shop the profile is used in, the main one first.
    links: list[ProfileLinkOut] = Field(default_factory=list)
    content_template: str
    source: str = "manual"  # "manual" | "detected"
    confirmed: bool = True
    title_prefix: str = ""  # prepended to every generated title (e.g. "COMFORT COLORS")
    listing_style: str = "classic"  # "classic" | "search"
    #: Whether this kind of profile has the search style at all (apparel does).
    search_style_available: bool = False
    #: The bounds listings are written to and the review counter follows.
    title_min_length: int = 110
    title_max_length: int = 140
    title_length_custom: bool = False
    #: How many of the category's attribute lists Etsy gave us (search style
    #: fills attributes only from them); None until the profile is next refreshed.
    attribute_lists: int | None = None
    fixed_image_ids: list[int] = Field(default_factory=list)
    updated_at: datetime | None = None
    is_fresh: bool = False  # cached reference payload present and <24h old
    reference_images: list[ReferenceImageOut] = Field(default_factory=list)
    # True once the image links pass the 6h display limit: ids, ranks and
    # size-chart classifications are still returned, the links are not.
    reference_images_expired: bool = False
    images_updated_at: datetime | None = None
    # What new drafts get for personalization (v7 §D4), and whether it is the
    # reference's ("reference"), the seller's own ("custom") or not read yet.
    personalization: PersonalizationOut | None = None
    personalization_source: str = "unknown"
    # The last refresh failed, and why (v6 §H); None once a refresh succeeds.
    refresh_error: str | None = None
    refresh_failed_at: datetime | None = None
    # Kept warm in the background: it wrote a listing or made a draft in the
    # last two weeks. Otherwise it refreshes when viewed or used.
    in_use: bool = False
    # A refresh was queued by this request; the data lands shortly.
    refreshing: bool = False
    # The reference listing's title, for finding a profile by it (v7 §D1). From
    # the six-hour listing cache only, so never older than listing content may be.
    reference_title: str | None = None


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
    """One shop's headline counts, from two sources that are labelled apart.

    ``app_published_*``: listings published **through the app**, counted from
    the publication records. They are the app's own history, so they are always
    known and do not depend on any cache.

    Everything else is counted from the cached copy of the shop's listings, which
    may be held for six hours at most: **every** listing in the shop, however it
    was published. When that copy is missing or too old those counts are not
    zero, they are unknown: ``shop_counts_known`` is false and a refresh has
    been queued (``syncing``).
    """

    app_published_this_month: int = 0
    app_published_last_month: int = 0
    shop_counts_known: bool = False
    # ``published_this_month`` needs the shop's listings themselves (six hours);
    # the counts by state do not (a day). False: that one figure is unknown.
    published_known: bool = False
    syncing: bool = False
    total: int = 0
    active: int = 0
    draft: int = 0
    published_this_month: int = 0
    published_last_month: int = 0
    fetched_at: datetime | None = None
    stale: bool = False  # cache is older than its 6h window (no refresh triggered)


class ReplaceImagesRequest(BaseModel):
    batch_id: uuid.UUID  # the uploaded batch of new product photos
    #: Only this listing group's photos, in the order the seller set ("" = the
    #: files at the root of the upload). None: every photo in the batch (B4).
    group_key: str | None = None
    #: "photos": only the images change; the title, tags and description stay
    #: as they are. No AI call, so it does not count as a listing generated.
    #: "full": also analyses the new cover and writes a new title and 13 tags
    #: (one listing generated).
    mode: Literal["photos", "full"] = "photos"


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
    # Permissions the app now asks for that this shop has not granted (connected
    # before they were added): the seller reconnects once to grant them.
    missing_scopes: list[str] = Field(default_factory=list)
    #: Its shop group (v8 §B); None = in no group.
    group_id: uuid.UUID | None = None


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
