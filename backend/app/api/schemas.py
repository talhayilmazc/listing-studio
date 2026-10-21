"""API request/response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

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
    #: The folder it was uploaded in ("" or None: loose): lets the page preview
    #: another grouping before it is chosen (pipeline/grouping.py).
    upload_folder: str | None = None
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
    #: Every status the batch is in (pipeline/batch_status.py, the one definition): the Batches
    #: filter, and ``headline``, the one every badge shows (Batches, the batch page, Overview).
    statuses: list[str] = Field(default_factory=list)
    headline: str | None = None
    #: Beside the badge: what went wrong with some of its listings ("1 draft failed"), A4.
    status_notes: list[str] = Field(default_factory=list)
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
    #: The seller's choice: "folder" | "sku" | "one"; None: folders, then SKUs.
    grouping_mode: str | None = None
    #: The grouping can still be changed: nothing has been written yet.
    regroupable: bool = True


class BoardMove(BaseModel):
    """Move photos to another group, to Unsorted ("~unsorted"), or (``new_group``)
    into a new group named ``to`` (its SKU). They join the end of the group."""

    asset_ids: list[uuid.UUID] = Field(min_length=1, max_length=2000)
    to: str = Field(max_length=200)
    new_group: bool = False


class BoardMerge(BaseModel):
    """Put every photo of ``from_key`` into ``into_key``, after its own."""

    from_key: str = Field(max_length=500)
    into_key: str = Field(max_length=500)


class BoardSku(BaseModel):
    group_key: str = Field(max_length=500)
    sku: str = Field(min_length=1, max_length=200)


class GroupingChoice(BaseModel):
    mode: Literal["folder", "sku", "one"]


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


class ProfileSetupNote(BaseModel):
    """A group's profile cannot build a draft in the group's shop yet: said on the
    group card where the profile is chosen, not first when a draft fails."""

    profile_id: uuid.UUID
    profile_name: str
    connection_id: uuid.UUID
    shop_name: str | None = None
    reason: str
    #: Not set up there, or failed: the setup dialog starts it. Either way the dialog (on the batch page) settles what is left.
    setup: bool = False
    checking: bool = False


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
    # Its written listing: who wrote the title and tags ("ai" / "manual"; None =
    # not written), and the profile it is built with (normally the group's).
    source: str | None = None
    listing_profile_id: uuid.UUID | None = None
    listing_profile_name: str | None = None
    # Set when the group's profile is not usable in the group's shop yet.
    profile_setup: "ProfileSetupNote | None" = None
    # The size charts its drafts get (from its size-chart profile, else its profile),
    # in their order, and where they sit among the photos (pipeline/chart_order.py):
    # one slot per chart (photos before it, -1 = after every photo).
    size_charts: list["SizeChartOut"] = Field(default_factory=list)
    chart_slots: list[int] = Field(default_factory=list)
    chart_slots_custom: bool = False  # dragged here; otherwise the profile's default
    chart_position: str | None = None  # the profile's preset: "after_cover" | "third" | "last"
    # Who decided the placement: "group" (changed for this group), "profile" (each
    # chart dragged on the profile) or "preset" (the profile's preset).
    chart_source: str = "preset"
    chart_profile_name: str | None = None
    # The listing on Etsy the charts are on (the profile's reference): the back link.
    chart_listing_id: int | None = None
    # Set when its photos and charts are more than Etsy keeps: what will be left out.
    image_limit: "ImageLimitOut | None" = None


class ImageLimitOut(BaseModel):
    """A listing group with more images than Etsy keeps: its photos and size
    charts together, the limit, and the photos that will be left out (the last
    ones in the photo order; the charts keep their places)."""

    total: int
    limit: int
    left_out: list[str]


class SizeChartOut(BaseModel):
    listing_image_id: int
    # Withheld once past the 6-hour image limit; the tile still shows it is there.
    url: str | None = None


class ChartPlacement(BaseModel):
    """One size chart's place: the photos before it, or -1 for after every photo."""

    listing_image_id: int
    slot: int


