"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { money, percent } from "@/lib/analytics";
import type { Breakdown, BreakdownMetric, LedgerType } from "@/lib/types";
import { ExportLink, ListingCell, SourceTag } from "./Shared";

const PAGE = 25;

/** What Etsy's ledger charged, by its own entry type; what isn't a cost is listed, not counted. */
export function LedgerTypes({ types, currency }: { types: LedgerType[]; currency: string | null }) {
  if (types.length === 0) return null;
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-xs text-slate-400">
        <tr>
          <th className="pb-1 font-medium">Etsy&apos;s ledger entry</th>
          <th className="pb-1 text-right font-medium">Entries</th>
          <th className="pb-1 text-right font-medium">Amount</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        {types.map((t) => (
          <tr key={t.ledger_type} className={t.counted ? "" : "text-slate-400"}>
            <td className="py-1.5">
              <span className={t.counted ? "text-slate-700" : ""}>{t.label}</span>{" "}
              <code translate="no" className="text-[11px] text-slate-400">{t.ledger_type}</code>
            </td>
            <td translate="no" className="py-1.5 text-right tabular-nums">{t.entries.toLocaleString()}</td>
            <td translate="no" className="py-1.5 text-right tabular-nums">{money(t.amount, currency)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * What a total is made of: the listings behind it, Etsy's ledger entry types
 * for a fee, and its days. Opened by clicking the figure.
 */
export function Drilldown({
  metric,
  shopId,
  days,
  currency,
  onClose,
}: {
  metric: BreakdownMetric;
  shopId: string | null;
  days: number;
  currency: string | null;
  onClose: () => void;
}) {
  const [data, setData] = useState<Breakdown | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState(PAGE);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setShown(PAGE);
    api
      .analyticsBreakdown(metric, shopId, days)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "This figure could not be loaded."));
    return () => {
      cancelled = true;
    };
  }, [metric, shopId, days]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const count = metric === "units" || metric === "orders";
  const fmt = (v: number | null | undefined) => (v === null || v === undefined ? "—" : count ? v.toLocaleString() : money(v, currency));

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/40 sm:items-center sm:p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={data ? `What ${data.label} is made of` : "Breakdown"}
        className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-t-xl bg-white shadow-xl sm:rounded-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-[0.08em] text-slate-500">
              {data ? data.label : "Loading…"}
            </p>
            {data && (
              <p key="figure" className="mt-1 flex flex-wrap items-baseline gap-2">
                <span translate="no" className="text-2xl font-semibold tabular-nums text-slate-900">{fmt(data.figure.value)}</span>
                <SourceTag source={data.figure.source} />
                <span className="text-xs text-slate-500">{`${data.period.start} to ${data.period.end}`}</span>
              </p>
            )}
          </div>
          <button type="button" onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700" aria-label="Close">
            ✕
          </button>
        </header>

        <div className="space-y-5 overflow-y-auto px-5 py-4 text-sm">
          {error && <p key="error" className="text-rose-700">{error}</p>}
          {data && data.figure.value === null && (
            <p key="blank" className="rounded-lg bg-slate-50 px-3 py-2 text-slate-600">
              {data.figure.note ?? "This figure isn't available for this period."}
            </p>
          )}
          {data && data.notes.length > 0 && (
            <ul key="notes" className="list-disc space-y-1 pl-4 text-xs text-slate-600">
              {data.notes.map((nte) => (
                <li key={nte}>{nte}</li>
              ))}
            </ul>
          )}

          {data && data.ledger_types.length > 0 && (
            <section key="ledger">
              <h3 className="mb-1 text-xs font-semibold text-slate-700">Which fees</h3>
              <LedgerTypes types={data.ledger_types} currency={currency} />
            </section>
          )}

          {data && (data.rows.length > 0 || data.unattributed) && (
            <section key="rows">
              <div className="mb-1 flex items-center justify-between gap-3">
                <h3 className="text-xs font-semibold text-slate-700">
                  <span>Which listings (<span translate="no">{data.rows.length.toLocaleString()}</span>)</span>
                </h3>
                <ExportLink href={api.analyticsExportUrl("breakdown", shopId, days, "previous", metric)} />
              </div>
              <table className="w-full table-fixed text-sm">
                <tbody className="divide-y divide-slate-100">
                  {data.unattributed ? (
                    <tr key="unattributed">
                      <td className="py-2 pr-2 text-slate-600">
                        Not attributed to a listing
                        <span className="block text-xs text-slate-400">
                          Etsy&apos;s ledger gives ad spend for the whole shop; upload the Ads report to split it by listing.
                        </span>
                      </td>
                      <td translate="no" className="w-28 py-2 text-right tabular-nums">{money(data.unattributed, currency)}</td>
                      <td className="w-14" />
                    </tr>
                  ) : null}
                  {data.rows.slice(0, shown).map((r) => (
                    <tr key={r.listing_id}>
                      <td className="py-2 pr-2">
                        <ListingCell row={r} days={days} />
                      </td>
                      <td translate="no" className={`w-28 py-2 text-right tabular-nums ${r.value < 0 ? "text-rose-700" : ""}`}>{fmt(r.value)}</td>
                      <td translate="no" className="w-14 py-2 text-right text-xs tabular-nums text-slate-500">{percent(r.share)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.rows.length > shown && (
                <button key="more" type="button" className="mt-2 text-xs font-medium text-brand-700 hover:underline" onClick={() => setShown(shown + 100)}>
                  <span>Show more (<span translate="no">{(data.rows.length - shown).toLocaleString()}</span> left)</span>
                </button>
              )}
            </section>
          )}

          {data && data.daily.length > 0 && (
            <details key="daily">
              <summary className="cursor-pointer text-xs font-semibold text-slate-700">Which days</summary>
              <table className="mt-1 w-full text-xs">
                <tbody className="divide-y divide-slate-100">
                  {[...data.daily].reverse().map((d) => (
                    <tr key={d.day}>
                      <td className="py-1 text-slate-600">{d.day}</td>
                      <td translate="no" className="py-1 text-right tabular-nums">{fmt(d.value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}

          {data && data.rows.length === 0 && data.daily.length === 0 && data.ledger_types.length === 0 && data.figure.value !== null && (
            <p key="empty" className="text-slate-500">Nothing is behind this figure in this period.</p>
          )}
        </div>
      </div>
    </div>
  );
}
