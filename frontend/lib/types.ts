export type AssetStatus = "uploaded" | "processed" | "failed";
export type BatchStatus = "uploading" | "processing" | "ready" | "applied" | "failed";

export interface Asset {
  id: string;
  original_filename: string;
  parsed_sku: string | null;
  group_key: string | null;
  /** The folder it was uploaded in ("" or null: loose). */
  upload_folder?: string | null;
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
  /** Every status the batch is in (lib/batchStatus.ts; backend pipeline/batch_status.py). */
  statuses?: string[];
  /** The one status every badge shows (backend pipeline/batch_status.py::headline). */
  headline?: string | null;
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
  /** How the images were grouped (lib/grouping.ts): listing groups, those found
   *  by the SKU in file names and from folders, and photos left Unsorted. */
  groups?: number;
  sku_groups?: number;
  folder_groups?: number;
  unsorted?: number;
  /** "Found 42 groups by SKU, 3 photos unsorted". */
  grouping?: string;
  /** The seller's choice (lib/grouping.ts); null: folders, then SKUs. */
  grouping_mode?: "folder" | "sku" | "one" | null;
  /** The grouping can still change: nothing has been written yet. */
  regroupable?: boolean;
}

export interface ReplaceImagesResult {
  listing_id: number;
  job_id: string;
}

/** More images than Etsy keeps on a listing: the photos that are left out (the last ones). */
export interface ImageLimit {
  total: number;
  limit: number;
  left_out: string[];
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
  /** The size charts its drafts get, in order, and where they sit (lib/chartOrder.ts). */
  size_charts?: { listing_image_id: number; url: string | null }[];
  chart_slots?: number[];
  /** Dragged on this group; otherwise the profile's default. */
  chart_slots_custom?: boolean;
  /** Who decided: "group" (changed for this group), "profile" (dragged on the profile) or "preset". */
  chart_source?: "group" | "profile" | "preset";
  chart_position?: "after_cover" | "third" | "last" | null;
  chart_profile_name?: string | null;
  /** The listing on Etsy the charts are on (the back link). */
  chart_listing_id?: number | null;
  /** Its photos and size charts are more than Etsy keeps: what will be left out. */
  image_limit?: ImageLimit | null;
  /** Its written listing: who wrote it (null = not written) and the profile it is built with. */
  source?: "ai" | "manual" | null;
  listing_profile_id?: string | null;
  listing_profile_name?: string | null;
  /** The group's profile cannot build a draft in the group's shop yet, and why. */
  profile_setup?: ProfileSetupNote | null;
}

export interface ProfileSetupNote {
  profile_id: string;
  profile_name: string;
  connection_id: string;
  shop_name: string | null;
  reason: string;
  /** Not set up there (or failed); either way the setup dialog on the batch page settles it. */
  setup: boolean;
  checking: boolean;
}

/** The title and tags of a profile's reference listing, to start a manual listing from. */
export interface ReferenceText {
  title: string;
  tags: string[];
  listing_id: number;
  profile_name: string;
}

/** Something a ticked shop still needs before its listings can become drafts there. */
export interface ShopNeed {
  kind: string;
  message: string;
  listings: number;
  /** False: the draft is still created, without it (a section the shop does not have). */
  blocking: boolean;
  profile_id: string | null;
  profile_name: string | null;
  setup: boolean;
  checking: boolean;
}

