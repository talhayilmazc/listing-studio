import type {
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
  PublishJob,
  Quota,
  ReplaceImagesResult,
  ShopListings,
} from "./types";

// Same-origin: Next rewrites /api/* to the FastAPI backend.
const BASE = "/api";

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
  quota: () => req<Quota>("/quota"),
  meta: () => req<Meta>("/meta"),
  assetImage: (id: string) => `${BASE}/assets/${id}/image`,
  connection: () => req<Connection>("/auth/etsy/status"),
  disconnect: () => req<Connection>("/auth/etsy/disconnect", { method: "POST" }),
  publishContent: (id: string) =>
    req<PublishJob>(`/content/${id}/publish`, { method: "POST" }),
  publishLive: (id: string) =>
    req<PublishJob>(`/content/${id}/publish-live`, { method: "POST" }),
  // Bulk: create drafts / publish-live for every approved item in the batch (D3/E).
  publishBatch: (id: string) =>
    req<BatchPublishResult>(`/batches/${id}/publish`, { method: "POST" }),
  publishBatchLive: (id: string) =>
    req<BatchPublishResult>(`/batches/${id}/publish-live`, { method: "POST" }),
  jobStatus: (jobId: string) => req<JobStatus>(`/jobs/${jobId}`),

  // Reference-listing profiles (Section B) + shop listings (B4).
  listProfiles: () => req<Profile[]>("/profiles"),
  getProfile: (id: string) => req<Profile>(`/profiles/${id}`),
  createProfile: (body: { name: string; reference_listing_id: number; content_template?: string }) =>
    req<Profile>("/profiles", { method: "POST", body: JSON.stringify(body) }),
  updateProfile: (
    id: string,
    body: Partial<{ name: string; content_template: string; fixed_image_ids: number[]; confirmed: boolean }>,
  ) => req<Profile>(`/profiles/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  confirmProfile: (id: string) => req<Profile>(`/profiles/${id}/confirm`, { method: "POST" }),
  refreshProfile: (id: string) => req<Profile>(`/profiles/${id}/refresh`, { method: "POST" }),
  deleteProfile: (id: string) => req<void>(`/profiles/${id}`, { method: "DELETE" }),
  detectProfiles: () => req<{ status: string }>("/shop/detect-profiles", { method: "POST" }),
  shopListings: () => req<ShopListings>("/shop/listings"),
  useListingAsProfile: (listingId: number) =>
    req<Profile>(`/shop/listings/${listingId}/use-as-profile`, { method: "POST" }),
  replaceImages: (listingId: number, batchId: string) =>
    req<ReplaceImagesResult>(`/shop/listings/${listingId}/replace-images`, {
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
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file, file.name);
    // Folder-derived listing group (D1); empty string = root (single) group.
    if (groupKey !== undefined) form.append("group_key", groupKey);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}/batches/${batchId}/assets`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as Asset);
      } else {
        reject(new ApiError(xhr.responseText || xhr.statusText, xhr.status));
      }
    };
    xhr.onerror = () => reject(new ApiError("network error", 0));
    xhr.send(form);
  });
}
