"use client";

import { useState } from "react";
import type { AiAccountCost, AiCost, AiPeriodCost } from "@/lib/types";

/**
 * What the AI provider's work cost us, per account. Admin only: these figures
 * (calls, tokens, models, dollars) are our cost of goods, and no screen or API
 * response a seller can reach carries them.
 */

type Period = "today" | "this_month" | "last_30_days" | "all_time";
const PERIODS: { id: Period; label: string }[] = [
  { id: "today", label: "Today" },
  { id: "this_month", label: "This month" },
  { id: "last_30_days", label: "Last 30 days" },
  { id: "all_time", label: "All time" },
];

const usd = (v: string | null, digits = 2) => (v === null ? "—" : `$${Number(v).toFixed(digits)}`);
const n = (v: number) => v.toLocaleString();

export function AiCostTab({ cost }: { cost: AiCost | null }) {
  const [period, setPeriod] = useState<Period>("this_month");
  const [open, setOpen] = useState<string | null>(null);
  if (!cost) return <p className="text-sm text-slate-400">Loading…</p>;

  const total = cost.total[period];
  const accounts = [...cost.accounts]
    .filter((a) => a[period].calls > 0)
    .sort((a, b) => Number(b[period].cost_usd) - Number(a[period].cost_usd));

  return (
    <div className="space-y-4" translate="no">
      <div className="card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-display text-2xl text-slate-900">AI cost</h2>
            <p className="mt-1 text-xs text-slate-500">
              <span>What writing listings cost us. Days are UTC; today is <span>{cost.as_of}</span>. Sellers never see any of this.</span>
            </p>
          </div>
          <div className="flex flex-wrap gap-1 rounded-lg border border-slate-200 p-1" role="tablist" aria-label="Period">
            {PERIODS.map((p) => (
              <button
                key={p.id}
                type="button"
                role="tab"
                aria-selected={period === p.id}
                onClick={() => setPeriod(p.id)}
                className={
                  "rounded-md px-2.5 py-1.5 text-xs font-medium max-sm:min-h-[2.75rem] " +
                  (period === p.id ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100")
                }
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
        <dl className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Cost" value={usd(total.cost_usd)} note={total.unpriced ? "at least: a model has no price set" : undefined} />
          <Stat label="Listings written" value={n(total.listings)} />
          <Stat label="Cost per listing" value={usd(total.cost_per_listing_usd, 4)} />
          <Stat label="Model calls" value={n(total.calls)} note={`${n(total.input_tokens)} in · ${n(total.output_tokens)} out tokens`} />
        </dl>
        <Models period={total} />
      </div>

      <div className="card divide-y divide-slate-100">
        {accounts.length === 0 && <p key="none" className="p-5 text-sm text-slate-500">No model calls in this period.</p>}
        {accounts.map((a) => (
          <Account key={a.id ?? "deleted"} account={a} period={period} open={open === (a.id ?? "deleted")} onToggle={() => setOpen(open === (a.id ?? "deleted") ? null : (a.id ?? "deleted"))} />
        ))}
      </div>

      <p className="text-xs text-slate-500">
        <span>
          Every model call is counted, including retries and attempts that produced no listing. Days before this
          page existed show only the listings that still existed at that point. Prices per million tokens:{" "}
        </span>
        <span>
          {Object.entries(cost.prices)
            .map(([model, p]) => `${model} $${p.input} in / $${p.output} out`)
            .join(" · ")}
        </span>
      </p>
    </div>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 font-display text-3xl leading-none tabular-nums text-slate-900">{value}</dd>
      {note && <p key="note" className="mt-1 text-[11px] text-slate-500">{note}</p>}
    </div>
  );
}

function Models({ period }: { period: AiPeriodCost }) {
  if (period.models.length === 0) return null;
  return (
    <div className="mt-5 overflow-x-auto">
      <table className="w-full min-w-[32rem] text-left text-xs">
        <thead>
          <tr className="border-b border-slate-200 text-slate-400">
            <th className="py-1.5 pr-3 font-medium">Model</th>
            <th className="py-1.5 pr-3 text-right font-medium">Calls</th>
            <th className="py-1.5 pr-3 text-right font-medium">Listings</th>
            <th className="py-1.5 pr-3 text-right font-medium">Input tokens</th>
            <th className="py-1.5 pr-3 text-right font-medium">Output tokens</th>
            <th className="py-1.5 text-right font-medium">Cost</th>
          </tr>
        </thead>
        <tbody>
          {period.models.map((m) => (
            <tr key={m.model} className="border-b border-slate-100 last:border-0">
              <td className="py-1.5 pr-3 text-slate-700">{m.model}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{n(m.calls)}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{n(m.listings)}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{n(m.input_tokens)}</td>
              <td className="py-1.5 pr-3 text-right tabular-nums">{n(m.output_tokens)}</td>
              <td className="py-1.5 text-right tabular-nums text-slate-900">{m.cost_usd === null ? "no price set" : usd(m.cost_usd, 4)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Account({ account, period, open, onToggle }: { account: AiAccountCost; period: Period; open: boolean; onToggle: () => void }) {
  const p = account[period];
  return (
    <div className="p-4 sm:p-5">
      <button type="button" onClick={onToggle} aria-expanded={open} className="grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 text-left max-sm:min-h-[2.75rem]">
        <span className="min-w-0">
          <span className="block truncate font-medium text-slate-800">{account.email ?? "Deleted accounts"}</span>
          <span className="block text-xs text-slate-500">
            <span>{n(p.listings)}</span><span> listings · </span><span>{n(p.calls)}</span><span> calls · </span>
            <span>{usd(p.cost_per_listing_usd, 4)}</span><span> per listing</span>
          </span>
        </span>
        <span className="text-right">
          <span className="block font-display text-2xl leading-none tabular-nums text-slate-900">{usd(p.cost_usd)}</span>
          <span className="text-[11px] text-slate-400">{open ? "hide models" : "by model"}</span>
        </span>
      </button>
      {open && <Models key="models" period={p} />}
    </div>
  );
}
