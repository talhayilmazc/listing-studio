import type { AiCell, AiSeries, AiSeriesSeller } from "./types";

/**
 * AI cost over time (admin only): the pieces the chart, the tables and the CSV
 * export share, so all three always say the same thing.
 */

export const PERIODS = [
  { id: "24h", label: "Last 24 hours", buckets: "hour" },
  { id: "48h", label: "Last 48 hours", buckets: "hour" },
  { id: "daily", label: "Daily", buckets: "day" },
  { id: "weekly", label: "Weekly", buckets: "week" },
  { id: "monthly", label: "Monthly", buckets: "month" },
] as const;
export type PeriodId = (typeof PERIODS)[number]["id"];

/**
 * One hue per seller, in a fixed order, validated as a set (colour-blind
 * separation, contrast on the card). The server gives each account a slot that
 * never changes with the period or the filter; there is no ninth hue.
 */
export const SELLER_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"] as const;
/** Sellers past the eighth, drawn together. */
export const OTHER_COLOR = "#898781";
/** Calls with no seller: our own runs and deleted accounts. */
export const NO_SELLER_COLOR = "#52514e";
export const NO_SELLER = "none";

export function sellerName(s: { id: string; email: string | null }): string {
  return s.id === NO_SELLER ? "No seller (our own runs)" : (s.email ?? "Deleted account");
}

export function sellerColor(s: { id: string; slot: number | null }): string {
  if (s.id === NO_SELLER) return NO_SELLER_COLOR;
  return s.slot === null ? OTHER_COLOR : (SELLER_COLORS[s.slot] ?? OTHER_COLOR);
}

export interface Layer {
  key: string;
  name: string;
  color: string;
  /** Cost per bucket, in dollars. */
  values: number[];
}

/**
 * The chart's layers, bottom to top: each seller with a colour of their own in
 * slot order, then every other seller together, then calls with no seller. The
 * order belongs to the accounts, not to who spent most this period.
 */
export function layers(series: AiSeries): Layer[] {
  const cost = (s: AiSeriesSeller) => s.cells.map((c) => Number(c.cost_usd));
  const own = series.sellers
    .filter((s) => s.id !== NO_SELLER && s.slot !== null)
    .sort((a, b) => (a.slot ?? 0) - (b.slot ?? 0))
    .map((s) => ({ key: s.id, name: sellerName(s), color: sellerColor(s), values: cost(s) }));
  const rest = series.sellers.filter((s) => s.id !== NO_SELLER && s.slot === null);
  const out: Layer[] = [...own];
  if (rest.length > 0) {
    out.push({
      key: "other",
      name: rest.length === 1 ? sellerName(rest[0]) : `${rest.length} other sellers`,
      color: OTHER_COLOR,
      values: series.buckets.map((_, i) => rest.reduce((sum, s) => sum + Number(s.cells[i].cost_usd), 0)),
    });
  }
  const none = series.sellers.find((s) => s.id === NO_SELLER);
  if (none) out.push({ key: NO_SELLER, name: sellerName(none), color: NO_SELLER_COLOR, values: cost(none) });
  return out;
}

/** Clean axis steps: 0 and up to four round steps reaching past the largest value. */
export function axisTicks(max: number): number[] {
  if (!(max > 0)) return [0];
  const raw = max / 3;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw * 0.999999) ?? raw;
  const out: number[] = [];
  for (let i = 0; i * step < max - step * 1e-9; i++) out.push(i * step);
  out.push(out.length * step);
  return out.map((v) => Number(v.toPrecision(12)));
}

/** Dollars with as many decimals as the amount needs to be read. */
export function usd(value: string | number | null | undefined, digits?: number): string {
  if (value === null || value === undefined) return "—";
  const v = Number(value);
  const d = digits ?? (v !== 0 && Math.abs(v) < 1 ? 4 : 2);
  return `$${v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
}

/** An axis label: no more decimals than the step between ticks needs. */
export function usdAxis(value: number, step: number): string {
  if (value === 0) return "$0";
  const needed = (step.toFixed(8).replace(/0+$/, "").split(".")[1] ?? "").length;
  return `$${value.toFixed(needed === 0 ? 0 : Math.max(2, needed))}`;
}

/** "+12.3%" / "-4.0%"; null when there is nothing to compare with. */
export function signed(percent: string | null): string | null {
  if (percent === null) return null;
  const v = Number(percent);
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}%`;
}

