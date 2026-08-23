export type AssetStatus = "uploaded" | "processed" | "failed";
export type BatchStatus = "uploading" | "processing" | "ready" | "applied" | "failed";

export interface Asset {
  id: string;
  original_filename: string;
  parsed_sku: string | null;
  rank: number | null;
  status: AssetStatus;
  mime_type: string | null;
  width: number | null;
  height: number | null;
  has_content: boolean;
}

export interface BatchSummary {
  id: string;
  status: BatchStatus;
  file_count: number;
  created_at: string;
  asset_count: number;
  processed_count: number;
  approved_count: number;
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
  original_filename: string;
  parsed_sku: string | null;
  rank: number | null;
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

export interface GenerateResult {
  generated: number;
  failed: number;
  skipped: number;
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
