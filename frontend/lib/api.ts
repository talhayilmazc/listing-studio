import type { GroupSettings, ScheduledData } from "./scheduled";
import type { ReplaceMode } from "./replaceModes";
import type { GroupingMode } from "./grouping";
import { UNREACHABLE, messageFor } from "./apiErrors";
import { shareInFlight } from "./inFlight";
import type {
  AnotherShopPreview,
  AnotherShopRequest,
  BatchShops,
  RewriteResult,
  WorkspaceTextRow,
  PublishJob,
  PublishSkipped,
  FoundArea,
  UnfinishedDraft,
  MockupAdjustmentRow,
  MockupDesign,
  MockupPlan,
  MockupEdits,
  MockupRecolourRow,
  SubgroupPreset,
  MockupPhoto,
  MockupRun,
  MockupPlacement,
  MockupTemplate,
  SlotIn,
  MockupZip,
  Checklist,
  GroupSchedule,
  Destination,
  SendPreview,
  SendResult,
  SendState,
  SendWhen,
  LinkSuggestion,
  ProfileSetup,
  ProfileShopTable,
  ScheduledApplied,
  ScheduledPreview,
  ShopGroups,
  UseInScope,
  SetupChoice,
  ShopsOverview,
  Attention,
  AnalyticsShops,
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
  PersonalizationFixResult,
  Profile,
  Publication,
  PublishPreview,
  PublishRequest,
  Quota,
  Shop,
  ShopsOut,
  ReplaceImagesResult,
  ReferenceText,
  ShopTickResult,
  AccountDeleted,
  ShopListings,
  ShopSummary,
  ItemOptions,
  SectionCreateResult,
  SkuResult,
} from "./types";

// Same-origin: in production the tunnel sends /api/* to the API itself (deploy/cloudflared/config.yml);
// in development Next rewrites it there (next.config.mjs).
const BASE = "/api";

/** `?shop=<id>` for the shop-scoped endpoints; nothing = the account's first shop. */
function shopQuery(shop?: string | null): string {
  return shop ? `?shop=${encodeURIComponent(shop)}` : "";
}

// Full-page navigation target that begins the Etsy OAuth redirect flow.
export const AUTH_START_URL = `${BASE}/auth/etsy/start`;

/** The answer, or an ApiError in plain words (lib/apiErrors.ts). */
async function send(url: string, init: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new ApiError(UNREACHABLE, 0);
  }
  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = undefined;
    }
    throw new ApiError(messageFor(res.status, body, res.statusText), res.status);
  }
  return res;
}

/** Identical GETs asked for at the same moment share one request (lib/inFlight.ts); each
 * caller parses its own copy of the answer, so no two callers hold the same object. */
