export type AssetStatus = "uploaded" | "processed" | "failed";
export type BatchStatus = "uploading" | "processing" | "ready" | "applied" | "failed";

export interface Asset {
  id: string;
  original_filename: string;
  parsed_sku: string | null;
  group_key: string | null;
  rank: number | null;
  status: AssetStatus;
  mime_type: string | null;
  width: number | null;
  height: number | null;
  has_content: boolean;
  error: string | null;
  /** The seller's square for when this image is the cover; null = automatic. */
  cover_crop?: { x: number; y: number; size: number; width: number | null; height: number | null } | null;
  /**
   * The image's files were deleted a limited time after its listing was
   * published. With `has_thumbnail` a small cover was kept and is what the
   * image URL now serves; without it there is nothing to show.
   */
  files_removed?: boolean;
  has_thumbnail?: boolean;
}

/** An image the app can still show: its files are there, or its cover thumbnail was kept. */
export const showable = (a: Asset): boolean => !a.files_removed || Boolean(a.has_thumbnail);

export interface BatchSummary {
  id: string;
  status: BatchStatus;
  file_count: number;
  created_at: string;
  asset_count: number;
  processed_count: number;
  approved_count: number;
  size_chart_profile_id: string | null;
  /** What the batch is called: the seller's name (`named`), else one taken
   *  from its contents ("BR5229 + 4 more"). */
  name: string;
  named: boolean;
  /** The shop the batch is for, and every shop its groups or drafts are in. */
  connection_id?: string | null;
  shop_name?: string | null;
  shop_names?: string[];
}

export interface ReplaceImagesResult {
  listing_id: number;
  job_id: string;
}

export interface Group {
  group_key: string;
  sku: string | null;
  image_count: number;
  has_content: boolean;
  /** The shop this group's listing is for; its profile and size charts are that shop's. */
  connection_id: string | null;
  shop_name: string | null;
  profile_id: string | null;
  size_chart_profile_id: string | null;
  /** Set by the seller on this group itself (else carried from the group before, or the batch). */
  manual: boolean;
  /** One of the seller's own listings this group follows (v7 §B). */
  pattern_listing_id?: number | null;
}

export interface BatchDetail extends BatchSummary {
  assets: Asset[];
}

/** What deleting one image of a listing group did. */
export interface ImageDeleteResult {
  batch: BatchDetail;
  group_key: string;
  /** It was the group's last image: the group and the listing written for it are gone. */
  group_removed: boolean;
  /** It was the cover: the next image is the cover now; the saved crop was dropped. */
  cover_changed: boolean;
  /** Drafts or live listings made from the group; they keep the photo on Etsy. */
  listings_on_etsy: number;
}

/** What the compliance scanner found on a listing (trademarks, character artwork). */
export interface Finding {
  rule: string;
  severity: "blocking" | "warning" | "info" | string;
  detail: string | null;
}

export interface Content {
  id: string;
  asset_id: string;
  title: string | null;
  tags: string[];
  description: string | null;
  approved: boolean;
  /** The shop this listing was written for (its profile's shop). */
  connection_id: string | null;
  /** Its drafts: one per shop it was sent to (v5 §E). */
  publications: Publication[];
  original_filename: string;
  parsed_sku: string | null;
  rank: number | null;
  /** The title length this listing's profile asks for. */
  title_min_length?: number;
  title_max_length?: number;
  listing_style?: "classic" | "search";
  /** Category attributes chosen from Etsy's lists, written to the draft. */
  attributes?: Record<string, string>;
  /** Compliance findings, shown on the card before publishing. */
  findings?: Finding[];
  /** Draft or go-live jobs that failed or are still waiting, with the reason. */
  work?: Work[];
}

/** A setting the seller must make on the draft in Shop Manager; Etsy's API cannot. */
export interface ManualStep {
  key: string;
  label: string;
  detail: string;
  /** The seller ticked it for this draft. */
  done: boolean;
}

/** One draft of a listing, in one shop. */
export interface Publication {
  connection_id: string;
  shop_name: string | null;
  etsy_listing_id: number;
  state: "draft" | "active" | string;
  /** Shop Manager for a draft; the public listing once active. */
  listing_link: string;
  /** For a draft: what still has to be set in Shop Manager before publishing. */
  manual_steps: ManualStep[];
  /** When the seller scheduled it to go live (UTC ISO), and where that stands (v6 §G). */
  scheduled_for?: string | null;
  schedule_status?: string | null;
  schedule_note?: string | null;
}

export interface PublishJob {
  content_id: string;
  job_id: string;
  connection_id: string | null;
  shop_name: string | null;
}

export interface PublishSkipped {
  content_id: string;
  reason: string;
  connection_id: string | null;
  shop_name: string | null;
}

export interface BatchPublishResult {
  jobs: PublishJob[];
  skipped: PublishSkipped[];
}

export interface PublishTarget {
  connection_id: string;
  /** Which of that shop's profiles to use; omitted = chosen for you. */
  profile_id?: string | null;
}

