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
  etsy_listing_id: number | null;
  etsy_listing_state: string | null;
  listing_link: string | null;
  original_filename: string;
  parsed_sku: string | null;
  rank: number | null;
}

export interface PublishJob {
  content_id: string;
  job_id: string;
}

export interface PublishSkipped {
  content_id: string;
  reason: string;
}

export interface BatchPublishResult {
  jobs: PublishJob[];
  skipped: PublishSkipped[];
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

export interface JobStatus {
  id: string;
  type: string;
  status: string;
  error: string | null;
  listing_id: number | null;
  listing_url: string | null;
  is_draft: boolean;
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
}
