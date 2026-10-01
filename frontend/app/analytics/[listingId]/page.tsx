"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { TREND_LABEL, listingName, money } from "@/lib/analytics";
import type { AnalyticsDetail, Trend } from "@/lib/types";
import { useShops } from "@/components/ShopProvider";
import { Chart, LINE_COLORS, type ChartPoint } from "@/components/analytics/Chart";
import { PeriodPicker, TrendBadge } from "@/components/analytics/Shared";
import { ActionItem } from "@/components/analytics/Today";
import { UnitEconomics } from "@/components/analytics/UnitEconomics";

import { Txt } from "@/components/Txt";
const TREND_MEANS: Record<Trend, string> = {
  rising: "Its 4-week average has risen for two periods running.",
  falling: "Its 4-week average has fallen for two periods running.",
  steady: "Its 4-week average is holding.",
  turned_up: "It was falling and has turned up: the direction changed, not just the level.",
  turned_down: "It was rising and has turned down: the direction changed, not just the level.",
  too_few: "Too few sales in the last 12 weeks to call a direction.",
};

function day(iso: string): Date {
  return new Date(`${iso}T00:00:00Z`);
}

/** One listing: 13 months of weekly revenue, its unit economics against the period before, and what to do. */
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
  const ccy = data?.data.currency ?? null;
  const from = data ? day(data.period.start).getTime() : Infinity;
  const hasLastYear = data?.weeks.some((w) => w.last_year !== null) ?? false;
  const points: ChartPoint[] = (data?.weeks ?? []).map((w, i, all) => {
    const d = day(w.start);
    const firstOfMonth = i === 0 || d.getUTCMonth() !== day(all[i - 1].start).getUTCMonth();
    const label = d.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
    return {
      key: w.start,
      label: `Week of ${label} · ${w.units} sold`,
      tick: firstOfMonth && d.getUTCMonth() % 2 === 0 && i < all.length - 2 ? d.toLocaleDateString(undefined, { month: "short", timeZone: "UTC" }) : undefined,
      bar: w.revenue,
      strong: d.getTime() + 6 * 86400000 >= from,
      lines: hasLastYear ? [w.avg4, w.last_year] : [w.avg4],
    };
  });

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
      {error && <div key="error" className="card p-4 text-sm text-slate-600">{error}</div>}
      {!data && !error && <p key="loading" className="text-sm text-slate-400">Loading…</p>}

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
                <span>
                  <Txt>{row.sku && `${row.sku} · `}</Txt>
                  <Txt>{row.profile_name && `profile ${row.profile_name} · `}</Txt>
                  <Txt>{row.launched && `launched ${row.launched} · `}</Txt>
                </span>
                <a href={row.url} target="_blank" rel="noreferrer" className="text-brand-700 hover:underline">
                  View on Etsy ↗
                </a>
              </p>
            </div>
          </header>

          {data.action ? (
            <section className="card px-5">
              <ul>
                <ActionItem action={{ ...data.action, listing: undefined }} days={days} currency={ccy} />
              </ul>
            </section>
          ) : (
            <section className="card p-5 text-sm text-slate-600">
              {data.economics ? "Nothing needs doing on this listing in this period." : "No sales in this period."}
            </section>
          )}

          <section className="card p-5">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold text-slate-800">Weekly revenue, last 13 months</h2>
              <TrendBadge trend={data.trend.signal} />
            </div>
            <p className="mb-3 mt-1 text-xs text-slate-500">
              <span>{TREND_MEANS[data.trend.signal]}</span>
              <span translate="no">{` 4-week averages, oldest first: ${data.trend.avg4.join(" → ")} sold a week.`}</span>
              <span>{hasLastYear ? " The dashed line is the same week a year earlier, to tell a seasonal dip from a real one." : " A year-earlier line appears once the sales read reaches back that far."}</span>
            </p>
            <Chart
              points={points}
              barLabel="Revenue that week (darker: this period)"
              lines={[
                { label: "4-week average", color: LINE_COLORS.current },
                ...(hasLastYear ? [{ label: "Same week a year earlier", color: LINE_COLORS.comparison, dashed: true }] : []),
              ]}
              currency={ccy}
            />
          </section>

          <section className="card p-5">
            <h2 className="text-sm font-semibold text-slate-800">Unit economics</h2>
            <p className="mb-3 mt-1 text-xs text-slate-500">{data.comparison_label}</p>
            {data.economics ? (
              <UnitEconomics cur={data.economics} prev={data.previous} currency={ccy} prevLabel={`Previous ${days} days`} />
            ) : (
              <p className="text-sm text-slate-500">
                {data.data.sales?.from ? "No sales in this period, so there is nothing to itemise." : "This shop's sales haven't been read."}
              </p>
            )}
          </section>

          <section className="card p-5">
            <h2 className="text-sm font-semibold text-slate-800">Etsy Ads, from your uploaded reports</h2>
            {data.ads.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">
                No Ads report covers this listing. Etsy&apos;s ledger only has the shop&apos;s total ad spend per day, so
                per-listing spend is blank until a report is uploaded.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="mt-2 w-full min-w-[24rem] text-sm">
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
                        <td translate="no" className="py-1.5 text-right tabular-nums">{money(a.spend, ccy)}</td>
                        <td translate="no" className="py-1.5 text-right tabular-nums">{a.ad_orders}</td>
                        <td translate="no" className="py-1.5 text-right tabular-nums">{money(a.ad_revenue, ccy)}</td>
                        <td translate="no" className="py-1.5 text-right tabular-nums">{a.ad_views ? a.ad_views.toLocaleString() : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
