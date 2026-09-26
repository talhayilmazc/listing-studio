"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { listingName, money, percent } from "@/lib/analytics";
import type { AnalyticsDetail, Figures } from "@/lib/types";
import { useShops } from "@/components/ShopProvider";
import { Approximations } from "@/components/analytics/Overview";
import { ClassBadge, Delta, PeriodPicker } from "@/components/analytics/Shared";
import { WeeklyChart } from "@/components/analytics/WeeklyChart";

/** One listing: its weekly sales over 13 months, where its money goes, and what to do. */
export default function ListingAnalytics({
  params,
  searchParams,
}: {
  params: { listingId: string };
  searchParams: { days?: string };
}) {
  const listingId = Number(params.listingId);
  const { selected } = useShops();
  const shopId = selected?.id ?? null;
  const [days, setDays] = useState(() => ([7, 30, 90].includes(Number(searchParams.days)) ? Number(searchParams.days) : 30));
  const [data, setData] = useState<AnalyticsDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .analyticsListing(listingId, shopId, days)
      .then((d) => !cancelled && (setData(d), setError(null)))
      .catch((e) => !cancelled && setError(e instanceof Error && e.message !== "not found" ? e.message : "This listing has no figures in this shop."));
    return () => {
      cancelled = true;
    };
  }, [listingId, shopId, days]);

  const row = data?.listing;
  const ccy = data?.status.currency ?? null;
  const periodStart = new Date(Date.now() - (days - 1) * 86400000).toISOString().slice(0, 10);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/analytics" className="text-sm text-slate-500 hover:text-slate-900">
          ← Analytics
        </Link>
        <div className="sm:ml-auto">
          <PeriodPicker days={days} onChange={setDays} />
        </div>
      </div>
      {error && <div className="card p-4 text-sm text-slate-600">{error}</div>}
      {!data && !error && <p className="text-sm text-slate-400">Loading…</p>}

      {row && data && (
        <>
          <header className="flex items-start gap-4">
            {row.thumbnail_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={row.thumbnail_url} alt="" className="h-16 w-16 shrink-0 rounded-lg object-cover" />
            ) : (
              <span className="h-16 w-16 shrink-0 rounded-lg bg-slate-100" aria-hidden />
            )}
            <div className="min-w-0">
              <h1 className="text-lg font-semibold text-slate-900">{listingName(row)}</h1>
              <p className="mt-0.5 text-xs text-slate-500">
                {row.sku && `${row.sku} · `}
                {row.profile_name && `profile ${row.profile_name} · `}
                <a href={row.url} target="_blank" rel="noreferrer" className="text-brand-700 hover:underline">
                  View on Etsy ↗
                </a>
              </p>
            </div>
          </header>

          <section className="card p-5">
            <div className="flex items-center gap-2">
              <ClassBadge klass={row.verdict.klass} />
              <span className="text-xs text-slate-500">last {days} days</span>
            </div>
            <p className="mt-2 text-sm text-slate-700">{row.verdict.reason}</p>
            <p className="mt-1 text-sm font-medium text-slate-900">{row.verdict.action}</p>
            <p className="mt-2 flex flex-wrap gap-3 text-xs">
              {row.verdict.links.map((l) => (
                <a key={l.url} href={l.url} target="_blank" rel="noreferrer" className="text-brand-700 hover:underline">
                  {l.label} ↗
                </a>
              ))}
            </p>
          </section>

          <section className="card p-5">
            <h2 className="text-sm font-semibold text-slate-800">Weekly revenue, last 13 months</h2>
            <p className="mb-4 text-xs text-slate-500">The last {days} days in colour; the same weeks last year show whether it&apos;s seasonal.</p>
            <WeeklyChart weeks={data.weeks} currency={ccy} periodStart={periodStart} />
          </section>

          <div className="grid gap-5 lg:grid-cols-2">
            <section className="card p-5">
              <h2 className="text-sm font-semibold text-slate-800">Where the money goes</h2>
              <Breakdown cur={row.current} prev={row.previous} currency={ccy} unitCost={data.unit_cost} source={data.unit_cost_source} days={days} />
              <Approximations />
            </section>
            <section className="card p-5">
              <h2 className="text-sm font-semibold text-slate-800">Etsy Ads</h2>
              {data.ads.length === 0 ? (
                <p className="mt-2 text-sm text-slate-400">No ad spend uploaded for this listing.</p>
              ) : (
                <table className="mt-2 w-full text-sm">
                  <thead className="text-left text-xs text-slate-400">
                    <tr>
                      <th className="pb-1 font-medium">Period</th>
                      <th className="pb-1 text-right font-medium">Spend</th>
                      <th className="pb-1 text-right font-medium">Orders</th>
                      <th className="pb-1 text-right font-medium">Revenue</th>
                      <th className="pb-1 text-right font-medium">Views</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {data.ads.map((a) => (
                      <tr key={`${a.period_start}-${a.period_end}`}>
                        <td className="py-1.5 text-slate-700">
                          {a.period_start === a.period_end ? a.period_start : `${a.period_start} → ${a.period_end}`}
                        </td>
                        <td className="py-1.5 text-right tabular-nums">{money(a.spend, ccy)}</td>
                        <td className="py-1.5 text-right tabular-nums">{a.ad_orders}</td>
                        <td className="py-1.5 text-right tabular-nums">{money(a.ad_revenue, ccy)}</td>
                        <td className="py-1.5 text-right tabular-nums">{a.ad_views ? a.ad_views.toLocaleString() : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}

function Breakdown({
  cur,
  prev,
  currency,
  unitCost,
  source,
  days,
}: {
  cur: Figures;
  prev: Figures;
  currency: string | null;
  unitCost: string;
  source: string;
  days: number;
}) {
  const lines: [string, number, number, string?][] = [
    ["Transaction fee", cur.transaction_fee, prev.transaction_fee],
    ["Payment processing", cur.payment_fee, prev.payment_fee],
    ["Listing fees", cur.listing_fee, prev.listing_fee],
    ["Product cost", cur.product_cost, prev.product_cost, `${unitCost} per item (${source})`],
    ["Shipping", cur.shipping_cost, prev.shipping_cost],
    ["Ads", cur.ad_spend, prev.ad_spend],
  ];
  const change = (a: number, b: number) => (b ? (a - b) / Math.abs(b) : null);
  return (
    <table className="mt-2 w-full text-sm">
      <thead className="text-left text-xs text-slate-400">
        <tr>
          <th className="pb-1 font-medium" />
          <th className="pb-1 text-right font-medium">Last {days} days</th>
          <th className="pb-1 text-right font-medium">Before</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        <tr>
          <td className="py-1.5 text-slate-800">
            Revenue <span className="text-xs text-slate-400">({cur.units} sold, {cur.orders} order lines)</span>
          </td>
          <td className="py-1.5 text-right tabular-nums text-slate-900">{money(cur.revenue, currency)}</td>
          <td className="py-1.5 text-right tabular-nums text-slate-500">{money(prev.revenue, currency)}</td>
        </tr>
        {lines.map(([label, a, b, note]) => (
          <tr key={label}>
            <td className="py-1.5 text-slate-600">
              − {label} {note && <span className="block text-xs text-slate-400">{note}</span>}
            </td>
            <td className="py-1.5 text-right tabular-nums text-slate-700">{money(a, currency)}</td>
            <td className="py-1.5 text-right tabular-nums text-slate-500">{money(b, currency)}</td>
          </tr>
        ))}
        <tr className="font-semibold">
          <td className="py-2 text-slate-900">
            Net profit <span className="text-xs font-normal">({percent(cur.margin)} margin)</span>
          </td>
          <td className={`py-2 text-right tabular-nums ${cur.net < 0 ? "text-rose-700" : "text-slate-900"}`}>{money(cur.net, currency)}</td>
          <td className="py-2 text-right tabular-nums text-slate-500">{money(prev.net, currency)}</td>
        </tr>
        <tr>
          <td className="pt-1 text-xs text-slate-500" colSpan={3}>
            Net vs before: <Delta change={change(cur.net, prev.net)} current={cur.net} /> · ACOS {percent(cur.acos)} · average order{" "}
            {money(cur.aov, currency)}
          </td>
        </tr>
      </tbody>
    </table>
  );
}