export interface PublishRequest {
  /** Omitted = each listing goes to the shop it was written for. */
  targets?: PublishTarget[];
  content_ids?: string[];
  /** Exactly these listing-and-shop combinations (the matrix's ticked cells). */
  pairs?: { content_id: string; connection_id: string; profile_id?: string | null }[];
}

export interface ShopTarget {
  connection_id: string;
  shop_name: string | null;
  ready: number;
  blocked: PublishSkipped[];
  profiles: { id: string; name: string; content_template: string; is_fresh: boolean }[];
}

/** What a publish would do, before it is confirmed (v5 §E quota protection). */
/** One listing in one shop: what would happen there, or why nothing can. */
export interface MatrixCell {
  connection_id: string;
  state: "available" | "draft" | "live" | "unavailable";
  reason: string | null;
  profile_id: string | null;
  profile_name: string | null;
  /** This request would create it. */
  chosen: boolean;
  /** Unavailable only because the profile is not set up in this shop yet (one click fixes it). */
  setup?: boolean;
}

export interface MatrixRow {
  content_id: string;
  title: string | null;
  original_filename: string;
  group_key: string | null;
  approved: boolean;
  own_connection_id: string | null;
  cells: MatrixCell[];
}

export interface MatrixColumn {
  connection_id: string;
  shop_name: string | null;
  profiles: { id: string; name: string; content_template: string; is_fresh: boolean }[];
  drafts: number;
  estimated_calls: number;
}

export interface PublishPreview {
  shops: ShopTarget[];
  columns: MatrixColumn[];
  rows: MatrixRow[];
  drafts: number;
  estimated_calls: number;
  calls_per_draft: number;
  /** What these drafts may still spend today. */
  budget_remaining: number;
  /** "account": the seller's own ceiling is the limit; "app": the app's shared budget is. */
  limited_by: "account" | "app";
  /** The account's ceiling: identical to the sidebar's. */
  ceiling: EtsyCeiling | null;
  fits: boolean;
  listings_that_fit: number;
  message: string | null;
}

/** A connected Etsy shop. `id` is what every shop filter and target refers to. */
export interface Shop {
  id: string;
  name: string;
  shop_name: string | null;
  display_name: string | null;
  shop_id: number | null;
  position: number;
  connected_at: string;
  /** Permissions added since this shop connected; reconnect once to grant them. */
  missing_scopes?: string[];
}

export interface ShopsOut {
  shops: Shop[];
  slots: { used: number; limit: number; app_used: number; app_limit: number; can_add: boolean };
}

export interface ReferenceImage {
  listing_image_id: number | null;
  rank: number | null;
  url: string | null;
  kind: string | null; // "size_chart" | "artwork" | null
  is_fixed: boolean;
}

/** The profile in one shop (v8 §C): usable there, or what is missing and why. */
export interface ProfileLink {
  connection_id: string;
  shop_name: string | null;
  /** The shop its reference listing is in. */
  main: boolean;
  ready: boolean;
  status: "ready" | "checking" | "incomplete" | "error" | string;
  reason: string | null;
  missing: { resource: string; label: string; reason: string }[];
  checked_at: string | null;
}

export interface Profile {
  id: string;
  /** The profile's main shop: its reference listing is there. The profile is the account's (v8 §C). */
  connection_id: string;
  shop_name: string | null;
  name: string;
  /** Null after its main shop changed, until a reference is chosen there. */
  reference_listing_id: number | null;
  /** Every shop the profile is used in, the main one first. */
  links: ProfileLink[];
  content_template: string;
  source: string; // "manual" | "detected"
  confirmed: boolean;
  title_prefix: string;
  /** "classic" (110–140 keyword title) or "search" (Etsy's current guidance). */
  listing_style: "classic" | "search";
  search_style_available: boolean;
  /** The title length listings are written to; the review counter follows it. */
  title_min_length: number;
  title_max_length: number;
  title_length_custom: boolean;
  /** Attribute value lists Etsy gave for the category; null until refreshed. */
  attribute_lists: number | null;
  fixed_image_ids: number[];
  updated_at: string | null;
  is_fresh: boolean;
  reference_images: ReferenceImage[];
  /** Image links passed the 6h display limit; ids and classifications remain. */
  reference_images_expired: boolean;
  images_updated_at: string | null;
  /** The last refresh failed, worded for the seller (v6 §H); null once one succeeds. */
  refresh_error: string | null;
  refresh_failed_at: string | null;
  /** Wrote a listing or made a draft in the last two weeks: kept warm in the background. */
  in_use: boolean;
  /** A refresh was queued when the list was fetched; look again shortly. */
  refreshing: boolean;
  /** What new drafts get (v7 §D4); null until the reference is read for it. */
  personalization: Personalization | null;
  personalization_source: "reference" | "custom" | "unknown" | string;
  /** The reference listing's title while the listing cache is fresh (for search). */
  reference_title?: string | null;
}

export interface Personalization {
  enabled: boolean;
  question_text: string | null;
  instructions: string | null;
  required: boolean;
  max_allowed_characters: number | null;
}