export interface ShopTickResult {
  changed: number;
  /** Profiles now being set up in that shop because a ticked listing needs them there. */
  setting_up: { id: string; name: string }[];
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
  /** The cover the title and tags were written from; differs from asset_id once the cover changed. */
  written_from_asset_id?: string | null;
  title: string | null;
  tags: string[];
  description: string | null;
  approved: boolean;
  /** The shop this listing was written for (its group's, else its batch's, else its profile's main shop). */
  connection_id: string | null;
  /** Its listing group in the batch. */
  group_key?: string;
  /** Who wrote the title and tags: the app, or the seller (no AI call). */
  source?: "ai" | "manual";
  /** The listing's profile, read-only on the review page: chosen at upload or on its group. */
  profile_id?: string | null;
  profile_name?: string | null;
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
  /** Its group's photos and size charts are more than Etsy keeps: what will be left out. */
  image_limit?: ImageLimit | null;
  /** Its profile looks print-on-demand and names no production partner (Etsy's Creativity Standards). */
  production_note?: string | null;
  /** Short-title style: the app's title still broke Etsy's title guidance after one rewrite (kept, never cut). */
  title_warnings?: string[];
  /** A listing the seller wrote: Etsy's refusals (errors) and the app's notes (warnings) on its saved text. */
  validation?: Validation | null;
  /** The search-readiness checklist, worked out by the server (pipeline/checklist.py). */
  checklist?: Checklist | null;
  /** What its drafts get in every shop (v8 §D): its own setting, or its profile's. */
  personalization?: Personalization | null;
  personalization_source?: "listing" | "profile" | "unknown" | string;
}

/** One print area on a template photo (backend api/mockups.py AreaOut). */
export interface MockupArea {
  /** Four corners TL, TR, BR, BL as shares (0..1) of the photo's width and height. */
  print_area: [number, number][];
  /** The suggestion for this area: "Reset to suggestion" and the editor's guides use it. */
  detected_area: [number, number][];
  /** "default" (suggested), "tap" (found from the seller's tap), "seller" (moved), "copied". */
  source: "default" | "tap" | "seller" | "copied" | string;
  confidence?: number | null;
  kind?: string | null;
  /** The analysis's estimate of the garment's own axis (degrees, clockwise): what a turn snaps to. */
  axis_angle?: number | null;
  /** "front" or "back": a back area prints the design's back file. */
  role?: "front" | "back" | string;
  /** The subgroup the seller linked this area to; null: by the garment's tone, else the photo's. */
  subgroup_id?: string | null;
  /** Linked to "Any": the design's own file whatever the garment's tone. */
  subgroup_any?: boolean;
  /** The garment's brightness ("light" or "dark"), read from the photo: the default link. */
  tone?: "light" | "dark" | string | null;
  /** "Design goes behind hair and arms" in this area, and why the analysis turned it off. */
  occlusion?: boolean;
  occlusion_note?: string | null;
}

/** One blank photo of a template (backend api/mockups.py PhotoOut). */
export interface MockupPhoto {
  id: string;
  template_id: string;
  name: string;
  position: number;
  /** "preparing" while the server finds the outline, then "ready" or "failed". */
  status: "preparing" | "ready" | "failed" | string;
  error?: string | null;
  width: number | null;
  height: number | null;
  mask_source: string | null;
  size: number;
  fold_strength: number;
  version: number;
  notice?: string | null;
  method?: string | null;
  confidence?: number | null;
  analysis_note?: string | null;
  /** The seller pressed "Looks right"; until then it is not used for mockups. */
  confirmed: boolean;
  needs_check: boolean;
  occlusion?: boolean;
  has_occluder?: boolean;
  /** The seller painted "Keep in front" on this photo. */
  has_front_brush?: boolean;
  view?: "front" | "back" | string;
  /** "single", "front_back" or "several"; set by the analysis when sure ("auto") or the seller. */
  layout?: "single" | "front_back" | "several" | string;
  layout_source?: "auto" | "seller" | string;
  /** The subgroup of its template the photo belongs to. */
  subgroup_id?: string | null;
  areas: MockupArea[];
  /** The garments the analysis really found (their suggested areas): copies go only onto these. */
  found_areas?: number[][][];
  garments_found?: number;
  /** "Print on all garments": the same design on every area, whatever the run says. */
  print_all?: boolean;
  /** Its suggestion came from the seller's own placement, learnt from this many photos. */
  own_placement?: number | null;
  /** Its colour group for light/dark design variants (null: not measured yet), the measured
   *  garment lightness (CIELAB L*), and where it came from ("uncertain": the seller is asked). */
  colour?: "light" | "dark" | null;
  colour_lightness?: number | null;
  colour_source?: "auto" | "uncertain" | "seller" | null;
}

