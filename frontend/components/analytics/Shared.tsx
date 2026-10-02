"use client";

import Link from "next/link";
import { AUTH_START_URL } from "@/lib/api";
import { CLASS_LABEL, CLASS_STYLE, SOURCE_LABEL, TREND_LABEL, changeLabel, changeTone, money } from "@/lib/analytics";
import type { Comparison, DataStatus, Figure, ListingClass, Trend } from "@/lib/types";
import { SalesPanel } from "./SalesPanel";

import { Txt } from "@/components/Txt";
export const PERIODS = [7, 30, 90] as const;
export type CompareMode = "previous" | "year";

export function ClassBadge({ klass }: { klass: ListingClass }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${CLASS_STYLE[klass]}`}>
      {CLASS_LABEL[klass]}
    </span>
  );
}

const TREND_MARK: Record<Trend, string> = {
  rising: "↗",
  falling: "↘",
  steady: "→",
  turned_up: "⤴",
  turned_down: "⤵",
  too_few: "·",
};

/** Direction of the 4-week average; a change of direction stands out, a level doesn't. */
export function TrendBadge({ trend }: { trend: Trend | null }) {
  if (!trend) return <span className="text-slate-400">—</span>;
  const turned = trend === "turned_up" || trend === "turned_down";
  const cls = trend === "turned_down" ? "bg-amber-50 text-amber-800 ring-1 ring-inset ring-amber-200"
    : trend === "turned_up" ? "bg-emerald-50 text-emerald-800 ring-1 ring-inset ring-emerald-200"
    : trend === "too_few" ? "text-slate-400" : "text-slate-600";
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap text-xs ${turned ? "rounded-full px-2 py-0.5 font-medium" : ""} ${cls}`}>
      <span aria-hidden>{TREND_MARK[trend]}</span>
      <span>{TREND_LABEL[trend]}</span>
    </span>
  );
}

/** A change against the comparison: the sign carries it, colour only supports it. */
export function Delta({ change, current, upIsGood = true }: { change: number | null; current: number | null | undefined; upIsGood?: boolean }) {
  const tone = changeTone(change, upIsGood);
  const cls = tone === "up" ? "text-emerald-700" : tone === "down" ? "text-rose-700" : "text-slate-500";
  return <span translate="no" className={`tabular-nums ${cls}`}>{changeLabel(change, current)}</span>;
}

/** Where a figure comes from. An estimate looks different from a charged amount. */
export function SourceTag({ source }: { source: string | null | undefined }) {
  if (!source || source === "none") return null;
  const estimate = source === "rates";
  const cls = estimate ? "border-dashed border-slate-300 text-slate-500" : "border-slate-200 text-slate-500";
  return (
    <span className={`inline-block whitespace-nowrap rounded border px-1.5 py-px text-[11px] ${cls}`}>
      {SOURCE_LABEL[source] ?? source}
    </span>
  );
}

/** A money figure, or a blank when nothing is behind it (never a zero). */
export function Amount({ figure, currency, negative }: { figure: Pick<Figure, "value">; currency: string | null; negative?: boolean }) {
  if (figure.value === null || figure.value === undefined) {
    return <span className="text-slate-400" title="Not available">—</span>;
  }
  return <span translate="no" className="tabular-nums">{negative && figure.value ? `−${money(figure.value, currency)}` : money(figure.value, currency)}</span>;
}

export function PeriodPicker({ days, onChange }: { days: number; onChange: (d: number) => void }) {
  return (
    <div className="inline-flex rounded-lg border border-slate-300 bg-white p-0.5 max-sm:flex max-sm:w-full" role="group" aria-label="Period">
      {PERIODS.map((d) => (
        <button
          key={d}
          type="button"
          onClick={() => onChange(d)}
          aria-pressed={days === d}
          className={
            "rounded-md px-3 py-1 text-sm max-sm:min-h-[2.5rem] max-sm:flex-1 max-sm:px-2 " +
            (days === d ? "bg-brand-600 font-medium text-white" : "text-slate-600 hover:bg-slate-100")
          }
        >
          <span>Last <span>{d}</span> days</span>
        </button>
      ))}
    </div>
  );
}