export interface ShopListing {
  listing_id: number;
  title: string | null;
  state: string | null;
  sku: string | null;
  shop_section_id: number | null;
  url: string | null;
  thumbnail_url: string | null;
  /** Unix seconds; for an active listing, when it went live. */
  state_timestamp: number | null;
}

/** Cached listing counts. Reading this never triggers a shop sync. */
export interface ShopSummary {
  /** Published through the app, from its own records: always known. */
  app_published_this_month: number;
  app_published_last_month: number;
  /** False when the shop's listing cache is empty or expired: the shop-wide
   *  counts below are then unknown (not zero), and a refresh is under way. */
  shop_counts_known: boolean;
  /** False: how many went live this month is unknown (it needs the shop's listings). */
  published_known?: boolean;
  syncing: boolean;
  total: number;
  active: number;
  draft: number;
  published_this_month: number;
  published_last_month: number;
  fetched_at: string | null;
  stale: boolean;
}

export interface ShopListings {
  listings: ShopListing[];
  stale: boolean;
}

/**
 * Why queued Etsy work is waiting for the daily reset (production-spec C): the
 * app has used 90% of Etsy's shared daily limit, or this shop its own allowance.
 */
export interface Pause {
  /** The daily reset (global_quota, tenant_quota), or a short wait before the job
   *  runs again by itself (etsy_rate_limit, etsy_unavailable, interrupted). */
  reason: "global_quota" | "tenant_quota" | "etsy_rate_limit" | "etsy_unavailable" | "interrupted";
  message: string;
  resumes_at: string;
}

/** A listing's latest draft or go-live job in one shop, while it has not succeeded. */
export interface Work {
  kind: "draft" | "publish";
  connection_id: string;
  shop_name: string | null;
  job_id: string;
  status: "failed" | "queued" | "running";
  error: string | null;
  pause: Pause | null;
}

export interface JobStatus {
  id: string;
  type: string;
  status: string;
  error: string | null;
  listing_id: number | null;
  listing_url: string | null;
  is_draft: boolean;
  connection_id: string | null;
  shop_name: string | null;
  /** For a finished draft: what still has to be set in Shop Manager. */
  manual_steps: ManualStep[];
  /** Set while the job waits for the reset. It is still queued and will run then. */
  pause: Pause | null;
}

export interface Validation {
  valid: boolean;
  errors: string[];
}

export interface ContentUpdateResult {
  content: Content;
  validation: Validation;
}

/** Admin only: what the AI provider's work costs us (never sent to sellers). */
export interface AiTotals {
  calls: number;
  failed: number;
  listings: number;
  input_tokens: number;
  output_tokens: number;
  cache_write_tokens: number;
  cache_write_1h_tokens: number;
  cache_read_tokens: number;
  cost_usd: string;
  unpriced: boolean;
  cost_per_listing_usd: string | null;
}

export interface AiPrice {
  model: string;
  input: string;
  output: string;
  cache_write: string;
  cache_write_1h: string;
  cache_read: string;
  custom: boolean;
}

export interface AiCost {
  as_of: string;
  today: AiTotals;
  this_month: AiTotals;
  sellers: { id: string | null; email: string | null; today: AiTotals; this_month: AiTotals }[];
  purposes: { purpose: string; label: string; today: AiTotals; this_month: AiTotals }[];
  days: (AiTotals & { day: string; models: (AiTotals & { model: string })[] })[];
  recent: {
    at: string;
    email: string | null;
    purpose: string;
    model: string;
    ok: boolean;
    error: string | null;
    input_tokens: number;
    output_tokens: number;
    cache_write_tokens: number;
    cache_read_tokens: number;
    cost_usd: string | null;
    duration_ms: number | null;
  }[];
  prices: AiPrice[];
  unpriced_models: string[];
}

/** Admin only: what is using the server's disk, and the upload cleanup. */
export interface AdminDisk {
  as_of: string;
  total_bytes: number | null;
  free_bytes: number | null;
  /** `bytes` null: not measured (the server's hourly disk check has not reported). */
  categories: { key: string; label: string; note: string; bytes: number | null; files: number | null }[];
  host_reported_at: string | null;
  host_fresh: boolean;
  retention: { published_days: number; unpublished_days: number };
  retention_defaults: { published_days: number; unpublished_days: number };
  /** False: the daily job only counts, nothing is deleted. */
  retention_applies: boolean;
  last_run: {
    at: string;
    applied: boolean;
    published_groups: number;
    unpublished_groups: number;
    images: number;
    files: number;
    freed_bytes: number;
    thumbnails: number;
    thumbnail_bytes: number;
    waiting: number;
  } | null;
}

/** Admin only: calls, listings and cost of one seller (or all) in one stretch of time. */
export interface AiCell {
  calls: number;
  failed: number;
  listings: number;
  cost_usd: string;
  unpriced: boolean;
  cost_per_listing_usd: string | null;
}

export interface AiBucket {
  /** Istanbul time, with its offset. */
  start: string;
  end: string;
  label: string;
  title: string;
  partial: boolean;
  /** Daily view only: the same date as a UTC day, as the provider's console counts. */
  utc_day: string | null;
  utc: AiCell | null;
  total: AiCell;
}

