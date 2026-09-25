"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { BatchActionItem, BatchActionPreview, BatchPublishResult } from "@/lib/types";

type Action = "drafts" | "publish";

const VERB: Record<Action, { button: string; confirm: string; acting: string; noun: string }> = {
  drafts: { button: "Create drafts", confirm: "Create", acting: "Creating", noun: "draft" },
  publish: { button: "Publish", confirm: "Publish", acting: "Publishing", noun: "listing" },
};

/** Skipped listings, grouped by why. */
function byReason(items: BatchActionItem[]): [string, BatchActionItem[]][] {
  const m = new Map<string, BatchActionItem[]>();
  for (const i of items) m.set(i.reason ?? "", [...(m.get(i.reason ?? "") ?? []), i]);
  return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
}

const plural = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;

/**
 * The Batches page's bulk bar: create drafts or publish across the selected
 * batches. Nothing happens until the seller has seen, per listing, what will be
 * done and what will be skipped and why. Only approved listings are acted on,
 * and publishing only makes existing drafts live (CLAUDE.md rule 3).
 */
export function BatchActions({
  selected,
  onClear,
  onDone,
}: {
  selected: string[];
  onClear: () => void;
  onDone: () => void;
}) {
  const [action, setAction] = useState<Action | null>(null);
  const [preview, setPreview] = useState<BatchActionPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  async function ask(a: Action) {
    setAction(a);
    setPreview(null);
    setResult(null);
    setError(null);
    setBusy(true);
    try {
      setPreview(await api.batchActionPreview(selected, a));
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    if (!action || !preview) return;
    setBusy(true);
    setError(null);
    try {
      const res: BatchPublishResult = await api.batchActionRun(selected, action);
      setResult(
        `${VERB[action].acting} ${plural(res.jobs.length, VERB[action].noun)}` +
          (res.skipped.length ? `; ${res.skipped.length} skipped.` : ".") +
          " Each batch's review page shows how they land.",
      );
      setPreview(null);
      setAction(null);
      onDone();
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  if (selected.length === 0 && !result) return null;

  return (
    <div className="sticky bottom-4 z-20 space-y-2">
      {(preview || error || result || (busy && action)) && (
        <div role="dialog" aria-label="Confirm the bulk action" className="card max-h-[50vh] space-y-3 overflow-y-auto p-4 text-sm shadow-lg">
          {busy && !preview && <p className="text-slate-500">Checking the selected batches…</p>}
          {error && <p className="text-rose-700">{error}</p>}
          {result && (
            <div className="flex items-start justify-between gap-3">
              <p className="text-slate-700">{result}</p>
              <button type="button" className="text-xs text-slate-400 underline" onClick={() => setResult(null)}>
                Dismiss
              </button>
            </div>
          )}
          {preview && action && (
            <>
              <p className="font-medium text-slate-900">
                {preview.act.length
                  ? `${VERB[action].confirm} ${plural(preview.act.length, VERB[action].noun)} from ${plural(selected.length, "batch", "batches")}`
                  : `Nothing to ${action === "drafts" ? "draft" : "publish"} in ${plural(selected.length, "batch", "batches")}`}
                {preview.skipped.length > 0 && (
                  <span className="font-normal text-slate-500"> · {preview.skipped.length} skipped</span>
                )}
              </p>
              <p className="text-xs text-slate-500">
                {action === "drafts"
                  ? "Only listings you approved become drafts; each goes to the shop it was written for."
                  : "Only drafts of listings you approved go live. Anything without a draft is left alone."}
              </p>
              {preview.act.length > 0 && (
                <details className="text-xs text-slate-600">
                  <summary className="cursor-pointer text-slate-700">
                    Will {action === "drafts" ? "draft" : "publish"} ({preview.act.length})
                  </summary>
                  <ul className="mt-1 space-y-0.5 pl-4">
                    {preview.act.map((i) => (
                      <li key={`${i.content_id}-${i.shop_name}`} className="truncate">
                        {i.title ?? i.original_filename}
                        {i.shop_name && <span className="text-slate-400"> · {i.shop_name}</span>}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {preview.skipped.length > 0 && (
                <div className="space-y-1 text-xs">
                  <p className="text-slate-700">Skipped, and why</p>
                  {byReason(preview.skipped).map(([reason, items]) => (
                    <details key={reason} className="text-amber-900">
                      <summary className="cursor-pointer">
                        <span className="tabular-nums font-medium">{items.length}</span> · {reason}
                      </summary>
                      <ul className="mt-0.5 space-y-0.5 pl-4 text-slate-600">
                        {items.map((i) => (
                          <li key={`${i.content_id}-${i.shop_name}`} className="truncate">
                            <span className="font-mono">{i.original_filename}</span>
                            {i.shop_name && <span className="text-slate-400"> · {i.shop_name}</span>}
                          </li>
                        ))}
                      </ul>
                    </details>
                  ))}
                </div>
              )}
              {action === "drafts" && !preview.fits && preview.message && (
                <p className="text-xs text-amber-800">{preview.message}</p>
              )}
              <div className="flex gap-2">
                <button
                  type="button"
                  className="btn-primary"
                  onClick={run}
                  disabled={busy || preview.act.length === 0 || (action === "drafts" && !preview.fits)}
                >
                  {busy
                    ? `${VERB[action].acting}…`
                    : `${VERB[action].confirm} ${plural(preview.act.length, VERB[action].noun)}`}
                </button>
                <button type="button" className="btn-secondary" onClick={() => setPreview(null)} disabled={busy}>
                  Cancel
                </button>
              </div>
            </>
          )}
        </div>
      )}
      {selected.length > 0 && (
        <div className="card flex flex-wrap items-center justify-between gap-3 px-4 py-3 shadow-lg">
          <span className="text-sm text-slate-700">
            <span className="tabular-nums font-medium">{selected.length}</span> {selected.length === 1 ? "batch" : "batches"} selected
          </span>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-secondary" onClick={() => ask("drafts")} disabled={busy}>
              Create drafts…
            </button>
            <button type="button" className="btn-primary" onClick={() => ask("publish")} disabled={busy}>
              Publish…
            </button>
            <button type="button" className="text-xs text-slate-500 underline" onClick={onClear} disabled={busy}>
              Clear selection
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
