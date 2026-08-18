"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { BatchCost } from "@/lib/types";

function usd(v: string): string {
  return `$${Number(v).toFixed(4)}`;
}

export function CostPanel({ batchId, refreshKey = 0 }: { batchId: string; refreshKey?: number }) {
  const [cost, setCost] = useState<BatchCost | null>(null);

  useEffect(() => {
    api.batchCost(batchId).then(setCost).catch(() => setCost(null));
  }, [batchId, refreshKey]);

  if (!cost) return null;

  return (
    <div className="card p-5">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-slate-700">Generation cost</h2>
        <span className="text-xs text-slate-400">from stored token counts</span>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-4">
        <Stat label="Batch total" value={usd(cost.total_cost_usd)} accent />
        <Stat label="Input tokens" value={cost.total_input_tokens.toLocaleString()} />
        <Stat label="Output tokens" value={cost.total_output_tokens.toLocaleString()} />
      </div>
      {cost.listings.length > 0 && (
        <div className="mt-4 overflow-hidden rounded-lg border border-slate-100">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-3 py-2 font-medium">Listing</th>
                <th className="px-3 py-2 text-right font-medium">In</th>
                <th className="px-3 py-2 text-right font-medium">Out</th>
                <th className="px-3 py-2 text-right font-medium">Cost</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {cost.listings.map((l) => (
                <tr key={l.content_id}>
                  <td className="px-3 py-2 font-mono text-xs text-slate-500">
                    {l.content_id.slice(0, 8)}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                    {l.input_tokens.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-slate-600">
                    {l.output_tokens.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums font-medium text-slate-800">
                    {usd(l.cost_usd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <div className={`text-lg font-semibold tabular-nums ${accent ? "text-brand-700" : "text-slate-900"}`}>
        {value}
      </div>
      <div className="text-xs text-slate-400">{label}</div>
    </div>
  );
}