export interface AiSeriesSeller {
  id: string;
  email: string | null;
  /** The seller's colour: fixed per account, whatever the period or the filter. */
  slot: number | null;
  total: AiCell;
  /** Of the period's cost for all sellers, 0..1. */
  share: string | null;
  cells: AiCell[];
}

/** Admin only: AI cost over time, per seller (never sent to sellers). */
export interface AiSeries {
  period: "24h" | "48h" | "daily" | "weekly" | "monthly";
  seller: string;
  time_zone: string;
  as_of: string;
  start: string;
  end: string;
  buckets: AiBucket[];
  sellers: AiSeriesSeller[];
  total: AiCell;
  all_sellers: AiCell;
  previous: AiCell;
  previous_start: string;
  previous_end: string;
  previous_covered: boolean;
  records_from: string | null;
  change: { cost: string | null; listings: string | null; cost_per_listing: string | null };
  options: { id: string; email: string | null; slot: number | null }[];
}

export interface QuotaDay {
  date: string; // YYYY-MM-DD (UTC)
  count: number;
}

/**
 * "Etsy requests today": the account's own ceiling. The same object on every
 * screen that shows it (sidebar, batch page, review page, admin).
 */
export interface EtsyCeiling {
  label: string;
  limit: number;
  used: number;
  remaining: number;
  /** No number of its own: the default applies. */
  follows_default: boolean;
  default: number;
  /** Requests today keeping shops and profiles current: not part of `used`. */
  upkeep: number;
  /** 00:00 UTC, and that instant as a time in the seller's zone ("7:00 PM CDT"). */
  resets_at: string;
  resets_label: string;
}

export interface Quota {
  ceiling: EtsyCeiling;
  usage_date: string;
  /** Last 7 days of the account's own requests, oldest first. */
  history: QuotaDay[];
  /** Set while new Etsy work is paused, saying which number stopped it. */
  pause: Pause | null;
  /** Set while writing new listings is paused on our side (AI provider). */
  generation_pause?: string | null;
  /** With ?shop=: that shop's part of `ceiling.used`. */
  shop_used: number | null;
}

export interface Meta {
  support_email: string;
  trademark_notice: string;
  /** Operator facts for the legal pages; production refuses to start without them. */
  operator_name: string;
  operator_location: string;
  governing_law: string;
  dispute_venue: string;
  /** Error reports go to Sentry; the Privacy Policy names it only when true. */
  error_tracking: boolean;
}

export interface AssetFailure {
  asset_id: string;
  original_filename: string;
  error: string;
}

export interface GenerateResult {
  generated: number;
  failed: number;
  skipped: number;
  failures: AssetFailure[];
  /** Groups a regenerate left alone on purpose, and why. */
  skipped_groups?: { group_key: string; reason: string }[];
  /** Writing is paused on our side: nothing was attempted or failed. */
  paused?: string | null;
}

export interface InviteRequest {
  id: string;
  email: string;
  shop: string | null;
  note: string | null;
  status: "pending" | "approved" | "declined";
  created_at: string;
  decided_at: string | null;
  has_account: boolean;
}

export interface InviteRequestApproved {
  code: string;
  invite: AdminInvite;
  request: InviteRequest;
}

export interface Connection {
  connected: boolean;
  status: string | null;
  etsy_user_id: number | null;
  shop_name: string | null;
  scopes: string[];
  connected_at: string | null;
  expires_at: string | null;
}

/** The signed-in account (production-spec A). Never carries a password or token. */
export interface Account {
  id: string;
  email: string;
  daily_quota: number;
  must_change_password: boolean;
  /** Shows the Admin entry. Never trusted: the server re-checks every admin request. */
  is_admin: boolean;
  /** Features an admin turned on for this account (v7 §B). */
  features?: Record<string, boolean>;
  /** IANA zone schedules are entered and shown in; null until detected. */
  time_zone?: string | null;
  /** The seller's own trademark filter (v7 §A4), and whether an admin set it instead. */
  trademark_filter?: boolean;
  trademark_filter_by_admin?: boolean;
  trademark_filter_effective?: boolean;
  trademark_filter_changed_at?: string | null;
}

/** One of the seller's own listings a new listing can be modelled on (v7 §B). */
export interface PatternListing {
  listing_id: number;
  title: string | null;
  tags: string[];
  state: string | null;
  thumbnail_url: string | null;
  url: string;
  units_90d: number | null;
}

// --- Admin panel ------------------------------------------------------------
/** Requests for one kind of work: `counted` go toward the account's daily
 *  ceiling, `upkeep` (the app keeping the shop's data current) do not. */
export interface AdminSpend {
  category: string;
  label: string;
  counted: number;
  upkeep: number;
}

