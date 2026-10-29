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
 * Confirm deleting batches (v7 §E3). Only Listyro's records: the batches disappear now, can be
 * restored for 7 days ("Recently deleted" on the Batches page), then are removed for good.
 * Nothing on Etsy is touched.
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
      const until = res.restorable_until ? new Date(res.restorable_until).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : null;
      onDeleted(
        `Deleted ${plural(res.deleted, "batch", "batches")}.` +
          (until ? ` Restore ${res.deleted === 1 ? "it" : "them"} from "Recently deleted" below until ${until}.` : "") +
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
        <span>Delete <span>{plural(summary.batches, "batch", "batches")}</span>?</span>
      </p>
      <p className="text-slate-600" data-testid="delete-batches-text">
        <span>{`Only Listyro's records: ${summary.batches === 1 ? "it disappears" : "they disappear"} from Listyro now, with ${plural(summary.files, "uploaded file", "uploaded files")} and every title, tag and description written for ${summary.batches === 1 ? "it" : "them"}. You can restore ${summary.batches === 1 ? "it" : "them"} for 7 days from "Recently deleted" on this page; after that ${summary.batches === 1 ? "it is" : "they are"} removed for good.`}</span>
      </p>
      <p className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
        <strong>Nothing on Etsy is touched.</strong><span>{" "}
        <span>{summary.onEtsy
          ? `${plural(summary.onEtsy, "listing", "listings")} made from ${summary.batches === 1 ? "it" : "them"} ${summary.onEtsy === 1 ? "is" : "are"} on Etsy as a draft or live, and stay${summary.onEtsy === 1 ? "s" : ""} exactly as ${summary.onEtsy === 1 ? "it is" : "they are"}; manage ${summary.onEtsy === 1 ? "it" : "them"} in Shop Manager. Scheduled go-lives from ${summary.batches === 1 ? "it" : "them"} are cancelled.`
          : "Drafts and live listings are only ever changed on Etsy by you."}</span></span>
      </p>
      <p className="text-xs text-slate-500">
        The record of what was created and published from it is kept, so your published counts and history don&apos;t
        change. Scheduled go-lives and queued drafts are cancelled and are not brought back by Restore.
      </p>
      {error && <p key="p-66-6" className="text-xs text-rose-700">{error}</p>}
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