/** A named group of blank photos (the same tee in ten colours, or several shots of one product). */
export interface MockupTemplate {
  id: string;
  name: string;
  position: number;
  photos: MockupPhoto[];
  needs_check: number;
  confirmed: number;
  /** Its suggestions use the seller's own placement, learnt from this many photos. */
  own_placement?: number | null;
  /** "Back area without a back design": the front design, or nothing. */
  back_without_design?: "front" | "plain" | string;
  /** Its subgroups, in order (at least one, named after it at first). */
  subgroups?: MockupSubgroup[];
  /** Downloads and listing photos named "<prefix>-<SKU>-<NN>.jpg"; null: "<SKU>_<template>_<photo>.jpg". */
  file_prefix?: string | null;
}

/** An upload slot as the designs page sends it: a subgroup's name, file-name suffix and "Use on". */
export interface SlotIn {
  name: string;
  suffix: string | null;
  use_on: string;
}

/** A subgroup of a template ("Comfort Colors White"): which garments it is for and the file-name suffix of its designs. */
/** An account's subgroup preset ("White design", suffix "-white", use on dark garments). */
export interface SubgroupPreset { id: string; name: string; use_on: string; suffix: string | null; position: number }

export interface MockupSubgroup {
  id: string;
  name: string;
  /** "Use on": "any", "dark" garments or "light" garments. */
  use_on: "any" | "dark" | "light" | string;
  /** The file-name suffix that pairs a design's files ("-white"). */
  suffix: string | null;
  position: number;
}

/** The seller's own print placement, learnt from the photos they confirmed. */
export interface MockupPlacement {
  photos: number;
  groups: { key: string; label: string; photos: number; in_use: boolean }[];
}

/** "Add area": the garment under the seller's tap, or a box there when none was found. */
export interface FoundArea {
  found: boolean;
  print_area: [number, number][];
  confidence?: number | null;
  kind?: string | null;
  note?: string | null;
}

/** A design uploaded to be printed on templates: its own listing group. */
export interface MockupDesign {
  id: string;
  original_filename: string;
  group_key: string;
  parsed_sku: string | null;
  width: number;
  height: number;
  has_alpha: boolean;
  /** It has a back file (printed on back areas). */
  has_back?: boolean;
  /** The subgroup (by name) its file is for; null: every subgroup. */
  subgroup?: string | null;
  /** The colour of its ink: "light", "dark" or "mixed". */
  ink?: string | null;
  /** Its own file's light/dark variant (from its name's suffix); null: a plain file. */
  variant?: "light" | "dark" | null;
  /** Only one variant uploaded: use it on the other colour group too. */
  fill_other?: boolean;
  /** Its files for other subgroups and its other variants: one design, one listing. */
  versions?: { id: string; subgroup: string | null; variant?: "light" | "dark" | null; original_filename: string; has_alpha?: boolean }[];
}

/** Before any mockup is made: which design file goes on which photos (backend pipeline/variants.py). */
export interface MockupPlan {
  variant_suffix: "shirt" | "ink";
  photos: { id: string; name: string; template: string; colour: "light" | "dark" | null; colour_source: string | null }[];
  designs: {
    design_id: string;
    variants: boolean;
    files: { file_id: string; filename: string; variant: "light" | "dark" | null; photos: string[] }[];
    missing: string[];
    missing_colours: string[];
    lacking: ("light" | "dark")[];
    uncertain: string[];
    fill_other: boolean;
  }[];
}

export interface MockupOutput {
  id: string;
  photo_id: string | null;
  template_name: string;
  photo_name?: string | null;
  /** "<SKU>_<template name>_<photo name>.jpg" */
  filename: string;
  available: boolean;
  /** Changes when the mockup is made again (its image URL carries it). */
  stamp?: string;
}

