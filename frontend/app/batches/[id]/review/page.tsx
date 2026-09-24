"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Content, Pause, PublishJob } from "@/lib/types";
import { resumeTime } from "@/lib/format";
import { waitForJob } from "@/lib/jobs";
import { applyChange, cardKey, reviewActions } from "@/lib/review";
import { ReviewCard } from "@/components/ReviewCard";

interface Progress {
  label: string;
  total: number;
  done: number;
  failed: number;
  skipped: number;
  /** Still running when we stopped watching; the list refreshes when they land. */
  running: number;
  /** Queued, waiting for the daily Etsy reset; they run by themselves then. */
  paused: number;
  pause: Pause | null;
}

export default function ReviewPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const [items, setItems] = useState<Content[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [busy, setBusy] = useState(false);

  // Cards are keyed by their Etsy state (cardKey): a reload remounts only the cards
  // whose draft or live state changed, so text being edited elsewhere survives.
  const load = useCallback(() => {
    api
      .listContent(id)
      .then(setItems)
      .catch((e) => setError(String(e.message ?? e)));
  }, [id]);

  // A card approved something or finished its own job: the bulk buttons follow.
  const onCardChange = useCallback(
    (change: Partial<Content> & { id: string }) =>
      setItems((cur) => (cur ? applyChange(cur, change) : cur)),
    [],
  );

  useEffect(() => {
    load();
  }, [load]);

  // Watch a set of queued jobs until each settles. Every job that lands refreshes
  // the list at once, so its card and the bulk buttons change without a reload.
  async function pollJobs(jobs: PublishJob[], label: string, skipped: number) {
    setProgress({
      label, total: jobs.length, done: 0, failed: 0, skipped, running: 0, paused: 0, pause: null,
    });
    await Promise.all(
      jobs.map(async (j) => {
        const s = await waitForJob(j.job_id);
        if (s === null) {
          setProgress((p) => p && { ...p, running: p.running + 1 });
        } else if (s.pause) {
          const pause = s.pause;
          setProgress((p) => p && { ...p, paused: p.paused + 1, pause });
        } else if (s.status === "succeeded") {
          setProgress((p) => p && { ...p, done: p.done + 1 });
        } else {
          setProgress((p) => p && { ...p, failed: p.failed + 1 });
        }
        load();
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
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  const createDraftsAll = () => runBulk("Creating drafts", () => api.publishBatch(id));
  const publishAll = () => runBulk("Publishing", () => api.publishBatchLive(id));

  const actions = reviewActions(items ?? []);

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
              <span className="font-medium text-slate-700">{actions.approved}</span> of{" "}
              {items.length} approved
            </span>
            {/* Each action appears when it has work, from the listings as they are now. */}
            {actions.toDraft > 0 && (
              <button className="btn-secondary" onClick={createDraftsAll} disabled={busy}>
                Create drafts for all ({actions.toDraft})
              </button>
            )}
            {actions.toPublish > 0 && (
              <button className="btn-primary" onClick={publishAll} disabled={busy}>
                Publish all ({actions.toPublish})
              </button>
            )}
          </div>
        )}
      </div>

      {progress && (
        <div className="card space-y-2 p-3">
          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-700">{progress.label}…</span>
            <span className="text-xs text-slate-500">
              {progress.done + progress.failed + progress.paused + progress.running}/{progress.total}
              {progress.failed > 0 && ` · ${progress.failed} failed`}
              {progress.paused > 0 && ` · ${progress.paused} waiting`}
              {progress.running > 0 && ` · ${progress.running} still running`}
              {progress.skipped > 0 && ` · ${progress.skipped} skipped`}
            </span>
          </div>
          <div className="progress">
            <div
              className="progress-fill"
              style={{
                width: `${progress.total ? ((progress.done + progress.failed + progress.paused + progress.running) / progress.total) * 100 : 0}%`,
              }}
            />
          </div>
          {progress.pause && (
            <p role="status" className="text-xs text-amber-800">
              {progress.paused} {progress.paused === 1 ? "listing is" : "listings are"} queued, not
              failed. {progress.pause.message} That is around{" "}
              {resumeTime(progress.pause.resumes_at)} your time.
            </p>
          )}
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
            <ReviewCard key={cardKey(c)} initial={c} onChange={onCardChange} />
          ))}
        </div>
      )}
    </div>
  );
}