export interface AdminUser {
  id: string;
  email: string;
  is_admin: boolean;
  status: "active" | "suspended";
  must_change_password: boolean;
  created_at: string;
  shop_name: string | null;
  shop_connected: boolean;
  /** Shop names only: an admin never sees a shop's listings or profiles. */
  shops: string[];
  shops_used: number;
  shops_limit: number;
  shops_limit_custom: boolean;
  listings_published: number;
  /** "Etsy requests today" for the account, as its own screens show it. */
  etsy: EtsyCeiling;
  /** What the day's Etsy requests were spent on, largest first. */
  spent_today: AdminSpend[];
  spent_yesterday: AdminSpend[];
  /** Admin override of the trademark filter (v7 §A4): null = the seller's own choice. */
  trademark_filter: boolean | null;
  /** The seller's own choice in Settings, and when they last changed it. */
  trademark_filter_seller: boolean;
  trademark_filter_changed_at: string | null;
  trademark_filter_effective: boolean;
  features?: Record<string, boolean>;
  /** The product allowance in force, its use this period and its reset. */
  allowance: Allowance | null;
}

export type InviteState = "unused" | "used" | "expired" | "revoked";

export interface AdminInvite {
  id: string;
  note: string | null;
  bound_email: string | null;
  state: InviteState;
  created_at: string;
  expires_at: string | null;
  used_at: string | null;
  used_by_email: string | null;
  created_by_email: string | null;
}

/** The code is readable only in this response; only its hash is stored. */
export interface InviteIssued {
  code: string;
  invite: AdminInvite;
}

export interface TempPasswordIssued {
  id: string;
  email: string;
  temporary_password: string;
}

export interface DayCount {
  date: string;
  count: number;
}

export interface AdminUsage {
  usage_date: string;
  global_used: number;
  global_limit: number;
  global_remaining: number;
  /** New jobs stop being started at this app-wide count. */
  pause_at: number;
  /** What may still be spent before new work pauses; when both counters reset. */
  until_pause: number;
  resets_at: string;
  /** What an account follows unless it has its own number. */
  ceiling_default: number;
  /** Connected shops across all accounts, against the app-wide ceiling. */
  shops_used: number;
  shops_limit: number;
  history: DayCount[];
  tenants: {
    id: string;
    email: string;
    used_today: number;
    limit: number;
    follows_default: boolean;
    /** Set when this tenant has work waiting for the reset today. */
    paused_reason: Pause["reason"] | null;
    history: DayCount[];
  }[];
}

/** "Approve all" (docs/duzeltmeler-v6.md §D): listings that fail validation stay unapproved. */
export interface ApproveAllResult {
  approved: number;
  already_approved: number;
  skipped: { content_id: string; original_filename: string; reason: string }[];
}

/** What one uploaded ZIP became (docs/duzeltmeler-v6.md §F). */
export interface ArchiveResult {
  assets: Asset[];
  skipped_unsupported: number;
  skipped_unsafe: number;
  skipped_nested: number;
  failed: { filename: string; error: string }[];
}

/** A scheduled go-live (docs/duzeltmeler-v6.md §G). */
export interface Schedule {
  content_id: string;
  connection_id: string;
  shop_name: string | null;
  title: string | null;
  asset_id: string;
  batch_id: string;
  batch_name?: string | null;
  etsy_listing_id: number;
  /** The draft in Shop Manager, or the live listing: every listing shown links back. */
  listing_link: string;
  scheduled_for: string;
  status: string;
  note: string | null;
  /** The account zone the time is shown in, and its abbreviation then ("CDT"). */
  time_zone?: string | null;
  zone_abbreviation?: string | null;
  /** While waiting for the daily budget: when it goes out instead. */
  resumes_at?: string | null;
}

export interface ScheduleItem {
  content_id: string;
  connection_id: string;
  /** Wall-clock time in the account's zone ("2026-09-28T17:00"); the server converts it. */
  local_time: string;
}

export interface ScheduleResult {
  scheduled: Schedule[];
  skipped: { content_id: string; connection_id: string; reason: string }[];
}

/** Several batches at once, from the Batches page. */
export interface BatchActionItem {
  batch_id: string;
  batch_name?: string | null;
  content_id: string;
  original_filename: string;
  title: string | null;
  shop_name: string | null;
  /** Why it is skipped; null for what will be acted on. */
  reason: string | null;
}

export interface BatchActionPreview {
  action: "drafts" | "publish";
  act: BatchActionItem[];
  skipped: BatchActionItem[];
  estimated_calls: number;
  fits: boolean;
  message: string | null;
}

/** What deleting batches removed, and what it left alone on Etsy (v7 §E3). */
export interface BatchDeleteResult {
  deleted: number;
  files_removed: number;
  listings_left_on_etsy: number;
  jobs_cancelled: number;
}

// --- Analytics (v7 §C, reworked) -----------------------------------------------------
// Money is in the shop's currency, in minor units (cents). A figure is null when
// nothing is behind it: shown blank with its note, never as a zero.

export type FigureSource = "sales" | "ledger" | "allocated" | "rates" | "costs" | "report" | "computed" | "none";

export interface Figure {
  value: number | null;
  source: FigureSource | string;
  note: string | null;
}

export type ListingClass = "winner" | "steady" | "fading" | "ad_sink" | "loser" | "new";