/** What the design editor needs for a run (backend api/mockup_edits.py). */
export interface MockupEdits {
  run_id: string;
  photos: { id: string; template_id: string; name: string; width: number; height: number; size: number;
    areas: { print_area: number[][]; subgroup: string | null }[] }[];
  designs: { id: string; original_filename: string; sku: string | null;
    files: { version_id: string | null; subgroup: string | null; filename: string; width: number; height: number;
      trim: number[] | null; trim_size: number[] | null }[] }[];
  adjustments: MockupAdjustmentRow[];
  recolours: MockupRecolourRow[];
  editable: boolean;
}
export interface MockupAdjustmentRow { design_id: string; photo_id: string; area: number; data: Record<string, unknown> | null }
export interface MockupRecolourRow { design_id: string; subgroup: string; regions: unknown[] | null }

export interface MockupRunDesign {
  design_id: string;
  original_filename: string;
  sku: string | null;
  group_key: string;
  done: number;
  total: number;
  in_listings: boolean;
  outputs: MockupOutput[];
}

export interface MockupZip {
  id: string;
  run_id: string;
  status: "queued" | "building" | "ready" | "failed" | "expired" | string;
  layout: "folders" | "flat" | string;
  files?: number | null;
  byte_size?: number | null;
  error?: string | null;
  expires_at?: string | null;
}

/** One "make the mockups" for a batch, with its progress per design. */
export interface MockupRun {
  id: string;
  batch_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | string;
  total: number;
  done: number;
  failed: number;
  error?: string | null;
  templates: string[];
  template_ids?: string[];
  /** Photos printed on, and photos of the chosen templates left out because they are not confirmed. */
  photos: number;
  skipped: number;
  skipped_photos?: string[];
  designs: MockupRunDesign[];
  zips: MockupZip[];
  /** Mockups being made again after the seller's edits (backend pipeline/mockup_refresh.py), each
   * with the version it had when asked for: its tile updates as soon as the version changes. */
  refreshing?: { design_id: string; photo_id: string; from_stamp?: string }[];
  /** Mockups that could not be made again, with the reason. */
  refresh_failed?: { design_id: string; photo_id: string; reason: string }[];
}

/** One check of the search-readiness checklist: what the server found, and why. */
export interface Check {
  key: string;
  label: string;
  status: "pass" | "warn" | "fail";
  reason: string;
  /** Where the rule comes from (a rule in the code, or Etsy's published guidance). */
  source: string;
}

/** The search-readiness checklist of one listing (backend pipeline/checklist.py). */
export interface Checklist {
  version: number;
  heading: string;
  note: string;
  summary: string;
  passed: number;
  total: number;
  checks: Check[];
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
  /** Ticked for this listing (kept on it): a draft is created here once it can be. */
  wanted: boolean;
  /** What stops it is this shop's setup, not the listing's own text. */
  by_shop?: boolean;
  /** Worth knowing before the draft (does not stop it): the title is rewritten once for this shop's prefix. */
  note?: string | null;
}

export interface MatrixRow {
  content_id: string;
  title: string | null;
  original_filename: string;
  group_key: string | null;
  approved: boolean;
  own_connection_id: string | null;
  cells: MatrixCell[];
  /** The listing's profile: it builds the draft in every shop and is changed on the group. */
  profile_id: string | null;
  profile_name: string | null;
}

export interface MatrixColumn {
  connection_id: string;
  shop_name: string | null;
  profiles: { id: string; name: string; content_template: string; is_fresh: boolean }[];
  drafts: number;
  /** Listings ticked for this shop (drafted ones included). */
  ticked: number;
  /** What this shop still needs for the listings ticked for it. */
  needs: ShopNeed[];
}

export interface PublishPreview {
  shops: ShopTarget[];
  columns: MatrixColumn[];
  rows: MatrixRow[];
  drafts: number;
  /** "account": the seller's own ceiling is the limit; "app": the app's shared budget is. */
  limited_by: "account" | "app";
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
  /** The shop groups it is in (a shop may be in several); [] = in none. */
  group_ids?: string[];
}

