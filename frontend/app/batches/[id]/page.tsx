"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { BatchDetail } from "@/lib/types";
import { StatusPill } from "@/components/StatusPill";
import { CostPanel } from "@/components/CostPanel";

export default function BatchPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const [batch, setBatch] = useState<BatchDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [failures, setFailures] = useState<{ original_filename: string; error: string }[]>([]);
  const [costKey, setCostKey] = useState(0);

  async function load() {
    try {
      setBatch(await api.getBatch(id));
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function generate() {
    setGenerating(true);
    setNotice(null);
    setFailures([]);
    try {
      const res = await api.generate(id);
      setNotice(`Generated ${res.generated}, failed ${res.failed}, skipped ${res.skipped}.`);
      setFailures(res.failures);
      await load();
      setCostKey((k) => k + 1);
    } catch (e: any) {
      setNotice(e.message ?? String(e));
    } finally {
      setGenerating(false);
    }
  }

  if (error) return <div className="card p-4 text-sm text-rose-700">{error}</div>;
  if (!batch) return <p className="text-sm text-slate-400">Loading…</p>;

  const withContent = batch.assets.filter((a) => a.has_content).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/" className="text-sm text-slate-400 hover:text-slate-600">
            ← Batches
          </Link>
          <div className="mt-1 flex items-center gap-2">
            <h1 className="text-2xl font-semibold text-slate-900">Batch {id.slice(0, 8)}</h1>
            <StatusPill status={batch.status} />
          </div>
          <p className="mt-1 text-sm text-slate-500">
            {batch.asset_count} files · {batch.processed_count} processed · {withContent} with
            content · {batch.approved_count} approved
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-secondary" onClick={generate} disabled={generating}>
            {generating ? "Generating…" : "Generate content"}
          </button>
          <Link href={`/batches/${id}/review`} className="btn-primary">
            Review listings
          </Link>
        </div>
      </div>

      {notice && (
        <div className="card border-brand-100 bg-brand-50 p-3 text-sm text-brand-800">{notice}</div>
      )}

      {failures.length > 0 && (
        <div className="card border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          <p className="font-medium">Generation failures</p>
          <ul className="mt-1 space-y-1">
            {failures.map((f, i) => (
              <li key={i} className="break-words">
                <span className="font-mono text-xs">{f.original_filename}</span>: {f.error}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {batch.assets.map((a) => (
          <div key={a.id} className="card overflow-hidden">
            <div className="aspect-square bg-slate-100">
              {a.status === "processed" ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={api.assetImage(a.id)}
                  alt={a.original_filename}
                  className="h-full w-full object-cover"
                />
              ) : (
                <div className="flex h-full items-center justify-center text-xs text-slate-400">
                  no preview
                </div>
              )}
            </div>
            <div className="space-y-1 p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium text-slate-800" title={a.original_filename}>
                  {a.original_filename}
                </span>
                {a.rank != null && (
                  <span className="shrink-0 text-xs text-slate-400">#{a.rank}</span>
                )}
              </div>
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs text-slate-500">{a.parsed_sku ?? "—"}</span>
                <StatusPill status={a.status} />
              </div>
              {a.has_content && (
                <span className="inline-block text-xs font-medium text-emerald-600">
                  ✓ content ready
                </span>
              )}
              {!a.has_content && a.error && (
                <p className="break-words text-xs text-rose-600" title={a.error}>
                  ⚠ {a.error}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>

      <CostPanel batchId={id} refreshKey={costKey} />
    </div>
  );
}
