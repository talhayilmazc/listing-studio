import type {
  Asset,
  BatchCost,
  BatchDetail,
  BatchSummary,
  Connection,
  Content,
  ContentUpdateResult,
  GenerateResult,
  Meta,
  Quota,
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
  generate: (id: string) => req<GenerateResult>(`/batches/${id}/generate`, { method: "POST" }),
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
};

/** Upload a single file with per-file progress via XHR. */
export function uploadAsset(
  batchId: string,
  file: File,
  onProgress: (pct: number) => void,
): Promise<Asset> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file, file.name);
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