export interface ShopsOut {
  shops: Shop[];
  slots: { used: number; limit: number; app_used: number; app_limit: number; can_add: boolean };
  /** New shop connections are paused by the operator, and the notice to show. */
  connections_paused?: boolean;
  pause_notice?: string | null;
  /** Only in the answer to a disconnect: what was cancelled with the shop. */
  disconnected?: Disconnected | null;
}

export interface Disconnected {
  shop_name: string | null;
  queued_jobs: number;
  scheduled_publishes: number;
  planned_drafts: number;
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
  /** One line about what the app did there on its own ("… was created in PCS, a copy of the main shop's"). */
  notice?: string | null;
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
  /** The reference listing's materials, copied to new drafts; [] none, null until the next refresh. */
  materials?: string[] | null;
  fixed_image_ids: number[];
  /** Where its size charts go on a draft: the preset... */
  size_chart_position?: "after_cover" | "third" | "last";
  /** ...and each chart's own place (in the order they appear), which is what drafts use. */
  size_chart_placement?: { listing_image_id: number; slot: number }[];
  /** The seller dragged them; otherwise the preset. */
  size_chart_custom?: boolean;
  max_listing_images?: number;
  /** Occasion / Holiday defaults ("" = none, null = no default) and Etsy's values for its category. */
  default_occasion?: string | null;
  default_holiday?: string | null;
  occasion_values?: string[];
  holiday_values?: string[];
  updated_at: string | null;
  is_fresh: boolean;
  reference_images: ReferenceImage[];
  /** Image links passed the 6h display limit; ids and classifications remain. */
  reference_images_expired: boolean;
  images_updated_at: string | null;
  /** The last refresh failed, worded for the seller (v6 §H); null once one succeeds. */
  refresh_error: string | null;
  /** Drafts would be refused (calculated shipping, no weight or size on the reference): what to do. */
  shipping_note?: string | null;
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
/** What needs the seller now (backend api/attention.py): Overview's block and the sidebar badges. */
export interface Attention {
  items: { key: string; label: string; count: number; href: string; nav: string }[];
  total: number;
  /** Sidebar entry (its href) -> the count on its badge. */
  nav: Record<string, number>;
}

export interface ShopOverviewRow {
  id: string;
  name: string;
  groups: string[];
  profiles: number;
  summary: ShopSummary;
}

export interface ShopsOverview {
  shops: ShopOverviewRow[];
  total: ShopSummary;
}

export interface ShopMonthFigure {
  minor: number | null;
  note?: string | null;
}

/** Analytics' "All shops": one month for each shop, and a total per currency. */
export interface AnalyticsShops {
  month: string;
  months: string[];
  shops: {
    id: string;
    name: string;
    currency: string | null;
    source: "statement" | "ledger" | "sales" | "none";
    revenue: ShopMonthFigure;
    net_etsy: ShopMonthFigure;
    profit: ShopMonthFigure;
    units: number | null;
  }[];
  totals: {
    currency: string | null;
    shops: number;
    revenue: ShopMonthFigure;
    net_etsy: ShopMonthFigure;
    profit: ShopMonthFigure;
    units: number | null;
  }[];
}

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
  /** When the shop's counts were last read from Etsy, even past the time they may be
   *  shown; null: never read (a shop just connected, before its first read). */
  counts_read_at?: string | null;
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
  reason: "global_quota" | "tenant_quota" | "etsy_rate_limit" | "etsy_unavailable" | "interrupted" | "shop_setup";
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
  /** Photos it did not send: the listing would have had more images than Etsy keeps. */
  left_out?: string[];
}

