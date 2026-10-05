/** Presentation helpers for the Analytics screens (v7 §C). No API or state logic. */

import type { ActionKind, ListingClass, ListingRow, Trend } from "./types";

/** Minor units in the shop's currency: 4700 → "$47.00", -3100 → "-$31.00"; null → "—". */
export function money(minor: number | null | undefined, currency: string | null | undefined): string {
  if (minor === null || minor === undefined) return "—";
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: currency || "USD" }).format(minor / 100);
  } catch {
    return `${(minor / 100).toFixed(2)} ${currency ?? ""}`.trim();
  }
}

export function percent(ratio: number | null | undefined, digits = 0): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return "—";
  return `${(ratio * 100).toFixed(digits)}%`;
}

/** Relative change; null when there was nothing to compare with. */
export function change(current: number | null | undefined, previous: number | null | undefined): number | null {
  if (current === null || current === undefined || !previous) return null;
  return (current - previous) / Math.abs(previous);
}

/** "+12%" / "−8%" against the comparison; "new" when there was nothing before. */
export function changeLabel(ratio: number | null, current: number | null | undefined): string {
  if (ratio === null) return current ? "new" : "—";
  const pct = Math.round(ratio * 100);
  if (pct === 0) return "±0%";
  return `${pct > 0 ? "+" : "−"}${Math.abs(pct)}%`;
}

/** Whether a change reads as good news (colour only supports the sign). */
export function changeTone(ratio: number | null, upIsGood = true): "up" | "down" | "flat" {
  if (ratio === null || Math.round(ratio * 100) === 0) return "flat";
  return ratio > 0 === upIsGood ? "up" : "down";
}

/** Where a figure comes from, in the seller's words. */
export const SOURCE_LABEL: Record<string, string> = {
  sales: "your sales",
  ledger: "Etsy's ledger",
  allocated: "Etsy's ledger, shared out",
  rates: "estimate from your rates",
  costs: "your costs",
  report: "your Ads report",
  computed: "calculated",
  none: "not available",
};

export const CLASS_LABEL: Record<ListingClass, string> = {
  ad_sink: "Ad sink",
  fading: "Fading",
  loser: "Loser",
  winner: "Winner",
  steady: "Steady",
  new: "New",
};

export const CLASS_STYLE: Record<ListingClass, string> = {
  ad_sink: "bg-rose-50 text-rose-800 ring-rose-200",
  fading: "bg-amber-50 text-amber-800 ring-amber-200",
  loser: "bg-slate-100 text-slate-700 ring-slate-300",
  winner: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  steady: "bg-white text-slate-600 ring-slate-200",
  new: "bg-brand-50 text-brand-700 ring-brand-100",
};

export const CLASS_ORDER: ListingClass[] = ["ad_sink", "fading", "loser", "winner", "steady", "new"];

export const ACTION_LABEL: Record<ActionKind, string> = {
  ad_sink: "Ads with no sales",
  ads_above_break_even: "Ads above break-even",
  selling_at_loss: "Selling at a loss",
  fading: "Fading",
  turned_down: "Turned down",
  room_to_advertise: "Room to advertise",
};

export const TREND_LABEL: Record<Trend, string> = {
  rising: "Rising",
  falling: "Falling",
  steady: "Steady",
  turned_up: "Turned up",
  turned_down: "Turned down",
  too_few: "Too few sales",
};

export type SortKey =
  | "stake" | "revenue" | "units" | "net" | "margin" | "net_per_unit" | "ads" | "acos" | "change" | "launched";

function sortValue(r: ListingRow, key: SortKey): number {
  const e = r.economics;
  switch (key) {
    case "stake":
      return r.action?.stake ?? -1;
    case "revenue":
      return e?.revenue ?? -Infinity;
    case "units":
      return e?.units ?? -Infinity;
    case "net":
      return e?.net ?? -Infinity;
    case "margin":
      return e?.margin ?? -Infinity;
    case "net_per_unit":
      return e?.net_per_unit ?? -Infinity;
    case "ads":
      return e?.ads ?? -Infinity;
    case "acos":
      return e?.acos ?? -Infinity;
    case "change":
      return change(e?.revenue, r.comparison?.revenue) ?? -Infinity;
    case "launched":
      return r.launched ? Date.parse(r.launched) : -Infinity;
  }
}

/** Filter by status and text (title, SKU, profile or number), then sort. */
export function filterRows(
  rows: ListingRow[],
  opts: { classes?: ListingClass[]; q?: string; sort?: SortKey; desc?: boolean },
): ListingRow[] {
  const words = (opts.q ?? "").toLowerCase().split(/\s+/).filter(Boolean);
  const keep = rows.filter((r) => {
    if (opts.classes && opts.classes.length && (!r.status || !opts.classes.includes(r.status))) return false;
    if (!words.length) return true;
    const hay = `${r.title ?? ""} ${r.sku ?? ""} ${r.profile_name ?? ""} ${r.listing_id}`.toLowerCase();
    return words.every((w) => hay.includes(w));
  });
  const sort = opts.sort ?? "stake";
  const dir = opts.desc === false ? 1 : -1;
  return [...keep].sort((a, b) => {
    const d = sortValue(a, sort) - sortValue(b, sort);
    return d !== 0 && Number.isFinite(d) ? d * dir : a.listing_id - b.listing_id;
  });
}

/** A listing's name while its content may be shown, else its number. */
export function listingName(row: { listing_id: number; title: string | null }): string {
  return row.title ?? `Listing ${row.listing_id}`;
}
