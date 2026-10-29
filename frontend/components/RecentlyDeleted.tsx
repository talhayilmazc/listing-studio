"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DeletedBatch } from "@/lib/types";

/**
 * Batches deleted in the last 7 days, each with "Restore" (Listyro's records only; nothing on
 * Etsy was touched by deleting, and nothing is by restoring). Hidden when there are none.
 */
export function RecentlyDeleted({ reloadKey, onRestored }: { reloadKey: number; onRestored: (message: string) => void }) {
  const [rows, setRows] = useState<DeletedBatch[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.deletedBatches().then(setRows).catch(() => setRows([]));
  }, [reloadKey]);
  if (!rows.length) return null;
  const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });

  async function restore(b: DeletedBatch) {
    setBusy(b.id);
    setError(null);
    try {
      await api.restoreBatch(b.id);
      setRows((cur) => cur.filter((r) => r.id !== b.id));
      onRestored(`Restored ${b.name}.`);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card space-y-2 p-4" data-testid="recently-deleted">
      <h2 className="text-sm font-semibold text-slate-900">Recently deleted</h2>
      <p className="text-xs text-slate-600">Restore brings a batch back as it was, except schedules and queued drafts the delete cancelled. After 7 days it is removed for good.</p>
      {error && <p key="error" role="alert" className="text-xs text-rose-700">{error}</p>}
      <ul className="divide-y divide-slate-100">
        {rows.map((b) => (
          <li key={b.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
            <span className="min-w-0 truncate" title={b.name}>
              <span className="font-medium text-slate-900" translate="no">{b.name}</span>
              <span className="text-xs text-slate-500" translate="no">{` · deleted ${day(b.deleted_at)} · restore until ${day(b.restorable_until)}`}</span>
            </span>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => restore(b)} disabled={busy !== null}
              data-testid="restore-batch">
              {busy === b.id ? "Restoring…" : "Restore"}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