export interface Validation {
  valid: boolean;
  errors: string[];
  /** A listing the seller wrote: the app's quality rules it does not meet (never blocking). */
  warnings?: string[];
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
  retention: { drafted_days: number; unpublished_days: number };
  retention_defaults: { drafted_days: number; unpublished_days: number };
  /** False: the daily job only counts, nothing is deleted. */
  retention_applies: boolean;
  last_run: {
    at: string;
    applied: boolean;
    drafted_groups: number;
    unpublished_groups: number;
    images: number;
    files: number;
    freed_bytes: number;
    thumbnails: number;
    thumbnail_bytes: number;
    waiting: number;
  } | null;
  /** Stored image files per account, largest first; tenant_id null: no account owns the folder. */
  accounts: { tenant_id: string | null; email: string | null; bytes: number; cap_bytes: number | null }[];
  /** At the last days' rate (one reading a day): null when not enough readings or not growing. */
  growth_per_day: number | null;
  storage_growth_per_day: number | null;
  days_until_full: number | null;
  /** Without the deploy clearing the build cache; the build cache counted as space a deploy gives
   *  back (deploy/update.sh clears it below ``reclaim_below_gb`` free); Docker's parts. */
  days_until_full_without_reclaim?: number | null;
  reclaimable_bytes?: number | null;
  reclaim_below_gb?: number;
  docker_parts?: { images?: number; containers?: number; build_cache?: number; logs?: number } | null;
  growth_basis_days: number;
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
  /** Who provides the application, as Etsy's API terms ask it to be stated. */
  provider_disclaimer?: string;
  /** The version of the Terms and Privacy Policy accounts agree to. */
  terms_version?: string;
  /** Months of analytics history kept (an operator setting). */
  analytics_retention_months?: number;
}