class GroupChartSlots(BaseModel):
    """A group's own size chart placement. ``placement`` is the charts in the order
    they appear, each with its slot; ``slots`` is the older form (one slot per
    chart, in the order the group lists them). Both null follows the profile again."""

    group_key: str = Field(max_length=500)
    slots: list[int] | None = Field(default=None, max_length=20)
    placement: list[ChartPlacement] | None = Field(default=None, max_length=20)


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


class ValidationInfo(BaseModel):
    valid: bool
    errors: list[str]
    #: Not blocking: the app's quality rules on a listing the seller wrote (content.check_listing).
    warnings: list[str] = Field(default_factory=list)


class ContentOut(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    # The cover the title and tags were written from; differs from asset_id once
    # the group's cover changed (the page offers "Regenerate").
    written_from_asset_id: uuid.UUID | None = None
    title: str | None
    tags: list[str]
    description: str | None
    approved: bool
    # Which model wrote it and what it cost are ours, not the seller's: they are
    # not in any response a seller can receive (core/ai_meter.py).
    # The shop this content was written for: its group's, else its batch's, else
    # its profile's main shop.
    connection_id: uuid.UUID | None = None
    # One draft per shop it was sent to (v5 §E).
    publications: list["PublicationOut"] = Field(default_factory=list)
    # asset context for the review screen
    original_filename: str
    parsed_sku: str | None
    rank: int | None
    # Its listing group in the batch (the link back to where its profile is chosen).
    group_key: str = ""
    # Who wrote the title and tags: "ai" (the app) or "manual" (the seller; no AI call).
    source: str = "ai"
    # The listing's profile, read-only here: it is chosen at upload or on the group.
    profile_id: uuid.UUID | None = None
    profile_name: str | None = None
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
    # Set when its group's photos and size charts are more than Etsy keeps.
    image_limit: "ImageLimitOut | None" = None
    # Set when its profile looks print-on-demand and names no production partner
    # (pipeline/creativity.py): a warning on the card, before any draft.
    production_note: str | None = None
    # The search-readiness checklist (pipeline/checklist.py): computed from the
    # listing's own text and settings, never blocking anything.
    checklist: "ChecklistOut | None" = None
    # Short-title style: the title the app wrote still broke Etsy's title guidance
    # after one rewrite (kept as written, never cut); one sentence each. Cleared
    # when the seller edits the title.
    title_warnings: list[str] = Field(default_factory=list)
    #: A listing the seller wrote: what Etsy would refuse (``errors``, blocking) and the
    #: app's quality rules it does not meet (``warnings``, not blocking), for its saved text.
    validation: ValidationInfo | None = None


class CheckOut(BaseModel):
    key: str
    label: str
    status: str  # "pass" | "warn" | "fail"
    reason: str
    source: str


class ChecklistOut(BaseModel):
    version: int
    heading: str
    note: str
    summary: str
    passed: int
    total: int
    checks: list[CheckOut] = Field(default_factory=list)


class ChecklistPreview(BaseModel):
    """The card's unsaved text, checked without saving; a field left out is the saved one."""

    title: str | None = Field(default=None, max_length=1000)
    tags: list[Annotated[str, Field(max_length=200)]] | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=100_000)


class ReferenceText(BaseModel):
    """The title and tags of a profile's reference listing (the seller's own), to start a manual listing from."""

    title: str
    tags: list[str]
    listing_id: int
    profile_name: str


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
    #: Free rewrites made in the period (not in ``used``), and how many each design gets.
    rewrites: int = 0
    free_rewrites: int = 3
    #: Listings that went live through the app in the same period, in all the account's shops.
    published: int = 0
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


class ContentText(BaseModel):
    """A listing's text as a card last read it from the server (only the fields sent)."""

    title: str | None = None
    tags: list[str] | None = None
    description: str | None = None


