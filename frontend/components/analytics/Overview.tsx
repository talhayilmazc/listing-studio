"use client";

import { CLASS_LABEL, CLASS_ORDER, money, moneyCompact, percent } from "@/lib/analytics";
import type { AnalyticsOverview, AnalyticsRow, ListingClass, ShopTotals } from "@/lib/types";
import { ClassBadge, Delta, ListingCell } from "./Shared";

function change(cur: number, prev: number | undefined): number | null {
  if (!prev) return null;
  return (cur - prev) / Math.abs(prev);
}

/** Profit summary, period comparison, what to look at first, best and worst five. */
export function Overview({
  data,
  onClass,
  onTab,
}: {
  data: AnalyticsOverview;
  onClass: (k: ListingClass) => void;
  onTab: (t: "ads" | "costs") => void;
}) {
  const cur = data.current;
  const prev = data.previous ?? undefined;
  const ccy = data.status.currency;
  if (!cur) return null;

  if (!data.status.has_sales && data.status.can_read_sales) {
    return (
      <div className="card p-6 text-sm text-slate-600">
        No sales yet in the last 13 months, or they haven&apos;t been read. Use <b>Read sales now</b> above;
        the figures appear here when it finishes.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <section className="card p-5">
        <div className="grid gap-5 sm:grid-cols-[1.4fr_1fr_1fr_1fr]">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.08em] text-slate-500">Net profit</p>
            <p className={`mt-1 text-5xl font-semibold ${cur.net < 0 ? "text-rose-700" : "text-slate-900"}`}>
              {moneyCompact(cur.net, ccy)}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              <Delta change={change(cur.net, prev?.net)} current={cur.net} /> vs the previous {data.days} days (
              {money(prev?.net ?? 0, ccy)})
            </p>
          </div>
          <Tile label="Revenue" value={moneyCompact(cur.revenue, ccy)} delta={<Delta change={change(cur.revenue, prev?.revenue)} current={cur.revenue} />} />
          <Tile label="Costs" value={moneyCompact(cur.costs, ccy)} delta={<Delta change={change(cur.costs, prev?.costs)} current={cur.costs} upIsGood={false} />} />
          <Tile label="Margin" value={percent(cur.margin)} delta={<span className="text-slate-500">was {percent(prev?.margin)}</span>} />
          <Tile label="Items sold" value={cur.units.toLocaleString()} delta={<Delta change={change(cur.units, prev?.units)} current={cur.units} />} />
          <Tile label="Average order" value={money(cur.aov, ccy)} delta={<span className="text-slate-500">was {money(prev?.aov, ccy)}</span>} />
          <Tile
            label="Ad spend"
            value={money(cur.ad_spend, ccy)}
            delta={<Delta change={change(cur.ad_spend, prev?.ad_spend)} current={cur.ad_spend} upIsGood={false} />}
          />
          <Tile
            label="ACOS"
            value={percent(cur.acos)}
            delta={<span className="text-slate-500">ad spend ÷ ad revenue{cur.roas ? ` · ROAS ${cur.roas.toFixed(1)}×` : ""}</span>}
          />
        </div>
        <Breakdown totals={cur} currency={ccy} onCosts={() => onTab("costs")} />
        {!data.status.ads_until && (
          <p className="mt-3 text-xs text-slate-500">
            No Etsy Ads report uploaded, so ad spend counts as zero.{" "}
            <button type="button" className="text-brand-700 underline" onClick={() => onTab("ads")}>
              Upload one
            </button>
          </p>
        )}
      </section>

      <section>
        <div className="flex flex-wrap gap-2">
          {CLASS_ORDER.filter((k) => data.classes[k]).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => onClass(k)}
              className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm hover:border-slate-300"
              title={`Show the ${CLASS_LABEL[k]} listings`}
            >
              <ClassBadge klass={k} />
              <span className="tabular-nums font-medium text-slate-800">{data.classes[k]}</span>
            </button>
          ))}
        </div>
      </section>

      {data.attention.length > 0 && (
        <section className="card p-5">
          <h2 className="text-sm font-semibold text-slate-800">Look at these first</h2>
          <ul className="mt-3 divide-y divide-slate-100">
            {data.attention.map((r) => (
              <Advice key={r.listing_id} row={r} days={data.days} />
            ))}
          </ul>
        </section>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <FiveTable title="Most profitable" rows={data.best} currency={ccy} days={data.days} />
        <FiveTable title="Least profitable" rows={data.worst} currency={ccy} days={data.days} />
      </div>
    </div>
  );
}

function Tile({ label, value, delta }: { label: string; value: string; delta: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-[0.08em] text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-slate-900">{value}</p>
      <p className="mt-0.5 text-xs">{delta}</p>
    </div>
  );
}

function Breakdown({ totals, currency, onCosts }: { totals: ShopTotals; currency: string | null; onCosts: () => void }) {
  const parts: [string, number][] = [
    ["Etsy fees", totals.fees],
    ["Product cost", totals.product_cost],
    ["Shipping", totals.shipping_cost],
    ["Ads", totals.ad_spend],
    ["Fixed costs", totals.fixed_costs],
  ];
  return (
    <p className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-500">
      {parts.map(([k, v], i) => (
        <span key={k}>
          {i > 0 && " · "}
          {k} <span className="tabular-nums text-slate-700">{money(v, currency)}</span>
        </span>
      ))}{" "}
      ·{" "}
      <button type="button" className="text-brand-700 underline" onClick={onCosts}>
        Your fees and costs
      </button>
    </p>
  );
}

export function Advice({ row, days }: { row: AnalyticsRow; days: number }) {
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-2">
        <ClassBadge klass={row.verdict.klass} />
        <div className="min-w-0 flex-1">
          <ListingCell row={row} days={days} />
        </div>
      </div>
      <p className="mt-1.5 text-sm text-slate-700">
        {row.verdict.reason} <span className="font-medium">{row.verdict.action}</span>
      </p>
      <p className="mt-1 flex flex-wrap gap-3 text-xs">
        {row.verdict.links.map((l) => (
          <a key={l.url} href={l.url} target="_blank" rel="noreferrer" className="text-brand-700 hover:underline">
            {l.label} ↗
          </a>
        ))}
      </p>
    </li>
  );
}

function FiveTable({ title, rows, currency, days }: { title: string; rows: AnalyticsRow[]; currency: string | null; days: number }) {
  return (
    <section className="card p-5">
      <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
      {rows.length === 0 ? (
        <p className="mt-3 text-sm text-slate-400">Nothing in this period.</p>
      ) : (
        <table className="mt-3 w-full table-fixed text-sm">
          <thead className="text-left text-xs text-slate-400">
            <tr>
              <th className="pb-2 font-medium">Listing</th>
              <th className="w-14 pb-2 text-right font-medium">Sold</th>
              <th className="w-24 pb-2 text-right font-medium">Net</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((r) => (
              <tr key={r.listing_id}>
                <td className="py-2 pr-2">
                  <ListingCell row={r} days={days} />
                </td>
                <td className="py-2 text-right tabular-nums text-slate-600">{r.current.units}</td>
                <td className={`py-2 text-right tabular-nums ${r.current.net < 0 ? "text-rose-700" : "text-slate-800"}`}>
                  {money(r.current.net, currency)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
