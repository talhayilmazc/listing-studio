"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Content, PublishJob } from "@/lib/types";
import { ReviewCard } from "@/components/ReviewCard";

interface Progress {
  label: string;
  total: number;
  done: number;
  failed: number;
  skipped: number;
}

export default function ReviewPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const [items, setItems] = useState<Content[] | null>(null);
  const [version, setVersion] = useState(0); // bump to remount cards after bulk actions
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .listContent(id)
      .then((c) => {
        setItems(c);
        setVersion((v) => v + 1);
      })
      .catch((e) => setError(String(e.message ?? e)));
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll a set of queued jobs to completion, updating the progress counters.
  async function pollJobs(jobs: PublishJob[], label: string, skipped: number) {
    setProgress({ label, total: jobs.length, done: 0, failed: 0, skipped });
    await Promise.all(
      jobs.map(async (j) => {
        for (let i = 0; i < 60; i++) {
          await new Promise((r) => setTimeout(r, 1500));
          const s = await api.jobStatus(j.job_id);
          if (s.status === "succeeded") {
            setProgress((p) => p && { ...p, done: p.done + 1 });
            return;
          }
          if (s.status === "failed") {
            setProgress((p) => p && { ...p, failed: p.failed + 1 });
            return;
          }
        }
        setProgress((p) => p && { ...p, failed: p.failed + 1 });
      }),
    );
  }

  async function runBulk(
    label: string,
    call: () => Promise<{ jobs: PublishJob[]; skipped: { reason: string }[] }>,
  ) {
    setBusy(true);
    setError(null);
    try {
      const res = await call();
      await pollJobs(res.jobs, label, res.skipped.length);
      load(); // reflect new draft/active links on the cards
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  const createDraftsAll = () => runBulk("Creating drafts", () => api.publishBatch(id));
  const publishAll = () => runBulk("Publishing", () => api.publishBatchLive(id));

  const approvedCount = (items ?? []).filter((c) => c.approved).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0">
          <Link href={`/batches/${id}`} className="text-sm text-slate-400 hover:text-slate-600">
            ← Batch {id.slice(0, 8)}
          </Link>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Edit and approve each listing, then create drafts. Publishing is always a separate,
            explicit step.
          </p>
        </div>
        {items && items.length > 0 && (
          <div className="flex items-center gap-3">
            <span className="text-xs tabular-nums text-slate-500">
              <span className="font-medium text-slate-700">{approvedCount}</span> of {items.length}{" "}
              approved
            </span>
            <button
              className="btn-secondary"
              onClick={createDraftsAll}
              disabled={busy || approvedCount === 0}
            >
              Create drafts for all
            </button>
            <button
              className="btn-primary"
              onClick={publishAll}
              disabled={busy || approvedCount === 0}
            >
              Publish all
            </button>
          </div>
        )}
      </div>

      {progress && (
        <div className="card space-y-2 p-3">
          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-700">{progress.label}…</span>
            <span className="text-xs text-slate-500">
              {progress.done + progress.failed}/{progress.total}
              {progress.failed > 0 && ` · ${progress.failed} failed`}
              {progress.skipped > 0 && ` · ${progress.skipped} skipped`}
            </span>
          </div>
          <div className="progress">
            <div
              className="progress-fill"
              style={{
                width: `${progress.total ? ((progress.done + progress.failed) / progress.total) * 100 : 0}%`,
              }}
            />
          </div>
        </div>
      )}

      {error && <div className="card p-4 text-sm text-rose-700">{error}</div>}

      {items === null && !error && <p className="text-sm text-slate-400">Loading…</p>}

      {items && items.length === 0 && (
        <div className="card flex flex-col items-center gap-3 p-12 text-center">
          <p className="text-slate-500">No generated content yet for this batch.</p>
          <Link href={`/batches/${id}`} className="btn-primary">
            Generate content
          </Link>
        </div>
      )}

      {items && items.length > 0 && (
        <div className="space-y-5">
          {items.map((c) => (
            <ReviewCard key={`${c.id}-${version}`} initial={c} />
          ))}
        </div>
      )}
    </div>
  );
}
