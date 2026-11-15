"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { estimateLine, progressLine, readAllLabel, type HealthSales } from "@/lib/listingHealth";

/**
 * Listing health's sales line (fix/health-sales-and-brand): a shop whose sales were never read cannot say which
 * listings sell. One plain line and one button per shop, the same estimate and start path as Reports → Profit and
 * sales, and "Read sales for all N shops" when several need it (read one after another, in the background).
 */
export function HealthSales({ sales, shopId, onChange }: { sales: HealthSales | null; shopId: string | null; onChange: (s: HealthSales) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!sales || !shopId) return null;
  const here = sales.shops.find((s) => s.connection_id === shopId);
  if (!here || here.known) return null;
  const others = sales.shops.filter((s) => s.needs_read && s.connection_id !== shopId).length;

  const read = async (ids: string[]) => {
    setBusy(true);
    setError(null);
    try {
      onChange(await api.healthReadSales(ids));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reading sales could not start.");
    } finally {
      setBusy(false);
    }
  };

  const progress = progressLine(here);
  const estimate = here.state === "counted" ? estimateLine(here) : "";
  return (
    <section className="card space-y-2 p-4 text-sm text-slate-700" data-testid="health-sales" data-state={here.state}>
      {here.needs_read && (
        <div key="line" className="flex flex-wrap items-center justify-between gap-3" data-testid="health-sales-line">
          <p className="min-w-0 flex-1">
            <span>{sales.read_line}</span>
            {estimate && <span key="estimate" translate="no">{` ${estimate}`}</span>}
          </p>
          <button type="button" className="btn-primary" disabled={busy} data-testid="health-read-sales" onClick={() => void read([shopId])}>
            {here.state === "failed" ? "Try again" : "Read sales"}
          </button>
        </div>
      )}
      {progress && (
        <p key="progress" className={here.state === "failed" ? "text-rose-800" : "text-slate-700"} data-testid="health-sales-progress" translate="no">{progress}</p>
      )}
      {here.state === "reading" && here.window_count ? (
        <div key="bar" className="progress" aria-hidden="true">
          <div className="progress-fill" style={{ width: `${Math.min(100, (here.read_count / here.window_count) * 100)}%` }} />
        </div>
      ) : null}
      {others > 0 && (
        <div key="all" className="flex flex-wrap items-center gap-3 border-t border-slate-200 pt-2" data-testid="health-sales-all">
          <span translate="no">{`${others} other shop${others === 1 ? " has" : "s have"} not had ${others === 1 ? "its" : "their"} sales read either.`}</span>
          <button type="button" className="btn-secondary" disabled={busy} data-testid="health-read-all" onClick={() => void read([])}>
            <span translate="no">{readAllLabel(sales.to_read)}</span>
          </button>
        </div>
      )}
      <p className="text-xs text-slate-600">
        <span>It runs in the background, one shop after another, and can be left. Listing health updates by itself when it ends. </span>
        <a className="tap underline" href="/analytics">Reports → Profit and sales</a>
      </p>
      {error && <p key="error" role="alert" className="text-rose-800"><span translate="no">{error}</span></p>}
    </section>
  );
}
