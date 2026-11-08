"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { conversion, waited } from "@/lib/funnel";
import type { Funnel } from "@/lib/types";

/**
 * Admin → Funnel (app/onboarding-v1): the accounts that signed up in the last 7 and 30 days and how
 * far each got, from first-party rows only (backend pipeline/funnel.py): counts, the conversion from
 * each step to the next, and the median time from signing up to a first approval. No names.
 */
export function FunnelPanel() {
  const [data, setData] = useState<Record<7 | 30, Funnel | null>>({ 7: null, 30: null });
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    Promise.all([api.adminFunnel(7), api.adminFunnel(30)])
      .then(([week, month]) => !cancelled && setData({ 7: week, 30: month }))
      .catch((e) => !cancelled && setError(e?.message ?? "The funnel could not be read."));
    return () => {
      cancelled = true;
    };
  }, []);
  if (error) return <p role="alert" className="text-sm text-rose-700">{error}</p>;
  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="funnel">
      {([7, 30] as const).map((days) => {
        const f = data[days];
        return (
          <section key={days} className="card min-h-[22rem] p-5" aria-labelledby={`funnel-${days}`}>
            <h3 id={`funnel-${days}`} className="text-base font-bold text-slate-900"><span>Signed up in the last <span>{days}</span> days</span></h3>
            {f === null ? (
              <div key="loading" className="mt-4 h-60 animate-pulse rounded bg-slate-100" aria-hidden />
            ) : (
              <div key="data">
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs text-slate-600">
                        <th className="py-1.5 pr-3 font-semibold">Step</th>
                        <th className="py-1.5 pr-3 text-right font-semibold">Accounts</th>
                        <th className="py-1.5 text-right font-semibold">From the step before</th>
                      </tr>
                    </thead>
                    <tbody translate="no">
                      {f.steps.map((s) => (
                        <tr key={s.name} className="border-t border-slate-100">
                          <td className="py-1.5 pr-3 text-slate-800">{s.label}</td>
                          <td className="py-1.5 pr-3 text-right tabular-nums text-slate-900">{s.count}</td>
                          <td className="py-1.5 text-right tabular-nums text-slate-700">{conversion(s.from_previous)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-3 text-sm text-slate-700" translate="no">
                  <span>Median time from sign-up to first approval: </span>
                  <span className="font-semibold text-slate-900">{waited(f.median_seconds_to_first_approval)}</span>
                  <span>{f.approved_with_time ? ` (${f.approved_with_time} account${f.approved_with_time === 1 ? "" : "s"})` : ""}</span>
                </p>
              </div>
            )}
          </section>
        );
      })}
      <p className="text-xs text-slate-600 lg:col-span-2">
        Each account is counted once per step, the first time it did it. Demo accounts are left out. Only the account id, the step and its time are stored.
      </p>
    </div>
  );
}