export interface DataStatus {
  connected: boolean;
  can_read_sales: boolean;
  currency?: string | null;
  sales?: { state: string; from: string | null; synced_at: string | null; read: number; of: number | null; note: string | null };
  ledger?: { state: string; from: string | null; to: string | null; history?: string; read: number; of: number | null; note: string | null };
  reports_until?: string | null;
  titles_refreshing?: boolean;
  listing_counts?: Record<string, number | boolean> | null;
  costs_entered?: { product: boolean; shipping: boolean; fixed: boolean; rates: boolean };
}

export type LineKey = "listing_fees" | "transaction_fees" | "processing_fees" | "ads" | "shipping" | "product" | "fixed";

export interface LedgerType {
  ledger_type: string;
  category: string | null;
  amount: number;
  entries: number;
  counted: boolean;
  label: string;
}

export interface Totals {
  revenue: Figure;
  units: number | null;
  orders: number | null;
  aov: number | null;
  lines: Record<LineKey, Figure>;
  costs: number;
  net: Figure;
  margin: number | null;
  net_excludes: string[];
  ads_unattributed: number | null;
  ledger_types: LedgerType[];
  notes?: string[];
}

export interface ListingRef {
  listing_id: number;
  title: string | null;
  state: string | null;
  url: string;
  thumbnail_url: string | null;
  sku: string | null;
  profile_name: string | null;
  launched: string | null;
}

export interface Economics {
  units: number;
  orders: number;
  revenue: number;
  fees: Record<"listing_fees" | "transaction_fees" | "processing_fees", number>;
  fees_total: number;
  fees_source: "allocated" | "rates" | string;
  product: number | null;
  unit_cost: string | null;
  unit_cost_source: string | null;
  shipping: number | null;
  ads: number | null;
  ad_orders: number | null;
  ad_revenue: number | null;
  net: number;
  margin: number | null;
  net_per_unit: number | null;
  net_excludes: string[];
  contribution: number;
  break_even_acos: number | null;
  acos: number | null;
  spend_per_sale: number | null;
}

export type ActionKind = "ad_sink" | "ads_above_break_even" | "selling_at_loss" | "fading" | "turned_down" | "room_to_advertise";

export interface Action {
  listing_id: number;
  kind: ActionKind;
  /** Money at stake per 30 days, minor units. */
  stake: number;
  reason: string;
  action: string;
  links: { label: string; url: string }[];
  listing?: ListingRef;
}

export interface Cohort {
  key: string;
  label: string;
  listings: number;
  selling: number;
  revenue: number;
  units: number;
  revenue_per_listing: number | null;
  first90_revenue: number;
  first90_listings: number;
  first90_per_listing: number | null;
}

export interface SeriesPoint {
  day: string;
  revenue: number;
  avg7: number;
  avg28: number;
}

export interface Comparison {
  mode: "previous" | "year";
  label: string;
  start?: string | null;
  end?: string | null;
  unavailable: string | null;
  year_available?: boolean;
}

export interface AnalyticsSummary {
  data: DataStatus;
  period?: { days: number; start: string; end: string };
  comparison?: Comparison;
  totals?: Totals;
  compared?: Totals | null;
  actions?: Action[];
  actions_total?: number;
  concentration?: {
    total: number;
    top_n: number;
    top_share: number | null;
    top_listings: number[];
    listings_for_80pct: number | null;
    selling_listings: number;
    top: (ListingRef & { revenue: number })[];
  };
  cohorts?: Cohort[];
  series?: { current: SeriesPoint[]; comparison: SeriesPoint[] | null };
}

export type Trend = "rising" | "falling" | "steady" | "turned_up" | "turned_down" | "too_few";

export interface ListingRow extends ListingRef {
  economics: Economics | null;
  comparison: { revenue: number; units: number; net: number } | null;
  trend: Trend | null;
  action: Action | null;
  status: ListingClass | null;
}

export interface AnalyticsDetail {
  data: DataStatus;
  listing: ListingRef;
  period: { days: number; start: string; end: string };
  comparison_label: string;
  economics: Economics | null;
  previous: Economics | null;
  unit_cost: string | null;
  unit_cost_source: string | null;
  trend: { signal: Trend; weekly_units: number[]; avg4: number[] };
  action: Action | null;
  weeks: { start: string; units: number; revenue: number; avg4: number; last_year: number | null }[];
  ads: { period_start: string; period_end: string; spend: number; ad_orders: number; ad_revenue: number; ad_views: number }[];
}

