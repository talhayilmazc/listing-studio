"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AnalyticsListings, AnalyticsSummary, ListingClass } from "@/lib/types";
import { useShops } from "@/components/ShopProvider";
import { AdsUpload } from "@/components/analytics/AdsUpload";
import { CostSettings } from "@/components/analytics/CostSettings";
import { ListingTable } from "@/components/analytics/ListingTable";
import { Overview } from "@/components/analytics/Overview";
import { type CompareMode, ComparePicker, ComparisonLine, PeriodPicker, StatusBar } from "@/components/analytics/Shared";
import { Today } from "@/components/analytics/Today";

type Tab = "today" | "overview" | "listings" | "ads" | "costs";

const TABS: { key: Tab; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "overview", label: "Overview" },
  { key: "listings", label: "Listings" },
  { key: "ads", label: "Ads report" },
  { key: "costs", label: "Fees & costs" },
];

/**
 * The shop's finances (v7 §C): what to do today, the income statement against
 * a stated comparison, and each listing's unit economics. Built from the
 * seller's own sales, Etsy's payment ledger for their shop, the Ads report
 * they upload, and the costs they enter.
 */
export default function AnalyticsPage() {
  const { selected } = useShops();
  const shopId = selected?.id ?? null;
  const [tab, setTab] = useState<Tab>("today");
  const [days, setDays] = useState(30);
  const [compare, setCompare] = useState<CompareMode>("previous");
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [listings, setListings] = useState<AnalyticsListings | null>(null);
  const [classes, setClasses] = useState<ListingClass[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, l] = await Promise.all([api.analyticsSummary(shopId, days, compare), api.analyticsListings(shopId, days, compare)]);
      setSummary(s);
      setListings(l);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The figures could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [shopId, days, compare]);

  useEffect(() => {
    load();
  }, [load]);

  const status = summary?.data;
  const currency = status?.currency ?? null;
  const report = tab === "today" || tab === "overview" || tab === "listings";

  return (
    <div className="space-y-5">
      <p className="max-w-3xl text-sm text-slate-500">
        What your shop and each listing earn after Etsy&apos;s fees, your costs and your ads, and what to do about it first.
        Every figure says where it comes from; one with nothing behind it is left blank, not shown as zero. Suggestions are
        for you to act on in Shop Manager; nothing on Etsy is changed from here.
      </p>

      <nav className="flex overflow-x-auto overflow-y-hidden border-b border-slate-200 sm:gap-1" aria-label="Analytics sections">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            aria-current={tab === t.key ? "page" : undefined}
            className={
              "-mb-px whitespace-nowrap border-b-2 px-2 py-2 text-sm sm:px-3 " +
              (tab === t.key ? "border-brand-600 font-medium text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800")
            }
          >
            {t.label}
          </button>
        ))}
      </nav>

      {status && <StatusBar key="status" status={status} shopId={shopId} onProgress={load} />}
      {error && <div key="error" className="card p-3 text-sm text-rose-700">{error}</div>}
      {!summary && !error && <p key="loading" className="text-sm text-slate-400">Loading…</p>}

      {report && status?.connected && (
        <div key="controls" className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <PeriodPicker days={days} onChange={setDays} />
            <ComparePicker mode={compare} comparison={summary?.comparison} onChange={setCompare} />
          </div>
          {tab !== "overview" && <ComparisonLine key="line" comparison={summary?.comparison} />}
        </div>
      )}

      <div className={loading && summary ? "opacity-60 transition-opacity" : "transition-opacity"}>
        {summary && tab === "today" && status?.connected && (
          <Today key="today" data={summary} onListings={() => setTab("listings")} />
        )}
        {summary && tab === "overview" && status?.connected && (
          <Overview key="overview" data={summary} shopId={shopId} compare={compare} onTab={setTab} />
        )}
        {listings && tab === "listings" && status?.connected && (
          <ListingTable
            key="listings"
            rows={listings.listings}
            currency={currency}
            days={days}
            comparisonLabel={listings.comparison?.unavailable ? undefined : listings.comparison?.label}
            classes={classes}
            onClasses={setClasses}
            exportHref={api.analyticsExportUrl("listings", shopId, days, compare)}
          />
        )}
        {tab === "ads" && status?.connected && <AdsUpload key="ads" shopId={shopId} currency={currency} onImported={load} />}
        {tab === "costs" && <CostSettings key="costs" shopId={shopId} currency={currency} onSaved={load} />}
      </div>

      {tab === "listings" && (
        <p key="legend" className="text-xs text-slate-400">
          Status follows what&apos;s worth doing: <b>Ad sink</b> — ads with no sale, or above the listing&apos;s break-even ·{" "}
          <b>Loser</b> — selling at a loss, or no sale in 90 days · <b>Fading</b> — half or less of the period before, or
          its 4-week average turned down · <b>Winner</b> — top fifth by net profit with 3+ sales, or room to advertise ·{" "}
          <b>Steady</b> — selling at a profit · <b>New</b> — no sale yet and live under 45 days; too early to judge.
          &quot;ACOS / b-e&quot; is ad spend ÷ ad revenue against the listing&apos;s break-even.
        </p>
      )}
    </div>
  );
}
