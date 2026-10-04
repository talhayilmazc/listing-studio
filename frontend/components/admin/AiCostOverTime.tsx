"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import {
  METRICS, NO_SELLER, PERIODS, axisLabels, axisTicks, csvName, figure, layers, sellerColor, sellerName, seriesCsv, share,
  signed, stamp, usd, usdAxis,
  type Metric, type PeriodId,
} from "@/lib/aiSeries";
import type { AiCell, AiSeries } from "@/lib/types";

import { Txt } from "@/components/Txt";
/**
 * AI cost over time, per seller. Admin only, like every AI cost figure.
 *
 * One row of filters (period, seller) scopes everything under it: the summary,
 * the chart, both tables and the export are the same response. Buckets are
 * Istanbul time; the daily view also carries the UTC day, which is the day the
 * provider's console counts.
 */

const PLOT_H = 208;

type Option = AiSeries["options"][number];

export function AiCostOverTime({ refreshKey, onError }: { refreshKey: string | undefined; onError: (m: string) => void }) {
  const [period, setPeriod] = useState<PeriodId>("daily");
  const [seller, setSeller] = useState("all");
  const [series, setSeries] = useState<AiSeries | null>(null);
  const [loading, setLoading] = useState(true);
  // Everyone the filter has offered so far: a seller picked in one period stays
  // pickable in another where they have no calls.
  const [known, setKnown] = useState<Option[]>([]);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api.admin
      .aiSeries(period, seller)
      .then((s) => {
        if (!live) return;
        setSeries(s);
        setKnown((before) => {
          const ids = new Set(s.options.map((o) => o.id));
          return [...s.options, ...before.filter((o) => !ids.has(o.id))];
        });
      })
      .catch((e) => live && onError(String(e.message ?? e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period, seller, refreshKey]);

  const options = useMemo(
    () => [...known].sort((a, b) => Number(a.id === NO_SELLER) - Number(b.id === NO_SELLER) || sellerName(a).localeCompare(sellerName(b))),
    [known],
  );

  function download() {
    if (!series) return;
    const url = URL.createObjectURL(new Blob(["﻿" + seriesCsv(series)], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = csvName(series);
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return (
    <section className="card p-5" translate="no" aria-labelledby="ai-cost-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="ai-cost-title" className="font-display text-2xl text-slate-900">AI cost</h2>
        <p className="text-xs text-slate-500">
          <span>Every call to the AI provider · Istanbul time (UTC+3) · sellers never see this</span>
        </p>
      </div>

      <div className="mt-4 flex flex-wrap items-end gap-x-4 gap-y-3">
        <div role="radiogroup" aria-label="Period" className="flex flex-wrap gap-1.5">
          {PERIODS.map((p) => (
            <button
              key={p.id}
              type="button"
              role="radio"
              aria-checked={period === p.id}
              onClick={() => setPeriod(p.id)}
              className={
                "rounded-full border px-3 py-1.5 text-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 max-sm:min-h-[2.75rem] " +
                (period === p.id
                  ? "border-brand-600 bg-brand-50 font-medium text-brand-800"
                  : "border-slate-300 bg-white text-slate-600 hover:border-slate-400 hover:text-slate-900")
              }
            >
              {p.label}
            </button>
          ))}
        </div>
        <label className="block min-w-0 max-sm:w-full">
          <span className="sr-only">Seller</span>
          <select className="field sm:w-64" value={seller} onChange={(e) => setSeller(e.target.value)}>
            <option value="all">All sellers</option>
            {options.map((o) => (
              <option key={o.id} value={o.id}>{sellerName(o)}</option>
            ))}
          </select>
        </label>
        <button type="button" className="btn-secondary sm:ml-auto" onClick={download} disabled={!series}>
          Export CSV
        </button>
      </div>

      {!series ? (
        <p key="loading" className="mt-6 text-sm text-slate-500">Loading AI cost…</p>
      ) : (
        <div key="body" className={"transition-opacity " + (loading ? "opacity-60" : "")} aria-busy={loading}>
          <Summary series={series} />
          <StackedCost series={series} />
          <BySeller series={series} />
          <ByPeriod series={series} />
        </div>
      )}
    </section>
  );
}

function hourly(series: AiSeries): boolean {
  return series.period === "24h" || series.period === "48h";
}

function Summary({ series }: { series: AiSeries }) {
  const { total, previous, change } = series;
  const versus = (percent: string | null) => {
    const text = signed(percent);
    return text === null ? null : `${text} vs previous period`;
  };
  const range = hourly(series)
    ? `${stamp(series.previous_start)} to ${stamp(series.previous_end)}`
    : `${stamp(series.previous_start, false)} to ${stamp(series.previous_end, false)}`;
  let headline = signed(change.cost) ?? "—";
  let why = `Was ${usd(previous.cost_usd)} in the same span before: ${range}`;
  if (!series.previous_covered) {
    headline = "—";
    why = series.records_from
      ? `Records start on ${stamp(series.records_from, false)}, so the previous period (${range}) is not fully recorded`
      : "No calls are recorded yet";
  } else if (previous.calls === 0) {
    why = `No calls in the previous period (${range})`;
  }
  return (
    <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4">
      <Stat
        label="Total cost"
        value={usd(total.cost_usd)}
        flag={total.unpriced}
        note={`${plural(total.calls, "call")} · ${total.failed.toLocaleString("en-US")} failed`}
      />
      <Stat label="Listings written" value={total.listings.toLocaleString("en-US")} note={versus(change.listings) ?? "Listings the AI wrote in this period"} />
      <Stat
        label="Average cost per listing"
        value={usd(total.cost_per_listing_usd, 4)}
        note={versus(change.cost_per_listing) ?? (total.listings ? "All calls, divided by listings written" : "No listings written in this period")}
      />
      <Stat label="Cost vs previous period" value={headline} note={why} />
    </dl>
  );
}

function Stat({ label, value, note, flag }: { label: string; value: string; note: string; flag?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 font-display text-3xl leading-none tabular-nums text-slate-900">
        <span>{value}</span>
        {flag && <span key="flag" className="ml-1 align-top text-sm text-amber-700" title="A model has no price set: this is a floor">+</span>}
      </dd>
      <p className="mt-1 text-[11px] text-slate-500">{note}</p>
    </div>
  );
}

/**
 * Cost per bucket, stacked by seller. Colour says who (fixed per account, with
 * a legend); height says how much, on one dollar axis. Each column is a hover,
 * focus and touch target that reads out every seller in it; the same figures
 * are in the tables below.
 */
function StackedCost({ series }: { series: AiSeries }) {
  const [hover, setHover] = useState<number | null>(null);
  const stack = useMemo(() => layers(series), [series]);
  const n = series.buckets.length;
  useEffect(() => setHover(null), [series.period, series.seller]);

  const heights = series.buckets.map((_, i) => stack.reduce((sum, l) => sum + l.values[i], 0));
  const ticks = axisTicks(Math.max(0, ...heights));
  const top = ticks[ticks.length - 1] || 1;
  const labels = useMemo(() => axisLabels(series), [series]);
  const at = hover !== null && hover < n ? hover : null;

  const scrub = (e: React.PointerEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    if (box.width <= 0) return;
    setHover(Math.min(n - 1, Math.max(0, Math.floor(((e.clientX - box.left) / box.width) * n))));
  };

  const what = PERIODS.find((p) => p.id === series.period)!.buckets;

  return (
    <div className="mt-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-sm font-medium text-slate-900">
          <span><span>Cost per <span>{what}</span>, by seller</span></span>
        </h3>
        <p className="text-xs text-slate-500">
          <span><span><span>{stamp(series.start, hourly(series))}</span> to now · the last <span>{what}</span> is still running</span></span>
        </p>
      </div>
      {stack.length === 0 ? (
        <p key="none" className="mt-3 rounded-lg border border-slate-100 bg-stone-50 px-3 py-6 text-center text-sm text-slate-500">
          No AI calls in this period.
        </p>
      ) : (
        <div key="chart">
          <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600" aria-label="Sellers">
            {stack.map((l) => (
              <li key={l.key} className="flex min-w-0 items-center gap-1.5">
                <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: l.color }} aria-hidden />
                <span className="max-w-[14rem] truncate">{l.name}</span>
              </li>
            ))}
          </ul>
          <div className="relative mt-3 flex gap-2" style={{ height: PLOT_H }}>
            <div className="relative w-12 shrink-0 text-right text-[11px] tabular-nums text-slate-500">
              {ticks.map((v) => (
                <span key={v} className="absolute right-0 translate-y-1/2" style={{ bottom: (v / top) * PLOT_H }}>
                  {usdAxis(v, ticks[1] ?? 1)}
                </span>
              ))}
            </div>
            <div className="relative min-w-0 flex-1">
              {ticks.map((v) => (
                <div key={v} className={"absolute inset-x-0 border-t " + (v === 0 ? "border-slate-300" : "border-slate-100")} style={{ bottom: (v / top) * PLOT_H }} aria-hidden />
              ))}
              {/* Bars are a few pixels wide at 48 buckets: a touch anywhere on the
                  plot picks the column under it, and dragging scrubs along. */}
              <div
                className="absolute inset-0 flex touch-pan-y"
                onPointerDown={(e) => e.pointerType !== "mouse" && scrub(e)}
                onPointerMove={(e) => e.pointerType !== "mouse" && scrub(e)}
              >
                {series.buckets.map((b, i) => {
                  let below = 0;
                  const parts = stack.map((l) => ({ l, v: l.values[i] })).filter((p) => p.v > 0);
                  return (
                    <button
                      key={b.start}
                      type="button"
                      className={"relative h-full min-w-0 flex-1 outline-none focus-visible:bg-brand-500/10 " + (at === i ? "bg-slate-900/[0.05]" : "")}
                      onPointerEnter={(e) => e.pointerType === "mouse" && setHover(i)}
                      onPointerLeave={(e) => e.pointerType === "mouse" && setHover(null)}
                      onFocus={() => setHover(i)}
                      onBlur={() => setHover(null)}
                      aria-label={`${b.title}: ${usd(b.total.cost_usd, 4)}${parts.map((p) => `, ${p.l.name} ${usd(p.v, 4)}`).join("")}`}
                    >
                      {/* Never wider than 24px, never touching its neighbour. */}
                      <span className="absolute inset-y-0 left-1/2 block -translate-x-1/2" style={{ width: "min(24px, calc(100% - 2px))" }} aria-hidden>
                        {parts.map((p, j) => {
                          const bottom = (below / top) * PLOT_H;
                          below += p.v;
                          const full = (p.v / top) * PLOT_H;
                          const last = j === parts.length - 1;
                          // A 2px gap of the surface separates the layers: no outline.
                          const height = Math.max(1, last || full < 4 ? full : full - 2);
                          return (
                            <span
                              key={p.l.key}
                              className="absolute inset-x-0 block"
                              style={{ bottom, height, background: p.l.color, borderRadius: last ? "4px 4px 0 0" : undefined, opacity: at === null || at === i ? 1 : 0.55 }}
                            />
                          );
                        })}
                      </span>
                    </button>
                  );
                })}
              </div>
              {at !== null && <Readout key="readout" series={series} index={at} stack={stack} floating />}
            </div>
          </div>
          <div className="ml-14 mt-1 flex text-[11px] text-slate-500" aria-hidden>
            {series.buckets.map((b, i) => (
              <span key={b.start} className="relative h-4 min-w-0 flex-1">
                {labels.wide[i] ? (
                  <span
                    className={
                      "absolute whitespace-nowrap " +
                      (i === n - 1 ? "right-0" : i === 0 ? "left-0" : "left-1/2 -translate-x-1/2") +
                      (labels.narrow[i] ? "" : " max-sm:hidden")
                    }
                  >
                    {b.label}
                  </span>
                ) : null}
              </span>
            ))}
          </div>
          <div className="mt-2 sm:hidden">
            {at !== null ? (
              <Readout key="readout" series={series} index={at} stack={stack} />
            ) : (
              <p key="hint" className="rounded-md border border-slate-100 px-2.5 py-2 text-xs text-slate-500">
                <span>Touch the chart and slide to read each <span>{what}</span>.</span>
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Readout({ series, index, stack, floating }: { series: AiSeries; index: number; stack: ReturnType<typeof layers>; floating?: boolean }) {
  const b = series.buckets[index];
  const n = series.buckets.length;
  const center = ((index + 0.5) / n) * 100;
  const flip = index >= n / 2;
  const rows = stack.map((l) => ({ l, v: l.values[index] })).filter((r) => r.v > 0);
  return (
    <div
      className={
        "rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs " +
        // Beside the column where there is room for it; on a phone it sits under the chart instead.
        (floating
          ? "pointer-events-none absolute top-0 z-10 w-max max-w-[17rem] shadow-card max-sm:hidden " +
            (flip ? "[transform:translateX(calc(-100%_-_10px))]" : "[transform:translateX(10px)]")
          : "")
      }
      style={floating ? { left: `${center}%` } : undefined}
      role={floating ? undefined : "status"}
    >
      <p className="font-medium text-slate-700">
        <span>{b.title}</span>
        {b.partial && <span key="partial" className="font-normal text-slate-500"> · so far</span>}
      </p>
      <p className="mt-0.5 flex items-baseline justify-between gap-4">
        <span className="text-slate-500">Total</span>
        <span className="font-semibold tabular-nums text-slate-900">{usd(b.total.cost_usd, 4)}</span>
      </p>
      {rows.map((r) => (
        <p key={r.l.key} className="flex items-center justify-between gap-4">
          <span className="flex min-w-0 items-center gap-1.5 text-slate-500">
            <span className="inline-block h-0.5 w-3 shrink-0" style={{ background: r.l.color }} aria-hidden />
            <span className="truncate">{r.l.name}</span>
          </span>
          <span className="tabular-nums text-slate-900">{usd(r.v, 4)}</span>
        </p>
      ))}
      <p className="mt-1 border-t border-slate-100 pt-1 text-slate-500">
        <span>{`${plural(b.total.listings, "listing")} · ${plural(b.total.calls, "call")} · ${b.total.failed.toLocaleString("en-US")} failed`}</span>
      </p>
      {b.utc && (
        <p key="utc" className="text-slate-500">
          <span><span><span>UTC day </span><Txt>{b.utc_day}</Txt><span>: </span><span>{usd(b.utc.cost_usd, 4)}</span></span></span>
        </p>
      )}
    </div>
  );
}

const plural = (n: number, word: string) => `${n.toLocaleString("en-US")} ${word}${n === 1 ? "" : "s"}`;

const TH = "whitespace-nowrap px-3 py-1.5 text-right font-medium";
const TD = "px-3 py-1.5 text-right tabular-nums";

function Swatch({ color }: { color: string }) {
  return <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: color }} aria-hidden />;
}

/** Each seller's total for the period, and their share of what every seller cost. */
function BySeller({ series }: { series: AiSeries }) {
  const rows = [...series.sellers].sort((a, b) => Number(b.total.cost_usd) - Number(a.total.cost_usd));
  if (rows.length === 0) return null;
  const filtered = series.seller !== "all";
  const shown = Number(series.all_sellers.cost_usd) > 0 ? (Number(series.total.cost_usd) / Number(series.all_sellers.cost_usd)).toFixed(4) : null;
  return (
    <div className="mt-6">
      <h3 className="text-sm font-medium text-slate-900">Sellers in this period</h3>
      <p className="mt-0.5 text-xs text-slate-500">
        <span>
          <span><span>Share is of all sellers&apos; cost in the period (</span><span>{usd(series.all_sellers.cost_usd, 4)}</span><span>)</span><Txt>{filtered ? ", whatever the seller filter" : ""}</Txt><span>.</span></span>
        </span>
      </p>
      <div className="mt-2 overflow-x-auto">
        <table className="text-left text-xs max-sm:w-max sm:w-full sm:min-w-[40rem]">
          <thead>
            <tr className="border-b border-slate-200 text-slate-500">
              <th className="py-1.5 pr-3 font-medium">Seller</th>
              <th className={TH}>Cost</th>
              <th className={TH}>Share of cost</th>
              <th className={TH}>Listings</th>
              <th className={TH}>Cost per listing</th>
              <th className={TH}>Calls</th>
              <th className="whitespace-nowrap py-1.5 pl-3 text-right font-medium">Failures</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id} className="border-b border-slate-100">
                <td className="py-1.5 pr-3 text-slate-800">
                  <span className="flex min-w-0 items-center gap-2">
                    <Swatch color={sellerColor(s)} />
                    <span className="max-w-[8.5rem] truncate sm:max-w-[16rem]" title={sellerName(s)}>{sellerName(s)}</span>
                  </span>
                </td>
                <td className={TD + " font-medium text-slate-900"}>{figure(s.total, "cost").text}</td>
                <td className={TD}>
                  <span className="inline-flex items-center justify-end gap-2">
                    <span className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100" aria-hidden>
                      <span className="block h-full rounded-full" style={{ width: `${Number(s.share ?? 0) * 100}%`, background: sellerColor(s) }} />
                    </span>
                    <span className="w-12 text-slate-900">{share(s.share)}</span>
                  </span>
                </td>
                <td className={TD}>{figure(s.total, "listings").text}</td>
                <td className={TD}>{figure(s.total, "per_listing").text}</td>
                <td className={TD}>{figure(s.total, "calls").text}</td>
                <td className="py-1.5 pl-3 text-right tabular-nums">{figure(s.total, "failed").text}</td>
              </tr>
            ))}
          </tbody>
          <tfoot className={rows.length === 1 ? "hidden" : undefined}>
            <tr className="border-t border-slate-300 font-medium text-slate-900">
              <td className="py-1.5 pr-3">{filtered ? "Total shown" : "All sellers"}</td>
              <td className={TD}>{figure(series.total, "cost").text}</td>
              <td className={TD}>{share(shown)}</td>
              <td className={TD}>{figure(series.total, "listings").text}</td>
              <td className={TD}>{figure(series.total, "per_listing").text}</td>
              <td className={TD}>{figure(series.total, "calls").text}</td>
              <td className="py-1.5 pl-3 text-right tabular-nums">{figure(series.total, "failed").text}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

function Figure({ cell, metric, strong }: { cell: AiCell; metric: Metric; strong?: boolean }) {
  const f = figure(cell, metric);
  return <span className={f.zero ? "text-slate-300" : strong ? "font-medium text-slate-900" : "text-slate-700"}>{f.text}</span>;
}

/** Seller × period: one figure at a time, with a total for every row and column. */
function ByPeriod({ series }: { series: AiSeries }) {
  const [metric, setMetric] = useState<Metric>("cost");
  // Columns in the chart's order: by account, not by who spent most.
  const sellers = [...series.sellers].sort(
    (a, b) => Number(a.id === NO_SELLER) - Number(b.id === NO_SELLER) || (a.slot ?? 99) - (b.slot ?? 99) || sellerName(a).localeCompare(sellerName(b)),
  );
  if (sellers.length === 0) return null;
  const daily = series.period === "daily";
  // One seller on screen: their column is the total, so it is not shown twice.
  const single = sellers.length === 1;
  const label = METRICS.find((m) => m.id === metric)!.label.toLowerCase();
  return (
    <div className="mt-6">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h3 className="text-sm font-medium text-slate-900">
          <span><span>Seller by period: <span>{label}</span></span></span>
        </h3>
        <div role="radiogroup" aria-label="Figure shown in the table" className="flex flex-wrap gap-1">
          {METRICS.map((m) => (
            <button
              key={m.id}
              type="button"
              role="radio"
              aria-checked={metric === m.id}
              onClick={() => setMetric(m.id)}
              className={
                "rounded-md px-2.5 py-1 text-xs transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 max-sm:min-h-[2.75rem] max-sm:px-3 " +
                (metric === m.id ? "bg-slate-900 font-medium text-white" : "text-slate-600 hover:bg-slate-100 hover:text-slate-900")
              }
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      {daily && (
        <p key="utc-note" className="mt-1 text-xs text-slate-500">
          Days are Istanbul days. &quot;UTC day&quot; is the same date as the provider&apos;s console counts it (03:00 to 03:00 Istanbul time):
          compare a finished UTC day with the console.
        </p>
      )}
      <div className="mt-2 max-h-[28rem] overflow-auto rounded-lg border border-slate-100">
        <table className="w-full border-separate border-spacing-0 text-left text-xs">
          <thead className="text-slate-500">
            <tr>
              <th className="sticky left-0 top-0 z-20 border-b border-slate-200 bg-slate-50 px-3 py-1.5 font-medium">Period</th>
              {!single && (
                <th key="total" className={"sticky top-0 z-10 whitespace-nowrap border-b border-slate-200 bg-slate-50 text-slate-700 " + TH}>
                  {series.seller === "all" ? "All sellers" : "Total"}
                </th>
              )}
              {daily && <th key="utc" className={"sticky top-0 z-10 whitespace-nowrap border-b border-x border-slate-200 bg-slate-50 " + TH}>UTC day</th>}
              {sellers.map((s) => (
                <th key={s.id} className={"sticky top-0 z-10 border-b border-slate-200 bg-slate-50 " + TH} title={sellerName(s)}>
                  <span className="inline-flex max-w-[10rem] items-center justify-end gap-1.5">
                    <Swatch color={sellerColor(s)} />
                    <span className="truncate">{sellerName(s)}</span>
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {series.buckets
              .map((b, i) => ({ b, i }))
              .reverse()
              .map(({ b, i }) => (
                <tr key={b.start}>
                  <th scope="row" className="sticky left-0 z-10 whitespace-nowrap border-b border-slate-100 bg-white px-3 py-1.5 text-left font-normal text-slate-700">
                    <span>{b.title}</span>
                    {b.partial && <span key="partial" className="text-slate-400"> · so far</span>}
                  </th>
                  {!single && (
                    <td key="total" className={"border-b border-slate-100 " + TD}>
                      <Figure cell={b.total} metric={metric} strong />
                    </td>
                  )}
                  {b.utc && (
                    <td key="utc" className={"border-b border-x border-slate-100 " + TD} title={`UTC day ${b.utc_day}`}>
                      <Figure cell={b.utc} metric={metric} />
                    </td>
                  )}
                  {sellers.map((s) => (
                    <td key={s.id} className={"border-b border-slate-100 " + TD}>
                      <Figure cell={s.cells[i]} metric={metric} />
                    </td>
                  ))}
                </tr>
              ))}
          </tbody>
          <tfoot>
            <tr className="font-medium">
              <th scope="row" className="sticky bottom-0 left-0 z-20 border-t border-slate-300 bg-slate-50 px-3 py-1.5 text-left font-medium text-slate-900">Total</th>
              {!single && (
                <td key="total" className={"sticky bottom-0 z-10 border-t border-slate-300 bg-slate-50 " + TD}>
                  <Figure cell={series.total} metric={metric} strong />
                </td>
              )}
              {daily && <td key="utc" className="sticky bottom-0 z-10 border-x border-t border-slate-300 border-x-slate-100 bg-slate-50" />}
              {sellers.map((s) => (
                <td key={s.id} className={"sticky bottom-0 z-10 border-t border-slate-300 bg-slate-50 " + TD}>
                  <Figure cell={s.total} metric={metric} strong />
                </td>
              ))}
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}
