"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { EmptyState, ListSkeleton } from "@/components/EmptyState";
import { REVIEW_EMPTY, approvedLine, queueOf, type Queue } from "@/lib/reviewQueue";
import { WORDS } from "@/lib/words";
import type { BatchSummary } from "@/lib/types";

/**
 * Review (app/onboarding-v1): every batch with listings waiting for the seller's approval, then
 * those approved and not sent yet. Each row opens that batch's review screen. Before this page
 * the only way in was Batches → a batch → "Review listings" (docs/app-audit.md, 6).
 */
export default function ReviewPage() {
  const [queue, setQueue] = useState<Queue | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listBatches()
      .then((b) => !cancelled && setQueue(queueOf(b)))
      .catch((e) => !cancelled && setFailed(e?.message ?? "The batches could not be read."));
    return () => {
      cancelled = true;
    };
  }, []);

  if (failed) {
    return (
      <div role="alert" className="card border-rose-300 p-4 text-sm text-rose-800">
        <span>{failed}</span>
      </div>
    );
  }
  if (queue === null) return <ListSkeleton rows={3} />;
  const nothing = !queue.toApprove.length && !queue.toSend.length && !queue.toWrite.length;
  if (nothing) return <EmptyState line={REVIEW_EMPTY} action={WORDS.upload} href="/upload" testId="review-empty" />;

  return (
    <div className="space-y-8">
      <p className="text-sm text-slate-700">Check what Listyro wrote, change anything, then approve it.</p>
      {queue.toApprove.length ? (
        <Section key="approve" title="Waiting for your approval" batches={queue.toApprove} action="Review" suffix="/review" primary />
      ) : null}
      {queue.toSend.length ? (
        <Section key="send" title="Approved, not sent yet" batches={queue.toSend} action="Send" suffix="/review#send" />
      ) : null}
      {queue.toWrite.length ? (
        <Section key="write" title="Not written yet" batches={queue.toWrite} action={WORDS.write} suffix="" />
      ) : null}
    </div>
  );
}

function Section({ title, batches, action, suffix, primary = false }: {
  title: string;
  batches: BatchSummary[];
  action: string;
  suffix: string;
  primary?: boolean;
}) {
  return (
    <section aria-labelledby={`review-${title}`}>
      <h2 id={`review-${title}`} className="mb-3 text-base font-bold text-slate-900">{title}</h2>
      <ul className="space-y-3">
        {batches.map((b, i) => (
          <li key={b.id} className="card flex min-h-[4.5rem] flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3" data-testid="review-row">
            <div className="min-w-0 flex-1">
              <p className="flex items-center gap-2 font-semibold text-slate-900">
                <span className="truncate" translate="no">{b.name}</span>
                {b.is_example ? (
                  <span key="ex" className="shrink-0 rounded-full bg-[var(--tag)] px-2 py-0.5 text-xs font-bold text-ink">Example</span>
                ) : null}
              </p>
              <p className="text-sm text-slate-600" translate="no">{approvedLine(b)}</p>
            </div>
            <Link
              href={`/batches/${b.id}${suffix}`}
              className={primary && i === 0 ? "btn-primary" : "btn-secondary"}
              data-testid="review-open"
            >
              {action}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
