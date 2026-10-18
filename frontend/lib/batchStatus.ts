// The Batches page's status filter (backend api/batches.py ``batch_statuses``; seller feedback D1):
// every status a batch can be in, several at once, a count each, kept in the URL (?status=a,b).

export const BATCH_STATUSES = [
  { key: "uploading", label: "Uploading" },
  { key: "not_written", label: "Not written yet" },
  { key: "in_review", label: "In review" },
  { key: "drafted", label: "Drafted" },
  { key: "scheduled", label: "Scheduled" },
  { key: "published", label: "Published" },
  { key: "half_made", label: "Half-made" },
  { key: "failed", label: "Failed" },
  { key: "deleted", label: "Deleted on Etsy" },
] as const;
export type BatchStatusKey = (typeof BATCH_STATUSES)[number]["key"];
const KEYS = new Set<string>(BATCH_STATUSES.map((s) => s.key));

/** The statuses named in the URL (unknown ones ignored, each once, in the list's order). */
export function parseStatuses(param: string | null | undefined): BatchStatusKey[] {
  const wanted = new Set((param ?? "").split(",").map((s) => s.trim()).filter((s) => KEYS.has(s)));
  return BATCH_STATUSES.map((s) => s.key).filter((k) => wanted.has(k));
}

/** The URL value for a choice ("" when none: every batch). */
export const statusParam = (keys: readonly string[]) => parseStatuses(keys.join(",")).join(",");

/** A batch matches when it has any of the chosen statuses (none chosen: every batch). */
export function matches(statuses: readonly string[] | null | undefined, chosen: readonly string[]): boolean {
  return !chosen.length || (statuses ?? []).some((s) => chosen.includes(s));
}

/** How many batches have each status. */
export function counts(batches: { statuses?: readonly string[] | null }[]): Record<BatchStatusKey, number> {
  const out = Object.fromEntries(BATCH_STATUSES.map((s) => [s.key, 0])) as Record<BatchStatusKey, number>;
  for (const b of batches) for (const s of b.statuses ?? []) if (s in out) out[s as BatchStatusKey] += 1;
  return out;
}

/** The URL with the filter set (other parameters kept). */
export function withStatuses(search: string, keys: readonly string[]): string {
  const params = new URLSearchParams(search);
  const value = statusParam(keys);
  if (value) params.set("status", value);
  else params.delete("status");
  const q = params.toString();
  return q ? `?${q}` : "";
}
