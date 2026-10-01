"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { formatWhen } from "@/lib/schedule";
import type { LedgerRead, SalesSync } from "@/lib/types";
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
  const seen = useRef<string | null>(null);

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
  const ledgerLive = sync?.ledger.state === "estimating" || sync?.ledger.state === "reading" || sync?.ledger.history_state === "reading";
  const live = sync?.state === "estimating" || sync?.state === "reading" || ledgerLive || updating !== null;
  // Only the background history is moving: a slower look is enough.
  const historyOnly = sync?.state === "complete" && sync.ledger.state === "complete" && updating === null;
  useEffect(() => {
    if (!live) return;
    const t = setInterval(async () => {
      const s = await load();
      if (!s) return;
      reloads.current += 1;
      // The figures are reloaded when something changed: a read finished, "Read
      // now" landed, or the fee history reached further back. While sales or
      // the first ledger read are under way, every third look as well.
      const mark = `${s.state}|${s.ledger.state}|${s.ledger.history_state}|${s.ledger.covers_from}`;
      const changed = seen.current !== null && seen.current !== mark;
      seen.current = mark;
      if (updating !== null && s.state === "complete" && s.synced_at !== updating) {
        setUpdating(null);
        onProgress();
      } else if (changed) {
        onProgress();
      } else if ((s.state === "reading" || s.ledger.state === "reading") && reloads.current % 3 === 0) {
        onProgress();
      }
    }, historyOnly ? 15000 : 4000);
    return () => clearInterval(t);
  }, [live, historyOnly, load, onProgress, updating]);

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
          {sync.ledger.state === "estimated" && (
            <p key="ledger-cost">
              {`Etsy's payment ledger (the fees and ad spend Etsy actually charged) has ${n(sync.ledger.total_count)} ${plural(sync.ledger.total_count ?? 0, "entry", "entries")} in the last ${sync.ledger.first_days} days: about ${n(sync.ledger.pages_estimate)} more ${plural(sync.ledger.pages_estimate ?? 0, "request", "requests")}. It is read alongside your sales.`}
            </p>
          )}
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
          <button type="button" className="btn-secondary" onClick={() => act(() => api.resumeSales(shopId))} disabled={busy}>
            Try again
          </button>
        </div>
      )}

      {sync.state !== "none" && sync.state !== "estimating" && (
        <LedgerLine
          key="ledger"
          ledger={sync.ledger}
          alone={sync.state === "complete"}
          busy={busy}
          timeZone={timeZone}
          onEstimate={() => act(() => api.estimateLedger(shopId))}
          onStart={() => act(() => api.startLedger(shopId))}
          onResume={() => act(() => api.resumeSales(shopId))}
        />
      )}

      {error && <p key="error" className="text-xs text-rose-700">{error}</p>}
    </div>
  );
}

/**
 * Etsy's payment ledger: what Etsy charged the shop per day (fees, ads). The
 * first read covers the last 90 days, because the ledger has several entries
 * per order; from there it is kept up to date nightly and held for 13 months.
 */
