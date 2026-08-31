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
  fixed_image_ids: number[];
  updated_at: string | null;
  is_fresh: boolean;
  reference_images: ReferenceImage[];
}

export interface ShopListing {
  listing_id: number;
  title: string | null;
  state: string | null;
  sku: string | null;
  shop_section_id: number | null;
  url: string | null;
  thumbnail_url: string | null;
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

export interface Quota {
  tenant_used: number;
  tenant_limit: number;
  tenant_remaining: number;
  global_used: number;
  global_limit: number;
  global_remaining: number;
  usage_date: string;
}

export interface Meta {
  support_email: string;
  trademark_notice: string;
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
