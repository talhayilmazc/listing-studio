"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { BatchSummary } from "@/lib/types";

/** Told to anything else showing the batch's name (the page title) when it changes. */
export const BATCH_RENAMED = "batch-renamed";

/**
 * The form that names a batch. A batch is called after its contents
 * ("BR5229 + 4 more") until the seller names it; an empty name goes back to that.
 */
export function BatchNameForm({
  batch,
  onDone,
  onCancel,
}: {
  batch: Pick<BatchSummary, "id" | "name" | "named">;
  onDone: (updated: BatchSummary) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(batch.named ? batch.name : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.renameBatch(batch.id, value);
      window.dispatchEvent(new CustomEvent(BATCH_RENAMED, { detail: updated }));
      onDone(updated);
    } catch (e: any) {
      setError(e.message ?? String(e));
      setBusy(false);
    }
  }

  return (
    <form
      className="flex w-full flex-wrap items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
      onKeyDown={(e) => e.key === "Escape" && onCancel()}
    >
      <input
        ref={input}
        className="field min-w-0 flex-1 py-1.5"
        value={value}
        maxLength={80}
        onChange={(e) => setValue(e.target.value)}
        placeholder={batch.named ? "Name" : batch.name}
        aria-label="Batch name"
      />
      <button type="submit" className="btn-primary px-3 py-1.5 text-sm" disabled={busy}>
        {busy ? "Saving…" : "Save"}
      </button>
      <button type="button" className="btn-secondary px-3 py-1.5 text-sm" onClick={onCancel} disabled={busy}>
        Cancel
      </button>
      <p className="w-full text-xs text-slate-500">
        {error ? (
          <span className="text-rose-700">{error}</span>
        ) : (
          <span>Leave it empty to go back to the name taken from its contents.</span>
        )}
      </p>
    </form>
  );
}

/** A batch's name with a rename button beside it (the batch page). */
export function BatchName({
  batch,
  onRenamed,
  className = "",
}: {
  batch: Pick<BatchSummary, "id" | "name" | "named">;
  onRenamed: (updated: BatchSummary) => void;
  className?: string;
}) {
  const [editing, setEditing] = useState(false);
  if (editing) {
    return (
      <BatchNameForm
        batch={batch}
        onCancel={() => setEditing(false)}
        onDone={(updated) => {
          setEditing(false);
          onRenamed(updated);
        }}
      />
    );
  }
  return (
    <span className={`flex min-w-0 items-center gap-2 ${className}`}>
      <span translate="no" className="min-w-0 truncate" title={batch.name}>{batch.name}</span>
      <button
        type="button"
        onClick={() => setEditing(true)}
        className="tap shrink-0 rounded px-1.5 py-0.5 text-xs font-normal text-brand-700 underline hover:text-brand-800"
        aria-label={`Rename ${batch.name}`}
      >
        {batch.named ? "Rename" : "Name it"}
      </button>
    </span>
  );
}