/** "0.4219" -> "42.2%" */
export function share(value: string | null): string {
  return value === null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * An instant as the server gave it (Istanbul time with its offset), read off
 * the text so the browser's own zone never comes into it: "3 Oct 16:30".
 */
export function stamp(iso: string, time = true): string {
  const day = `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1]}`;
  return time ? `${day} ${iso.slice(11, 16)}` : `${day} ${iso.slice(0, 4)}`;
}

/** Which buckets carry an axis label, at the roomy and at the phone density. */
export function axisLabels(series: AiSeries): { wide: boolean[]; narrow: boolean[] } {
  const n = series.buckets.length;
  const hourly = series.period === "24h" || series.period === "48h";
  const every = { "24h": [3, 6], "48h": [6, 12], daily: [5, 10], weekly: [2, 4], monthly: [1, 3] }[series.period];
  const pick = (step: number) =>
    series.buckets.map((b, i) => (hourly ? Number(b.start.slice(11, 13)) % step === 0 : (n - 1 - i) % step === 0));
  return { wide: pick(every[0]), narrow: pick(every[1]) };
}

export type Metric = "cost" | "listings" | "per_listing" | "calls" | "failed";
export const METRICS: { id: Metric; label: string }[] = [
  { id: "cost", label: "Cost" },
  { id: "listings", label: "Listings" },
  { id: "per_listing", label: "Cost per listing" },
  { id: "calls", label: "Calls" },
  { id: "failed", label: "Failures" },
];

/** One figure of a cell as the table shows it; `zero` says it is nothing (drawn muted). */
export function figure(cell: AiCell, metric: Metric): { text: string; zero: boolean } {
  if (metric === "cost") return { text: usd(cell.cost_usd, 4) + (cell.unpriced ? "+" : ""), zero: Number(cell.cost_usd) === 0 && !cell.unpriced };
  if (metric === "per_listing") return { text: usd(cell.cost_per_listing_usd, 4), zero: cell.cost_per_listing_usd === null };
  const v = metric === "listings" ? cell.listings : metric === "calls" ? cell.calls : cell.failed;
  return { text: v.toLocaleString("en-US"), zero: v === 0 };
}

/** A spreadsheet must never run a cell: text that starts like a formula is quoted as text. */
function field(value: string | number | null): string {
  if (value === null) return "";
  let text = String(value);
  if (typeof value === "string" && /^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

const CSV_HEAD = [
  "period", "starts_istanbul", "ends_istanbul", "seller", "cost_usd", "listings", "cost_per_listing_usd", "calls", "failures",
  "share_of_all_sellers_cost", "utc_day", "utc_day_cost_usd", "utc_day_calls",
];

/**
 * What is on screen, as rows: every seller in every bucket, each bucket's total,
 * each seller's total for the period with their share, the grand total, and the
 * previous period it is compared with. Times are Istanbul's; in the daily view
 * the bucket totals also carry the UTC day, for the provider's console.
 */
export function seriesCsv(series: AiSeries): string {
  const local = (iso: string) => `${iso.slice(0, 10)} ${iso.slice(11, 16)}`;
  const figures = (c: AiCell) => [c.cost_usd, c.listings, c.cost_per_listing_usd, c.calls, c.failed];
  const rows: (string | number | null)[][] = [];
  const all = "All sellers shown";
  series.buckets.forEach((b, i) => {
    for (const s of series.sellers) {
      rows.push([b.title, local(b.start), local(b.end), sellerName(s), ...figures(s.cells[i]), null, null, null, null]);
    }
    rows.push([b.title, local(b.start), local(b.end), all, ...figures(b.total), null, b.utc_day, b.utc?.cost_usd ?? null, b.utc?.calls ?? null]);
  });
  const whole = [local(series.start), local(series.end)];
  for (const s of series.sellers) rows.push(["Total", ...whole, sellerName(s), ...figures(s.total), s.share, null, null, null]);
  const shown = Number(series.all_sellers.cost_usd) > 0 ? (Number(series.total.cost_usd) / Number(series.all_sellers.cost_usd)).toFixed(4) : null;
  rows.push(["Total", ...whole, all, ...figures(series.total), shown, null, null, null]);
  rows.push([
    series.previous_covered ? "Previous period" : "Previous period (not fully recorded)",
    local(series.previous_start), local(series.previous_end), all, ...figures(series.previous), null, null, null, null,
  ]);
  return [CSV_HEAD, ...rows].map((r) => r.map(field).join(",")).join("\r\n") + "\r\n";
}

export function csvName(series: AiSeries): string {
  const who = series.seller === "all" ? "all-sellers" : series.seller === NO_SELLER ? "no-seller" : "one-seller";
  const day = new Intl.DateTimeFormat("en-CA", { timeZone: series.time_zone }).format(new Date(series.as_of));
  return `ai-cost-${series.period}-${who}-${day}.csv`;
}
