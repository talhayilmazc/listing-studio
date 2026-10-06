"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { StyleRate, StyleRateKey, StyleResult, TitleStyleComparison } from "@/lib/types";

/**
 * "Etsy recommended (short)" against "Long keyword" titles, for listings this shop
 * published with the app in the same period (Part D). Every rate carries its
 * sample size and a 95% interval; below the thresholds it says "not enough data
 * yet" and shows no rate. These are listing views on Etsy, not search
 * impressions. Nothing is rewritten from it: the seller decides.
 */

const STYLE_LABEL: Record<StyleResult["style"], string> = {
  short: "Etsy recommended (short)",
  long: "Long keyword",
};
const RATES: { key: StyleRateKey; label: string; percent: boolean }[] = [
  { key: "views_per_listing_day", label: "Views per listing per day", percent: false },
  { key: "favorites_per_view", label: "Favourites per view", percent: true },
  { key: "orders_per_view", label: "Orders per view", percent: true },
];
const PERIODS = [30, 90, 180, 365];

function fmt(v: number, percent: boolean): string {
  return percent ? `${(v * 100).toFixed(2)}%` : v.toFixed(2);
}

function Rate({ rate, percent }: { rate: StyleRate | null; percent: boolean }) {
  if (!rate) return <span className="text-slate-400">—</span>;
  return (
    <span translate="no" className="tabular-nums">
      <span className="font-medium text-slate-900">{fmt(rate.value, percent)}</span>
      <span className="block text-xs text-slate-500">{`95%: ${fmt(rate.low, percent)} – ${fmt(rate.high, percent)}`}</span>
    </span>
  );
}

export function TitleStyles({ shopId }: { shopId: string | null }) {
  const [days, setDays] = useState(90);
  const [data, setData] = useState<TitleStyleComparison | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setData(null);
    api
      .titleStyles(shopId, days)
      .then((d) => live && (setData(d), setError(null)))
      .catch((e) => live && setError(e instanceof Error ? e.message : "The comparison could not be loaded."));
    return () => {
      live = false;
    };
  }, [shopId, days]);

  return (
    <section className="card space-y-3 p-4" aria-labelledby="title-styles">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="title-styles" className="text-sm font-semibold text-slate-900">Title style comparison</h2>
        <label className="flex items-center gap-2 text-xs text-slate-500">
          <span>Published in the last</span>
          <select className="field py-1" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {PERIODS.map((d) => <option key={d} value={d}>{`${d} days`}</option>)}
          </select>
        </label>
      </div>
      <p className="text-xs text-slate-500">
        <span>
          Listings published with the app in the same period, by the title style they were written in. Views are listing
          views on Etsy, not search impressions. Nothing is changed on your listings from here.
        </span>
      </p>
      {error && <p key="error" className="text-sm text-rose-700">{error}</p>}
      {!data && !error && <p key="loading" className="text-sm text-slate-500">Loading…</p>}
      {data && data.connected && (
        <div key="table" className="overflow-x-auto">
          <table className="w-full min-w-[36rem] text-sm">
            <thead className="border-b border-slate-200 text-xs text-slate-500">
              <tr>
                <th scope="col" className="px-2 py-2 text-left font-medium">Style</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">Listings</th>
                <th scope="col" className="px-2 py-2 text-right font-medium">Views</th>
                {RATES.map((r) => <th key={r.key} scope="col" className="px-2 py-2 text-right font-medium">{r.label}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.styles.map((s) => (
                <tr key={s.style} className="align-top">
                  <td className="px-2 py-2">
                    <span className="block text-slate-800">{STYLE_LABEL[s.style]}</span>
                    {s.note && <span key="note" className="block text-xs text-amber-700" translate="no">{s.note}</span>}
                  </td>
                  <td className="px-2 py-2 text-right tabular-nums" translate="no">{s.listings}</td>
                  <td className="px-2 py-2 text-right tabular-nums" translate="no">{s.views.toLocaleString()}</td>
                  {RATES.map((r) => (
                    <td key={r.key} className="px-2 py-2 text-right"><Rate rate={s[r.key]} percent={r.percent} /></td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && data.connected && data.difference && (
        <ul key="difference" className="space-y-1 text-xs text-slate-600" translate="no">
          {RATES.map((r) => {
            const d = data.difference?.[r.key];
            if (!d) return null;
            return (
              <li key={r.key}>
                <span>{`${r.label}, short − long: ${fmt(d.value, r.percent)} (95%: ${fmt(d.low, r.percent)} – ${fmt(d.high, r.percent)}). `}</span>
                <span>{d.clear ? "The interval excludes zero." : "The interval includes zero: no clear difference."}</span>
              </li>
            );
          })}
        </ul>
      )}
      {data && data.connected && (
        <ul key="caveats" className="list-disc space-y-0.5 pl-4 text-xs text-slate-400">
          <li key="threshold">
            <span>{`A style's rates appear once it has ${data.thresholds.listings} listings and ${data.thresholds.views.toLocaleString()} views.`}</span>
          </li>
          {data.orders_note && <li key="orders"><span>{data.orders_note}</span></li>}
          {data.caveats.map((c) => <li key={c}><span>{c}</span></li>)}
        </ul>
      )}
    </section>
  );
}
