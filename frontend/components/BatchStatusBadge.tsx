"use client";

import { BATCH_STATUSES, headline, tone } from "@/lib/batchStatus";

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
export function BatchStatusBadge({
  statuses,
  main: mainKey,
  notes = [],
}: {
  statuses: readonly string[] | null | undefined;
  /** The server's badge: the stage most of its listings are in (A4); else worked out here. */
  main?: string | null;
  /** Beside the badge: what went wrong with some listings ("1 draft failed"). */
  notes?: readonly string[];
}) {
  const main = BATCH_STATUSES.find((s) => s.key === mainKey) ?? headline(statuses);
  if (!main) return null;
  const all = (statuses ?? []).length > 1 ? `${main.hint}. Also: ${(statuses ?? []).filter((s) => s !== main.key).join(", ").replace(/_/g, " ")}` : main.hint;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span translate="no" title={all} data-testid="batch-badge" data-status={main.key}
        className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium leading-5 ${TONES[tone(main.key)]}`}>
        {main.label}
      </span>
      {notes.length > 0 && (
        <span key="notes" translate="no" className="text-xs text-amber-800" data-testid="batch-notes">{notes.join(" · ")}</span>
      )}
    </span>
  );
}
