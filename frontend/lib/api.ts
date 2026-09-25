import type {
  Account,
  AdminInvite,
  AdminUsage,
  AdminUser,
  ApproveAllResult,
  ArchiveResult,
  Schedule,
  ScheduleItem,
  ScheduleResult,
  InviteIssued,
  TempPasswordIssued,
  Asset,
  BatchCost,
  BatchDetail,
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
    setQuota: (id: string, dailyQuota: number) =>
      req<AdminUser>(`/admin/users/${id}/quota`, {
        method: "PUT",
        body: JSON.stringify({ daily_quota: dailyQuota }),
      }),
    /** null = back to the default ceiling. */
    setShopLimit: (id: string, maxShops: number | null) =>
      req<AdminUser>(`/admin/users/${id}/shops`, {
        method: "PUT",
        body: JSON.stringify({ max_shops: maxShops }),
      }),
    invites: () => req<AdminInvite[]>("/admin/invites"),
    createInvite: (body: { email?: string; note?: string; expires_in_days: number | null }) =>
      req<InviteIssued>("/admin/invites", { method: "POST", body: JSON.stringify(body) }),
    revokeInvite: (id: string) =>
      req<AdminInvite>(`/admin/invites/${id}/revoke`, { method: "POST" }),
    usage: () => req<AdminUsage>("/admin/usage"),
  },

  listBatches: () => req<BatchSummary[]>("/batches"),
  getBatch: (id: string) => req<BatchDetail>(`/batches/${id}`),
  createBatch: () => req<BatchSummary>("/batches", { method: "POST" }),
  finalizeBatch: (id: string) => req<BatchSummary>(`/batches/${id}/finalize`, { method: "POST" }),
  // Choose which profile's size charts to append when publishing this batch (Task 4).
  setSizeChartProfile: (id: string, profileId: string | null) =>
    req<BatchSummary>(`/batches/${id}/size-chart-profile`, {
      method: "PUT",
      body: JSON.stringify({ profile_id: profileId }),
    }),
  // Generate content for a batch; pass a groupKey to limit to one folder group (D3).
  // profileId is optional — each group can carry its own assigned profile (v4 §E).
  generate: (id: string, profileId?: string, groupKey?: string) =>
    req<GenerateResult>(`/batches/${id}/generate`, {
      method: "POST",
      body: JSON.stringify({
        ...(profileId ? { profile_id: profileId } : {}),
        ...(groupKey != null ? { group_key: groupKey } : {}),
      }),
    }),
  // Per-group profile selection (v4 §E): omit group_key to bulk-apply to all groups.
  /** Save a listing group's image order; the first is the cover (v6 §E). "" = root files. */
  orderGroup: (batchId: string, groupKey: string, assetIds: string[]) =>
    req<BatchDetail>(`/batches/${batchId}/groups/order`, {
      method: "PUT",
      body: JSON.stringify({ group_key: groupKey, asset_ids: assetIds }),
    }),
  listGroups: (id: string) => req<Group[]>(`/batches/${id}/groups`),
  assignGroup: (
    id: string,
    body: { group_key?: string | null; profile_id?: string | null; size_chart_profile_id?: string | null },
  ) => req<Group[]>(`/batches/${id}/groups`, { method: "PUT", body: JSON.stringify(body) }),
  batchCost: (id: string) => req<BatchCost>(`/batches/${id}/cost`),
  listContent: (id: string) => req<Content[]>(`/batches/${id}/content`),
  updateContent: (id: string, body: Partial<Pick<Content, "title" | "tags" | "description">>) =>
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
  listProfiles: (shop?: string | null) => req<Profile[]>(`/profiles${shopQuery(shop)}`),
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
    }>,
  ) => req<Profile>(`/profiles/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  confirmProfile: (id: string) => req<Profile>(`/profiles/${id}/confirm`, { method: "POST" }),
  refreshProfile: (id: string) => req<Profile>(`/profiles/${id}/refresh`, { method: "POST" }),
  deleteProfile: (id: string) => req<void>(`/profiles/${id}`, { method: "DELETE" }),
  detectProfiles: (shop?: string | null) =>
    req<{ status: string }>(`/shop/detect-profiles${shopQuery(shop)}`, { method: "POST" }),
  shopListings: (shop?: string | null) => req<ShopListings>(`/shop/listings${shopQuery(shop)}`),
  /** Cached counts only — unlike shopListings this never triggers a sync. */
  shopSummary: (shop?: string | null) => req<ShopSummary>(`/shop/summary${shopQuery(shop)}`),
  useListingAsProfile: (listingId: number, shop?: string | null) =>
    req<Profile>(`/shop/listings/${listingId}/use-as-profile${shopQuery(shop)}`, {
      method: "POST",
    }),
  replaceImages: (listingId: number, batchId: string, shop?: string | null) =>
    req<ReplaceImagesResult>(`/shop/listings/${listingId}/replace-images${shopQuery(shop)}`, {
      method: "POST",
      body: JSON.stringify({ batch_id: batchId }),
    }),
};

/** Upload a single file with per-file progress via XHR. */
export function uploadAsset(
  batchId: string,
  file: File,
  onProgress: (pct: number) => void,
  groupKey?: string,
): Promise<Asset> {
  const form = new FormData();
  form.append("file", file, file.name);
  // Folder-derived listing group (D1); empty string = root (single) group.
  if (groupKey !== undefined) form.append("group_key", groupKey);
  return postForm<Asset>(`${BASE}/batches/${batchId}/assets`, form, onProgress);
}

/** Upload a ZIP; the server unpacks it, keeping its folders as groups (v6 §F). */
export function uploadArchive(
  batchId: string,
  file: File,
  onProgress: (pct: number) => void,
): Promise<ArchiveResult> {
  const form = new FormData();
  form.append("file", file, file.name);
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
