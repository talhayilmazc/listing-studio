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
}

export interface BatchSummary {
  id: string;
  status: BatchStatus;
  file_count: number;
  created_at: string;
  asset_count: number;
  processed_count: number;
  approved_count: number;
  size_chart_profile_id: string | null;
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
  profile_id: string | null;
  size_chart_profile_id: string | null;
  manual: boolean;
  /** One of the seller's own listings this group follows (v7 §B). */
  pattern_listing_id?: number | null;
}

export interface BatchDetail extends BatchSummary {
  assets: Asset[];
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
  model_used: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  /** The shop this listing was written for (its profile's shop). */
  connection_id: string | null;
  /** Its drafts: one per shop it was sent to (v5 §E). */
  publications: Publication[];
  original_filename: string;
  parsed_sku: string | null;
  rank: number | null;
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
}

export interface ShopTarget {
  connection_id: string;
  shop_name: string | null;
  ready: number;
  blocked: PublishSkipped[];
  profiles: { id: string; name: string; content_template: string; is_fresh: boolean }[];
}

/** What a publish would do, before it is confirmed (v5 §E quota protection). */
export interface PublishPreview {
  shops: ShopTarget[];
  drafts: number;
  estimated_calls: number;
  calls_per_draft: number;
  budget_remaining: number;
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

export interface Profile {
  id: string;
  /** The shop this profile belongs to: each shop has its own reference listings. */
  connection_id: string;
  shop_name: string | null;
  name: string;
  reference_listing_id: number;
  content_template: string;
  source: string; // "manual" | "detected"
  confirmed: boolean;
  title_prefix: string;
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

export interface ListingCost {
  content_id: string;
  asset_id: string;
  model_used: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string;
}

export interface BatchCost {
  listing_count: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: string;
  listings: ListingCost[];
}

export interface QuotaDay {
  date: string; // YYYY-MM-DD (UTC)
  count: number;
}

export interface Quota {
  tenant_used: number;
  tenant_limit: number;
  tenant_remaining: number;
  global_used: number;
  global_limit: number;
  global_remaining: number;
  usage_date: string;
  /** Last 7 days of this shop's API usage, oldest first. */
  history: QuotaDay[];
  /** App-wide count at which new work pauses (90% of global_limit). */
  global_pause_at: number;
  /** Set while new Etsy work is paused for this shop. */
  pause: Pause | null;
  /** Requests today keeping shops and profiles current: not in tenant_used (v7 §D3). */
  upkeep_used?: number;
  /** With ?shop=: that shop's share of today's requests. */
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
  quota_used_today: number;
  daily_quota: number;
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
  /** Connected shops across all accounts, against the app-wide ceiling. */
  shops_used: number;
  shops_limit: number;
  history: DayCount[];
  tenants: {
    id: string;
    email: string;
    used_today: number;
    daily_quota: number;
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

export interface AnalyticsListings {
  data: DataStatus;
  period?: { days: number; start: string; end: string };
  comparison?: Comparison;
  listings: ListingRow[];
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

export type BreakdownMetric =
  | "revenue" | "units" | "orders" | "listing_fees" | "transaction_fees" | "processing_fees"
  | "ads" | "shipping" | "product" | "net";

export interface Breakdown {
  period: { days: number; start: string; end: string };
  metric: BreakdownMetric;
  label: string;
  figure: Partial<Figure> & { value: number | null };
  listed_total: number;
  unattributed: number | null;
  rows: (ListingRef & { value: number; share: number | null })[];
  ledger_types: LedgerType[];
  daily: { day: string; value: number }[];
  notes: string[];
}

export interface CostSettings {
  listing_fee: string;
  transaction_pct: string;
  payment_pct: string;
  payment_fixed: string;
  shipping_cost: string;
  monthly_fixed: string;
  product_cost: string;
  product_cost_by_profile: Record<string, string>;
  product_cost_by_sku: Record<string, string>;
  defaults?: Record<string, string>;
}

export type AdsField = "listing_id" | "title" | "date" | "spend" | "orders" | "revenue" | "views";

export interface AdsPreview {
  headers: string[];
  sample: string[][];
  rows: number;
  mapping: Record<AdsField, string | null>;
}

export interface AdsImportResult {
  upload_id: string | null;
  matched: number;
  replaced: number;
  skipped: number;
  unmatched: { line: number; label: string; why: string }[];
  unmatched_total: number;
  spend: number;
  titles_refreshing: boolean;
}

export interface AdsUpload {
  upload_id: string;
  created_at: string;
  period_start: string;
  period_end: string;
  listings: number;
  spend: number;
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
  started_at: string | null;
  finished_at: string | null;
  synced_at: string | null;
  ledger: LedgerRead;
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
export interface Allowance {
  amount: number;
  period: "daily" | "weekly" | "monthly" | string;
  /** Set for this seller; otherwise the system default. */
  custom: boolean;
  used: number;
  generations: number;
  drafts: number;
  /** Drafts or photo replacements queued: counted as used. */
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
