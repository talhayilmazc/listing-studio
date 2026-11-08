/**
 * The Review page's lists (app/(app)/review/page.tsx): the batches with a listing waiting for
 * approval, and those approved but not sent anywhere yet, newest first. From each batch's own
 * statuses (backend pipeline/batch_status.py), never guessed from counts.
 */
import type { BatchSummary } from "./types";

export type Queue = { toApprove: BatchSummary[]; toSend: BatchSummary[]; toWrite: BatchSummary[] };

const has = (b: BatchSummary, key: string) => (b.statuses ?? []).includes(key);

export function queueOf(batches: BatchSummary[]): Queue {
  const newest = [...batches].sort((a, b) => b.created_at.localeCompare(a.created_at));
  return {
    toApprove: newest.filter((b) => has(b, "in_review")),
    // Approved and not drafted: the examples are approved only to see how it works; they never go out.
    toSend: newest.filter((b) => has(b, "approved") && !has(b, "in_review") && !b.is_example),
    toWrite: newest.filter((b) => has(b, "not_written") && !has(b, "in_review")),
  };
}

export const REVIEW_EMPTY = "Listings Listyro writes wait here for you to check and approve.";

/** "4 of 6 approved". */
export function approvedLine(b: BatchSummary): string {
  const total = b.groups ?? b.processed_count;
  return `${b.approved_count} of ${total} approved`;
}