class ContentUpdate(BaseModel):
    title: str | None = None
    tags: list[str] | None = None
    description: str | None = None
    #: What the card last read for the fields it sends. A field changed elsewhere since
    #: (another tab or window) is refused with 409 instead of being overwritten.
    base: ContentText | None = None
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
    #: The same, short, as the card shows it ("needs a title"), each once.
    reasons: list[str] = Field(default_factory=list)


class ApproveGroup(BaseModel):
    """One reason, counted: "14 listings have no title yet: write them, or press Generate"."""

    line: str
    count: int
    content_ids: list[uuid.UUID]


class ApproveAllResult(BaseModel):
    approved: int = 0  # newly approved now
    already_approved: int = 0
    skipped: list[ApproveSkipped] = Field(default_factory=list)  # failed validation
    #: One line for the whole result (pipeline/approval.py); None when nothing was skipped.
    summary: str | None = None
    #: Why listings were not approved, one line per reason with how many.
    problems: list[ApproveGroup] = Field(default_factory=list)
    #: Listings the seller wrote that were approved with notes (quality rules), counted.
    notes: list[ApproveGroup] = Field(default_factory=list)


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
    #: "Generate the empty ones": write groups with no listing and the seller's own
    #: listings nothing was typed into yet (no title, no tags, the profile's
    #: description untouched); every listing with anything typed is left as it is.
    only_empty: bool = False


class ManualRequest(BaseModel):
    """"Write title and tags myself": one group, or every group not written yet."""

    group_key: str | None = None
    ignore_unsorted: bool = False


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
    estimated_calls: int = Field(default=0, exclude=True)  # Etsy request count: never sent to sellers (A3); admins see it
    fits: bool = True
    message: str | None = None


class PublishTarget(BaseModel):
    connection_id: uuid.UUID
    # Not used any more: a listing is always built with its own profile, the one
    # chosen at upload or on its group (kept so older clients are not refused).
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


class ShopTick(BaseModel):
    """Tick (or untick) one shop, or every shop of one shop group, for the batch's
    listings on the review page. Exactly one of the two."""

    connection_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    on: bool
    #: Only these listings; omitted = every listing of the batch.
    content_ids: list[uuid.UUID] | None = None


class ShopTickOut(BaseModel):
    changed: int
    #: Profiles now being set up in that shop because a ticked listing needs them there.
    setting_up: list["ProfileChoiceOut"] = Field(default_factory=list)


class ShopNeedOut(BaseModel):
    """Something a ticked shop still needs, said before anything is approved or created."""

    #: "profile" (not set up there), "shipping_profile" / "return_policy" /
    #: "readiness_state" / "production_partners" (set up, that one missing),
    #: "checking" (being set up now), "section", or "other".
    kind: str
    message: str
    #: How many ticked listings it concerns.
    listings: int = 0
    #: True: no draft is created there until it is settled. False: the draft is
    #: created without it (a section this shop does not have).
    blocking: bool = True
    profile_id: uuid.UUID | None = None
    profile_name: str | None = None
    #: The profile is not set up there yet; the setup dialog on the review page does it (one dialog for every shop).
    setup: bool = False
    checking: bool = False


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
    # Ticked for this listing (kept on the listing): a draft is created here once
    # the listing is approved and the shop has what it needs.
    wanted: bool = False
    # What stops it is this shop's setup (the profile there), not the listing's text.
    by_shop: bool = False
    # Something the seller should know before the draft is made (it does not stop it):
    # the title is rewritten once for this shop's longer prefix (pipeline/title_fit.py).
    note: str | None = None


class MatrixRowOut(BaseModel):
    content_id: uuid.UUID
    title: str | None = None
    original_filename: str
    group_key: str | None = None
    approved: bool
    # The shop the listing was written for.
    own_connection_id: uuid.UUID | None = None
    cells: list[MatrixCellOut] = Field(default_factory=list)
    # The listing's profile: the one chosen at upload or on its group. It builds
    # the draft in every shop; it is changed on the group, not here.
    profile_id: uuid.UUID | None = None
    profile_name: str | None = None


