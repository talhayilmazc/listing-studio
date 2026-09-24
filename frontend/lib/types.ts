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
}

export interface BatchDetail extends BatchSummary {
  assets: Asset[];
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
  reason: "global_quota" | "tenant_quota";
  message: string;
  resumes_at: string;
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
