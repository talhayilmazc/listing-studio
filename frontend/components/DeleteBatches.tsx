"use client";

import { useState } from "react";
import { api } from "@/lib/api";

export interface DeleteSummary {
  batches: number;
  files: number;
  /** Listings made from these batches that are on Etsy (drafts or live). */
  onEtsy: number;
}

/**
 * Confirm deleting batches (v7 §E3). Says exactly what goes (uploads, generated
 * content) and what stays: anything already on Etsy is not touched.
 */
export function DeleteBatches({
  ids,
  summary,
  onClose,
  onDeleted,
}: {
  ids: string[];
  summary: DeleteSummary;
  onClose: () => void;
  onDeleted: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      const res = ids.length === 1 ? await api.deleteBatch(ids[0]) : await api.deleteBatches(ids);
      onDeleted(
        `Deleted ${plural(res.deleted, "batch", "batches")} and ${plural(res.files_removed, "uploaded file", "uploaded files")}.` +
          (res.listings_left_on_etsy
            ? res.listings_left_on_etsy === 1
              ? " The listing made from it on Etsy was left as it is."
              : ` The ${res.listings_left_on_etsy} listings made from them on Etsy were left as they are.`
            : ""),
      );
    } catch (e: any) {
      setError(e.message ?? String(e));
      setBusy(false);
    }
  }

  return (
    <div role="alertdialog" aria-label="Delete batches" className="card space-y-3 border-rose-200 p-4 text-sm shadow-lg">
      <p className="font-medium text-slate-900">
        Delete {plural(summary.batches, "batch", "batches")}?
      </p>
      <p className="text-slate-600">
        This removes {plural(summary.files, "uploaded file", "uploaded files")} and every title, tag
        and description written for them, here in the app. It cannot be undone.
      </p>
      <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
        <strong>Nothing on Etsy is touched.</strong>{" "}
        {summary.onEtsy
          ? `${plural(summary.onEtsy, "listing", "listings")} made from ${summary.batches === 1 ? "it" : "them"} ${summary.onEtsy === 1 ? "is" : "are"} on Etsy as a draft or live, and stay${summary.onEtsy === 1 ? "s" : ""} exactly as ${summary.onEtsy === 1 ? "it is" : "they are"}; manage ${summary.onEtsy === 1 ? "it" : "them"} in Shop Manager. Scheduled go-lives from ${summary.batches === 1 ? "it" : "them"} are cancelled.`
          : "Drafts and live listings are only ever changed on Etsy by you."}
      </p>
      {error && <p className="text-xs text-rose-700">{error}</p>}
      <div className="flex gap-2">
        <button
          type="button"
          className="rounded-md bg-rose-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-rose-700 disabled:opacity-50"
          onClick={remove}
          disabled={busy}
        >
          {busy ? "Deleting…" : `Delete ${plural(summary.batches, "batch", "batches")}`}
        </button>
        <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  );
}
