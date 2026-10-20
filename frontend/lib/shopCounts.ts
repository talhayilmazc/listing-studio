// A shop's listing counts as Overview shows them (the shop card and the "All shops"
// rows). A count that was not read from Etsy is never shown as 0: it says
// "reading…" while a read is under way and "not read yet" otherwise, with when the
// counts were last read. (The shop card used to count the rows of the six-hour
// listing copy, so a shop whose copy had expired, or was still being read just
// after connecting, showed "Listings 0, Active 0, Drafts 0".)
//
// Pure: app/(app)/dashboard/page.tsx draws it; lib/shopCounts.test.ts checks it.

import type { ShopSummary } from "./types";

export const READING = "reading…";
export const NOT_READ = "not read yet";

/** One count as shown: the number once Etsy's counts are read, else words. null: still loading. */
export function shownCount(s: ShopSummary | null | undefined, n: number): number | string | null {
  if (!s) return null;
  if (s.shop_counts_known) return n;
  return s.syncing ? READING : NOT_READ;
}

/** "3 hours ago" (format.ts's relativeTime, without its imports, so this stays testable). */
export function ago(iso: string, now: number = Date.now()): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.round((now - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

/** The line under a shop's counts: when they were read, or why none are shown. */
export function countsNote(s: ShopSummary | null | undefined, now: number = Date.now()): string | null {
  if (!s) return null;
  const read = s.counts_read_at ? ago(s.counts_read_at, now) : null;
  if (s.shop_counts_known) return read ? `Read from Etsy ${read}.` : null;
  if (s.syncing) return "Reading this shop's listings from Etsy…";
  return read
    ? `Last read from Etsy ${read}, too long ago to show; they are read again shortly.`
    : "Not read from Etsy yet; they are read shortly.";
}

/** Look again while a read is under way: the counts turn into numbers without a reload. */
export const shouldPoll = (s: ShopSummary | null | undefined): boolean => Boolean(s && !s.shop_counts_known && s.syncing);