/** One month's import: what was read, beside the app's own calculation. Amounts are minor units. */
export interface ImportMonth {
  month: string;
  shop_name: string | null;
  statement: {
    imported_at: string;
    rows: number;
    first_day: string;
    last_day: string;
    currency: string | null;
    /** The categories add up to exactly this. */
    net_minor: number;
    categories: { key: string; label: string; group: string; minor: number; rows: number }[];
    /** Sales less sales tax and buyer-paid state fees: never the raw sales figure. */
    revenue_minor: number;
    /** Refunds less the sales tax returned with them, and the two parts. */
    refunds_net_minor: number;
    refunded_minor: number;
    tax_returned_minor: number;
    etsy_fees_minor: number;
    ads_minor: number;
    shipping_minor: number;
    pass_through_minor: number;
    /** Transfers to the bank: neither income nor cost. */
    deposits: { day: string; minor: number }[];
    deposits_minor: number;
    unrecognised: { type: string; title: string; category: string; minor: number }[];
    notes: string[];
  } | null;
  ads: {
    report_days: number;
    month_days: number;
    /** "Ad spend for clicks this month" (the Ads report); null: not imported. */
    reported_minor: number | null;
    report_revenue_minor: number | null;
    report_orders: number | null;
    clicks: number | null;
    views: number | null;
    /** "Charged by Etsy this month" (the statement); null: not imported. */
    charged_minor: number | null;
    charge_days: number;
    matched_days: number | null;
    billed_later: ImportAdsDay[];
    billed_from_before: ImportAdsDay[];
    billed_differently: ImportAdsDay[];
    /** The listed days explain the gap to the cent; null until both are imported. */
    exact: boolean | null;
    note: string;
  };
  orders: {
    orders: number;
    matched: number;
    unmatched: number;
    exact: number;
    statement_minor: number;
    items_minor: number;
    shipping_minor: number;
    difference_minor: number;
    unmatched_minor: number;
    differing: number;
    largest: { receipt_id: number; statement_minor: number; items_minor: number; shipping_minor: number; difference_minor: number }[];
    note: string;
  } | null;
  listing_fees: { listings: number; fees: number; minor: number; credits_minor: number } | null;
  comparison: {
    key: string;
    label: string;
    statement_minor: number | null;
    /** null: the app has no figure of its own (never shown as zero). */
    ours_minor: number | null;
    ours_source: string | null;
    difference_minor: number | null;
    status: "match" | "explained" | "unexplained" | "statement_only" | string;
    reason: string;
  }[];
}

export interface ImportAdsDay {
  day: string;
  reported_minor: number | null;
  charged_minor: number | null;
  reason: string;
}

export interface ImportStatus {
  shop_name: string | null;
  currency: string | null;
  months: {
    month: string;
    statement_imported_at: string | null;
    statement_rows: number | null;
    statement_net_minor: number | null;
    statement_first_day: string | null;
    statement_last_day: string | null;
    ads_days: number;
    month_days: number;
    state: string;
  }[];
}

/** Where reading the shop's sales stands, and what it costs (v7 §C1). */
export interface SalesSync {
  can_read: boolean;
  state: "none" | "estimating" | "estimated" | "reading" | "waiting" | "complete" | "failed";
  total_count: number | null;
  window_count: number | null;
  pages_estimate: number | null;
  days_estimate: number | null;
  daily_requests: number;
  read_count: number;
  requests_used: number;
  requests_today: number;
  last_update_requests: number | null;
  resumes_at: string | null;
  note: string | null;
  /** The one-time second read that adds the order lines. */
  reread: "reading" | "done" | null;
  started_at: string | null;
  finished_at: string | null;
  synced_at: string | null;
  ledger: LedgerRead;
}

/** One shop in the one-time sales re-read. Counts and dates only. */
export interface SalesRereadShop {
  connection_id: string;
  shop_name: string | null;
  /** "queued": not begun, its figures stand. "waiting": for the next night or its turn. */
  status: "queued" | "reading" | "waiting" | "done" | "failed";
  read_count: number;
  window_count: number | null;
  requests_used: number;
  requests_left: number | null;
  /** The UTC day it is expected to finish (an estimate). */
  finishes_on: string | null;
  finished_at: string | null;
  note: string | null;
}

export interface SalesReread {
  active: boolean;
  reads_back_to: string;
  per_shop_daily: number;
  resets_at: string;
  requests_left: number;
  finishes_on: string | null;
  shops: SalesRereadShop[];
}

/** Admin only: every account's shops and the nightly share of the app's budget. */
export interface SalesRereadAdmin extends SalesReread {
  nightly_cap: number;
  budget_percent: number;
  used_tonight: number;
  queued: number;
  reading: number;
  done: number;
  failed: number;
  shops: (SalesRereadShop & { email: string | null })[];
}

/** Reading Etsy's payment ledger: the shop's fees and ad spend per day. */
export interface LedgerRead {
  state: "none" | "estimating" | "estimated" | "reading" | "waiting" | "complete" | "failed";
  total_count: number | null;
  pages_estimate: number | null;
  first_days: number;
  read_count: number;
  requests_used: number;
  resumes_at: string | null;
  note: string | null;
  covers_from: string | null;
  covers_to: string | null;
  /** Filling in the 13 months before the first read, in the background. */
  history_state: "none" | "reading" | "waiting" | "complete" | "failed";
  history_target: string | null;
  history_requests: number;
  history_requests_left: number | null;
  history_note: string | null;
}

/** Our product allowance: listings generated plus drafts created per period.
 *  Not Etsy's API quota, which is Etsy's shared daily ceiling. */
