"use client";

import { headline, tone } from "@/lib/batchStatus";

const TONES: Record<ReturnType<typeof tone>, string> = {
  danger: "border-rose-300 bg-rose-50 text-rose-800",
  warning: "border-amber-300 bg-amber-50 text-amber-900",
  done: "border-emerald-300 bg-emerald-50 text-emerald-800",
  neutral: "border-slate-300 bg-white text-slate-700",
};

/**
 * The one badge for a batch, wherever it is shown (Batches, the batch page header, Overview):
 * its headline status from the one definition (backend pipeline/batch_status.py), with the full
 * list of its statuses in the tooltip. Before this, each place showed the upload's own state
 * ("ready") and the filter its own idea of the listings'.
 */
export function BatchStatusBadge({ statuses }: { statuses: readonly string[] | null | undefined }) {
  const main = headline(statuses);
  if (!main) return null;
  const all = (statuses ?? []).length > 1 ? `${main.hint}. Also: ${(statuses ?? []).filter((s) => s !== main.key).join(", ").replace(/_/g, " ")}` : main.hint;
  return (
    <span translate="no" title={all} data-testid="batch-badge" data-status={main.key}
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium leading-5 ${TONES[tone(main.key)]}`}>
      {main.label}
    </span>
  );
}
