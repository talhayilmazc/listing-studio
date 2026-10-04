"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { change, money, moneyCompact, percent } from "@/lib/analytics";
import type { AnalyticsSummary, BreakdownMetric, Figure, LineKey, Totals } from "@/lib/types";
import { Chart, LINE_COLORS, type ChartPoint } from "./Chart";
import { Drilldown, LedgerTypes } from "./Drilldown";
import { Txt } from "@/components/Txt";
import { type CompareMode, ComparisonLine, Delta, ExportLink, ListingCell, SourceTag } from "./Shared";

const LINES: [LineKey, string][] = [
  ["transaction_fees", "Transaction fees"],
  ["processing_fees", "Payment processing"],
  ["listing_fees", "Listing fees"],
  ["ads", "Ad spend"],
  ["shipping", "Shipping"],
  ["product", "Product cost"],
  ["fixed", "Fixed costs"],
];

function shortDay(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
}

/**
 * The shop's income statement for the period against the comparison: every
 * line says where it comes from, a line with nothing behind it is blank with
 * the reason beside it, and every total opens what it is made of.
 */
export function Overview({
  data,
  shopId,
  compare,
  onTab,
}: {
  data: AnalyticsSummary;
  shopId: string | null;
  compare: CompareMode;
  onTab: (t: "ads" | "costs" | "listings") => void;
}) {
  const [open, setOpen] = useState<BreakdownMetric | null>(null);
  const cur = data.totals;
  const prev = data.compared ?? undefined;
  const ccy = data.data.currency ?? null;
  const days = data.period?.days ?? 30;
  if (!cur) return null;

  if (cur.revenue.value === null) {
    return (
      <div className="card p-6 text-sm text-slate-600">
        No sales have been read yet, so there is nothing to total. The figures appear here as your sales are read (above);
        until then they are left blank rather than shown as zero.
      </div>
    );
  }

  const series = data.series;
  const cmpSeries = series?.comparison ?? null;
  const points: ChartPoint[] = (series?.current ?? []).map((p, i, all) => ({
    key: p.day,
    label: shortDay(p.day),
    tick: i % Math.max(1, Math.ceil(all.length / 6)) === 0 ? shortDay(p.day) : undefined,
    bar: p.revenue,
    lines: cmpSeries ? [p.avg7, cmpSeries[i]?.avg7 ?? null] : [p.avg7],
  }));
  const chartLines = [
    { label: "7-day average", color: LINE_COLORS.current },
    ...(cmpSeries ? [{ label: compare === "year" ? "7-day average, a year earlier" : "7-day average, previous period", color: LINE_COLORS.comparison, dashed: true }] : []),
  ];
  const first = series?.current[0];
  const last = series?.current[series.current.length - 1];
  const conc = data.concentration;
  const cohorts = [...(data.cohorts ?? [])].sort((a, b) => Number(a.key === "zz") - Number(b.key === "zz"));

  return (
    <div className="space-y-6">
      <section className="card p-5">
        <ComparisonLine comparison={data.comparison} />
        <div className="mt-3 grid gap-5 sm:grid-cols-2 lg:grid-cols-[1.5fr_1fr_1fr_1fr_1fr]">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.08em] text-slate-500">Net profit</p>
            <button
              type="button"
              translate="no"
              onClick={() => setOpen("net")}
              title="What this is made of"
              className={`mt-1 block text-left text-4xl font-semibold underline decoration-slate-200 decoration-dotted underline-offset-8 hover:decoration-slate-400 ${(cur.net.value ?? 0) < 0 ? "text-rose-700" : "text-slate-900"}`}
            >
              {moneyCompact(cur.net.value, ccy)}
            </button>
            <p className="mt-1.5 text-xs text-slate-500">
              <Delta change={change(cur.net.value, prev?.net.value)} current={cur.net.value} />
              {prev && <span key="was" translate="no">{` · was ${money(prev.net.value, ccy)}`}</span>}
            </p>
            {cur.net.note && (
              <p key="excl" className="mt-1 text-xs text-amber-800">
                <span>{cur.net.note}</span> <span>So the real profit is lower than this.</span>
              </p>
            )}
          </div>
          <Tile label="Revenue" value={moneyCompact(cur.revenue.value, ccy)} onOpen={() => setOpen("revenue")}
            delta={<Delta change={change(cur.revenue.value, prev?.revenue.value)} current={cur.revenue.value} />} />
          <Tile label="Margin" value={percent(cur.margin)}
            delta={<span className="text-slate-500" translate="no">{prev ? `was ${percent(prev.margin)}` : ""}</span>} />
          <Tile label="Items sold" value={(cur.units ?? 0).toLocaleString()} onOpen={() => setOpen("units")}
            delta={<Delta change={change(cur.units, prev?.units)} current={cur.units} />} />
          <Tile label="Average order line" value={money(cur.aov, ccy)}
            delta={<span className="text-slate-500" translate="no">{prev ? `was ${money(prev.aov, ccy)}` : ""}</span>} />
        </div>
      </section>

      <section className="card p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold text-slate-800">Income statement</h2>
          <p className="text-xs text-slate-500">Click any amount to see what it is made of.</p>
        </div>
        <Statement cur={cur} prev={prev} currency={ccy} onOpen={setOpen} onTab={onTab} />
        {cur.notes && cur.notes.length > 0 && (
          <ul key="notes" className="mt-3 list-disc space-y-0.5 pl-4 text-xs text-slate-500">
            {cur.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="card p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-sm font-semibold text-slate-800">Revenue trend</h2>
          <ExportLink href={api.analyticsExportUrl("daily", shopId, days, compare)} />
        </div>
        <p className="mb-3 mt-1 text-xs text-slate-500">
          <span>Read the line, not the bars: single days jump around, the 7-day average shows where sales are heading.</span>
          {first && last && (
            <span key="move" translate="no">{` It went from ${money(first.avg7, ccy)} a day to ${money(last.avg7, ccy)} a day over the period.`}</span>
          )}
        </p>
        <Chart points={points} barLabel="Revenue that day" lines={chartLines} currency={ccy} />
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {conc && (
          <section key="conc" className="card min-w-0 p-5">
            <h2 className="text-sm font-semibold text-slate-800">Concentration</h2>
            {conc.top_share === null ? (
              <p className="mt-2 text-sm text-slate-400">No revenue in this period.</p>
            ) : (
              <>
                <p className="mt-2 text-sm text-slate-700">
                  <span>Your top <span translate="no">{Math.min(conc.top_n, conc.top.length)}</span> listings bring in </span>
                  <span translate="no" className="text-xl font-semibold tabular-nums text-slate-900">{percent(conc.top_share)}</span>
                  <span> of revenue.</span>
                </p>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100" aria-hidden>
                  <div className="h-full rounded-full bg-slate-700" style={{ width: `${Math.min(100, conc.top_share * 100)}%` }} />
                </div>
                <p className="mt-2 text-xs text-slate-500">
                  <span translate="no">{`${(conc.listings_for_80pct ?? 0).toLocaleString()} of ${conc.selling_listings.toLocaleString()} selling listings make 80% of revenue.`}</span>
                  <span>{conc.top_share >= 0.6 ? " A problem with any one of these moves the whole shop." : ""}</span>
                </p>
                <table className="mt-3 w-full table-fixed text-sm">
                  <tbody className="divide-y divide-slate-100">
                    {conc.top.map((r) => (
                      <tr key={r.listing_id}>
                        <td className="py-1.5 pr-2">
                          <ListingCell row={r} days={days} />
                        </td>
                        <td translate="no" className="w-24 py-1.5 text-right tabular-nums">{money(r.revenue, ccy)}</td>
                        <td translate="no" className="w-12 py-1.5 text-right text-xs tabular-nums text-slate-500">
                          {percent(conc.total ? r.revenue / conc.total : null)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </section>
        )}

        <section className="card min-w-0 p-5">
          <h2 className="text-sm font-semibold text-slate-800">By launch date</h2>
          <p className="mt-1 text-xs text-slate-500">
            A listing live for a month and one live for two years aren&apos;t comparable on revenue. &quot;First 90 days&quot;
            compares them at the same age; it is blank where a listing&apos;s first 90 days fall outside the sales read.
          </p>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[26rem] text-sm">
              <thead className="text-left text-xs text-slate-400">
                <tr>
                  <th className="pb-1 font-medium">Launched</th>
                  <th className="pb-1 pl-3 text-right font-medium">Listings</th>
                  <th className="pb-1 pl-3 text-right font-medium">Selling</th>
                  <th className="pb-1 pl-3 text-right font-medium">Revenue</th>
                  <th className="pb-1 pl-3 text-right font-medium">Per listing</th>
                  <th className="pb-1 pl-3 text-right font-medium">First 90 days, per listing</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100" translate="no">
                {cohorts.map((c) => (
                  <tr key={c.key}>
                    <td className="py-1.5 text-slate-700">{c.label}</td>
                    <td className="whitespace-nowrap py-1.5 pl-3 text-right tabular-nums">{c.listings.toLocaleString()}</td>
                    <td className="whitespace-nowrap py-1.5 pl-3 text-right tabular-nums text-slate-500">{percent(c.listings ? c.selling / c.listings : null)}</td>
                    <td className="whitespace-nowrap py-1.5 pl-3 text-right tabular-nums">{money(c.revenue, ccy)}</td>
                    <td className="whitespace-nowrap py-1.5 pl-3 text-right tabular-nums">{money(c.revenue_per_listing, ccy)}</td>
                    <td className="whitespace-nowrap py-1.5 pl-3 text-right tabular-nums" title={c.first90_listings ? `${c.first90_listings} listings` : "Outside the sales read"}>
                      {money(c.first90_per_listing, ccy)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      {cur.ledger_types.length > 0 && (
        <section key="ledger" className="card p-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold text-slate-800">What Etsy&apos;s ledger charged</h2>
            <ExportLink href={api.analyticsExportUrl("ledger", shopId, days, compare)} />
          </div>
          <p className="mb-2 mt-1 text-xs text-slate-500">
            Every entry type in the period. Only the types known to be fees or ads are counted as costs; the rest (payouts,
            sales, refunds and anything not recognised) are listed so nothing is hidden, and left out of the totals.
          </p>
          <LedgerTypes types={cur.ledger_types} currency={ccy} />
        </section>
      )}

      {open && <Drilldown key="drill" metric={open} shopId={shopId} days={days} currency={ccy} onClose={() => setOpen(null)} />}
    </div>
  );
}

function Tile({ label, value, delta, onOpen }: { label: string; value: string; delta: React.ReactNode; onOpen?: () => void }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-[0.08em] text-slate-500">{label}</p>
      {onOpen ? (
        <button
          type="button"
          translate="no"
          onClick={onOpen}
          title="What this is made of"
          className="mt-1 block text-left text-xl font-semibold text-slate-900 underline decoration-slate-200 decoration-dotted underline-offset-4 hover:decoration-slate-400"
        >
          {value}
        </button>
      ) : (
        <p translate="no" className="mt-1 text-xl font-semibold text-slate-900">{value}</p>
      )}
      <p className="mt-0.5 text-xs">{delta}</p>
    </div>
  );
}

function Cell({ figure, currency, onOpen, minus }: { figure: Figure | undefined; currency: string | null; onOpen?: () => void; minus?: boolean }) {
  if (!figure || figure.value === null) return <span className="text-slate-300">blank</span>;
  const text = minus && figure.value ? `−${money(figure.value, currency)}` : money(figure.value, currency);
  if (!onOpen) return <span translate="no" className="whitespace-nowrap tabular-nums">{text}</span>;
  return (
    <button
      type="button"
      translate="no"
      onClick={onOpen}
      title="What this is made of"
      className="whitespace-nowrap tabular-nums underline decoration-slate-300 decoration-dotted underline-offset-4 hover:text-brand-700 hover:decoration-brand-500"
    >
      {text}
    </button>
  );
}

function Statement({
  cur,
  prev,
  currency,
  onOpen,
  onTab,
}: {
  cur: Totals;
  prev: Totals | undefined;
  currency: string | null;
  onOpen: (m: BreakdownMetric) => void;
  onTab: (t: "ads" | "costs" | "listings") => void;
}) {
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-slate-400">
          <tr>
            <th className="pb-1 font-medium" />
            <th className="pb-1 text-right font-medium">This period</th>
            <th className="hidden pb-1 text-right font-medium sm:table-cell">Compared with</th>
            <th className="w-16 pb-1 text-right font-medium">Change</th>
            <th className="hidden w-44 pb-1 pl-4 font-medium sm:table-cell">From</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          <tr>
            <td className="py-2 text-slate-900">
              Revenue
              <span className="block text-xs text-slate-500">
                <span translate="no">{`${(cur.units ?? 0).toLocaleString()} items in ${(cur.orders ?? 0).toLocaleString()} order lines. `}</span>
                <span>{cur.revenue.note}</span>
              </span>
            </td>
            <td className="py-2 text-right align-top text-slate-900"><Cell figure={cur.revenue} currency={currency} onOpen={() => onOpen("revenue")} /></td>
            <td className="hidden py-2 text-right align-top text-slate-500 sm:table-cell"><Cell figure={prev?.revenue} currency={currency} /></td>
            <td className="py-2 text-right align-top text-xs"><Delta change={change(cur.revenue.value, prev?.revenue.value)} current={cur.revenue.value} /></td>
            <td className="hidden py-2 pl-4 align-top sm:table-cell"><SourceTag source={cur.revenue.source} /></td>
          </tr>
          {LINES.map(([key, label]) => {
            const f = cur.lines[key];
            const p = prev?.lines[key];
            const blank = f.value === null;
            return (
              <tr key={key}>
                <td className="py-2 text-slate-700">
                  <span>− <span>{label}</span></span>
                  <span className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-slate-500 sm:hidden">
                    <SourceTag source={f.source} />
                    <Txt>{p && p.value !== null ? `was ${money(p.value, currency)}` : ""}</Txt>
                  </span>
                  {f.note && (
                    <span key="note" className={`block text-xs ${blank ? "text-amber-800" : "text-slate-500"}`}>
                      <span>{f.note}</span>
                      {blank && key === "product" && (
                        <button key="costs" type="button" className="ml-1 text-brand-700 underline" onClick={() => onTab("costs")}>Enter costs</button>
                      )}
                      {blank && key === "ads" && (
                        <button key="ads" type="button" className="ml-1 text-brand-700 underline" onClick={() => onTab("ads")}>Import from Etsy</button>
                      )}
                    </span>
                  )}
                  {key === "ads" && cur.ads_unattributed ? (
                    <span key="unattr" className="block text-xs text-slate-500">
                      <span translate="no">{`${money(cur.ads_unattributed, currency)} of it isn't attributed to a listing: `}</span>
                      <span>Etsy&apos;s ledger is shop-wide; per-listing spend comes from the Ads report.</span>
                      <button type="button" className="ml-1 text-brand-700 underline" onClick={() => onTab("ads")}>Import from Etsy</button>
                    </span>
                  ) : null}
                </td>
                <td className="py-2 text-right align-top text-slate-800">
                  <Cell figure={f} currency={currency} minus onOpen={key === "fixed" ? undefined : () => onOpen(key as BreakdownMetric)} />
                </td>
                <td className="hidden py-2 text-right align-top text-slate-500 sm:table-cell"><Cell figure={p} currency={currency} minus /></td>
                <td className="py-2 text-right align-top text-xs"><Delta change={change(f.value, p?.value)} current={f.value} upIsGood={false} /></td>
                <td className="hidden py-2 pl-4 align-top sm:table-cell"><SourceTag source={f.source} /></td>
              </tr>
            );
          })}
          <tr className="font-semibold">
            <td className="py-2.5 text-slate-900">
              <span>Net profit</span>
              <span className="ml-1 text-xs font-normal text-slate-500" translate="no">{`${percent(cur.margin)} margin`}</span>
              {cur.net.note && <span key="note" className="block text-xs font-normal text-amber-800">{cur.net.note}</span>}
            </td>
            <td className={`py-2.5 text-right align-top ${(cur.net.value ?? 0) < 0 ? "text-rose-700" : "text-slate-900"}`}>
              <Cell figure={cur.net} currency={currency} onOpen={() => onOpen("net")} />
            </td>
            <td className="hidden py-2.5 text-right align-top font-normal text-slate-500 sm:table-cell"><Cell figure={prev?.net} currency={currency} /></td>
            <td className="py-2.5 text-right align-top text-xs font-normal"><Delta change={change(cur.net.value, prev?.net.value)} current={cur.net.value} /></td>
            <td className="hidden py-2.5 pl-4 align-top font-normal sm:table-cell"><SourceTag source={cur.net.source} /></td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
