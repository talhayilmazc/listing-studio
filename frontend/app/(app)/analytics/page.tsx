"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AnalyticsSummary, MonthView } from "@/lib/types";
import { useShops } from "@/components/ShopProvider";
import { ShopBadge } from "@/components/ShopPicker";
import { ImportFromEtsy } from "@/components/analytics/ImportFromEtsy";
import { Month, monthName } from "@/components/analytics/Month";
import { MonthListings } from "@/components/analytics/MonthListings";
import { ProductCosts } from "@/components/analytics/ProductCosts";
import { SalesReread } from "@/components/analytics/SalesReread";
import { StatusBar } from "@/components/analytics/Shared";
import { SOURCES } from "@/lib/analyticsDefinitions";

type Tab = "month" | "listings" | "import" | "costs";

const TABS: { key: Tab; label: string }[] = [
  { key: "month", label: "Month" },
  { key: "listings", label: "Listings" },
  { key: "import", label: "Import from Etsy" },
  { key: "costs", label: "Product costs" },
];

/**
 * The shop's money, a month at a time: the month's account line by line beside
 * last month and the same month last year, what needs attention, and every
 * listing's result. Totals come from the imported statement, else Etsy's
 * ledger, else nothing; every number says whether it is exact, calculated or
 * estimated (lib/analyticsDefinitions.ts, docs/analytics.md).
 */
export default function AnalyticsPage() {
  const { selected } = useShops();
  const shopId = selected?.id ?? null;
  const [tab, setTab] = useState<Tab>("month");
  const [month, setMonth] = useState<string | null>(null);
  const [view, setView] = useState<MonthView | null>(null);
  const [status, setStatus] = useState<AnalyticsSummary["data"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [v, s] = await Promise.all([api.analyticsMonth(shopId, month), api.analyticsSummary(shopId, 30, "previous")]);
      setView(v);
      setStatus(s.data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The figures could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [shopId, month]);

  useEffect(() => {
    load();
  }, [load]);
  // Another shop: its own newest month.
  useEffect(() => {
    setMonth(null);
    setView(null);
  }, [shopId]);

  const connected = view?.connected ?? false;
  const report = tab === "month" || tab === "listings";

  return (
    <div className="space-y-5">
      <p className="max-w-3xl text-sm text-slate-500">
        What your shop and each listing earned in a month, after Etsy&apos;s fees, your ads and your product costs. Every
        number says whether it is exact, calculated or estimated; one with nothing behind it is left blank, not shown as
        zero. Suggestions are for you to act on in Shop Manager; nothing on Etsy is changed from here.
      </p>

      <nav className="flex justify-between overflow-y-hidden border-b border-slate-200 sm:justify-start sm:gap-1" aria-label="Analytics sections">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            aria-current={tab === t.key ? "page" : undefined}
            className={
              "-mb-px whitespace-nowrap border-b-2 px-1.5 py-3 text-[13px] sm:px-3 sm:py-2 sm:text-sm " +
              (tab === t.key ? "border-brand-600 font-medium text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800")
            }
          >
            {t.label}
          </button>
        ))}
      </nav>

      {selected && (
        <p key="shop" className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
          <span>Figures for</span>
          <ShopBadge name={selected.name} />
          <span>(change it with the shop switcher)</span>
        </p>
      )}
      {status && <StatusBar key="status" status={status} shopId={shopId} onProgress={load} />}
      <SalesReread key="reread" onProgress={load} />
      {error && <div key="error" className="card p-3 text-sm text-rose-700">{error}</div>}
      {!view && !error && <p key="loading" className="text-sm text-slate-400">Loading…</p>}

      {view && connected && report && (
        <div key="month-picker" className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <label className="flex items-center gap-2 text-sm text-slate-600">
            <span>Month</span>
            <select className="field py-1.5" value={view.month} onChange={(e) => setMonth(e.target.value.slice(0, 7))}>
              {view.months.map((m) => (
                <option key={m.month} value={m.month}>
                  {`${monthName(m.month)}${m.statement ? " · statement imported" : m.in_progress ? " · in progress" : ""}`}
                </option>
              ))}
            </select>
          </label>
          <span className="rounded border border-slate-200 px-1.5 py-px text-[11px] text-slate-600" title={SOURCES[view.sheet.source].text}>
            {SOURCES[view.sheet.source].label}
          </span>
          {view.sheet.source !== "statement" && (
            <button key="import" type="button" className="tap text-xs text-brand-700 underline" onClick={() => setTab("import")}>
              Import this month&apos;s statement
            </button>
          )}
        </div>
      )}

      <div className={loading && view ? "opacity-60 transition-opacity" : "transition-opacity"}>
        {view && connected && tab === "month" && (
          <Month key="month" view={view} onCosts={() => setTab("costs")} onImport={() => setTab("import")} />
        )}
        {view && connected && tab === "listings" && <MonthListings key="listings" view={view} onCosts={() => setTab("costs")} />}
        {tab === "import" && connected && <ImportFromEtsy key="import" shopId={shopId} onImported={load} />}
        {tab === "costs" && connected && <ProductCosts key="costs" shopId={shopId} currency={view?.currency ?? null} onSaved={load} />}
        {view && !connected && <p key="none" className="card p-4 text-sm text-slate-500">Connect a shop to see its figures.</p>}
      </div>
    </div>
  );
}
