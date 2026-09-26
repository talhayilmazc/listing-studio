"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AnalyticsListings, AnalyticsOverview, ListingClass } from "@/lib/types";
import { useShops } from "@/components/ShopProvider";
import { AdsUpload } from "@/components/analytics/AdsUpload";
import { CostSettings } from "@/components/analytics/CostSettings";
import { ListingTable } from "@/components/analytics/ListingTable";
import { Overview } from "@/components/analytics/Overview";
import { PeriodPicker, StatusBar } from "@/components/analytics/Shared";

type Tab = "overview" | "listings" | "ads" | "costs";

const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "listings", label: "Listings" },
  { key: "ads", label: "Ads report" },
  { key: "costs", label: "Fees & costs" },
];


/**
 * Profit and what to do about each listing (v7 §C), over the seller's own sales,
 * the Etsy Ads report they uploaded, and the fees and costs they entered.
 */
export default function AnalyticsPage() {
  const { selected } = useShops();
  const shopId = selected?.id ?? null;
  const [tab, setTab] = useState<Tab>("overview");
  const [days, setDays] = useState(30);
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [listings, setListings] = useState<AnalyticsListings | null>(null);
  const [classes, setClasses] = useState<ListingClass[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [o, l] = await Promise.all([api.analyticsOverview(shopId, days), api.analyticsListings(shopId, days)]);
      setOverview(o);
      setListings(l);
      setError(null);
      return o;
    } catch (e) {
      setError(e instanceof Error ? e.message : "The figures could not be loaded.");
      return null;
    } finally {
      setLoading(false);
    }
  }, [shopId, days]);

  useEffect(() => {
    load();
  }, [load]);

  const status = overview?.status;
  const currency = status?.currency ?? null;

  return (
    <div className="space-y-5">
      <p className="max-w-3xl text-sm text-slate-500">
        What each of your listings earns after Etsy&apos;s fees, your costs and your ads, and where to look first.
        Built from your own shop&apos;s sales, the ads report you upload, and the fees and costs you enter.
        Suggestions are for you to act on in Shop Manager; nothing on Etsy is changed from here.
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <PeriodPicker days={days} onChange={setDays} />
        <nav className="flex gap-1 border-b border-slate-200 sm:ml-auto" aria-label="Analytics sections">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => setTab(t.key)}
              aria-current={tab === t.key ? "page" : undefined}
              className={
                "-mb-px border-b-2 px-3 py-1.5 text-sm " +
                (tab === t.key ? "border-brand-600 font-medium text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800")
              }
            >
              {t.label}
            </button>
          ))}
        </nav>
      </div>

      {status && <StatusBar key="statusbar-119-6" status={status} shopId={shopId} onProgress={load} />}
      {error && <div key="div-120-6" className="card p-3 text-sm text-rose-700">{error}</div>}
      {!overview && !error && <p key="p-121-6" className="text-sm text-slate-400">Loading…</p>}

      <div className={loading && overview ? "opacity-60 transition-opacity" : "transition-opacity"}>
        {overview && tab === "overview" && status?.connected && (
          <Overview key="overview-124-8"
            data={overview}
            onClass={(k) => {
              setClasses([k]);
              setTab("listings");
            }}
            onTab={setTab}
          />
        )}
        {listings && tab === "listings" && status?.connected && (
          <ListingTable key="listingtable-134-8" rows={listings.listings} currency={currency} days={days} classes={classes} onClasses={setClasses} />
        )}
        {tab === "ads" && status?.connected && <AdsUpload key="adsupload-137-8" shopId={shopId} currency={currency} onImported={load} />}
        {tab === "costs" && <CostSettings key="costsettings-138-8" shopId={shopId} currency={currency} onSaved={load} />}
      </div>

      <p className="text-xs text-slate-400">
        Classes: <b>Winner</b> — among your top fifth by net profit with 3+ sales · <b>Fading</b> — half or less of the
        previous period&apos;s sales (from 3+) · <b>Ad sink</b> — 5 or more (in your currency) spent on ads with no sale or a loss · <b>Loser</b> — no sale in
        90 days, or selling at a loss · <b>Steady</b> — selling at a profit · <b>New</b> — no sale yet and live under 45 days, or under 30 once its ad has views or spend; too early to judge.
      </p>
    </div>
  );
}
