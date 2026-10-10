import type { ReplaceMode } from "./replaceModes";
import type { GroupingMode } from "./grouping";
import type {
  DistributionPreview,
  GroupPlanPreview,
  GroupPlanSummary,
  GroupSchedule,
  LinkSuggestion,
  ProfileSetup,
  ShopGroups,
  UseInScope,
  Account,
  Allowance,
  AllowanceDefault,
  AnalyticsDetail,
  AnalyticsSummary,
  MonthView,
  TitleStyleComparison,
  ProductCostsView,
  SalesReread,
  SalesRereadAdmin,
  SalesSync,
  AdminInvite,
  AdminUsage,
  AdminUser,
  ApproveAllResult,
  ArchiveResult,
  BatchActionPreview,
  BatchDeleteResult,
  Personalization,
  PatternListing,
  Schedule,
  ScheduleItem,
  ScheduleResult,
  InviteIssued,
  InviteRequest,
  InviteRequestApproved,
  TempPasswordIssued,
  Asset,
  AdminDisk,
  AiCost,
  AiPrice,
  AiSeries,
  BatchDetail,
  ImageDeleteResult,
  ImportMonth,
  ImportStatus,
  BatchPublishResult,
  BatchSummary,
  Connection,
  Content,
  ContentUpdateResult,
  GenerateResult,
  JobStatus,
  Group,
  Meta,
  Profile,
  Publication,
  PublishPreview,
  PublishRequest,
  Quota,
  Shop,
  ShopsOut,
  ReplaceImagesResult,
  ShopListings,
  ShopSummary,
} from "./types";

// Same-origin: Next rewrites /api/* to the FastAPI backend.
const BASE = "/api";

/** `?shop=<id>` for the shop-scoped endpoints; nothing = the account's first shop. */
function shopQuery(shop?: string | null): string {
  return shop ? `?shop=${encodeURIComponent(shop)}` : "";
}

