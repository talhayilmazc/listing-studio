"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { money } from "@/lib/analytics";
import { SOURCES } from "@/lib/analyticsDefinitions";
import type { AnalyticsShops, ShopMonthFigure } from "@/lib/types";
import { monthName } from "./Month";

/**
 * Analytics' "All shops": one month for every shop, a row per shop and a total per
 * currency. Each shop's figures are the ones its own report shows; a figure with
 * nothing behind it is blank, and so is a total that would leave a shop out (the
 * note says which). Picking a shop opens its full report.
 */
export function AllShops({ onPick }: { onPick: (shopId: string) => void }) {
  const [month, setMonth] = useState<string | null>(null);
  const [data, setData] = useState<AnalyticsShops | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    const call = ++latest.current;
    api
      .analyticsShops(month)
      .then((d) => call === latest.current && (setData(d), setError(null)))
      .catch((e) => call === latest.current && setError(e instanceof Error ? e.message : "The figures could not be loaded."));
  }, [month]);

  const figure = (f: ShopMonthFigure, currency: string | null) => (
    <span title={f.note ?? undefined}>{money(f.minor, currency)}</span>
  );

  if (error) return <div className="card p-3 text-sm text-rose-700">{error}</div>;
  if (!data) return <p className="text-sm text-slate-400">Loading…</p>;
  return (
    <section className="space-y-3" data-testid="analytics-all-shops">
      <label className="flex items-center gap-2 text-sm text-slate-600">
        <span>Month</span>
        <select className="field py-1.5" value={data.month.slice(0, 7)} onChange={(e) => setMonth(e.target.value)}>
          {data.months.map((m) => <option key={m} value={m.slice(0, 7)}>{monthName(m)}</option>)}
        </select>
      </label>
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs text-slate-500">
            <tr>
              <th className="px-3 py-2 font-medium">Shop</th>
              <th className="whitespace-nowrap px-3 py-2 font-medium">Source</th>
              <th className="whitespace-nowrap px-3 py-2 text-right font-medium">Revenue</th>
              <th className="whitespace-nowrap px-3 py-2 text-right font-medium">After Etsy fees and ads</th>
              <th className="whitespace-nowrap px-3 py-2 text-right font-medium">Profit</th>
              <th className="whitespace-nowrap px-3 py-2 text-right font-medium">Items sold</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data.shops.map((s) => (
              <tr key={s.id} data-testid="analytics-shop-row">
                <td className="max-w-[14rem] px-3 py-2">
                  <button type="button" className="tap block max-w-full truncate text-left font-medium text-brand-700 underline"
                    onClick={() => onPick(s.id)} title={`Open ${s.name}'s report`}>
                    <span translate="no">{s.name}</span>
                  </button>
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-xs text-slate-600" title={SOURCES[s.source].text}>{SOURCES[s.source].label}</td>
                <td translate="no" className="whitespace-nowrap px-3 py-2 text-right tabular-nums">{figure(s.revenue, s.currency)}</td>
                <td translate="no" className="whitespace-nowrap px-3 py-2 text-right tabular-nums">{figure(s.net_etsy, s.currency)}</td>
                <td translate="no" className="whitespace-nowrap px-3 py-2 text-right tabular-nums">{figure(s.profit, s.currency)}</td>
                <td translate="no" className="whitespace-nowrap px-3 py-2 text-right tabular-nums">{s.units ?? "—"}</td>
              </tr>
            ))}
          </tbody>
          <tfoot className="border-t border-slate-200 bg-slate-50 font-medium">
            {data.totals.map((t) => (
              <tr key={t.currency ?? "none"} data-testid="analytics-total">
                <td className="px-3 py-2" colSpan={2}>
                  <span translate="no">{`All shops${data.totals.length > 1 ? ` in ${t.currency ?? "unknown currency"}` : ""} (${t.shops})`}</span>
                </td>
                <td translate="no" className="whitespace-nowrap px-3 py-2 text-right tabular-nums">{figure(t.revenue, t.currency)}</td>
                <td translate="no" className="whitespace-nowrap px-3 py-2 text-right tabular-nums">{figure(t.net_etsy, t.currency)}</td>
                <td translate="no" className="whitespace-nowrap px-3 py-2 text-right tabular-nums">{figure(t.profit, t.currency)}</td>
                <td translate="no" className="whitespace-nowrap px-3 py-2 text-right tabular-nums">{t.units ?? "—"}</td>
              </tr>
            ))}
          </tfoot>
        </table>
      </div>
      <p className="text-xs text-slate-500">
        A blank figure has nothing behind it yet (hover it for why); a total with a blank shop in it is blank too, never a
        smaller number. Shops in different currencies are totalled apart. Pick a shop for its full report: listings,
        imports and product costs.
      </p>
    </section>
  );
}