const sharedGet = shareInFlight<{ status: number; text: string }>();

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const call = () => send(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if ((init?.method ?? "GET").toUpperCase() === "GET" && !init?.signal) {
    const { status, text } = await sharedGet(path, () =>
      call().then(async (res) => ({ status: res.status, text: res.status === 204 ? "" : await res.text() })));
    return status === 204 ? (undefined as T) : (JSON.parse(text) as T);
  }
  const res = await call();
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

/**
 * The version every image URL carries (backend pipeline/thumbs.py ``VERSION``): with it the
 * browser keeps an image for a year (``immutable``), so it never downloads one twice; a new
 * version (a new kind of copy) is a new URL.
 */
export const IMAGE_VERSION = 1;
/** The copies a screen may ask for (backend pipeline/thumbs.py): "small" 240 px for tiles up to
 * about 120 CSS px, "thumb" 480 px for grids and cards, "medium" 1200 px for viewers and editors.
 * No size: the full file (downloads only). */
export type ImageSize = "small" | "thumb" | "medium";

/** `?a=1&b=2`, leaving out empty values. */
function query(params: Record<string, string | number | null | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** A multipart request: the browser sets the boundary, so no Content-Type here. */
async function reqForm<T>(path: string, form: FormData, method: "POST" | "PUT" = "POST"): Promise<T> {
  const res = await send(`${BASE}${path}`, { method, body: form, cache: "no-store" });
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
  /** `acceptTerms`: the tick beside "I agree to the Terms of Service and Privacy Policy". */
  register: (email: string, password: string, inviteCode: string, acceptTerms: boolean) =>
    req<Account>("/account/register", {
      method: "POST",
      body: JSON.stringify({ email, password, invite_code: inviteCode, accept_terms: acceptTerms }),
    }),
  /** The one-time acceptance screen: agree to the version it showed. */
  acceptTerms: (version: string) =>
    req<Account>("/account/terms", { method: "POST", body: JSON.stringify({ accept: true, version }) }),
  /** Settings → Delete account (the account's own password confirms it). */
  deleteAccount: (password: string) =>
    req<AccountDeleted>("/account/delete", { method: "POST", body: JSON.stringify({ password, confirm: true }) }),
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
    /** How many requests wait for a decision (the badge on the Invites tab). */
    pendingInviteRequests: () => req<{ pending: number }>("/admin/invite-requests/pending"),
    analyticsRetention: () => req<{ months: number; default_months: number }>("/admin/analytics-retention"),
    setAnalyticsRetention: (months: number) =>
      req<{ months: number; default_months: number }>("/admin/analytics-retention", { method: "PUT", body: JSON.stringify({ months }) }),
    connectionsPaused: () => req<{ on: boolean; notice: string }>("/admin/connections-paused"),
    setConnectionsPaused: (on: boolean) =>
      req<{ on: boolean; notice: string }>("/admin/connections-paused", { method: "PUT", body: JSON.stringify({ on }) }),
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
    opts: { replace?: boolean; replaceApproved?: boolean; ignoreUnsorted?: boolean; onlyEmpty?: boolean } = {},
  ) =>
    req<GenerateResult>(`/batches/${id}/generate`, {
      method: "POST",
      body: JSON.stringify({
        ...(profileId ? { profile_id: profileId } : {}),
        ...(groupKey != null ? { group_key: groupKey } : {}),
        ...(opts.replace ? { replace: true } : {}),
        ...(opts.replaceApproved ? { replace_approved: true } : {}),
        ...(opts.ignoreUnsorted ? { ignore_unsorted: true } : {}),
        // "Generate the empty ones": only listings nothing was typed into.
        ...(opts.onlyEmpty ? { only_empty: true } : {}),
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
  /** Where a group's size charts sit among its photos (in the order they appear); null follows the profile again. */
  setChartSlots: (batchId: string, groupKey: string, placement: { listing_image_id: number; slot: number }[] | null) =>
    req<Group[]>(`/batches/${batchId}/groups/chart-slots`, {
      method: "PUT",
      body: JSON.stringify({ group_key: groupKey, placement }),
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
      /** The shop shown: a listing on Etsy in another shop takes the profile in this shop only. */
      for_shop?: string | null;
    },
  ) => req<Group[]>(`/batches/${id}/groups`, { method: "PUT", body: JSON.stringify(body) }),
  /** Where the batch's listings stand in each shop, and their SKU and profile per shop. */
  batchShops: (id: string) => req<BatchShops>(`/batches/${id}/shops`),
  /** "Publish in another shop": what it would do (nothing is kept). */
  anotherShopPreview: (id: string, body: AnotherShopRequest) =>
    req<AnotherShopPreview>(`/batches/${id}/another-shop/preview`, { method: "POST", body: JSON.stringify(body) }),
  /** "Rewrite for this shop": kept as that shop's text; a rewrite of the design (allowance rule). */
  anotherShopRewrite: (id: string, body: { connection_id: string; content_ids: string[]; profile_id?: string | null }) =>
    req<RewriteResult>(`/batches/${id}/another-shop/rewrite`, { method: "POST", body: JSON.stringify(body) }),
  // The shop workspace: a listing's profile, SKUs and text for one shop, saved as changed.
  workspaceProfile: (id: string, shop: string, contentIds: string[], profileId: string | null) =>
    req<AnotherShopPreview>(`/batches/${id}/shops/${shop}/profile`, {
      method: "PUT",
      body: JSON.stringify({ content_ids: contentIds, profile_id: profileId }),
    }),
  workspaceSkusCheck: (id: string, shop: string, skus: Record<string, string>) =>
    req<{ saved: number; refused: string[] }>(`/batches/${id}/shops/${shop}/skus/check`, { method: "POST", body: JSON.stringify({ skus }) }),
  workspaceSkus: (id: string, shop: string, skus: Record<string, string>) =>
    req<{ saved: number; refused: string[] }>(`/batches/${id}/shops/${shop}/skus`, { method: "PUT", body: JSON.stringify({ skus }) }),
  /** "Approve selected": approved, and approved for that shop (its text there edited since). */
  workspaceApprove: (id: string, shop: string, contentIds: string[]) =>
    req<AnotherShopPreview>(`/batches/${id}/shops/${shop}/approve`, { method: "POST", body: JSON.stringify({ content_ids: contentIds }) }),
  workspaceTexts: (id: string, shop: string, texts: NonNullable<AnotherShopRequest["texts"]>) =>
    req<WorkspaceTextRow[]>(`/batches/${id}/shops/${shop}/texts`, { method: "PUT", body: JSON.stringify({ texts }) }),
  /** The same, done: drafts in that shop; nothing changes in the others. */
  anotherShop: (id: string, body: AnotherShopRequest) =>
    req<{ jobs: PublishJob[]; skipped: PublishSkipped[]; message: string }>(`/batches/${id}/another-shop`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listContent: (id: string) => req<Content[]>(`/batches/${id}/content`),
  /**
   * "Write title and tags myself": an empty listing for one group (or every group
   * not written yet), with everything else from its profile. No AI is called.
   */
  writeManually: (id: string, groupKey?: string, opts: { ignoreUnsorted?: boolean } = {}) =>
    req<GenerateResult>(`/batches/${id}/manual`, {
      method: "POST",
      body: JSON.stringify({
        ...(groupKey != null ? { group_key: groupKey } : {}),
        ...(opts.ignoreUnsorted ? { ignore_unsorted: true } : {}),
      }),
    }),
  /** The title and tags of the listing's profile's reference listing (the seller's own). */
  referenceText: (contentId: string) => req<ReferenceText>(`/content/${contentId}/reference-text`),
  // Occasion, Holiday and Section per shop on the review card (Etsy's own values).
  itemOptions: (contentId: string) => req<ItemOptions>(`/content/${contentId}/options`),
  /** Only the fields sent change: values ([] = none), null = back to the writer's;
   *  sections per shop: an id, null (no section) or "default" (the suggestion). */
  setItemOptions: (
    contentId: string,
    body: { occasion?: string[] | null; holiday?: string[] | null; sections?: Record<string, number | null | "default"> },
  ) => req<ItemOptions>(`/content/${contentId}/options`, { method: "PUT", body: JSON.stringify(body) }),
  setItemOptionsForSelected: (
    batchId: string,
    body: {
      content_ids: string[];
      occasion?: string[] | null;
      holiday?: string[] | null;
      section_connection_id?: string;
      section_id?: number | null | "default";
    },
  ) =>
    req<{ updated: number; skipped: { content_id: string; reason: string }[] }>(`/batches/${batchId}/options`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // The listing's SKU (Etsy's rules; drafts on Etsy only change on "Update SKU on Etsy").
  // ``shopId``: the shop shown; once the listing is on Etsy elsewhere, its SKU there only.
  setGroupSku: (batchId: string, groupKey: string, sku: string, shopId: string | null = null) =>
    req<SkuResult>(`/batches/${batchId}/groups/sku`, {
      method: "PUT",
      body: JSON.stringify({ group_key: groupKey, sku, connection_id: shopId }),
    }),
  setListingSku: (contentId: string, sku: string, shopId: string | null = null) =>
    req<SkuResult>(`/content/${contentId}/sku`, { method: "PUT", body: JSON.stringify({ sku, connection_id: shopId }) }),
  setSkuForSelected: (batchId: string, body: { content_ids: string[]; sku?: string; prefix?: string; suffix?: string }) =>
    req<{ updated: number; results: SkuResult[]; skipped: { content_id: string; reason: string }[] }>(`/batches/${batchId}/sku`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Without ``confirm`` it only says which shops and what it costs. */
  updateSkuOnEtsy: (contentId: string, confirm = false, connectionIds: string[] = []) =>
    req<{ queued: boolean; sku: string; shops: SkuResult["etsy"]; requests: number }>(`/content/${contentId}/sku/etsy`, {
      method: "POST",
      body: JSON.stringify({ confirm, connection_ids: connectionIds }),
    }),
  /** Nothing is created unless ``confirm``. */
  createSection: (connectionId: string, title: string, contentIds: string[], confirm = false) =>
    req<SectionCreateResult>(`/shops/${connectionId}/sections`, {
      method: "POST",
      body: JSON.stringify({ title, content_ids: contentIds, confirm }),
    }),
  /** ``base``: what the card last read for the fields it sends (a field another tab changed
   * since is refused, 409). ``keepalive``: sent even while the page is being left. */
  updateContent: (
    id: string,
    body: Partial<Pick<Content, "title" | "tags" | "description">> & {
      personalization?: Personalization | null;
      base?: Partial<Pick<Content, "title" | "tags" | "description">>;
      /** A shop the listing is not in yet: the text sent is that shop's own. */
      shop?: string | null;
    },
    opts: { keepalive?: boolean } = {},
  ) =>
    req<ContentUpdateResult>(`/content/${id}`, { method: "PATCH", body: JSON.stringify(body), keepalive: opts.keepalive }),
  // Mockup templates and their photos (backend api/mockups.py): no Etsy call, no AI call.
  mockupTemplates: () => req<MockupTemplate[]>("/mockup-templates"),
  mockupTemplate: (id: string) => req<MockupTemplate>(`/mockup-templates/${id}`),
  createMockupTemplate: (name: string, presets = false) =>
    req<MockupTemplate>("/mockup-templates", { method: "POST", body: JSON.stringify({ name, presets }) }),
  /** The account's subgroup presets (backend api/mockups.py), used by any template. */
  subgroupPresets: () => req<SubgroupPreset[]>("/mockup-subgroup-presets"),
  addSubgroupPreset: (body: { name: string; use_on?: string; suffix?: string | null }) =>
    req<SubgroupPreset>("/mockup-subgroup-presets", { method: "POST", body: JSON.stringify(body) }),
  updateSubgroupPreset: (id: string, body: { name?: string; use_on?: string; suffix?: string | null }) =>
    req<SubgroupPreset>(`/mockup-subgroup-presets/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteSubgroupPreset: (id: string) => req<void>(`/mockup-subgroup-presets/${id}`, { method: "DELETE" }),
  assignSubgroupPresets: (templateId: string, presetIds: string[]) =>
    req<MockupTemplate>(`/mockup-templates/${templateId}/presets`, { method: "POST", body: JSON.stringify({ preset_ids: presetIds }) }),
  saveSubgroupAsPreset: (subgroupId: string) => req<SubgroupPreset>(`/mockup-subgroups/${subgroupId}/preset`, { method: "POST" }),
  renameMockupTemplate: (id: string, name: string) =>
    req<MockupTemplate>(`/mockup-templates/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  /** "This template has several garments in one photo". */
  updateMockupTemplate: (id: string, body: { name?: string; back_without_design?: "front" | "plain"; file_prefix?: string }) =>
    req<MockupTemplate>(`/mockup-templates/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteMockupTemplate: (id: string) => req<void>(`/mockup-templates/${id}`, { method: "DELETE" }),
  /** A template's subgroups ("Comfort Colors White"): add, change, remove (its photos go to the first one). */
  addMockupSubgroup: (templateId: string, body: { name: string; use_on?: string; suffix?: string | null }) =>
    req<MockupTemplate>(`/mockup-templates/${templateId}/subgroups`, { method: "POST", body: JSON.stringify(body) }),
  updateMockupSubgroup: (id: string, body: { name?: string; use_on?: string; suffix?: string | null; position?: number }) =>
    req<MockupTemplate>(`/mockup-subgroups/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteMockupSubgroup: (id: string) => req<MockupTemplate>(`/mockup-subgroups/${id}`, { method: "DELETE" }),
  orderMockupTemplates: (ids: string[]) =>
    req<MockupTemplate[]>("/mockup-templates/order", { method: "PUT", body: JSON.stringify({ ids }) }),
  /** One blank photo (up to 30 MB) into a template, with upload progress, a timeout and the server's own error. */
  addMockupPhoto: (templateId: string, file: File, name: string, rightsConfirmed: boolean, onProgress: (pct: number) => void) => {
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("name", name);
    form.append("rights_confirmed", rightsConfirmed ? "true" : "false");
    return postForm<MockupPhoto>(`${BASE}/mockup-templates/${templateId}/photos`, form, onProgress, UPLOAD_TIMEOUT_MS);
  },
  orderMockupPhotos: (templateId: string, ids: string[]) =>
    req<MockupTemplate>(`/mockup-templates/${templateId}/photos/order`, { method: "PUT", body: JSON.stringify({ ids }) }),
  mockupPhoto: (id: string) => req<MockupPhoto>(`/mockup-photos/${id}`),
  /** A version of personalization text within Etsy's limit, by the content model: an offer only, nothing saved. */
  shortenPersonalization: (field: "question" | "instructions", text: string) =>
    req<{ text: string | null; message: string | null }>("/personalization/shorten", { method: "POST", body: JSON.stringify({ field, text }) }),
  /** "Use this" on a profile: the new text, then every held listing that follows it goes ahead. */
  fixProfilePersonalization: (profileId: string, body: { question_text?: string; instructions?: string }) =>
    req<PersonalizationFixResult & { profile: Profile }>(`/profiles/${profileId}/personalization/fix`, { method: "POST", body: JSON.stringify(body) }),
  /** "Use this" on a listing card (its own setting, or its profile's: then every listing that follows it). */
  fixContentPersonalization: (contentId: string, body: { question_text?: string; instructions?: string }) =>
    req<PersonalizationFixResult & { content: Content }>(`/content/${contentId}/personalization/fix`, { method: "POST", body: JSON.stringify(body) }),
  updateMockupPhoto: (
    id: string,
    body: {
      name?: string; size?: number; fold_strength?: number; occlusion?: boolean; view?: "front" | "back"; template_id?: string;
      layout?: "single" | "front_back" | "several"; subgroup_id?: string; colour?: "light" | "dark";
    },
  ) => req<MockupPhoto>(`/mockup-photos/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  /** The editor's print areas; confirm = "Looks right". */
  saveMockupAreas: (
    id: string,
    areas: {
      print_area: [number, number][]; detected_area?: [number, number][]; source: string; kind?: string | null; role?: string;
      subgroup_id?: string | null; tone?: string | null; subgroup_any?: boolean;
    }[],
    confirm: boolean,
    size?: number,
    printAll?: boolean,
  ) => req<MockupPhoto>(`/mockup-photos/${id}/areas`, { method: "PUT", body: JSON.stringify({ areas, confirm, size, print_all: printAll }) }),
  /** "Add area": the garment under the tap (shares of the photo), alone. Nothing is saved. */
  /** "Click a garment to add an area": the garment under the click; else a box of ``like``'s size and angle there. */
  findMockupGarment: (id: string, x: number, y: number, areas: [number, number][][], like?: [number, number][] | null) =>
    req<FoundArea>(`/mockup-photos/${id}/find`, { method: "POST", body: JSON.stringify({ x, y, areas, like: like ?? null }) }),
  /** "Apply this area to selected": each target placed by its own garment (fit), or the same shares. */
  copyMockupAreas: (id: string, photoIds: string[], fit = true) =>
    req<MockupPhoto[]>(`/mockup-photos/${id}/copy`, { method: "POST", body: JSON.stringify({ photo_ids: photoIds, fit }) }),
  /** "Confirm selected": these photos' areas confirmed as they are. */
  mockupPlacement: () => req<MockupPlacement>("/mockup-placement"),
  /** Forget the seller's own placement: new photos are suggested from the standard again. */
  resetMockupPlacement: () => req<MockupPlacement>("/mockup-placement", { method: "DELETE" }),
  confirmMockupPhotos: (templateId: string, photoIds: string[]) =>
    req<MockupTemplate>(`/mockup-templates/${templateId}/confirm`, { method: "POST", body: JSON.stringify({ photo_ids: photoIds }) }),
  /** The "Keep in front" brush: a gray PNG (white in front, black not, mid gray as found). */
  saveFrontBrush: async (id: string, png: Blob) => {
    const form = new FormData();
    form.append("file", png, "brush.png");
    const res = await fetch(`${BASE}/mockup-photos/${id}/front`, { method: "PUT", body: form, cache: "no-store" });
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? "The brush could not be saved.");
    return (await res.json()) as MockupPhoto;
  },
  clearFrontBrush: (id: string) => req<MockupPhoto>(`/mockup-photos/${id}/front`, { method: "DELETE" }),
  /** What is in front of the garment: "cutout" (the photo's pixels, transparent elsewhere), "combined" or "brush" (gray). */
  mockupFrontUrl: (id: string, layer: "cutout" | "combined" | "brush", version: number, w?: number) =>
    `${BASE}/mockup-photos/${id}/front?layer=${layer}&v=${version}${w ? `&w=${w}` : ""}`,
  deleteMockupPhoto: (id: string) => req<void>(`/mockup-photos/${id}`, { method: "DELETE" }),
  mockupPhotoImageUrl: (id: string, size?: ImageSize) => `${BASE}/mockup-photos/${id}/image` + query({ size, v: IMAGE_VERSION }),
  /** The photo with a sample design printed (shaded and warped by the fabric), with unsaved areas: a JPEG. */
  previewMockupPhoto: async (
    id: string,
    body: { areas?: [number, number][][]; size?: number; fold_strength?: number; occlusion?: boolean; behind?: boolean[] },
  ) => {
    const res = await fetch(`${BASE}/mockup-photos/${id}/preview`, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? "The preview could not be drawn.");
    return res.blob();
  },
  /** A design; with subgroup slots, the slot it was dropped on and every slot's name, suffix and "Use on" (pairs files into one design). */
  addMockupDesign: (batchId: string, file: File, onProgress: (pct: number) => void, slots?: { subgroup?: string | null; subgroups: SlotIn[] }) => {
    const form = new FormData();
    form.append("file", file, file.name);
    if (slots?.subgroups.length) form.append("subgroups", JSON.stringify(slots.subgroups));
    if (slots?.subgroup) form.append("subgroup", slots.subgroup);
    return postForm<MockupDesign>(`${BASE}/batches/${batchId}/designs`, form, onProgress, UPLOAD_TIMEOUT_MS);
  },
  /** A ZIP of designs: each image added (its SKU read from its name); what fails, with why. */
  addMockupDesignArchive: (batchId: string, file: File, onProgress: (pct: number) => void, subgroups?: SlotIn[]) => {
    const form = new FormData();
    form.append("file", file, file.name);
    if (subgroups?.length) form.append("subgroups", JSON.stringify(subgroups));
    return postForm<{ added: MockupDesign[]; failed: { filename: string; reason: string }[] }>(
      `${BASE}/batches/${batchId}/designs/archive`, form, onProgress, UPLOAD_TIMEOUT_MS);
  },
  /** A design's back file (printed on back areas), and removing it. */
  setDesignBack: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return reqForm<MockupDesign>(`/mockup-designs/${id}/back`, form, "PUT");
  },
  removeDesignBack: (id: string) => req<MockupDesign>(`/mockup-designs/${id}/back`, { method: "DELETE" }),
  updateMockupDesign: (id: string, sku: string | null) =>
    req<MockupDesign>(`/mockup-designs/${id}`, { method: "PATCH", body: JSON.stringify({ sku }) }),
  /** "Use this design on the other group too" (only one light/dark variant uploaded). */
  setDesignFillOther: (id: string, fill_other: boolean) =>
    req<MockupDesign>(`/mockup-designs/${id}`, { method: "PATCH", body: JSON.stringify({ fill_other }) }),
  variantSetting: () => req<{ variant_suffix: "shirt" | "ink" }>("/mockup-settings"),
  setVariantSetting: (variant_suffix: "shirt" | "ink") =>
    req<{ variant_suffix: "shirt" | "ink" }>("/mockup-settings", { method: "PUT", body: JSON.stringify({ variant_suffix }) }),
  /** Which design file goes on which photos of the chosen templates, before anything is made. */
  mockupPlan: (batchId: string, templateIds: string[], sides: "front" | "back" | "both", designIds?: string[] | null) =>
    req<MockupPlan>(`/batches/${batchId}/mockups/plan`, {
      method: "POST", body: JSON.stringify({ template_ids: templateIds, sides, design_ids: designIds ?? null }),
    }),
  /** Delete a design and its files ("onlyFile": just its own file; a version then takes its place). */
  deleteMockupDesign: (id: string, onlyFile = false) =>
    req<{ deleted: boolean; design: MockupDesign | null }>(`/mockup-designs/${id}${onlyFile ? "?only_file=true" : ""}`, { method: "DELETE" }),
  /** Delete one of a design's files for another subgroup. */
  deleteMockupDesignVersion: (id: string) => req<MockupDesign>(`/mockup-design-versions/${id}`, { method: "DELETE" }),
  /** Move a design's file (or one of its versions) to another subgroup's slot. */
  moveMockupDesign: (id: string, subgroup: string | null) =>
    req<MockupDesign>(`/mockup-designs/${id}`, { method: "PATCH", body: JSON.stringify({ subgroup }) }),
  moveMockupDesignVersion: (id: string, subgroup: string) =>
    req<MockupDesign>(`/mockup-design-versions/${id}`, { method: "PATCH", body: JSON.stringify({ subgroup }) }),
  /** A mockup's image; with its version (``stamp``) the browser keeps it, and a mockup made
   * again has a new version, so an earlier file is never shown. */
  mockupOutputImageUrl: (runId: string, outputId: string, size?: ImageSize, stamp?: string) =>
    `${BASE}/mockup-runs/${runId}/outputs/${outputId}/image` + query({ size, v: stamp }),
  /** "Use for listings": a listing group per design, the chosen cover first. */
  useMockupsForListings: (runId: string, designIds: string[] | null, covers: Record<string, string>) =>
    req<{ groups: number; photos: number }>(`/mockup-runs/${runId}/listings`, {
      method: "POST",
      body: JSON.stringify({ design_ids: designIds, covers }),
    }),
  requestMockupZip: (runId: string, designIds: string[] | null, layout: "folders" | "flat") =>
    req<MockupZip>(`/mockup-runs/${runId}/zip`, { method: "POST", body: JSON.stringify({ design_ids: designIds, layout }) }),
  mockupZip: (id: string) => req<MockupZip>(`/mockup-zips/${id}`),
  mockupZipUrl: (id: string) => `${BASE}/mockup-zips/${id}/download`,
  startMockups: (
    batchId: string,
    templateIds: string[],
    options: { sides?: "front" | "back" | "both"; garment_mode?: "same" | "order"; design_ids?: string[] | null } = {},
  ) =>
    req<MockupRun>(`/batches/${batchId}/mockups`, {
      method: "POST",
      body: JSON.stringify({ template_ids: templateIds, ...options }),
    }),
  mockupRun: (runId: string) => req<MockupRun>(`/mockup-runs/${runId}`),
  /** A batch's designs (a run's results opened from its notification). */
  batchDesigns: (batchId: string) => req<MockupDesign[]>(`/batches/${batchId}/designs`),
  /** The design editor (backend api/mockup_edits.py): every edit is a render-time layer. */
  mockupEdits: (runId: string) => req<MockupEdits>(`/mockup-runs/${runId}/edits`),
  /** A design file at full size (the editor's arithmetic needs its pixels); with its ``stamp`` the browser keeps it. */
  mockupDesignFileUrl: (designId: string, versionId?: string | null, stamp?: string) =>
    `${BASE}/mockup-designs/${designId}/file` + query({ version: versionId ?? undefined, v: stamp }),
  saveAdjustment: (row: MockupAdjustmentRow) =>
    req<MockupAdjustmentRow>("/mockup-adjustments", { method: "PUT", body: JSON.stringify(row) }),
  applyAdjustment: (body: { run_id: string; design_id: string; photo_id: string; area: number; before: unknown; after: unknown;
    scope: "design_photos" | "photo_designs" | "template_all" }) =>
    req<{ changed: number; undo: MockupAdjustmentRow[] }>("/mockup-adjustments/apply", { method: "POST", body: JSON.stringify(body) }),
  restoreAdjustments: (rows: MockupAdjustmentRow[]) =>
    req<{ changed: number }>("/mockup-adjustments/restore", { method: "POST", body: JSON.stringify({ rows }) }),
  saveRecolour: (designId: string, body: { subgroup: string; regions: unknown[]; scope?: "photo_designs" | "template_all" | null }) =>
    req<{ changed: number; undo: MockupRecolourRow[] }>(`/mockup-designs/${designId}/recolour`, { method: "PUT", body: JSON.stringify(body) }),
  restoreRecolours: (rows: MockupRecolourRow[]) =>
    req<{ changed: number }>("/mockup-recolours/restore", { method: "POST", body: JSON.stringify({ rows }) }),
  /** Make these mockups again: the run comes back with them in ``refreshing`` (asked for while
   * others are being made, they join the same queue). */
  rerenderMockups: (runId: string, pairs: [string, string][]) =>
    req<MockupRun>(`/mockup-runs/${runId}/rerender`, { method: "POST", body: JSON.stringify({ pairs }) }),
  /** The checklist for the card's unsaved text: nothing is saved, nothing read from Etsy. */
  previewChecklist: (id: string, body: Partial<Pick<Content, "title" | "tags" | "description">>) =>
    req<Checklist>(`/content/${id}/checklist`, { method: "POST", body: JSON.stringify(body) }),
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
  // --- The Scheduled page (B8) ---
  scheduled: () => req<ScheduledData>("/scheduled"),
  scheduledPreview: (op: string, keys: string[], params: Record<string, unknown> = {}) =>
    req<ScheduledPreview>("/scheduled/preview", { method: "POST", body: JSON.stringify({ op, keys, params }) }),
  scheduledApply: (op: string, keys: string[], params: Record<string, unknown> = {}) =>
    req<ScheduledApplied>("/scheduled/apply", { method: "POST", body: JSON.stringify({ op, keys, params }) }),
  scheduledRestore: (undo: ScheduledApplied["undo"]) =>
    req<ScheduledApplied>("/scheduled/restore", { method: "POST", body: JSON.stringify({ undo }) }),
  pauseAllSchedules: (paused: boolean) => req<ScheduledData>("/scheduled/paused", { method: "PUT", body: JSON.stringify({ paused }) }),
  setGroupSchedule: (id: string, body: { paused?: boolean; settings?: GroupSettings }) =>
    req<ScheduledData>(`/scheduled/groups/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  reflowGroup: (id: string, settings: GroupSettings, apply = false) =>
    req<ScheduledPreview>(`/scheduled/groups/${id}/reflow`, { method: "POST", body: JSON.stringify({ settings, apply }) }),
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
  /** "Delete my sales history" for the shop (the server refuses it without the confirmation). */
  deleteSalesHistory: (shop: string | null) =>
    req<{ deleted: Record<string, number>; history_from: string; status: SalesSync }>(
      `/analytics/sales/history/delete${shopQuery(shop)}`, { method: "POST", body: JSON.stringify({ confirm: true }) }),
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
   * A batch photo: its ``size`` copy (see ``ImageSize``); none is the full file.
   * `aspect` additionally crops to that tile ratio, centred on the artwork
   * rather than the frame, so a portrait mockup is not sliced through.
   */
  assetImage: (id: string, size?: ImageSize, aspect?: "4:5" | "16:10") =>
    `${BASE}/assets/${id}/image` + query({ size, ar: size ? aspect : undefined, v: IMAGE_VERSION }),
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
      /** Each chart's own place, in the order they appear; null: back to the preset. */
      size_chart_placement: { listing_image_id: number; slot: number }[] | null;
      /** "" = none, null = no default. */
      default_occasion: string | null;
      default_holiday: string | null;
      /** null: back to the default bound. */
      title_min_length: number | null;
      title_max_length: number | null;
      /** null: back to the reference's question (v7 §D4). */
      personalization: Personalization | null;
      /** How it's made: sent together; both null clears them. */
      how_made_production: string | null;
      how_made_tools: string[] | null;
    }>,
  ) => req<Profile>(`/profiles/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  /** The same How it's made answers on many profiles; no ids: every confirmed profile. */
  howMadeForProfiles: (production: string, tools: string[], profile_ids?: string[] | null) =>
    req<{ updated: number }>("/profiles/how-made", {
      method: "POST",
      body: JSON.stringify({ production, tools, profile_ids: profile_ids ?? null }),
    }),
  confirmProfile: (id: string) => req<Profile>(`/profiles/${id}/confirm`, { method: "POST" }),
  refreshProfile: (id: string) => req<Profile>(`/profiles/${id}/refresh`, { method: "POST" }),
  deleteProfile: (id: string) => req<void>(`/profiles/${id}`, { method: "DELETE" }),
  // --- One profile across shops (v8 §C) ---
  /** Listings created on Etsy whose draft did not finish, and "Finish these drafts" (backend api/unfinished.py). */
  unfinishedDrafts: (batchId?: string) =>
    req<UnfinishedDraft[]>(`/drafts/unfinished${batchId ? `?batch_id=${batchId}` : ""}`),
  /** Take a draft a newer one replaced off the list (only the app's record; Etsy is not touched). */
  dismissReplacedDraft: (recordId: string) =>
    req<void>(`/drafts/unfinished/${encodeURIComponent(recordId)}/dismiss`, { method: "POST" }),
  finishDrafts: (body: { record_ids?: string[] | null; attempt_ids?: string[] | null; batch_id?: string | null }) =>
    req<{ started: number; skipped: { record_id: string; attempt_id: string | null; reason: string }[] }>("/drafts/unfinished/finish", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  useProfileIn: (id: string, scope: UseInScope) =>
    req<{ profile: Profile; started: string[] }>(`/profiles/${id}/use-in`, { method: "POST", body: JSON.stringify(scope) }),
  profileSetup: (id: string) => req<ProfileSetup>(`/profiles/${id}/setup`),
  applySetup: (id: string, choices: SetupChoice[]) =>
    req<{ profile: Profile; requests: number }>(`/profiles/${id}/setup/apply`, { method: "POST", body: JSON.stringify({ choices }) }),
  createInShops: (id: string, items: { connection_id: string; resource: string }[]) =>
    req<{ profile: Profile; requests: number }>(`/profiles/${id}/setup/create`, { method: "POST", body: JSON.stringify({ items }) }),
  pickInShop: (id: string, shop: string, resource: string, ids: number[]) =>
    req<Profile>(`/profiles/${id}/setup/${shop}`, { method: "PUT", body: JSON.stringify({ resource, ids }) }),
  checkShopAgain: (id: string, shop: string) => req<Profile>(`/profiles/${id}/setup/${shop}/check`, { method: "POST" }),
  profileShopTable: (id: string) => req<ProfileShopTable>(`/profiles/${id}/shops`),
  /** One value for one shop on a profile's Shops table; ``follow``: the shop follows the profile again. */
  setProfileShopValue: (id: string, shop: string, field: "section_title" | "title_prefix", value: string | null, follow = false) =>
    req<ProfileShopTable>(`/profiles/${id}/shops/${shop}`, { method: "PUT", body: JSON.stringify({ field, value, follow }) }),
  setShopDailyLimit: (shop: string, daily_limit: number | null) =>
    req<{ connection_id: string; daily_limit: number | null }>(`/shops/${shop}/daily-limit`, { method: "PUT", body: JSON.stringify({ daily_limit }) }),
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
  // --- Where and when (B4) ---
  sendState: (batch: string) => req<SendState>(`/batches/${batch}/send`),
  /** The batch's destination, or (with contentIds) those listings' own; null = back to the default. */
  setSendTo: (batch: string, send_to: Destination | null, contentIds?: string[]) =>
    req<SendState>(`/batches/${batch}/send-to`, {
      method: "PUT",
      body: JSON.stringify({ send_to, content_ids: contentIds ?? null }),
    }),
  sendToAllShops: (batch: string) => req<SendState>(`/batches/${batch}/send-to/all-shops`, { method: "POST" }),
  // ``only``: one shop and these listings (the batch page's shop workspace); omitted: the batch's choice.
  sendPreview: (batch: string, when: SendWhen, schedule?: GroupSchedule | null, only?: { shop: string; contentIds: string[] }) =>
    req<SendPreview>(`/batches/${batch}/send/preview`, {
      method: "POST",
      body: JSON.stringify({ when, ...(schedule ? { schedule } : {}), ...(only ? { connection_id: only.shop, content_ids: only.contentIds } : {}) }),
    }),
  send: (batch: string, when: SendWhen, schedule?: GroupSchedule | null, only?: { shop: string; contentIds: string[] }) =>
    req<SendResult>(`/batches/${batch}/send`, {
      method: "POST",
      body: JSON.stringify({ when, ...(schedule ? { schedule } : {}), ...(only ? { connection_id: only.shop, content_ids: only.contentIds } : {}) }),
    }),
  detectProfiles: (shop?: string | null) =>
    req<{ status: string }>(`/shop/detect-profiles${shopQuery(shop)}`, { method: "POST" }),
  shopListings: (shop?: string | null) => req<ShopListings>(`/shop/listings${shopQuery(shop)}`),
  /** Cached counts only — unlike shopListings this never triggers a sync. */
  /** One shop's counts; no shop = every shop added up (the switcher's "All shops"). */
  shopSummary: (shop?: string | null) => req<ShopSummary>(shop ? `/shop/summary${shopQuery(shop)}` : "/shop/summary?all=1"),
  shopsOverview: () => req<ShopsOverview>("/shop/overview"),
  attention: () => req<Attention>("/attention"),
  analyticsShops: (month?: string | null) =>
    req<AnalyticsShops>(`/analytics/shops${month ? `?month=${encodeURIComponent(month)}` : ""}`),
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

/** How long an upload may take before the page stops waiting and says so. */
export const UPLOAD_TIMEOUT_MS = 180_000;

function postForm<T>(url: string, form: FormData, onProgress: (pct: number) => void, timeoutMs = 0): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    // Never wait silently: a request that takes too long ends with a message.
    xhr.timeout = timeoutMs;
    xhr.ontimeout = () =>
      reject(new ApiError("The upload took too long and was stopped. Check your connection and try again.", 0));
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as T);
      } else {
        // Refusals (415 not an image, 413 too large) carry a readable `detail`;
        // show that, not the raw JSON envelope.
        let body: unknown;
        try {
          body = JSON.parse(xhr.responseText);
        } catch {
          /* non-JSON body: the status says what happened */
        }
        reject(new ApiError(messageFor(xhr.status, body, xhr.statusText), xhr.status));
      }
    };
    xhr.onerror = () => reject(new ApiError("The upload could not reach the server. Check your connection and try again.", 0));
    xhr.send(form);
  });
}
