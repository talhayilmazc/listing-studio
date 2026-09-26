/** Presentation helpers for the Analytics screens (v7 §C). No API or state logic. */

import type { AnalyticsRow, ListingClass } from "./types";

/** Minor units in the shop's currency: 4700 → "$47.00", -3100 → "-$31.00". */
export function money(minor: number | null | undefined, currency: string | null | undefined): string {
  if (minor === null || minor === undefined) return "—";
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: currency || "USD" }).format(minor / 100);
  } catch {
    return `${(minor / 100).toFixed(2)} ${currency ?? ""}`.trim();
  }
}

/** Big figures compact: 1284000 → "$12.8K". */
export function moneyCompact(minor: number, currency: string | null | undefined): string {
  if (Math.abs(minor) < 1_000_000) return money(minor, currency);
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: currency || "USD",
      notation: "compact",
      maximumFractionDigits: 1,
    }).format(minor / 100);
  } catch {
    return money(minor, currency);
  }
}

export function percent(ratio: number | null | undefined, digits = 0): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** "+12%" / "−8%" against the previous period; "new" when there was nothing before. */
export function changeLabel(change: number | null, current: number): string {
  if (change === null) return current ? "new" : "—";
  const pct = Math.round(change * 100);
  if (pct === 0) return "±0%";
  return `${pct > 0 ? "+" : "−"}${Math.abs(pct)}%`;
}

/** Whether a change reads as good news (for its colour, always paired with the sign). */
export function changeTone(change: number | null, upIsGood = true): "up" | "down" | "flat" {
  if (change === null || Math.round(change * 100) === 0) return "flat";
  return change > 0 === upIsGood ? "up" : "down";
}

export const CLASS_LABEL: Record<ListingClass, string> = {
  ad_sink: "Ad sink",
  fading: "Fading",
  loser: "Loser",
  winner: "Winner",
  steady: "Steady",
  new: "New",
};

/** Badge colours: status-style, and always shown with the label. */
export const CLASS_STYLE: Record<ListingClass, string> = {
  ad_sink: "bg-rose-50 text-rose-800 ring-rose-200",
  fading: "bg-amber-50 text-amber-800 ring-amber-200",
  loser: "bg-slate-100 text-slate-700 ring-slate-300",
  winner: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  steady: "bg-white text-slate-600 ring-slate-200",
  new: "bg-brand-50 text-brand-700 ring-brand-100",
};

/** Most in need of attention first — the order the table opens in. */
export const CLASS_ORDER: ListingClass[] = ["ad_sink", "fading", "loser", "winner", "steady", "new"];

export type SortKey = "attention" | "revenue" | "net" | "margin" | "units" | "ad_spend" | "acos" | "change";

function sortValue(row: AnalyticsRow, key: SortKey): number {
  const c = row.current;
  switch (key) {
    case "revenue":
      return c.revenue;
    case "net":
      return c.net;
    case "margin":
      return c.margin ?? -Infinity;
    case "units":
      return c.units;
    case "ad_spend":
      return c.ad_spend;
    case "acos":
      return c.acos ?? -Infinity;
    case "change":
      return row.revenue_change ?? -Infinity;
    default:
      return 0;
  }
}

/**
 * Filter by class and text (title, SKU, profile or id), then sort. "attention"
 * keeps the server's order, which already puts what to look at first on top.
 */
export function filterRows(
  rows: AnalyticsRow[],
  opts: { classes?: ListingClass[]; q?: string; sort?: SortKey; desc?: boolean },
): AnalyticsRow[] {
  const words = (opts.q ?? "").toLowerCase().split(/\s+/).filter(Boolean);
  const keep = rows.filter((r) => {
    if (opts.classes && opts.classes.length && !opts.classes.includes(r.verdict.klass)) return false;
    if (!words.length) return true;
    const hay = `${r.title ?? ""} ${r.sku ?? ""} ${r.profile_name ?? ""} ${r.listing_id}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
  const sort = opts.sort ?? "attention";
  if (sort === "attention") return opts.desc === false ? [...keep].reverse() : keep;
  const dir = opts.desc === false ? 1 : -1;
  return [...keep].sort((a, b) => {
    const d = sortValue(a, sort) - sortValue(b, sort);
    return d !== 0 ? d * dir : a.listing_id - b.listing_id;
  });
}

/** A listing's name while its content may be shown, else its id. */
export function listingName(row: { listing_id: number; title: string | null }): string {
  return row.title ?? `Listing ${row.listing_id}`;
}