class MatrixColumnOut(BaseModel):
    connection_id: uuid.UUID
    shop_name: str | None
    profiles: list["ProfileChoiceOut"] = Field(default_factory=list)
    drafts: int = 0  # drafts this request would create in this shop
    estimated_calls: int = Field(default=0, exclude=True)  # Etsy request count: never sent to sellers (A3); admins see it
    ticked: int = 0  # listings ticked for this shop (drafted ones included)
    # What this shop still needs for the listings ticked for it, before approving.
    needs: list[ShopNeedOut] = Field(default_factory=list)


class PublishPreviewOut(BaseModel):
    """What a publish would do, before it is confirmed (v5 §E quota protection)."""

    shops: list[ShopTargetOut]
    # Every listing against every connected shop: what would be created where,
    # what is there already, and what cannot go there and why (Priority 2).
    columns: list[MatrixColumnOut] = Field(default_factory=list)
    rows: list[MatrixRowOut] = Field(default_factory=list)
    drafts: int  # drafts that would be created
    estimated_calls: int = Field(exclude=True)  # Etsy request count: never sent to sellers (A3); admins see it
    calls_per_draft: int = Field(exclude=True)  # Etsy request count: never sent to sellers (A3); admins see it
    # What these drafts may still spend today: the account's remaining requests,
    # or less when the app's shared budget is the smaller (``limited_by`` "app").
    budget_remaining: int = Field(exclude=True)  # Etsy request count: never sent to sellers (A3); admins see it
    limited_by: str = "account"
    # The account's ceiling, identical to what the sidebar and batch page show.
    ceiling: EtsyCeilingOut | None = Field(default=None, exclude=True)  # Etsy request count: never sent to sellers (A3); admins see it
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
    # Photos the job did not send because the listing would have had more images
    # than Etsy keeps (the last ones in the photo order; the size charts stayed).
    left_out: list[str] = Field(default_factory=list)


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
    #: Where the size charts go on its drafts (pipeline/chart_order.py).
    size_chart_position: Literal["after_cover", "third", "last"] | None = None
    #: Each chart dragged to its own place: the charts in the order they appear,
    #: each with its slot. Null goes back to the preset above. Choosing a preset
    #: (sending ``size_chart_position``) also clears it.
    size_chart_placement: list["ChartPlacement"] | None = Field(default=None, max_length=20)
    #: Its listings' Occasion / Holiday unless the design shows another: one of
    #: Etsy's values, "" = none; sending null removes the default.
    default_occasion: str | None = Field(default=None, max_length=200)
    default_holiday: str | None = Field(default=None, max_length=200)
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
    #: One line about what the app did on its own there: "Shipping profile "MemoPaid" was
    #: created in PCS, a copy of the main shop's." / "… is no longer in PCS: a new copy was made."
    notice: str | None = None


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
    #: The reference listing's materials, copied to new drafts; [] when it has none,
    #: None until the profile is next refreshed (read before materials were copied).
    materials: list[str] | None = None
    fixed_image_ids: list[int] = Field(default_factory=list)
    size_chart_position: str = "last"  # "after_cover" | "third" | "last"
    # Where each chart goes by default, in the order they appear (what drafts use
    # unless a group moved them), and whether the seller dragged them (else the preset).
    size_chart_placement: list["ChartPlacement"] = Field(default_factory=list)
    size_chart_custom: bool = False
    # The most images a listing holds: the editor shows this many places.
    max_listing_images: int = 20
    #: Occasion / Holiday defaults ("" = none, null = no default) and Etsy's values
    #: for its category to choose them from.
    default_occasion: str | None = None
    default_holiday: str | None = None
    occasion_values: list[str] = Field(default_factory=list)
    holiday_values: list[str] = Field(default_factory=list)
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
    #: One line when drafts would be refused: a calculated shipping profile and no weight
    #: or size on the reference listing, with what to do (reference.size_problem).
    shipping_note: str | None = None
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
    #: When the shop's counts were last read from Etsy (its listings or its per-state
    #: counts, whichever is newer), even past the time they may be shown: "not read since
    #: 2:10 PM". None: never read (a shop just connected, before its first read).
    counts_read_at: datetime | None = None