/** "Listings generated": designs written in the period. Drafts and publishing do not count. */
export interface Allowance {
  label: string;
  amount: number;
  period: "daily" | "weekly" | "monthly" | string;
  /** Set for this seller; otherwise the system default. */
  custom: boolean;
  used: number;
  generations: number;
  /** Replace images queued, their listing not yet written: counted as used. */
  pending: number;
  remaining: number;
  period_start: string;
  resets_at: string;
  /** "Thu, Oct 1, 12:00 AM CDT", in the seller's own zone. */
  resets_label: string;
  time_zone: string;
}

export interface AllowanceDefault {
  amount: number;
  period: "daily" | "weekly" | "monthly";
}

// --- Analytics: the month view (backend/app/api/pnl.py) ------------------------------------

/** How a figure was arrived at; on every number. */
export type Basis = "exact" | "calculated" | "estimated";

/** One number. `minor` null: nothing behind it, and `note` says why (never shown as zero). */
export interface MonthFigure {
  key: string;
  minor: number | null;
  basis: Basis | null;
  source: string | null;
  note: string | null;
  parts: Record<string, number> | null;
}

export interface MonthSheet {
  month: string;
  /** Where the month's totals come from: statement > ledger > sales > none. */
  source: "statement" | "ledger" | "sales" | "none";
  sections: { key: string; lines: MonthFigure[]; total: MonthFigure }[];
  /** Profit before product cost; on a statement month, the statement's net to the cent. */
  net_etsy: MonthFigure;
  profit: MonthFigure;
  revenue: MonthFigure;
  deposits_minor: number | null;
  ads_report: { spend_minor: number | null; revenue_minor: number | null; days: number; month_days: number };
  break_even: {
    roas: number | null;
    margin: number | null;
    basis: Basis | null;
    note: string | null;
    complete: boolean;
    actual_roas: number | null;
    actual_note: string | null;
  };
  units: number | null;
  complete: boolean;
  incomplete: { key: "no_statement" | "product_cost" | "sales_lines" | string; text: string }[];
}

export type MonthClass = "winner" | "steady" | "fading" | "losing" | "new";

export interface MonthListing {
  listing_id: number;
  title: string | null;
  state: string | null;
  /** The listing's page on Etsy: always present. */
  url: string;
  thumbnail_url: string | null;
  sku: string | null;
  profile_name: string | null;
  class: MonthClass;
  reason: string;
  units: number;
  orders: number;
  items_minor: number;
  shipping_paid_minor: number;
  revenue_minor: number;
  refunds_minor: number | null;
  fees_minor: number | null;
  offsite_ads_minor: number | null;
  other_minor: number | null;
  product_cost_minor: number | null;
  uncosted_units: number;
  before_cost_minor: number | null;
  /** Profit before ads; null until the listing's product cost is entered. */
  result_minor: number | null;
  per_unit_minor: number | null;
  costed: boolean;
  basis: Basis;
  units_before: number;
  /** Items sold in each of `trend_months`, oldest first. */
  trend: number[];
}

export interface MonthAttention {
  kind: string;
  stake_minor: number | null;
  basis: Basis | null;
  title: string;
  why: string;
  do: string;
  listing_id: number | null;
}

export interface MonthView {
  connected: boolean;
  shop_name: string | null;
  currency: string | null;
  month: string;
  months: { month: string; statement: boolean; in_progress: boolean }[];
  sheet: MonthSheet;
  last_month: MonthSheet | null;
  last_year: MonthSheet | null;
  attention: MonthAttention[];
  unattributed: { groups: Record<string, number>; orders: number; orders_minor: number; total: number | null; etsy_ads_minor: number | null };
  listings: MonthListing[];
  classes: Partial<Record<MonthClass, number>>;
  trend_months: string[];
  titles_refreshing: boolean;
  sizes_supported: boolean;
}

export interface ProductCostsView {
  shop_name: string | null;
  profiles: {
    profile_id: string;
    name: string;
    /** Decimal text as entered; null: not entered (never assumed). */
    production: string | null;
    shipping: string | null;
    sizes: Record<string, { production: string | null; shipping: string | null }>;
  }[];
  sizes_supported: boolean;
}

// --- One profile across shops (v8 §C) and shop groups (v8 §B) -------------------------

export interface ResourceSetup {
  resource: "shipping_profile" | "return_policy" | "readiness_state" | "production_partners" | string;
  label: string;
  reason: string | null;
  creatable: boolean;
  create_summary: string | null;
  requests: number;
  options: { id: number; label: string }[];
}

export interface ShopSetup {
  connection_id: string;
  shop_name: string | null;
  link: ProfileLink;
  open: ResourceSetup[];
  choices_expired: boolean;
}

export interface ProfileSetup {
  profile: Profile;
  shops: ShopSetup[];
}

export interface LinkSuggestion {
  name: string;
  profiles: Profile[];
  why: string;
}

export interface ShopGroup {
  id: string;
  name: string;
  shops: { id: string; name: string }[];
}

export interface ShopGroups {
  groups: ShopGroup[];
  ungrouped: { id: string; name: string }[];
}

export type UseInScope = { scope: "all" } | { scope: "group"; group_id: string } | { scope: "shops"; connection_ids: string[] };