/** What the period is compared with; year-on-year only once the history reaches back. */
export function ComparePicker({
  mode,
  comparison,
  onChange,
}: {
  mode: CompareMode;
  comparison: Comparison | undefined;
  onChange: (m: CompareMode) => void;
}) {
  const yearOk = comparison?.year_available !== false;
  const opts: [CompareMode, string][] = [["previous", "vs previous period"], ["year", "vs a year earlier"]];
  return (
    <div className="inline-flex rounded-lg border border-slate-300 bg-white p-0.5 max-sm:flex max-sm:w-full" role="group" aria-label="Compare with">
      {opts.map(([m, label]) => (
        <button
          key={m}
          type="button"
          onClick={() => onChange(m)}
          aria-pressed={mode === m}
          disabled={m === "year" && !yearOk && mode !== "year"}
          title={m === "year" && !yearOk ? "Needs 13 months of sales history, which this shop doesn't have yet." : undefined}
          className={
            "rounded-md px-3 py-1 text-sm disabled:cursor-not-allowed disabled:text-slate-300 max-sm:min-h-[2.5rem] max-sm:flex-1 max-sm:px-2 " +
            (mode === m ? "bg-slate-800 font-medium text-white" : "text-slate-600 hover:bg-slate-100")
          }
        >
          {label}
        </button>
      ))}
    </div>
  );
}

/** The comparison stated in words, or why it can't be made. */
export function ComparisonLine({ comparison }: { comparison: Comparison | undefined }) {
  if (!comparison) return null;
  return (
    <p className="text-xs text-slate-500">
      <span className="font-medium text-slate-700">{comparison.label}</span>
      <Txt>{comparison.start && comparison.end ? ` (${comparison.start} to ${comparison.end})` : ""}</Txt>
      {comparison.unavailable && <span key="why" className="text-amber-800">{` — ${comparison.unavailable}`}</span>}
    </p>
  );
}

/** A CSV of what's on screen, for the seller's own spreadsheet. */
export function ExportLink({ href, label = "Export CSV" }: { href: string; label?: string }) {
  return (
    <a href={href} download className="tap inline-flex items-center gap-1 whitespace-nowrap text-xs font-medium text-brand-700 hover:underline">
      <span aria-hidden>↓</span>
      <span>{label}</span>
    </a>
  );
}

/**
 * Where the figures come from: the connect or reconnect prompt, or the reads
 * themselves (sales and Etsy's ledger: cost, progress and freshness; SalesPanel).
 */
export function StatusBar({
  status,
  shopId,
  onProgress,
}: {
  status: DataStatus;
  shopId: string | null;
  onProgress: () => void;
}) {
  if (!status.connected) {
    return (
      <div className="card p-4 text-sm text-slate-600">
        Connect your Etsy shop to see its sales and profit.{" "}
        <Link href="/connect" className="font-medium text-brand-700 underline">
          Connect a shop
        </Link>
      </div>
    );
  }
  if (!status.can_read_sales) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        <p>
          Reconnect this shop once to let the Service read its sales and Etsy&apos;s fee ledger. Only which listing sold,
          how many, the price and the date are read, plus the shop&apos;s daily fee and ad-spend totals; they are kept as
          daily totals for 13 months, and nothing about your buyers is ever stored.
        </p>
        <a href={AUTH_START_URL} className="mt-2 inline-block font-medium underline">
          Reconnect
        </a>
      </div>
    );
  }
  const counts = status.listing_counts;
  return (
    <div className="space-y-1">
      <SalesPanel shopId={shopId} onProgress={onProgress} />
      {status.titles_refreshing && (
        <p key="titles" className="text-xs text-slate-500">
          Listing titles are being refreshed from Etsy; until then listings show by number.
        </p>
      )}
      {counts && counts.truncated === true && (
        <p key="truncated" className="text-xs text-amber-800">
          This shop has more listings than one refresh reads; up to 5,000 of each state are shown.
        </p>
      )}
    </div>
  );
}

/** A listing's thumbnail and name, linking to its analytics, with the Etsy back link. */
export function ListingCell({
  row,
  days,
}: {
  row: { listing_id: number; title: string | null; url: string; thumbnail_url: string | null; sku?: string | null; state?: string | null };
  days: number;
}) {
  const state = row.state && row.state !== "active" ? row.state.replace("_", " ") : "";
  return (
    <div className="flex min-w-0 items-center gap-2">
      {row.thumbnail_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={row.thumbnail_url} alt="" className="h-9 w-9 shrink-0 rounded object-cover" loading="lazy" />
      ) : (
        <span className="h-9 w-9 shrink-0 rounded bg-slate-100" aria-hidden />
      )}
      <div className="min-w-0">
        <Link
          href={`/analytics/${row.listing_id}?days=${days}`}
          className="block truncate text-slate-800 hover:text-brand-700 hover:underline max-sm:py-2.5"
          title={row.title ?? undefined}
        >
          {row.title ?? `Listing ${row.listing_id}`}
        </Link>
        <span className="block truncate text-xs text-slate-400">
          <Txt>{row.sku ? `${row.sku} · ` : ""}</Txt>
          <Txt>{state ? `${state} · ` : ""}</Txt>
          <a href={row.url} target="_blank" rel="noreferrer" className="tap hover:text-brand-700 hover:underline">
            View on Etsy ↗
          </a>
        </span>
      </div>
    </div>
  );
}
