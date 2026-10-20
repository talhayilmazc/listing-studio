// A batch's statuses (backend pipeline/batch_status.py, the one definition): the Batches page's
// filter (several statuses, a count each, kept in the URL as ?status=a,b) and the one badge every
// place shows for a batch (Batches, the batch page header, Overview). Same keys and order as the
// server; a test keeps them together.

export const BATCH_STATUSES = [
  { key: "uploading", label: "Uploading", hint: "Its files are still being uploaded" },
  { key: "not_written", label: "Not written yet", hint: "A group of its photos has no listing yet" },
  { key: "in_review", label: "In review", hint: "A written listing is not approved yet" },
  { key: "approved", label: "Approved, not drafted", hint: "An approved listing has nothing on Etsy yet" },
  { key: "partly_drafted", label: "Drafted in some shops", hint: "A listing has drafts in some of its shops, not all" },
  { key: "drafted", label: "Drafted", hint: "A listing has a draft in every one of its shops" },
  { key: "scheduled", label: "Scheduled", hint: "A draft will go live at a time you chose" },
  { key: "partly_published", label: "Partly published", hint: "Some of it is live, not every listing in every shop" },
  { key: "published", label: "Published", hint: "Every listing is live in every shop it was meant for" },
  { key: "half_made", label: "Half-made", hint: "A draft was started on Etsy and not finished" },
  { key: "failed", label: "Failed", hint: "A draft or going live failed" },
  { key: "deleted", label: "Deleted on Etsy", hint: "A draft was deleted on Etsy" },
] as const;
export type BatchStatusKey = (typeof BATCH_STATUSES)[number]["key"];
const KEYS = new Set<string>(BATCH_STATUSES.map((s) => s.key));

/** The one status a badge shows: what needs the seller first, then how far it got (server HEADLINE). */
export const HEADLINE: readonly BatchStatusKey[] = [
  "failed", "half_made", "uploading", "not_written", "in_review", "approved",
  "partly_drafted", "drafted", "scheduled", "partly_published", "published", "deleted",
];

export function headline(statuses: readonly string[] | null | undefined): (typeof BATCH_STATUSES)[number] | null {
  const key = HEADLINE.find((k) => (statuses ?? []).includes(k));
  return key ? BATCH_STATUSES.find((s) => s.key === key)! : null;
}

/** The badge's tone: what needs the seller stands out, what is done is calm. */
export function tone(key: BatchStatusKey | undefined): "danger" | "warning" | "done" | "neutral" {
  if (key === "failed") return "danger";
  if (key === "half_made" || key === "in_review" || key === "not_written" || key === "approved") return "warning";
  if (key === "published") return "done";
  return "neutral";
}

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