function LedgerLine({
  ledger,
  alone,
  busy,
  timeZone,
  onEstimate,
  onStart,
  onResume,
}: {
  ledger: LedgerRead;
  /** The sales read is done, so the ledger can be estimated and started on its own. */
  alone: boolean;
  busy: boolean;
  timeZone: string;
  onEstimate: () => void;
  onStart: () => void;
  onResume: () => void;
}) {
  const pct = ledger.total_count ? Math.min(100, (ledger.read_count / ledger.total_count) * 100) : 0;
  if (ledger.state === "none" && alone) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-2">
        <span>
          Fees and ad spend are estimates until Etsy&apos;s payment ledger is read: it holds what Etsy actually charged
          your shop each day.
        </span>
        <button type="button" className="btn-secondary" onClick={onEstimate} disabled={busy}>
          See what it costs
        </button>
      </div>
    );
  }
  if (ledger.state === "estimating") {
    return <p className="border-t border-slate-100 pt-2 text-brand-700">Counting the entries in Etsy&apos;s payment ledger…</p>;
  }
  if (ledger.state === "estimated" && alone) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-2">
        <span>
          {`Etsy's payment ledger has ${n(ledger.total_count)} ${plural(ledger.total_count ?? 0, "entry", "entries")} in the last ${ledger.first_days} days: about ${n(ledger.pages_estimate)} ${plural(ledger.pages_estimate ?? 0, "request", "requests")} from the app's shared daily budget. From there it is kept up to date every night; periods before those days keep using your fee rates.`}
        </span>
        <button type="button" className="btn-primary" onClick={onStart} disabled={busy}>
          Read the ledger
        </button>
      </div>
    );
  }
  if (ledger.state === "reading" || ledger.state === "waiting") {
    return (
      <div className="space-y-1.5 border-t border-slate-100 pt-2">
        <span>
          <span>{ledger.state === "reading" ? "Reading Etsy's ledger: " : "Ledger paused at "}</span>
          <span className="font-medium tabular-nums">{n(ledger.read_count)}</span>
          <span>{` of ${n(ledger.total_count)} entries`}</span>
        </span>
        <div className="progress">
          <div className="progress-fill" style={{ width: pct + "%" }} />
        </div>
        <p className="text-xs text-slate-500">
          {ledger.state === "waiting" && ledger.resumes_at
            ? `Today's share of the budget is used; it carries on at ${formatWhen(ledger.resumes_at, timeZone)}. Until it finishes, fees are estimated from your rates.`
            : "Until it finishes, fees are estimated from your rates and shop-wide ad spend is blank."}
        </p>
      </div>
    );
  }
  if (ledger.state === "complete") {
    const day = (iso: string) => new Date(`${iso}T00:00:00Z`).getTime();
    const filling = ledger.history_state !== "complete" && ledger.covers_from && ledger.history_target && ledger.covers_to;
    const span = filling ? day(ledger.covers_to!) - day(ledger.history_target!) : 0;
    const histPct = filling && span > 0 ? Math.min(100, Math.max(0, ((day(ledger.covers_to!) - day(ledger.covers_from!)) / span) * 100)) : 0;
    return (
      <div className="space-y-1.5">
        <p className="text-xs text-slate-500">
          {ledger.covers_from && ledger.covers_to
            ? `Fees and ad spend from Etsy's ledger: ${ledger.covers_from} to ${ledger.covers_to} · ${n(ledger.requests_used)} requests so far · updated every night`
            : "Fees and ad spend come from Etsy's ledger · updated every night"}
        </p>
        {filling && ledger.history_state !== "failed" && (
          <div key="history" className="space-y-1 border-t border-slate-100 pt-2">
            <p className="text-xs text-slate-600">
              <span>
                {ledger.history_state === "none"
                  ? "13 months of fee history will be read in the background, once your sales are read: "
                  : "Reading 13 months of fee history in the background: "}
              </span>
              <span className="font-medium">{`back to ${ledger.covers_from} so far, going to ${ledger.history_target}`}</span>
              <Txt>{ledger.history_requests_left != null ? ` · about ${n(ledger.history_requests_left)} ${plural(ledger.history_requests_left, "request", "requests")} to go` : ""}</Txt>
            </p>
            <div className="progress">
              <div className="progress-fill" style={{ width: histPct + "%" }} />
            </div>
            <p className="text-xs text-slate-500">
              <span>
                It runs after everything else, inside this shop&apos;s daily share of the budget, over as many days as it
                needs. Periods before the date it has reached, including a year-earlier comparison, show fees estimated
                from your rates until it gets there.
              </span>
              <Txt>{ledger.history_state === "waiting" && ledger.history_note ? ` Paused for today: ${ledger.history_note}.` : ""}</Txt>
            </p>
          </div>
        )}
        {ledger.history_state === "failed" && (
          <div key="history-failed" className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-2">
            <span className="text-rose-700">{ledger.history_note ?? "Reading the fee history stopped."}</span>
            <button type="button" className="btn-secondary" onClick={onResume} disabled={busy}>
              Try again
            </button>
          </div>
        )}
      </div>
    );
  }
  if (ledger.state === "failed") {
    return (
      <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-2">
        <span className="text-rose-700">{ledger.note ?? "Reading Etsy's ledger stopped."}</span>
        <button type="button" className="btn-secondary" onClick={onResume} disabled={busy}>
          Try again
        </button>
      </div>
    );
  }
  return null;
}