/** What deleting the account did at once; the rest follows by a job. */
export interface AccountDeleted {
  shops_disconnected: number;
  queued_jobs: number;
  scheduled_publishes: number;
  planned_drafts: number;
  within_days: number;
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
  /** How many shops they said they run; null on requests from before it was asked. */
  shop_count?: number | null;
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
  /** The sign-up page with the code and address filled in: what is sent to the person. */
  link: string;
  /** A ready message to paste into an e-mail. */
  message: string;
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
  /** The Terms / Privacy version in force, and whether this account still has to agree to it. */
  terms_version?: string;
  terms_due?: boolean;
  terms_accepted_version?: string | null;
  terms_accepted_at?: string | null;
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
  /** Stored image files allowed, in bytes; custom: an admin's own number. */
  storage_cap_bytes: number;
  storage_cap_custom: boolean;
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
/** One reason, counted: "14 listings have no title yet: write them, or press Generate". */
export interface ApproveGroup {
  line: string;
  count: number;
  content_ids: string[];
}

export interface ApproveAllResult {
  approved: number;
  already_approved: number;
  /** ``reasons``: the same, short, as the card shows it ("needs a title"). */
  skipped: { content_id: string; original_filename: string; reason: string; reasons?: string[] }[];
  /** One line for the whole result; null when nothing was skipped. */
  summary?: string | null;
  problems?: ApproveGroup[];
  /** Hand-written listings approved with notes (the app's quality rules), counted. */
  notes?: ApproveGroup[];
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
  days_estimate: number | null;
  read_count: number;
  resumes_at: string | null;
  note: string | null;
  /** The one-time second read that adds the order lines. */
  reread: "reading" | "done" | null;
  started_at: string | null;
  finished_at: string | null;
  synced_at: string | null;
  /** When the figures were last brought up to date ("Updated <time>"). */
  updated_at?: string | null;
  /** "Delete my sales history": the first day still shown (null: all kept). */
  history_from?: string | null;
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
  /** Admin only (sellers never receive request counts). */
  requests_used?: number;
  requests_left?: number | null;
  /** The UTC day it is expected to finish (an estimate). */
  finishes_on: string | null;
  finished_at: string | null;
  note: string | null;
}

export interface SalesReread {
  active: boolean;
  reads_back_to: string;
  /** Admin only. */
  per_shop_daily?: number;
  resets_at: string;
  /** Admin only. */
  requests_left?: number;
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
  first_days: number;
  read_count: number;
  resumes_at: string | null;
  note: string | null;
  covers_from: string | null;
  covers_to: string | null;
  /** Filling in the 13 months before the first read, in the background. */
  history_state: "none" | "reading" | "waiting" | "complete" | "failed";
  history_target: string | null;
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
  /** Free rewrites made this period (not part of `used`), and how many each design gets. */
  rewrites?: number;
  free_rewrites?: number;
  /** Listings that went live through the app in the same period, in all the account's shops. */
  published?: number;
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
  /** Listing views on Etsy in the month (not search impressions); null when not measured. */
  views: number | null;
  favorites: number | null;
  /** Orders ÷ views; null without views. */
  conversion: number | null;
  views_days: number;
  /** Why the views are blank, if they are. */
  traffic_note: string | null;
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
  /** "listing views on Etsy, not search impressions" */
  traffic_label?: string;
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
  options: { id: number; label: string }[];
}

/** One shop-specific setting of a profile in one shop: what is used, or what will be. */
export interface SettingUse {
  resource: "shipping_profile" | "return_policy" | "readiness_state" | "production_partners" | string;
  /** "Shipping profile" */
  label: string;
  /** The main shop's own setting it stands for. */
  source_label: string | null;
  /** The shop's own setting in use now; [] = nothing yet. */
  linked_ids: number[];
  linked_label: string | null;
  /** "same name" | "same terms" | "your choice" | "created" */
  how: string | null;
  /** What the setup dialog preselects. */
  default: "keep" | "copy" | "choose";
  reason: string | null;
  creatable: boolean;
  create_summary: string | null;
  options: { id: number; label: string }[];
  /** The last copy of it was refused (or not answered): why, and what fixes it. */
  failure?: SetupFailure | null;
}

/** Etsy refused a copy: Etsy's own reason in plain words, and the one action that fixes it. */
export interface SetupFailure {
  reason: string;
  action: "reconnect" | "fix" | "finish" | "refresh" | "manual";
  /** For "fix": the request field Etsy named (shown in words in ``reason``). */
  field?: string | null;
}

export interface ShopSetup {
  connection_id: string;
  shop_name: string | null;
  link: ProfileLink;
  open: ResourceSetup[];
  settings: SettingUse[];
  choices_expired: boolean;
  /** The shop's own lists are not read yet: the panel reads them and says "reading…". */
  lists_missing?: boolean;
}

export interface SetupChoice {
  connection_id: string;
  resource: string;
  use: "pick" | "copy";
  ids: number[];
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

// --- Distribution and group scheduling (v8 §B) ---------------------------------------

export interface DistributionRow {
  content_id: string;
  title: string;
  sku: string | null;
  /** The group it goes to, or the single shop chosen in place of one. */
  group_id: string | null;
  connection_id: string | null;
  group_name: string;
  shops: {
    shop_id: string;
    shop_name: string;
    ok: boolean;
    reason: string | null;
    /** The listing's profile is not set up there yet: the setup dialog can do it. */
    setup_profile_id?: string | null;
    setup_profile_name?: string | null;
  }[];
  warning: string | null;
}

export interface DistributionAssignment {
  content_id: string;
  group_id: string | null;
  connection_id: string | null;
}

export interface DistributionPreview {
  rows: DistributionRow[];
  left_out: { content_id: string; title: string; reason: string }[];
  per_group: Record<string, number>;
}

export interface GroupSchedule {
  start_date: string;
  per_shop_per_day: number;
  window_start: string;
  window_end: string;
  spacing_minutes: number;
  stagger_minutes: number;
}

export interface GroupPlanPreview {
  shops: {
    shop_id: string;
    shop_name: string;
    group_name: string;
    listings: number;
    days: { date: string; slots: { time: string; zone: string; content_id: string; title: string }[] }[];
  }[];
  finishes_on: string | null;
  drafts: number;
  skipped: { content_id: string; shop: string; reason: string }[];
  notes: string[];
  time_zone: string;
}

export interface GroupPlanSummary {
  id: string;
  batch_id: string | null;
  batch_name: string | null;
  created_at: string;
  cancelled: boolean;
  counts: Record<string, number>;
  first_publish: string | null;
  last_publish: string | null;
}

export interface StyleRate {
  value: number;
  low: number;
  high: number;
}

export interface StyleResult {
  style: "short" | "long";
  listings: number;
  views: number;
  enough: boolean;
  note: string | null;
  views_per_listing_day: StyleRate | null;
  favorites_per_view: StyleRate | null;
  orders_per_view: StyleRate | null;
}

export type StyleRateKey = "views_per_listing_day" | "favorites_per_view" | "orders_per_view";

export interface TitleStyleComparison {
  connected: boolean;
  shop_name?: string;
  label: string;
  period: { start: string; end: string };
  styles: StyleResult[];
  difference: Record<StyleRateKey, (StyleRate & { clear: boolean }) | null> | null;
  orders_note: string | null;
  thresholds: { listings: number; views: number };
  caveats: string[];
}

/** Occasion / Holiday on the review card: Etsy's own values for the listing's category. */
export interface PropertyOptions {
  name: string;
  values: string[];
  /** How many it takes (1: one). */
  max_values: number;
  selected: string[];
  source: "seller" | "writer" | "none";
  /** The profile's default: a value, "" = none, null = no default. */
  profile_default: string | null;
}

export interface ShopSection {
  connection_id: string;
  shop_name: string | null;
  /** null while the shop's sections are being read. */
  sections: { id: number; title: string }[] | null;
  selected_id: number | null;
  selected_title: string | null;
  source: "seller" | "carried" | "suggested" | "none";
  reason: string;
  /** A section chosen elsewhere that this shop does not have. */
  missing_title: string | null;
  can_create: boolean;
}

export interface ItemOptions {
  content_id: string;
  occasion: PropertyOptions | null;
  holiday: PropertyOptions | null;
  shops: ShopSection[];
  note: string | null;
}

export interface SectionCreateResult {
  queued: boolean;
  title: string;
  shop_name: string | null;
  /** Already a section there: chosen, nothing to create. */
  exists?: boolean;
  message: string;
}

/** A listing's SKU after an edit (api/skus.py). */
export interface SkuResult {
  group_key: string;
  sku: string;
  /** Another listing in the same shop has it: a warning, never a refusal. */
  warnings: string[];
  /** Drafts on Etsy with the old SKU; nothing is sent until "Update SKU on Etsy". */
  etsy: { connection_id: string; shop_name: string | null; etsy_listing_id: number; current_sku: string | null; requests: number }[];
}

/** A listing created on Etsy whose draft did not finish (backend api/unfinished.py). */
export interface UnfinishedDraft {
  /** How this draft is addressed (finish, dismiss). */
  record_id: string;
  attempt_id: string | null;
  /** The listing text in the app; null when its batch was deleted. */
  content_id: string | null;
  batch_id: string | null;
  connection_id: string;
  shop_name: string;
  etsy_listing_id: number;
  /** The draft in Shop Manager. */
  edit_url: string;
  title: string;
  /** The step that failed, and what Etsy said, in plain words. */
  step: string | null;
  error: string | null;
  /** A job is finishing it now. */
  finishing: boolean;
  /** Why it cannot be finished yet ("Processing profile is not set up in BCT"), and whether one click sets it up. */
  blocked: string | null;
  profile_id: string | null;
  profile_name: string | null;
  setup: boolean;
  /** What to do (finish, fix_personalization, fix_shop, replaced, batch_deleted) and the list it is in. */
  category: UnfinishedCategory;
  category_label: string;
  section: UnfinishedSection;
  /** Why Etsy would refuse its personalization question (fix it on the card, then finish). */
  personalization_problems: string[];
  /** Another draft of the same design in this shop: its listing id, Shop Manager link and what matched. */
  replaced_by?: number | null;
  replaced_edit_url?: string | null;
  replaced_key?: string | null;
  /** "Finish draft" can run now; "I deleted it on Etsy" takes it off the list. */
  can_finish?: boolean;
  can_dismiss?: boolean;
}

export type UnfinishedCategory = "finish" | "fix_personalization" | "fix_shop" | "replaced" | "batch_deleted";
export type UnfinishedSection = "finish" | "fix" | "delete";