// Full-page navigation target that begins the Etsy OAuth redirect flow.
export const AUTH_START_URL = `${BASE}/auth/etsy/start`;

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = (await res.json())?.detail;
    } catch {
      detail = res.statusText;
    }
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), res.status);
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/** `?a=1&b=2`, leaving out empty values. */
function query(params: Record<string, string | number | null | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** A multipart request: the browser sets the boundary, so no Content-Type here. */
async function reqForm<T>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "POST", body: form, cache: "no-store" });
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = (await res.json())?.detail;
    } catch {
      detail = res.statusText;
    }
    throw new ApiError(typeof detail === "string" ? detail : JSON.stringify(detail), res.status);
  }
  return (await res.json()) as T;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export const api = {
  // --- Account (production-spec A). Session travels in an HttpOnly cookie,
  // which same-origin fetch sends automatically.
  /** The account's time zone; `detected` only fills it when empty (first sign-in). */
  setTimeZone: (timeZone: string, detected = false) =>
    req<Account>("/account/time-zone", { method: "PUT", body: JSON.stringify({ time_zone: timeZone, detected }) }),
  /** The seller's own trademark filter. Off needs `acceptRisk` with the wording's version. */
  setTrademarkFilter: (enabled: boolean, acceptRisk = false, statementVersion?: string) =>
    req<Account>("/account/trademark-filter", {
      method: "PUT",
      body: JSON.stringify({ enabled, accept_risk: acceptRisk, statement_version: statementVersion }),
    }),
  /** Our product allowance: used, left, and when it resets. */
  myAllowance: () => req<Allowance>("/account/allowance"),
  me: () => req<Account>("/account/me"),
  login: (email: string, password: string) =>
    req<Account>("/account/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  register: (email: string, password: string, inviteCode: string) =>
    req<Account>("/account/register", {
      method: "POST",
      body: JSON.stringify({ email, password, invite_code: inviteCode }),
    }),
  logout: () => req<void>("/account/logout", { method: "POST" }),
  changePassword: (currentPassword: string, newPassword: string) =>
    req<Account>("/account/password", {
      method: "POST",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),

  // --- Admin panel. The server answers 404 to anyone who is not an admin.
  admin: {
    users: () => req<AdminUser[]>("/admin/users"),
    suspend: (id: string) => req<AdminUser>(`/admin/users/${id}/suspend`, { method: "POST" }),
    reactivate: (id: string) =>
      req<AdminUser>(`/admin/users/${id}/reactivate`, { method: "POST" }),
    temporaryPassword: (id: string) =>
      req<TempPasswordIssued>(`/admin/users/${id}/temporary-password`, { method: "POST" }),
    /** null: the account follows the default again. */
    setQuota: (id: string, dailyQuota: number | null) =>
      req<AdminUser>(`/admin/users/${id}/quota`, {
        method: "PUT",
        body: JSON.stringify({ daily_quota: dailyQuota }),
      }),
    /** Both null = back to the system default. */
    setAllowance: (id: string, amount: number | null, period: string | null) =>
      req<AdminUser>(`/admin/users/${id}/allowance`, { method: "PUT", body: JSON.stringify({ amount, period }) }),
    allowanceDefault: () => req<AllowanceDefault>("/admin/allowance-default"),
    setAllowanceDefault: (body: AllowanceDefault) =>
      req<AllowanceDefault>("/admin/allowance-default", { method: "PUT", body: JSON.stringify(body) }),
    /** null = back to the default ceiling. */
    setStorageCap: (id: string, gb: number | null) =>
      req<AdminUser>(`/admin/users/${id}/storage-cap`, { method: "PUT", body: JSON.stringify({ gb }) }),
    setShopLimit: (id: string, maxShops: number | null) =>
      req<AdminUser>(`/admin/users/${id}/shops`, {
        method: "PUT",
        body: JSON.stringify({ max_shops: maxShops }),
      }),
    setFeatures: (id: string, features: Record<string, boolean>) =>
      req<AdminUser>(`/admin/users/${id}/features`, { method: "PUT", body: JSON.stringify({ features }) }),
    /** null = back to the app default (TRADEMARK_FILTER). */
    setTrademarkFilter: (id: string, enabled: boolean | null) =>
      req<AdminUser>(`/admin/users/${id}/trademark-filter`, {
        method: "PUT",
        body: JSON.stringify({ enabled }),
      }),
    invites: () => req<AdminInvite[]>("/admin/invites"),
    createInvite: (body: { email?: string; note?: string; expires_in_days: number | null }) =>
      req<InviteIssued>("/admin/invites", { method: "POST", body: JSON.stringify(body) }),
    inviteRequests: () => req<InviteRequest[]>("/admin/invite-requests"),
    approveInviteRequest: (id: string) =>
      req<InviteRequestApproved>(`/admin/invite-requests/${id}/approve`, { method: "POST" }),
    declineInviteRequest: (id: string) =>
      req<InviteRequest>(`/admin/invite-requests/${id}/decline`, { method: "POST" }),
    revokeInvite: (id: string) =>
      req<AdminInvite>(`/admin/invites/${id}/revoke`, { method: "POST" }),
    usage: () => req<AdminUsage>("/admin/usage"),
    disk: () => req<AdminDisk>("/admin/disk"),
    salesReread: () => req<SalesRereadAdmin>("/admin/sales-reread"),
    setUploadRetention: (body: AdminDisk["retention"]) =>
      req<AdminDisk["retention"]>("/admin/upload-retention", { method: "PUT", body: JSON.stringify(body) }),
    aiCost: () => req<AiCost>("/admin/ai-cost"),
    aiSeries: (period: AiSeries["period"], seller: string) =>
      req<AiSeries>(`/admin/ai-cost/series?period=${period}&seller=${encodeURIComponent(seller)}`),
    setAiPrice: (body: Omit<AiPrice, "custom">) =>
      req<AiPrice[]>("/admin/ai-prices", { method: "PUT", body: JSON.stringify(body) }),
    resetAiPrice: (model: string) =>
      req<AiPrice[]>(`/admin/ai-prices/${encodeURIComponent(model)}`, { method: "DELETE" }),
  },

  listBatches: () => req<BatchSummary[]>("/batches"),
  getBatch: (id: string) => req<BatchDetail>(`/batches/${id}`),
  /** Start an upload for one shop (required when several are connected). */
  createBatch: (shop?: string | null) =>
    req<BatchSummary>("/batches", { method: "POST", body: JSON.stringify(shop ? { connection_id: shop } : {}) }),
  /** Remove one image from its listing group (nothing on Etsy changes). */
  deleteImage: (assetId: string) => req<ImageDeleteResult>(`/assets/${assetId}`, { method: "DELETE" }),
  /** Name a batch (an empty name goes back to the one taken from its contents). */
  renameBatch: (id: string, name: string) =>
    req<BatchSummary>(`/batches/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  /** A batch without its files: name, shop, counts. */
  batchSummary: (id: string) => req<BatchSummary>(`/batches/${id}/summary`),
  /** The shop a batch is for; groups not set by hand move with it. */
  setBatchShop: (id: string, shop: string) =>
    req<Group[]>(`/batches/${id}/shop`, { method: "PUT", body: JSON.stringify({ connection_id: shop }) }),
  finalizeBatch: (id: string) => req<BatchSummary>(`/batches/${id}/finalize`, { method: "POST" }),
  /** Turn the batch's photos into listings another way (before anything is written). */
  regroup: (id: string, mode: GroupingMode) =>
    req<BatchSummary>(`/batches/${id}/grouping`, { method: "POST", body: JSON.stringify({ mode }) }),
  // Choose which profile's size charts to append when publishing this batch (Task 4).
  setSizeChartProfile: (id: string, profileId: string | null) =>
    req<BatchSummary>(`/batches/${id}/size-chart-profile`, {
      method: "PUT",
      body: JSON.stringify({ profile_id: profileId }),
    }),
  // Generate content for a batch; pass a groupKey to limit to one folder group (D3).
  // profileId is optional — each group can carry its own assigned profile (v4 §E).
  /**
   * `replace`: "Regenerate", new content for a group that has some; approved
   * content needs `replaceApproved` too. A group whose draft is on Etsy is never
   * replaced (the server says why in `skipped_groups`).
   */
  generate: (
    id: string,
    profileId?: string,
    groupKey?: string,
    opts: { replace?: boolean; replaceApproved?: boolean; ignoreUnsorted?: boolean } = {},
  ) =>
    req<GenerateResult>(`/batches/${id}/generate`, {
      method: "POST",
      body: JSON.stringify({
        ...(profileId ? { profile_id: profileId } : {}),
        ...(groupKey != null ? { group_key: groupKey } : {}),
        ...(opts.replace ? { replace: true } : {}),
        ...(opts.replaceApproved ? { replace_approved: true } : {}),
        ...(opts.ignoreUnsorted ? { ignore_unsorted: true } : {}),
      }),
    }),
  // Per-group profile selection (v4 §E): omit group_key to bulk-apply to all groups.
  /** Save a listing group's image order; the first is the cover (v6 §E). "" = root files. */
  orderGroup: (batchId: string, groupKey: string, assetIds: string[]) =>
    req<BatchDetail>(`/batches/${batchId}/groups/order`, {
      method: "PUT",
      body: JSON.stringify({ group_key: groupKey, asset_ids: assetIds }),
    }),
  // The grouping board: move photos (to a group, to Unsorted "~unsorted", or into a new group), merge, edit a SKU.
  boardMove: (batchId: string, assetIds: string[], to: string, newGroup = false) =>
    req<BatchDetail>(`/batches/${batchId}/board/move`, {
      method: "POST",
      body: JSON.stringify({ asset_ids: assetIds, to, new_group: newGroup }),
    }),
  boardMerge: (batchId: string, fromKey: string, intoKey: string) =>
    req<BatchDetail>(`/batches/${batchId}/board/merge`, {
      method: "POST",
      body: JSON.stringify({ from_key: fromKey, into_key: intoKey }),
    }),
  boardSku: (batchId: string, groupKey: string, sku: string) =>
    req<BatchDetail>(`/batches/${batchId}/board/sku`, { method: "PUT", body: JSON.stringify({ group_key: groupKey, sku }) }),
  listGroups: (id: string) => req<Group[]>(`/batches/${id}/groups`),
  /** Where a group's size charts sit among its photos; null follows the profile again. */
  setChartSlots: (batchId: string, groupKey: string, slots: number[] | null) =>
    req<Group[]>(`/batches/${batchId}/groups/chart-slots`, {
      method: "PUT",
      body: JSON.stringify({ group_key: groupKey, slots }),
    }),
  assignGroup: (
    id: string,
    /** Only the fields sent are changed. One group (the following unset ones take
     *  the same), a selection (`group_keys`), or every group not set by hand. */
    body: {
      group_key?: string | null;
      group_keys?: string[];
      connection_id?: string | null;
      profile_id?: string | null;
      size_chart_profile_id?: string | null;
    },
  ) => req<Group[]>(`/batches/${id}/groups`, { method: "PUT", body: JSON.stringify(body) }),
  listContent: (id: string) => req<Content[]>(`/batches/${id}/content`),
  updateContent: (id: string, body: Partial<Pick<Content, "title" | "tags" | "description">> & { personalization?: Personalization | null }) =>
    req<ContentUpdateResult>(`/content/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  approve: (id: string, approved: boolean) =>
    req<ContentUpdateResult>(`/content/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ approved }),
    }),
  /** With `shop`, also that shop's share of today's requests (display only). */
  /** Approve every listing in the batch that passes validation; the rest are listed with why. */
  approveAll: (batchId: string) =>
    req<ApproveAllResult>(`/batches/${batchId}/approve-all`, { method: "POST" }),
  /** Scheduled go-lives, soonest first (v6 §G). */
  listSchedules: (shop?: string | null) => req<Schedule[]>(`/schedules${shopQuery(shop)}`),
  /** Schedule or move drafts' go-live; each must be an approved listing's draft. */
  schedule: (items: ScheduleItem[], replace = true) =>
    req<ScheduleResult>("/schedules", { method: "POST", body: JSON.stringify({ items, replace }) }),
  cancelSchedule: (contentId: string, shopId: string) =>
    req<void>(`/schedules/${contentId}/${shopId}`, { method: "DELETE" }),
  /** What creating drafts / publishing would do across these batches; nothing is queued. */
  batchActionPreview: (batchIds: string[], action: "drafts" | "publish") =>
    req<BatchActionPreview>("/batch-actions/preview", {
      method: "POST",
      body: JSON.stringify({ batch_ids: batchIds, action }),
    }),
  batchActionRun: (batchIds: string[], action: "drafts" | "publish") =>
    req<BatchPublishResult>("/batch-actions/run", {
      method: "POST",
      body: JSON.stringify({ batch_ids: batchIds, action }),
    }),
  /** Where the cover is cut for Etsy: a square in the processed image's pixels. */
  setCoverCrop: (assetId: string, crop: { x: number; y: number; size: number }) =>
    req<unknown>(`/assets/${assetId}/cover-crop`, { method: "PUT", body: JSON.stringify(crop) }),
  resetCoverCrop: (assetId: string) => req<void>(`/assets/${assetId}/cover-crop`, { method: "DELETE" }),
  /** Delete batches: uploads and content here; nothing on Etsy is touched. */
  deleteBatch: (id: string) => req<BatchDeleteResult>(`/batches/${id}`, { method: "DELETE" }),
  deleteBatches: (ids: string[]) =>
    req<BatchDeleteResult>("/batch-actions/delete", { method: "POST", body: JSON.stringify({ batch_ids: ids }) }),
  /** Refresh this shop's listings from Etsy now (upkeep, not the seller's quota). */
  syncShopListings: (shop?: string | null) =>
    req<{ queued: boolean }>(`/shop/listings/sync${shopQuery(shop)}`, { method: "POST" }),
  /** The seller's own active listings to model new ones on (v7 §B). */
  patternListings: (shop?: string | null, q = "") =>
    req<PatternListing[]>(`/shop/pattern-listings${shopQuery(shop)}${q ? (shop ? "&" : "?") + "q=" + encodeURIComponent(q) : ""}`),
  setGroupPattern: (batchId: string, groupKey: string, listingId: number | null) =>
    req<Group[]>(`/batches/${batchId}/groups/pattern`, {
      method: "PUT",
      body: JSON.stringify({ group_key: groupKey, pattern_listing_id: listingId }),
    }),
  // --- Analytics (v7 §C): the seller's own sales, ads report and costs.
  analyticsSummary: (shop: string | null, days: number, compare: "previous" | "year") =>
    req<AnalyticsSummary>(`/analytics/summary${query({ shop, days, compare })}`),
  analyticsListing: (listingId: number, shop: string | null, days: number) =>
    req<AnalyticsDetail>(`/analytics/listings/${listingId}${query({ shop, days })}`),
  salesStatus: (shop: string | null) => req<SalesSync>(`/analytics/sales/status${shopQuery(shop)}`),
  /** One month of the shop's money: the receipt, what needs attention, every listing. `month` is "YYYY-MM". */
  analyticsMonth: (shop: string | null, month?: string | null) =>
    req<MonthView>(`/analytics/month${shopQuery(shop)}${month ? `${shop ? "&" : "?"}month=${month}` : ""}`),
  /** Listings published with the app in the last `days`, by title style (Part D). */
  titleStyles: (shop: string | null, days = 90) =>
    req<TitleStyleComparison>(`/analytics/title-styles${shopQuery(shop)}${shop ? "&" : "?"}days=${days}`),
  productCosts: (shop: string | null) => req<ProductCostsView>(`/analytics/product-costs${shopQuery(shop)}`),
  saveProductCosts: (shop: string | null, profiles: Record<string, unknown>) =>
    req<ProductCostsView>(`/analytics/product-costs${shopQuery(shop)}`, { method: "PUT", body: JSON.stringify({ profiles }) }),
  /** The one-time second read of the sales, for each of the account's shops. */
  salesReread: () => req<SalesReread>("/analytics/sales/reread"),
  /** Work out what the first read costs (a few requests), before starting it. */
  estimateSales: (shop: string | null) =>
    req<SalesSync>(`/analytics/sales/estimate${shopQuery(shop)}`, { method: "POST" }),
  startSales: (shop: string | null) => req<SalesSync>(`/analytics/sales/start${shopQuery(shop)}`, { method: "POST" }),
  /** After a failure: carry on where the read stopped. */
  resumeSales: (shop: string | null) => req<SalesSync>(`/analytics/sales/resume${shopQuery(shop)}`, { method: "POST" }),
  /** Etsy's payment ledger on its own, for a shop whose sales are read already. */
  estimateLedger: (shop: string | null) =>
    req<SalesSync>(`/analytics/ledger/estimate${shopQuery(shop)}`, { method: "POST" }),
  startLedger: (shop: string | null) =>
    req<SalesSync>(`/analytics/ledger/start${shopQuery(shop)}`, { method: "POST" }),
  /** Read the shop's latest sales now (upkeep, not the seller's quota). */
  refreshSales: (shop: string | null) =>
    req<{ queued: boolean }>(`/analytics/sales/refresh${shopQuery(shop)}`, { method: "POST" }),
  importStatement: (shop: string | null, file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return reqForm<ImportMonth>(`/analytics/import/statement${shopQuery(shop)}`, form);
  },
  /** One report per month the file covers. */
  importAds: (shop: string | null, file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return reqForm<ImportMonth[]>(`/analytics/import/ads${shopQuery(shop)}`, form);
  },
  importStatus: (shop: string | null) => req<ImportStatus>(`/analytics/import/status${shopQuery(shop)}`),
  /** `month` is "YYYY-MM". */
  importMonth: (shop: string | null, month: string) => req<ImportMonth>(`/analytics/import/months/${month}${shopQuery(shop)}`),
  deleteImportMonth: (shop: string | null, month: string) =>
    req<void>(`/analytics/import/months/${month}${shopQuery(shop)}`, { method: "DELETE" }),
  quota: (shop?: string | null) => req<Quota>(`/quota${shopQuery(shop)}`),
  meta: () => req<Meta>("/meta"),
  /**
   * `width` requests a cached preview derivative; omit it for the full image.
   * `aspect` additionally crops to that tile ratio, centred on the artwork
   * rather than the frame, so a portrait mockup is not sliced through.
   */
  assetImage: (id: string, width?: 112 | 224 | 448 | 896, aspect?: "4:5" | "16:10") =>
    `${BASE}/assets/${id}/image` +
    (width ? `?w=${width}` + (aspect ? `&ar=${aspect}` : "") : ""),
  connection: () => req<Connection>("/auth/etsy/status"),

  // Connected shops (v5 §E). Each is one Etsy connection; ids are connection ids.
  shops: () => req<ShopsOut>("/shops"),
  renameShop: (id: string, displayName: string) =>
    req<Shop>(`/shops/${id}`, { method: "PATCH", body: JSON.stringify({ display_name: displayName }) }),
  orderShops: (ids: string[]) =>
    req<ShopsOut>("/shops/order", { method: "POST", body: JSON.stringify({ ids }) }),
  disconnectShop: (id: string) => req<ShopsOut>(`/shops/${id}/disconnect`, { method: "POST" }),

  // Drafts: each listing to each chosen shop (default: the shop it was written for).
  publishContent: (id: string, body?: PublishRequest) =>
    req<BatchPublishResult>(`/content/${id}/publish`, {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }),
  publishLive: (id: string, connectionIds?: string[]) =>
    req<BatchPublishResult>(`/content/${id}/publish-live`, {
      method: "POST",
      body: JSON.stringify(connectionIds ? { connection_ids: connectionIds } : {}),
    }),
  // Bulk: create drafts / publish-live for every approved item in the batch (D3/E).
  publishBatch: (id: string, body?: PublishRequest) =>
    req<BatchPublishResult>(`/batches/${id}/publish`, {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }),
  /** What a publish would do per shop, and whether it fits today's budget. */
  publishPreview: (id: string, body?: PublishRequest) =>
    req<PublishPreview>(`/batches/${id}/publish/preview`, {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }),
  /** The seller confirms (or un-confirms) setting one manual field on one draft. */
  tickManualStep: (contentId: string, shopId: string, key: string, done: boolean) =>
    req<Publication>(
      `/content/${contentId}/publications/${shopId}/manual-steps/${encodeURIComponent(key)}`,
      { method: "PUT", body: JSON.stringify({ done }) },
    ),
  /** "Mark all as done": every manual setting on every approved draft of the batch. */
  markManualStepsDone: (batchId: string) =>
    req<{ updated_drafts: number }>(`/batches/${batchId}/manual-steps/done`, { method: "POST" }),
  publishBatchLive: (id: string) =>
    req<BatchPublishResult>(`/batches/${id}/publish-live`, { method: "POST", body: "{}" }),
  jobStatus: (jobId: string) => req<JobStatus>(`/jobs/${jobId}`),

  // Reference-listing profiles (Section B) + shop listings (B4).
  /** Every profile of the account, or one shop's. */
  /** `refresh`: the Profiles page opening, which refreshes what it shows if due. */
  listProfiles: (shop?: string | null, refresh = false) =>
    req<Profile[]>(`/profiles${shopQuery(shop)}${refresh ? (shop ? "&" : "?") + "refresh=1" : ""}`),
  getProfile: (id: string) => req<Profile>(`/profiles/${id}`),
  createProfile: (body: {
    connection_id: string;
    name: string;
    reference_listing_id: number;
    content_template?: string;
  }) =>
    req<Profile>("/profiles", { method: "POST", body: JSON.stringify(body) }),
  updateProfile: (
    id: string,
    body: Partial<{
      name: string;
      content_template: string;
      fixed_image_ids: number[];
      confirmed: boolean;
      title_prefix: string;
      listing_style: "classic" | "search";
      size_chart_position: "after_cover" | "third" | "last";
      /** null: back to the default bound. */
      title_min_length: number | null;
      title_max_length: number | null;
      /** null: back to the reference's question (v7 §D4). */
      personalization: Personalization | null;
    }>,
  ) => req<Profile>(`/profiles/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  confirmProfile: (id: string) => req<Profile>(`/profiles/${id}/confirm`, { method: "POST" }),
  refreshProfile: (id: string) => req<Profile>(`/profiles/${id}/refresh`, { method: "POST" }),
  deleteProfile: (id: string) => req<void>(`/profiles/${id}`, { method: "DELETE" }),
  // --- One profile across shops (v8 §C) ---
  useProfileIn: (id: string, scope: UseInScope) =>
    req<{ profile: Profile; started: string[] }>(`/profiles/${id}/use-in`, { method: "POST", body: JSON.stringify(scope) }),
  profileSetup: (id: string) => req<ProfileSetup>(`/profiles/${id}/setup`),
  createInShops: (id: string, items: { connection_id: string; resource: string }[]) =>
    req<{ profile: Profile; requests: number }>(`/profiles/${id}/setup/create`, { method: "POST", body: JSON.stringify({ items }) }),
  pickInShop: (id: string, shop: string, resource: string, ids: number[]) =>
    req<Profile>(`/profiles/${id}/setup/${shop}`, { method: "PUT", body: JSON.stringify({ resource, ids }) }),
  checkShopAgain: (id: string, shop: string) => req<Profile>(`/profiles/${id}/setup/${shop}/check`, { method: "POST" }),
  stopUsingInShop: (id: string, shop: string) => req<Profile>(`/profiles/${id}/links/${shop}`, { method: "DELETE" }),
  setProfileReference: (id: string, listingId: number) =>
    req<Profile>(`/profiles/${id}/reference`, { method: "PUT", body: JSON.stringify({ reference_listing_id: listingId }) }),
  linkSuggestions: () => req<LinkSuggestion[]>("/profiles/link-suggestions"),
  linkProfiles: (keep: string, other: string) =>
    req<Profile>(`/profiles/${keep}/link-profile`, { method: "POST", body: JSON.stringify({ other_profile_id: other }) }),
  // --- Shop groups (v8 §B) ---
  shopGroups: () => req<ShopGroups>("/shop-groups"),
  createShopGroup: (name: string, connection_ids: string[]) =>
    req<ShopGroups>("/shop-groups", { method: "POST", body: JSON.stringify({ name, connection_ids }) }),
  updateShopGroup: (id: string, body: { name?: string; connection_ids?: string[] }) =>
    req<ShopGroups>(`/shop-groups/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  setPersonalizationForAll: (batch: string, personalization: Personalization | null, contentIds?: string[]) =>
    req<Content[]>(`/batches/${batch}/personalization`, {
      method: "POST",
      body: JSON.stringify({ personalization, content_ids: contentIds ?? null }),
    }),
  deleteShopGroup: (id: string) => req<ShopGroups>(`/shop-groups/${id}`, { method: "DELETE" }),
  // --- Distribution and group scheduling (v8 §B) ---
  previewDistribution: (batch: string, body: { mode: "assign" | "split"; group_ids: string[]; content_ids?: string[] }) =>
    req<DistributionPreview>(`/batches/${batch}/distribution/preview`, { method: "POST", body: JSON.stringify(body) }),
  planDistribution: (batch: string, assignments: { content_id: string; group_id: string }[], schedule: GroupSchedule) =>
    req<GroupPlanPreview>(`/batches/${batch}/distribution/plan`, { method: "POST", body: JSON.stringify({ assignments, schedule }) }),
  confirmDistribution: (batch: string, assignments: { content_id: string; group_id: string }[], schedule: GroupSchedule) =>
    req<{ plan_id: string; plan: GroupPlanPreview }>(`/batches/${batch}/distribution/confirm`, {
      method: "POST",
      body: JSON.stringify({ assignments, schedule }),
    }),
  groupPlans: () => req<GroupPlanSummary[]>("/plans"),
  cancelGroupPlan: (id: string) => req<GroupPlanSummary>(`/plans/${id}`, { method: "DELETE" }),
  detectProfiles: (shop?: string | null) =>
    req<{ status: string }>(`/shop/detect-profiles${shopQuery(shop)}`, { method: "POST" }),
  shopListings: (shop?: string | null) => req<ShopListings>(`/shop/listings${shopQuery(shop)}`),
  /** Cached counts only — unlike shopListings this never triggers a sync. */
  shopSummary: (shop?: string | null) => req<ShopSummary>(`/shop/summary${shopQuery(shop)}`),
  useListingAsProfile: (listingId: number, shop?: string | null) =>
    req<Profile>(`/shop/listings/${listingId}/use-as-profile${shopQuery(shop)}`, {
      method: "POST",
    }),
  /** `groupKey`: only that listing group's photos, in its order ("" = root files). */
  /** `mode` "photos" leaves the title, tags and description alone; "full" writes a new title and tags. */
  replaceImages: (listingId: number, batchId: string, mode: ReplaceMode, shop?: string | null, groupKey?: string) =>
    req<ReplaceImagesResult>(`/shop/listings/${listingId}/replace-images${shopQuery(shop)}`, {
      method: "POST",
      body: JSON.stringify({ batch_id: batchId, mode, ...(groupKey != null ? { group_key: groupKey } : {}) }),
    }),
};

/** Upload a single file with per-file progress via XHR. */
export function uploadAsset(
  batchId: string,
  file: File,
  onProgress: (pct: number) => void,
  groupKey?: string,
  grouping?: GroupingMode,
): Promise<Asset> {
  const form = new FormData();
  form.append("file", file, file.name);
  // The folder it is in (D1); empty string = loose.
  if (groupKey !== undefined) form.append("group_key", groupKey);
  // How photos become listings (lib/grouping.ts); the server groups by it.
  if (grouping) form.append("grouping", grouping);
  return postForm<Asset>(`${BASE}/batches/${batchId}/assets`, form, onProgress);
}

/** Upload a ZIP; the server unpacks it, keeping its folders as groups (v6 §F). */
export function uploadArchive(
  batchId: string,
  file: File,
  onProgress: (pct: number) => void,
  grouping?: GroupingMode,
): Promise<ArchiveResult> {
  const form = new FormData();
  form.append("file", file, file.name);
  if (grouping) form.append("grouping", grouping);
  return postForm<ArchiveResult>(`${BASE}/batches/${batchId}/archive`, form, onProgress);
}

function postForm<T>(url: string, form: FormData, onProgress: (pct: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as T);
      } else {
        // Refusals (415 not an image, 413 too large) carry a readable `detail`;
        // show that, not the raw JSON envelope.
        let message = xhr.statusText || "upload failed";
        try {
          const detail = JSON.parse(xhr.responseText)?.detail;
          if (typeof detail === "string") message = detail;
        } catch {
          /* non-JSON body: keep the status text */
        }
        reject(new ApiError(message, xhr.status));
      }
    };
    xhr.onerror = () => reject(new ApiError("network error", 0));
    xhr.send(form);
  });
}
