import type { AdminDisk } from "./types";

/**
 * The server's disk by category (admin only): the pieces the bar and the table
 * share, so they always agree.
 */

/** Bytes as a person reads them: "412 MB", "6.1 GB"; unknown is a dash. */
export function size(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

/**
 * One hue per category, in a fixed order (validated as a set). What is left
 * over is not a thing of its own, so it is grey; free space is the empty track.
 */
export const DISK_COLORS: Record<string, string> = {
  uploads: "#2a78d6",
  derivatives: "#eb6834",
  database: "#1baf7a",
  backups: "#eda100",
  docker: "#e87ba4",
  other: "#898781",
  unmeasured: "#898781",
};
export const FREE_COLOR = "#e7e5e4";

export interface Segment {
  key: string;
  label: string;
  bytes: number;
  /** Share of the whole disk, 0..1. */
  share: number;
  color: string;
}

/**
 * The bar's segments, left to right, ending with free space. While the host
 * has not reported backups and Docker, what the app cannot attribute is one
 * grey segment that says so, rather than categories drawn as zero.
 */
export function segments(disk: AdminDisk): Segment[] {
  const total = disk.total_bytes;
  const free = disk.free_bytes;
  if (!total || free === null) return [];
  const known = disk.categories.filter((c) => c.bytes !== null && c.key !== "other");
  const out: Segment[] = known.map((c) => ({ key: c.key, label: c.label, bytes: c.bytes ?? 0, share: 0, color: DISK_COLORS[c.key] ?? DISK_COLORS.other }));
  const other = disk.categories.find((c) => c.key === "other");
  const rest = Math.max(0, total - free - out.reduce((sum, s) => sum + s.bytes, 0));
  if (other && other.bytes !== null) {
    out.push({ key: "other", label: other.label, bytes: other.bytes, share: 0, color: DISK_COLORS.other });
  } else if (rest > 0) {
    const missing = disk.categories.filter((c) => c.bytes === null && c.key !== "other").map((c) => c.label.toLowerCase());
    out.push({ key: "unmeasured", label: `Not measured separately yet (${[...missing, "system"].join(", ")})`, bytes: rest, share: 0, color: DISK_COLORS.unmeasured });
  }
  out.push({ key: "free", label: "Free", bytes: free, share: 0, color: FREE_COLOR });
  return out.filter((s) => s.bytes > 0).map((s) => ({ ...s, share: s.bytes / total }));
}

export function percent(share: number): string {
  const p = share * 100;
  return p > 0 && p < 1 ? "<1%" : `${Math.round(p)}%`;
}