class ShopOverviewRow(BaseModel):
    """One shop on Overview's "All shops"."""

    id: uuid.UUID
    name: str
    #: The shop groups it is in.
    groups: list[str] = Field(default_factory=list)
    #: Confirmed profiles that can build a draft there (its own and those set up there).
    profiles: int = 0
    summary: ShopSummaryOut


class ShopsOverviewOut(BaseModel):
    shops: list[ShopOverviewRow]
    #: Every shop added up; a shop whose counts are unknown makes the shop-wide total "syncing".
    total: ShopSummaryOut


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
    #: The shop groups it is in (v8 §B; a shop may be in several); [] = in none.
    group_ids: list[uuid.UUID] = Field(default_factory=list)


#: The notice Etsy's API Terms of Use ask every application to show, word for word.
#: It also meets the Commercial Access review criterion (developers.etsy.com/documentation):
#: it carries the trademark sentence and the not-endorsed-or-certified statement. One copy for
#: the API; the pages use the same text (frontend/lib/legal.ts, checked by a test).
TRADEMARK_NOTICE = (
    "The term 'Etsy' is a trademark of Etsy, Inc. This Application uses Etsy's API, "
    "but is not endorsed or certified by Etsy."
)
PROVIDER_DISCLAIMER = (
    "THIS APPLICATION IS SOLELY PROVIDED BY TALHA YILMAZ AND AHMET DÜZGÜN, OPERATING AS LISTYRO."
)


class ShopSlotsOut(BaseModel):
    used: int
    limit: int
    app_used: int
    app_limit: int
    can_add: bool


class DisconnectedOut(BaseModel):
    """What disconnecting a shop cancelled, so the seller is told."""

    shop_name: str | None = None
    queued_jobs: int = 0
    scheduled_publishes: int = 0
    planned_drafts: int = 0


class ShopsOut(BaseModel):
    shops: list[ShopOut]
    slots: ShopSlotsOut
    # New shop connections are paused by the operator; the notice is shown on the Shops page.
    connections_paused: bool = False
    pause_notice: str | None = None
    # Only in the answer to a disconnect: what was cancelled with the shop.
    disconnected: DisconnectedOut | None = None


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
    trademark_notice: str = TRADEMARK_NOTICE
    # Who provides the application, as Etsy's API terms ask it to be stated.
    provider_disclaimer: str = PROVIDER_DISCLAIMER
    # The version of the Terms and Privacy Policy accounts agree to (core/terms.py).
    terms_version: str = ""
    # Months of analytics history kept (an operator setting): the Privacy Policy shows it.
    analytics_retention_months: int = 24


# --- Occasion, Holiday and Section on the review card (pipeline/item_options.py) ---------------


class PropertyOptionsOut(BaseModel):
    name: str
    #: Etsy's own values for the listing's category.
    values: list[str]
    #: How many it takes (1: one).
    max_values: int = 1
    selected: list[str] = Field(default_factory=list)
    source: str = "none"  # "seller" | "writer" | "none"
    #: The profile's default: a value, "" = none, null = no default.
    profile_default: str | None = None


class SectionOptionOut(BaseModel):
    id: int
    title: str


class ShopSectionOut(BaseModel):
    connection_id: uuid.UUID
    shop_name: str | None = None
    #: None while the shop's sections are being read (kept 24 hours).
    sections: list[SectionOptionOut] | None = None
    selected_id: int | None = None
    selected_title: str | None = None
    source: str = "none"  # "seller" | "carried" | "suggested" | "none"
    reason: str = ""
    #: A section chosen in another shop (or chosen here and deleted on Etsy) that
    #: this shop does not have: the card offers to create it, or another pick.
    missing_title: str | None = None
    #: The shop gave the app permission to create sections (shops_w).
    can_create: bool = False


