"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { formatWhen } from "@/lib/schedule";
import type { SalesSync } from "@/lib/types";
import { useSession } from "@/components/SessionProvider";

import { Txt } from "@/components/Txt";
const n = (v: number | null | undefined) => (v ?? 0).toLocaleString();
const plural = (v: number, one: string, many: string) => (v === 1 ? one : many);

/**
 * Reading the shop's sales (v7 §C1). Etsy returns 100 sales per request, so
 * the first read of a big shop is shown as a cost before it starts, runs in the
 * background with its progress here, and the figures below fill in as it goes.
 */
export function SalesPanel({ shopId, onProgress }: { shopId: string | null; onProgress: () => void }) {
  const { timeZone } = useSession();
  const [sync, setSync] = useState<SalesSync | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [updating, setUpdating] = useState<string | null>(null); // synced_at before "Read now"
  const reloads = useRef(0);

  const load = useCallback(async () => {
    try {
      const s = await api.salesStatus(shopId);
      setSync(s);
      return s;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load where reading your sales stands.");
      return null;
    }
  }, [shopId]);

  useEffect(() => {
    setSync(null);
    load();
  }, [load]);

  // While estimating or reading, look again every few seconds; refresh the
  // figures every third look, so partial totals appear as they land.
  const live = sync?.state === "estimating" || sync?.state === "reading" || updating !== null;
  useEffect(() => {
    if (!live) return;
    const t = setInterval(async () => {
      const s = await load();
      reloads.current += 1;
      if (s?.state === "complete" && (updating === null || s.synced_at !== updating)) {
        setUpdating(null);
        onProgress();
      } else if (s?.state === "reading" && reloads.current % 3 === 0) {
        onProgress();
      }
    }, 4000);
    return () => clearInterval(t);
  }, [live, load, onProgress, updating]);

  async function act(f: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await f();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  }

  if (!sync) return null;
  const estimate = () => act(() => api.estimateSales(shopId));
  const start = () => act(() => api.startSales(shopId));
  const readNow = () =>
    act(async () => {
      setUpdating(sync.synced_at ?? "");
      await api.refreshSales(shopId);
    });

  const pct = sync.window_count ? Math.min(100, (sync.read_count / sync.window_count) * 100) : 0;
  const days = sync.days_estimate ?? 1;

  return (
    <div translate="no" className="card space-y-2 p-4 text-sm text-slate-700">
      {sync.state === "none" && (
        <div key="none" className="flex flex-wrap items-center justify-between gap-3">
          <span>
            Read your shop&apos;s sales to see what each listing earns. First we work out what reading them costs (a
            few requests).
          </span>
          <button type="button" className="btn-primary" onClick={estimate} disabled={busy}>
            See what it costs
          </button>
        </div>
      )}

      {sync.state === "estimating" && (
        <p key="estimating" className="text-brand-700">Working out how many sales your shop has…</p>
      )}

      {sync.state === "estimated" && (
        <div key="estimated" className="space-y-2">
          <p>
            <span>{`Your shop has ${n(sync.window_count)} ${plural(sync.window_count ?? 0, "sale", "sales")} in the last 13 months`}</span>
            <span className="text-slate-500">{` (${n(sync.total_count)} in all)`}</span>
            <span>{`. Etsy returns 100 per request, so reading them takes about ${n(sync.pages_estimate)} ${plural(sync.pages_estimate ?? 0, "request", "requests")} from the app's shared daily budget`}</span>
            <span>
              {days > 1
                ? `, spread over ${days} days at up to ${sync.daily_requests} a day so other work isn't crowded out.`
                : "; it finishes today."}
            </span>
          </p>
          <p className="text-xs text-slate-500">
            It runs in the background and can be left; the figures below fill in as it goes. After this, each night
            reads only new sales (usually one request).
          </p>
          <div className="flex gap-2">
            <button type="button" className="btn-primary" onClick={start} disabled={busy}>
              Start reading
            </button>
            <button type="button" className="btn-secondary" onClick={estimate} disabled={busy}>
              Estimate again
            </button>
          </div>
        </div>
      )}

      {(sync.state === "reading" || sync.state === "waiting") && (
        <div key="reading" className="space-y-1.5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span>
              <span>{sync.state === "reading" ? "Reading your sales: " : "Paused at "}</span>
              <span className="font-medium tabular-nums">{n(sync.read_count)}</span>
              <span>{` of ${n(sync.window_count)} sales`}</span>
            </span>
            <span className="text-xs tabular-nums text-slate-500">
              {`${n(sync.requests_used)} ${plural(sync.requests_used, "request", "requests")} so far`}
            </span>
          </div>
          <div className="progress">
            <div className="progress-fill" style={{ width: pct + "%" }} />
          </div>
          <p className="text-xs text-slate-500">
            {sync.state === "waiting" && sync.resumes_at
              ? `Today's share of the budget for reading sales is used; it carries on at ${formatWhen(sync.resumes_at, timeZone)}. The figures below include what's read so far.`
              : "The figures below include what's read so far."}
          </p>
        </div>
      )}

      {sync.state === "complete" && (
        <div key="complete" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
          <span>
            {updating !== null
              ? "Reading new sales…"
              : `Sales read ${sync.synced_at ? relativeTime(sync.synced_at) : ""} · updated every night`}
          </span>
          <span>
            <span><span>{`· first read: ${n(sync.read_count)} sales in ${n(sync.requests_used)} requests`}</span>
            <Txt>{sync.last_update_requests != null ? ` · last update: ${sync.last_update_requests} ${plural(sync.last_update_requests, "request", "requests")}` : ""}</Txt></span>
          </span>
          <button type="button" className="underline hover:text-slate-900 disabled:opacity-50" onClick={readNow} disabled={busy || updating !== null}>
            Read now
          </button>
        </div>
      )}

      {sync.state === "failed" && (
        <div key="failed" className="flex flex-wrap items-center gap-3">
          <span className="text-rose-700">{sync.note ?? "Reading your sales stopped."}</span>
          <button type="button" className="btn-secondary" onClick={estimate} disabled={busy}>
            Try again
          </button>
        </div>
      )}

      {error && <p key="error" className="text-xs text-rose-700">{error}</p>}
    </div>
  );
}
