"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { BatchSummary } from "@/lib/types";
import { StatusPill } from "@/components/StatusPill";

export default function Home() {
  const [batches, setBatches] = useState<BatchSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listBatches()
      .then(setBatches)
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Upload batches</h1>
          <p className="mt-1 text-sm text-slate-500">
            Upload a folder of your original designs, then review and approve the generated draft
            listings.
          </p>
        </div>
        <Link href="/upload" className="btn-primary">
          New upload
        </Link>
      </div>

      {error && (
        <div className="card border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{error}</div>
      )}

      {batches === null && !error && <p className="text-sm text-slate-400">Loading…</p>}

      {batches && batches.length === 0 && (
        <div className="card flex flex-col items-center gap-3 p-12 text-center">
          <p className="text-slate-500">No batches yet.</p>
          <Link href="/upload" className="btn-primary">
            Upload your first folder
          </Link>
        </div>
      )}

      {batches && batches.length > 0 && (
        <div className="grid gap-3">
          {batches.map((b) => (
            <Link
              key={b.id}
              href={`/batches/${b.id}`}
              className="card flex items-center justify-between p-4 transition-shadow hover:shadow-md"
            >
              <div className="flex items-center gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-900">
                      Batch {b.id.slice(0, 8)}
                    </span>
                    <StatusPill status={b.status} />
                  </div>
                  <p className="mt-0.5 text-xs text-slate-400">
                    {new Date(b.created_at).toLocaleString()}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-6 text-sm text-slate-600">
                <Metric label="files" value={b.asset_count} />
                <Metric label="processed" value={b.processed_count} />
                <Metric label="approved" value={b.approved_count} />
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="text-right">
      <div className="font-semibold tabular-nums text-slate-900">{value}</div>
      <div className="text-xs text-slate-400">{label}</div>
    </div>
  );
}
