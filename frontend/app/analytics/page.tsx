"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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

const POLL_MS = 5000;
const POLL_TRIES = 36;

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
  const [reading, setReading] = useState(false);
  const readFrom = useRef<string | null>(null);

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

  // While a sales read runs, look again every few seconds until it has finished.
  useEffect(() => {
    if (!reading) return;
    let tries = 0;
    const t = setInterval(async () => {
      tries += 1;
      const o = await api.analyticsOverview(shopId, days).catch(() => null);
      if ((o && o.status.synced_at !== readFrom.current) || tries >= POLL_TRIES) {
        clearInterval(t);
        setReading(false);
        load();
      }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [reading, shopId, days, load]);

  const readNow = async () => {
    readFrom.current = overview?.status.synced_at ?? null;
    try {
      await api.refreshSales(shopId);
      setReading(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start reading your sales.");
    }
  };

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

      {status && <StatusBar status={status} reading={reading} onRead={readNow} />}
      {error && <div className="card p-3 text-sm text-rose-700">{error}</div>}
      {!overview && !error && <p className="text-sm text-slate-400">Loading…</p>}

      <div className={loading && overview ? "opacity-60 transition-opacity" : "transition-opacity"}>
        {overview && tab === "overview" && status?.connected && (
          <Overview
            data={overview}
            onClass={(k) => {
              setClasses([k]);
              setTab("listings");
            }}
            onTab={setTab}
          />
        )}
        {listings && tab === "listings" && status?.connected && (
          <ListingTable rows={listings.listings} currency={currency} days={days} classes={classes} onClasses={setClasses} />
        )}
        {tab === "ads" && status?.connected && <AdsUpload shopId={shopId} currency={currency} onImported={load} />}
        {tab === "costs" && <CostSettings shopId={shopId} currency={currency} onSaved={load} />}
      </div>

      <p className="text-xs text-slate-400">
        Classes: <b>Winner</b> — among your top fifth by net profit with 3+ sales · <b>Fading</b> — half or less of the
        previous period&apos;s sales (from 3+) · <b>Ad sink</b> — 5 or more (in your currency) spent on ads with no sale or a loss · <b>Loser</b> — no sale in
        90 days, or selling at a loss · <b>Steady</b> — selling at a profit · <b>New</b> — under 90 days old, no sale yet.
      </p>
    </div>
  );
}