class ItemOptionsOut(BaseModel):
    content_id: uuid.UUID
    occasion: PropertyOptionsOut | None = None
    holiday: PropertyOptionsOut | None = None
    shops: list[ShopSectionOut] = Field(default_factory=list)
    #: Why Occasion / Holiday are not offered (category not read yet, ...).
    note: str | None = None


class ItemOptionsUpdate(BaseModel):
    """Only the fields sent change. ``occasion`` / ``holiday``: Etsy's values ([] =
    none); null = back to the writer's. ``sections``: per shop, a section id, null
    (no section) or "default" (the suggestion again)."""

    occasion: list[str] | None = Field(default=None, max_length=20)
    holiday: list[str] | None = Field(default=None, max_length=20)
    sections: dict[uuid.UUID, int | Literal["default"] | None] | None = None


class ItemOptionsBulk(BaseModel):
    """"Set for selected" on the review page: the same values for each listing."""

    content_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    occasion: list[str] | None = Field(default=None, max_length=20)
    holiday: list[str] | None = Field(default=None, max_length=20)
    #: One shop's section for every selected listing that goes there.
    section_connection_id: uuid.UUID | None = None
    section_id: int | Literal["default"] | None = None


class BulkSkipped(BaseModel):
    content_id: uuid.UUID
    reason: str


class ItemOptionsBulkOut(BaseModel):
    updated: int
    skipped: list[BulkSkipped] = Field(default_factory=list)


class SectionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=24)
    #: The listings that get it as their section in that shop.
    content_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    #: Nothing is created until the seller confirms.
    confirm: bool = False


class SectionCreateOut(BaseModel):
    queued: bool
    title: str
    shop_name: str | None = None
    #: Etsy requests it takes (its sections read again, then the create).
    requests: int = Field(default=3, exclude=True)  # Etsy request count: never sent to sellers (A3); admins see it
    #: The section is already there (nothing to create): it is chosen for these listings.
    exists: bool = False
    message: str



# --- Editable SKU (pipeline/skus.py, api/skus.py) ----------------------------------------------


class GroupSku(BaseModel):
    group_key: str = Field(max_length=500)
    sku: str = Field(max_length=200)


class ListingSku(BaseModel):
    sku: str = Field(max_length=200)


class EtsySkuUpdate(BaseModel):
    """A shop where this listing's draft still has another SKU."""

    connection_id: uuid.UUID
    shop_name: str | None = None
    etsy_listing_id: int
    current_sku: str | None = None
    #: Etsy requests updating it there takes.
    requests: int = Field(default=3, exclude=True)  # Etsy request count: never sent to sellers (A3); admins see it


class SkuResult(BaseModel):
    group_key: str
    sku: str
    #: Another listing in the same shop has it: a warning, never a refusal.
    warnings: list[str] = Field(default_factory=list)
    #: Drafts on Etsy with the old SKU; nothing is sent until "Update SKU on Etsy".
    etsy: list[EtsySkuUpdate] = Field(default_factory=list)


class SkuBulk(BaseModel):
    """"Set SKU for selected": one SKU, or each listing's own with a prefix/suffix."""

    content_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    sku: str | None = Field(default=None, max_length=200)
    prefix: str | None = Field(default=None, max_length=40)
    suffix: str | None = Field(default=None, max_length=40)


class SkuBulkOut(BaseModel):
    updated: int
    results: list[SkuResult] = Field(default_factory=list)
    skipped: list["BulkSkipped"] = Field(default_factory=list)


class SkuEtsyRequest(BaseModel):
    #: Only these shops; empty = every shop whose draft has another SKU.
    connection_ids: list[uuid.UUID] = Field(default_factory=list)
    #: Nothing is sent until the seller confirms.
    confirm: bool = False


class SkuEtsyOut(BaseModel):
    queued: bool
    sku: str
    shops: list[EtsySkuUpdate] = Field(default_factory=list)
    requests: int = Field(default=0, exclude=True)  # Etsy request count: never sent to sellers (A3); admins see it
